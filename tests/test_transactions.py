"""
Transaction boundaries.

Before db.tx(), sqlite3 opened a transaction implicitly at the first write
and closed it at whatever commit() came next, so the boundary was wherever a
commit happened to sit rather than where anyone had decided it should be. And
nothing ever called rollback: a failed multi-statement write was undone only
because closing a connection discards an open transaction — which worked, but
by accident, and only if the connection was actually closed.
"""

import pytest

import access
import db
from fixtures import _ins, add_session, later_today


def clients(repo):
    return repo.count("clients")


def add(repo, name):
    return _ins(repo, "clients", name_en=name, created_at=db.now(), active=1)


# ---------------------------------------------------------------- basics

def test_a_block_that_finishes_is_committed(repo):
    with repo.begin():
        add(repo, "Kept")
    assert clients(repo) == 1
    assert not repo.in_transaction


def test_a_block_that_raises_is_rolled_back(repo):
    with pytest.raises(ValueError):
        with repo.begin():
            add(repo, "Doomed")
            raise ValueError("something went wrong halfway")
    assert clients(repo) == 0
    assert not repo.in_transaction


def test_every_write_in_a_failed_block_goes_back(repo):
    """Not just the last one — the whole block is one unit."""
    with pytest.raises(RuntimeError):
        with repo.begin():
            add(repo, "One")
            add(repo, "Two")
            add(repo, "Three")
            raise RuntimeError("boom")
    assert clients(repo) == 0


def test_a_write_outside_a_block_autocommits(repo):
    """A single statement needs no ceremony; isolation_level=None commits it."""
    add(repo, "Loner")
    assert clients(repo) == 1
    assert not repo.in_transaction


# ---------------------------------------------------------------- nesting

def test_an_inner_block_does_not_commit_early(repo):
    """
    The outermost block owns the transaction. This is what makes
    swap_and_check_in() one transaction rather than three.
    """
    with repo.begin():
        with repo.begin():
            add(repo, "Inner")
        # Still inside the outer block: nothing is visible to a second
        # connection yet, and the transaction is still open.
        assert repo.in_transaction
    assert clients(repo) == 1


def test_a_failure_after_an_inner_block_rolls_the_inner_one_back_too(repo):
    """
    There are no savepoints, deliberately. Nothing in this app wants to
    half-succeed: a swap that moves a booking and then fails to check the
    client in must not leave the booking moved.
    """
    with pytest.raises(ValueError):
        with repo.begin():
            with repo.begin():
                add(repo, "Inner")
            add(repo, "Outer")
            raise ValueError("failed after the inner block closed")
    assert clients(repo) == 0


def test_nesting_survives_three_levels(repo):
    with repo.begin():
        with repo.begin():
            with repo.begin():
                add(repo, "Deep")
    assert clients(repo) == 1
    assert not repo.in_transaction


def test_the_connection_is_reusable_after_a_rollback(repo):
    with pytest.raises(ValueError):
        with repo.begin():
            add(repo, "Doomed")
            raise ValueError
    with repo.begin():
        add(repo, "Fine")
    assert clients(repo) == 1


# ------------------------------------------------------- the write lock

@pytest.mark.sqlite_only
def test_a_block_takes_the_write_lock_at_the_top(repo):
    """
    BEGIN IMMEDIATE, not BEGIN. A deferred transaction takes the lock at the
    first write, so book()'s count-then-insert would take its count without
    holding it and two concurrent sales could each see room for the last
    slot.

    SQLite-only: this is about a second connection being locked out of one
    file. MongoDB's answer to the same problem is document-level and is
    covered by test_parity's rollback check instead.
    """
    other = db.connect()
    # Don't sit through connect()'s 3s busy_timeout just to be told no.
    other.execute("PRAGMA busy_timeout=0")
    try:
        with repo.begin():
            # No write yet — under a deferred transaction nothing would be
            # locked at this point.
            with pytest.raises(Exception) as exc:
                other.execute("BEGIN IMMEDIATE")
            assert "locked" in str(exc.value).lower(), exc.value
    finally:
        other.close()


def a_session_today_they_are_not_booked_into(a):
    """
    What a swap is actually for: the client turns up for something running
    today that they hold no slot in. today_ballet is already theirs.
    """
    return add_session(a.repo, a.flex, a.bea, later_today(2), 1.0,
                       status="scheduled")


# ------------------------------------------------- the boundaries that moved

def test_a_failed_swap_leaves_the_booking_where_it_was(academy, monkeypatch):
    """
    swap_and_check_in() used to be three transactions for one press of the
    confirm button: move_booking, then _log, then check_in, each committing.
    A crash between the move and the check-in left the booking sitting on a
    session the client was never marked present at, and no event logged to
    say what had happened.
    """
    a = academy
    repo = a.repo
    target = a_session_today_they_are_not_booked_into(a)
    give_up = repo.find_one("bookings", {
        "client_id": a.dual, "status": "booked",
        "session_id": {"ne": a.today_ballet}})["session_id"]
    events_before = repo.count("access_events")

    monkeypatch.setattr(access, "check_in",
                        lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        access.swap_and_check_in(repo, a.dual, give_up, target)

    assert repo.count("bookings", {"client_id": a.dual,
                                   "session_id": give_up}) == 1, \
        "the booking was not moved"
    assert repo.count("access_events") == events_before, \
        "and no event was logged"


def test_a_successful_swap_moves_the_booking_and_checks_in(academy):
    """The happy path still works — one transaction, same result."""
    a = academy
    repo = a.repo
    target = a_session_today_they_are_not_booked_into(a)
    give_up = repo.find_one("bookings", {
        "client_id": a.dual, "status": "booked",
        "session_id": {"ne": a.today_ballet}})["session_id"]

    r = access.swap_and_check_in(repo, a.dual, give_up, target)

    assert r["ok"], r
    assert repo.find_one("bookings", {"client_id": a.dual,
                                     "session_id": target})["status"] == "present"
    assert repo.count("bookings", {"client_id": a.dual,
                                   "session_id": give_up}) == 0


def test_the_sweep_and_the_freezes_it_lifts_are_one_transaction(academy, monkeypatch):
    """
    settle_past_sessions() calls lift_expired_freezes(), which called
    unfreeze_plan() once per due row — each committing on its own. A failure
    partway through left some plans unfrozen and others not, with the sweep
    that triggered it not applied at all.
    """
    from datetime import date, timedelta
    a = academy
    repo = a.repo
    access.freeze_plan(repo, a.dual_ballet_plan,
                       from_date=(date.today() - timedelta(days=8)).isoformat(),
                       until=(date.today() - timedelta(days=1)).isoformat())
    assert access.plan_state(repo, a.dual_ballet_plan)["frozen"] is True

    # Fail after the freeze has been lifted but before the sweep completes.
    real = access.lift_expired_freezes

    def lift_then_fail(c):
        real(c)
        raise RuntimeError("crash after lifting")

    monkeypatch.setattr(access, "lift_expired_freezes", lift_then_fail)
    with pytest.raises(RuntimeError):
        access.settle_past_sessions(repo)

    assert access.plan_state(repo, a.dual_ballet_plan)["frozen"] is True, \
        "the lift went back with the sweep"


def test_refresh_expiry_is_committed_by_whoever_opened_the_block(academy):
    """
    refresh_expiry() deliberately does not commit. That used to be an
    invariant held by a comment; it is now structural — it is always called
    inside a caller's tx().
    """
    a = academy
    repo = a.repo
    before = access.plan_state(repo, a.dual_ballet_plan)["expires_on"]
    booked = repo.find("bookings", {"subscription_id": a.dual_ballet_plan})
    when = {s["id"]: s["starts_at"] for s in repo.find(
        "sessions", {"id": {"in": [b["session_id"] for b in booked]}})}
    latest = max(booked, key=lambda b: when[b["session_id"]])["session_id"]

    access.unbook(repo, a.dual, latest)

    assert not repo.in_transaction, "unbook closed its own block"
    after = access.plan_state(repo, a.dual_ballet_plan)["expires_on"]
    assert after < before, f"{before} -> {after}"
