"""
The two backends, side by side, answering the same questions.

This is the proof the abstraction holds. Everything else checks that one
backend behaves correctly; this checks that they behave *identically* — which
is the only claim that matters once the app can be pointed at either.

It is affordable because of one decision: integer ids from a counters
collection rather than ObjectIds. Both backends allocate 1, 2, 3… from an
empty database, so two documents built by the same sequence of calls compare
equal field for field. With ObjectIds every comparison would need an id map
and the tests would be checking their own translation layer.

Skipped entirely when MB_TEST_MONGO_URI is unset.
"""

import os
import uuid

import pytest

import access
import db
import repo as data
from fixtures import build_academy, later_today

pytestmark = pytest.mark.parity

# Tokens carry a random nonce, so the two backends legitimately mint
# different ones for the same client. Compared by shape, never by value.
VOLATILE = ("token", "card_url", "created_at", "issued_at", "scanned_at",
            "checked_in_at", "confirmed_at")


@pytest.fixture
def frozen_clock(monkeypatch):
    """
    One `db.now()` for the whole test, shared by both backends.

    Without this, five of these tests failed on differences of one or two
    seconds — `1789194822` against `1789194823`. Not a divergence: the two
    academies are built one after the other, SQLite in milliseconds and
    MongoDB in about five seconds, so anything derived from "now" at build
    time (the session running later today, and everything computed from it)
    legitimately differs.

    Scrubbing those fields would have been the wrong fix: `starts_at` is
    exactly the kind of value these tests exist to compare. Pinning the
    clock keeps them comparable while leaving them meaningful.
    """
    fixed = db.now()
    monkeypatch.setattr(db, "now", lambda: fixed)
    return fixed


@pytest.fixture
def both(tmp_path, monkeypatch):
    """One repository per backend, each over its own empty database."""
    uri = os.environ.get("MB_TEST_MONGO_URI")
    if not uri:
        pytest.skip("MB_TEST_MONGO_URI is not set")

    monkeypatch.setenv("MB_DB_BACKEND", "sqlite")
    monkeypatch.setenv("MB_SQLITE_PATH", str(tmp_path / "parity.db"))
    db.init()
    lite = data.connect()

    name = f"mbtest_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("MB_MONGO_URI", uri)
    monkeypatch.setenv("MB_MONGO_DB", name)
    monkeypatch.setenv("MB_MONGO_ALLOW_DROP", "1")
    monkeypatch.setenv("MB_DB_BACKEND", "mongo")
    mongo = data.connect()
    mongo.drop_all()

    yield lite, mongo

    lite.close()
    from pymongo import MongoClient
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.drop_database(name)
    finally:
        client.close()
    mongo.close()


def scrub(value):
    """Drop the fields that are legitimately different (see VOLATILE)."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in sorted(value.items())
                if k not in VOLATILE}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, float):
        # SQLite returns REAL and Mongo a double; both are IEEE 754, but a
        # sum can differ in the last bit.
        return round(value, 6)
    return value


def same(lite_result, mongo_result, what):
    assert scrub(lite_result) == scrub(mongo_result), (
        f"{what} differs between backends\n"
        f"  sqlite: {scrub(lite_result)!r}\n"
        f"  mongo : {scrub(mongo_result)!r}")


def on_both(both, fn, what):
    """Run the same callable against each repo and compare what comes back."""
    results = [fn(r) for r in both]
    same(results[0], results[1], what)
    return results[0]


@pytest.fixture
def built(both, frozen_clock):
    """The same academy constructed on each backend."""
    academies = [build_academy(r) for r in both]
    # The fixture only uses repository primitives, so the ids it hands back
    # must match. If they do not, nothing below is comparable.
    for field in ("dual", "ballet", "flex", "today_ballet", "dual_ballet_plan"):
        assert getattr(academies[0], field) == getattr(academies[1], field), field
    return both, academies


# ---------------------------------------------------------------- documents

def test_the_same_calls_build_the_same_documents(built):
    repos, _ = built
    for coll in ("instructors", "classes", "sessions", "clients",
                 "subscriptions", "bookings", "credentials",
                 "instructor_hours"):
        on_both(repos, lambda r, c=coll: r.find(c), f"{coll} contents")


def test_ids_start_from_one_on_both(built):
    repos, _ = built
    on_both(repos, lambda r: [c["id"] for c in r.find("clients")], "client ids")


# ---------------------------------------------------------------- reads

def test_plan_state_agrees(built):
    repos, acs = built
    on_both(repos, lambda r: access.plan_state(r, acs[0].dual_ballet_plan),
            "plan_state")


def test_plan_states_bulk_agrees(built):
    repos, acs = built
    ids = [acs[0].dual_ballet_plan, acs[0].dual_flex_plan, acs[0].solo_ballet_plan]
    on_both(repos, lambda r: access.plan_states(r, ids), "plan_states")


def test_active_plan_agrees(built):
    repos, acs = built
    on_both(repos, lambda r: access.active_plan(r, acs[0].dual, acs[0].ballet),
            "active_plan for a class")
    on_both(repos, lambda r: access.active_plan(r, acs[0].dual),
            "active_plan with no class")


def test_active_plans_agrees(built):
    repos, acs = built
    on_both(repos, lambda r: access.active_plans(r, acs[0].dual), "active_plans")


def test_month_intake_agrees(built):
    repos, _ = built
    from datetime import date
    on_both(repos, lambda r: access.month_intake(r, date.today().strftime("%Y-%m")),
            "month_intake")


def test_expected_today_agrees(built):
    repos, _ = built
    on_both(repos, access.expected_today, "expected_today")


def test_instructor_hours_agree(built):
    repos, acs = built
    from datetime import date, timedelta
    a = (date.today() - timedelta(days=30)).isoformat()
    b = date.today().isoformat()
    on_both(repos, lambda r: access.logged_hours(r, acs[0].ana, a, b), "logged_hours")
    on_both(repos, lambda r: access.taught_hours(r, acs[0].ana, a, b), "taught_hours")


def test_session_roster_agrees(built):
    repos, acs = built
    on_both(repos, lambda r: access.session_roster(r, acs[0].today_ballet),
            "session_roster")


def test_swap_options_agree(built):
    repos, acs = built
    on_both(repos, lambda r: access.swap_options(r, acs[0].dual), "swap_options")


def test_the_port_methods_agree(built):
    repos, acs = built
    a = acs[0]
    checks = [
        ("sessions_in_range", lambda r: r.sessions_in_range(*access.day_bounds())),
        ("classes_with_counts", lambda r: r.classes_with_counts(1)),
        ("class_students", lambda r: r.class_students(a.ballet)),
        ("class_sessions", lambda r: r.class_sessions(a.ballet, 10)),
        ("search_clients", lambda r: r.search_clients(1, "")),
        ("search_clients q", lambda r: r.search_clients(1, "an")),
        ("client_cards", lambda r: r.client_cards(a.dual)),
        ("client_upcoming", lambda r: r.client_upcoming(a.dual, db.now())),
        ("client_history", lambda r: r.client_history(a.dual, db.now(), 100)),
        ("plan_sessions", lambda r: r.plan_sessions(a.dual, a.dual_ballet_plan)),
        ("client_totals", lambda r: r.client_totals(a.dual)),
        ("recent_attendance", lambda r: r.recent_attendance(a.dual, 4)),
        ("next_booked_session", lambda r: r.next_booked_session(a.dual, db.now())),
        ("client_day_bookings",
         lambda r: r.client_day_bookings(a.dual, *access.day_bounds())),
        ("giveable_slots", lambda r: r.giveable_slots(a.dual, db.now())),
        ("day_attendance_totals",
         lambda r: r.day_attendance_totals(*access.day_bounds())),
        ("active_plans_with_clients", lambda r: r.active_plans_with_clients()),
        ("active_plans_for", lambda r: r.active_plans_for([a.dual, a.solo_ballet])),
        ("plan_counts_bulk",
         lambda r: r.plan_counts_bulk([a.dual_ballet_plan, a.dual_flex_plan])),
        ("attendance_counts", lambda r: r.attendance_counts(a.ballet_sessions[:5])),
        ("card_counts_bulk", lambda r: r.card_counts_bulk([a.dual, a.planless])),
        ("taught_totals_bulk", lambda r: r.taught_totals_bulk([a.ana, a.bea])),
        ("last_session_ts", lambda r: r.last_session_ts(a.dual_ballet_plan)),
        ("max_starts_at", lambda r: r.max_starts_at(a.ballet_sessions[:4])),
        ("slot_conflict", lambda r: r.slot_conflict(
            later_today(3), later_today(3) + 3600)),
    ]
    for what, fn in checks:
        on_both(repos, fn, what)


# ---------------------------------------------------------------- writes

def test_a_scan_and_check_in_agree(built):
    repos, acs = built
    def scan(r):
        v = access.verify(r, acs[repos.index(r)].dual_ballet_card)
        if v.get("granted"):
            access.check_in(r, v["event_id"])
        return v
    on_both(repos, scan, "verify + check_in")
    on_both(repos, lambda r: access.plan_state(r, acs[0].dual_ballet_plan),
            "plan_state after a check-in")


def test_a_second_scan_agrees(built):
    repos, acs = built
    for r, a in zip(repos, acs):
        v = access.verify(r, a.dual_ballet_card)
        access.check_in(r, v["event_id"])
    def again(r):
        return access.verify(r, acs[repos.index(r)].dual_ballet_card)
    on_both(repos, again, "the second scan of the day")


def test_freezing_agrees(built):
    repos, acs = built
    on_both(repos, lambda r: access.freeze_plan(r, acs[0].dual_ballet_plan,
                                                reason="travelling"), "freeze_plan")
    on_both(repos, lambda r: access.plan_state(r, acs[0].dual_ballet_plan),
            "plan_state while frozen")
    on_both(repos, lambda r: access.unfreeze_plan(r, acs[0].dual_ballet_plan),
            "unfreeze_plan")


def test_selling_a_plan_agrees(built):
    repos, acs = built
    a = acs[0]
    on_both(repos, lambda r: access.add_plan(
        r, a.planless, a.ballet, "4 sessions", 4, a.ballet_sessions[-4:],
        price=900.0), "add_plan")
    on_both(repos, lambda r: r.find("subscriptions", {"client_id": a.planless}),
            "the plan that was sold")


def test_booking_and_unbooking_agree(built):
    repos, acs = built
    a = acs[0]
    target = a.ballet_sessions[-1]
    on_both(repos, lambda r: access.book(r, a.solo_ballet, target,
                                         a.solo_ballet_plan), "book")
    on_both(repos, lambda r: access.plan_state(r, a.solo_ballet_plan),
            "plan_state after booking")
    on_both(repos, lambda r: access.unbook(r, a.solo_ballet, target), "unbook")
    on_both(repos, lambda r: access.plan_state(r, a.solo_ballet_plan),
            "plan_state after unbooking")


def test_the_absent_sweep_agrees(built):
    repos, acs = built
    a = acs[0]
    for r in repos:
        from fixtures import add_session
        sid = add_session(r, a.ballet, a.ana, db.now() - 4 * 3600, 1.5,
                          status="scheduled")
        r.insert("bookings", {"client_id": a.planless, "session_id": sid,
                              "subscription_id": None, "status": "booked",
                              "created_at": db.now()})
    on_both(repos, access.settle_past_sessions, "settle_past_sessions")
    on_both(repos, lambda r: r.find("bookings", {"client_id": a.planless}),
            "the swept booking")


def test_deleting_a_plan_agrees(built):
    repos, acs = built
    on_both(repos, lambda r: access.delete_plan(r, acs[0].dual_flex_plan),
            "delete_plan")
    on_both(repos, lambda r: r.find("credentials", {"client_id": acs[0].dual}),
            "credentials afterwards")


def test_archiving_a_class_agrees(built):
    repos, acs = built
    on_both(repos, lambda r: access.delete_class(r, acs[0].ballet), "delete_class")
    on_both(repos, lambda r: r.find("sessions", {"class_id": acs[0].ballet}),
            "sessions afterwards")


# ---------------------------------------------------------------- the traps

def test_a_range_comparison_does_not_match_null_on_either_backend(both, frozen_clock):
    """
    The single most likely silent divergence. BSON sorts null before every
    string, so `{"frozen_until": {"$lte": today}}` matches a null on Mongo
    and never on SQLite. The filter compiler adds `$ne: None`; this checks it.
    """
    from datetime import date
    def probe(r):
        cid = r.insert("clients", {"name_en": "X", "created_at": db.now(),
                                   "active": 1})
        cls = r.insert("classes", {"name": "C", "colour": "#87438E",
                                   "duration_hours": 1.0})
        r.insert("subscriptions", {
            "client_id": cid, "class_id": cls, "plan": "p", "sessions_total": 4,
            "starts_on": "2026-01-01", "expires_on": "2026-01-01",
            "frozen_on": None, "frozen_until": None,
            "active": 1, "created_at": db.now()})
        return r.count("subscriptions",
                       {"frozen_until": {"lte": date.today().isoformat()}})
    results = [probe(r) for r in both]
    assert results == [0, 0], f"a null frozen_until was matched: {results}"


def test_an_all_null_document_round_trips_identically(both, frozen_clock):
    """Missing and null are the same thing on SQLite and not on Mongo."""
    def probe(r):
        cid = r.insert("clients", {"name_en": "Sparse", "created_at": db.now()})
        return r.get("clients", cid)
    same(*[probe(r) for r in both], "a document with everything left out")


def test_insert_ignore_agrees_on_a_duplicate(both, frozen_clock):
    def probe(r):
        cid = r.insert("clients", {"name_en": "X", "created_at": db.now(),
                                   "active": 1})
        cls = r.insert("classes", {"name": "C", "colour": "#87438E",
                                   "duration_hours": 1.0})
        sid = r.insert("sessions", {"class_id": cls, "starts_at": 1,
                                    "duration_hours": 1.0, "ends_at": 3601})
        doc = {"client_id": cid, "session_id": sid, "status": "booked",
               "created_at": db.now()}
        return [r.insert_ignore("bookings", doc) is not None,
                r.insert_ignore("bookings", doc) is not None,
                r.count("bookings")]
    same(*[probe(r) for r in both], "insert_ignore on a duplicate")


def test_an_aggregate_over_nothing_is_zero_not_none(both):
    same(*[r.plan_counts_bulk([9999])[9999] for r in both], "plan_counts of nothing")
    same(*[r.attendance_counts([9999])[9999] for r in both],
         "attendance_counts of nothing")
    same(*[r.taught_totals_bulk([9999])[9999] for r in both],
         "taught_totals of nothing")


def test_a_rollback_agrees(both, frozen_clock):
    def probe(r):
        try:
            with r.begin():
                r.insert("clients", {"name_en": "Doomed", "created_at": db.now(),
                                     "active": 1})
                raise ValueError("boom")
        except ValueError:
            pass
        return r.count("clients")
    same(*[probe(r) for r in both], "a rolled-back insert")
