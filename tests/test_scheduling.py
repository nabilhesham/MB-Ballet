"""
One session at a time, academy-wide — and the sweep that settles the past.

These pin `slot_conflict()` and `settle_past_sessions()` before their
`starts_at + duration_hours*3600` predicate is replaced by a stored
`ends_at` column. Both are boundary logic, so the cases that matter are the
exact ones: back-to-back, one second of overlap, a cancelled session, and a
session whose end has just passed.
"""

from datetime import date, datetime, time, timedelta

import pytest

import access
import db
from fixtures import _ins, add_session


@pytest.fixture
def empty(repo):
    """Two classes and an instructor, and no sessions at all."""
    ins = _ins(repo, "instructors", name="Ana", hourly_rate=100.0, active=1)
    a = _ins(repo, "classes", name="Ballet", colour="#87438E",
             duration_hours=1.5, instructor_id=ins, active=1)
    b = _ins(repo, "classes", name="Flex", colour="#EAAECA",
             duration_hours=1.0, instructor_id=ins, active=1)
    return type("E", (), {"repo": repo, "ins": ins, "ballet": a, "flex": b})


def at(day_offset: int, hour: int, minute: int = 0) -> int:
    d = date.today() + timedelta(days=day_offset)
    return int(datetime.combine(d, time(hour, minute)).timestamp())


def add(e, class_id, starts_at, hours=1.0, status="scheduled"):
    return add_session(e.repo, class_id, e.ins, starts_at, hours, status=status)


# ---------------------------------------------------------------- slot_conflict

def test_an_empty_timetable_has_no_conflict(empty):
    assert access.slot_conflict(empty.repo, at(1, 18), 1.5) is None


def test_an_exact_overlap_is_refused(empty):
    add(empty, empty.ballet, at(1, 18), 1.5)
    clash = access.slot_conflict(empty.repo, at(1, 18), 1.5)
    assert clash is not None
    assert clash["class_name"] == "Ballet"


def test_back_to_back_is_allowed(empty):
    """A session ending at 19:30 and one starting at 19:30 do not clash."""
    add(empty, empty.ballet, at(1, 18), 1.5)          # 18:00-19:30
    assert access.slot_conflict(empty.repo, at(1, 19, 30), 1.0) is None


def test_the_slot_immediately_before_is_allowed(empty):
    add(empty, empty.ballet, at(1, 18), 1.5)          # 18:00-19:30
    assert access.slot_conflict(empty.repo, at(1, 17), 1.0) is None   # 17:00-18:00


def test_one_minute_of_overlap_is_refused(empty):
    add(empty, empty.ballet, at(1, 18), 1.5)          # 18:00-19:30
    assert access.slot_conflict(empty.repo, at(1, 19, 29), 1.0) is not None


def test_a_new_session_wholly_inside_an_existing_one_is_refused(empty):
    add(empty, empty.ballet, at(1, 18), 3.0)          # 18:00-21:00
    assert access.slot_conflict(empty.repo, at(1, 19), 0.5) is not None


def test_a_new_session_wholly_containing_an_existing_one_is_refused(empty):
    add(empty, empty.ballet, at(1, 19), 0.5)          # 19:00-19:30
    assert access.slot_conflict(empty.repo, at(1, 18), 3.0) is not None


def test_the_rule_is_academy_wide_not_per_class(empty):
    """Whatever is running, nothing else runs beside it."""
    add(empty, empty.ballet, at(1, 18), 1.5)
    clash = access.slot_conflict(empty.repo, at(1, 18), 1.0)
    assert clash is not None, "a different class must still clash"


def test_a_cancelled_session_occupies_nothing(empty):
    """Cancelling is how reception frees a slot up."""
    add(empty, empty.ballet, at(1, 18), 1.5, status="cancelled")
    assert access.slot_conflict(empty.repo, at(1, 18), 1.5) is None


def test_a_session_does_not_clash_with_itself_when_excluded(empty):
    sid = add(empty, empty.ballet, at(1, 18), 1.5)
    assert access.slot_conflict(empty.repo, at(1, 18), 1.5) is not None
    assert access.slot_conflict(empty.repo, at(1, 18), 1.5, exclude_id=sid) is None


def test_the_rule_applies_to_the_past_too(empty):
    """A slot that has been and gone is still a slot."""
    add(empty, empty.ballet, at(-7, 18), 1.5, status="completed")
    assert access.slot_conflict(empty.repo, at(-7, 18), 1.5) is not None


def test_the_earliest_clashing_session_is_the_one_reported(empty):
    add(empty, empty.flex, at(1, 19), 1.0)
    add(empty, empty.ballet, at(1, 18), 3.0)
    clash = access.slot_conflict(empty.repo, at(1, 18), 3.0)
    assert clash["class_name"] == "Ballet", "ORDER BY starts_at picks the earlier"


def test_a_fractional_duration_is_respected(empty):
    """1.5 hours is 90 minutes, not 1 or 2."""
    add(empty, empty.ballet, at(1, 18), 1.5)          # 18:00-19:30
    assert access.slot_conflict(empty.repo, at(1, 19, 0), 0.5) is not None
    assert access.slot_conflict(empty.repo, at(1, 19, 30), 0.5) is None


# -------------------------------------------------- settle_past_sessions

def test_a_session_that_has_ended_becomes_completed(empty):
    sid = add(empty, empty.ballet, db.now() - 4 * 3600, 1.5)
    access.settle_past_sessions(empty.repo)
    assert empty.repo.raw("SELECT status FROM sessions WHERE id=?",
                              (sid,)).fetchone()["status"] == "completed"


def test_a_session_still_running_is_left_alone(empty):
    """Its start has passed but its end has not."""
    sid = add(empty, empty.ballet, db.now() - 600, 1.5)
    access.settle_past_sessions(empty.repo)
    assert empty.repo.raw("SELECT status FROM sessions WHERE id=?",
                              (sid,)).fetchone()["status"] == "scheduled"


def test_a_future_session_is_left_alone(empty):
    sid = add(empty, empty.ballet, at(2, 18), 1.5)
    access.settle_past_sessions(empty.repo)
    assert empty.repo.raw("SELECT status FROM sessions WHERE id=?",
                              (sid,)).fetchone()["status"] == "scheduled"


def test_a_cancelled_session_is_never_completed_by_the_sweep(empty):
    sid = add(empty, empty.ballet, db.now() - 4 * 3600, 1.5, status="cancelled")
    access.settle_past_sessions(empty.repo)
    assert empty.repo.raw("SELECT status FROM sessions WHERE id=?",
                              (sid,)).fetchone()["status"] == "cancelled"


def test_a_booking_on_a_cancelled_session_is_not_swept_absent(empty):
    repo = empty.repo
    sid = add(empty, empty.ballet, db.now() - 4 * 3600, 1.5, status="cancelled")
    cid = _ins(repo, "clients", name_en="X", created_at=db.now(), active=1)
    _ins(repo, "bookings", client_id=cid, session_id=sid,
         status="booked", created_at=db.now())
    access.settle_past_sessions(repo)
    assert repo.raw("SELECT status FROM bookings WHERE session_id=?",
                        (sid,)).fetchone()["status"] == "booked"


def test_the_sweep_returns_how_many_bookings_it_settled(empty):
    repo = empty.repo
    cid = _ins(repo, "clients", name_en="X", created_at=db.now(), active=1)
    for offset in (4, 8):
        sid = add(empty, empty.ballet, db.now() - offset * 3600, 1.0)
        _ins(repo, "bookings", client_id=cid, session_id=sid,
             status="booked", created_at=db.now())
    assert access.settle_past_sessions(repo) == 2


def test_the_sweep_is_idempotent(empty):
    repo = empty.repo
    cid = _ins(repo, "clients", name_en="X", created_at=db.now(), active=1)
    sid = add(empty, empty.ballet, db.now() - 4 * 3600, 1.0)
    _ins(repo, "bookings", client_id=cid, session_id=sid,
         status="booked", created_at=db.now())
    assert access.settle_past_sessions(repo) == 1
    assert access.settle_past_sessions(repo) == 0


# ------------------------------------------------------------------- ends_at

def test_no_session_ever_disagrees_with_its_stored_end(academy):
    """
    ends_at is stored, not derived, so it can fall out of step with the two
    columns it comes from. A row that does is invisible to slot_conflict()
    and to the absent sweep — a wrong answer with nothing on screen to
    suggest it. Every path that writes starts_at or duration_hours must
    write this too.
    """
    wrong = academy.repo.raw(
        "SELECT COUNT(*) n FROM sessions"
        " WHERE ends_at IS NULL"
        "    OR ends_at != CAST(starts_at + duration_hours * 3600 AS INTEGER)"
    ).fetchone()["n"]
    assert wrong == 0


def test_migrate_repairs_a_session_whose_end_is_missing(empty):
    """
    The backfill is targeted at NULL rows rather than run once behind a
    marker, so it also repairs anything a future write path forgets.
    """
    repo = empty.repo
    sid = add(empty, empty.ballet, at(1, 18), 1.5)
    repo.raw("UPDATE sessions SET ends_at=NULL WHERE id=?", (sid,))
    assert access.slot_conflict(repo, at(1, 18), 1.5) is None, "precondition: invisible"

    db.migrate(repo.conn)

    assert access.slot_conflict(repo, at(1, 18), 1.5) is not None
    assert repo.raw("SELECT ends_at FROM sessions WHERE id=?",
                        (sid,)).fetchone()["ends_at"] == at(1, 19, 30)
