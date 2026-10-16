"""
A session is as long as its class says, and an absence says when it was written.

Both halves of one bug, reported three times as different things: "the
session completes after one hour however the duration is 1:30 or 2 hours",
and the client who walked in at 1h05 marked absent in a room they were
sitting in.

`sessions.duration_hours` is copied from the class when the session is
created and was never touched again, so a class corrected from 1 hour to
1.5 left every session already on the timetable one hour long -- and
`ends_at`, which is what `complete_finished_sessions()` and
`settle_absences()` compare against, is written from the *session's* own
length. The sweep was firing at exactly the moment it was told to.

`_repair_running_sessions()` cannot see this and in fact cements it: it
makes `ends_at` agree with `duration_hours`, which is the number that is
wrong. That repair is for a row disagreeing with itself; this is for a row
disagreeing with its class.
"""

import pytest
from fastapi.testclient import TestClient

import access
import db
from fixtures import add_session, later_today


@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def _class_body(repo, clid, **over):
    c = repo.get("classes", clid)
    body = {"name": c["name"], "description": c["description"],
            "colour": c["colour"], "duration_hours": c["duration_hours"],
            "level": c["level"], "instructor_id": c["instructor_id"]}
    body.update(over)
    return body


# --------------------------------------------------- the cascade

def test_lengthening_a_class_lengthens_its_upcoming_sessions(academy):
    repo = academy.repo
    starts = db.now() + 3 * 86400
    sid = add_session(repo, academy.ballet, academy.ana, starts, 1.0)

    out = access.relength_sessions(repo, academy.ballet, 1.5)

    assert out["relengthened"] == 1
    assert out["skipped"] == []
    s = repo.get("sessions", sid)
    assert s["duration_hours"] == 1.5
    # The column the sweep actually reads moves with it, or the class still
    # closes itself at the old hour.
    assert s["ends_at"] == access.ends_at_of(starts, 1.5)


def test_a_session_already_the_right_length_is_not_written(academy):
    """Re-saving a class with nothing changed costs one read and no writes."""
    repo = academy.repo
    add_session(repo, academy.ballet, academy.ana, db.now() + 3 * 86400, 1.5)

    assert access.relength_sessions(repo, academy.ballet, 1.5) == {
        "relengthened": 0, "skipped": []}


def test_a_past_session_keeps_the_length_it_ran(academy):
    """
    A past session's length is the record of how long it ran, and
    relengthening it would rewrite who was absent at the end of it.
    """
    repo = academy.repo
    starts = db.now() - 10 * 86400
    sid = add_session(repo, academy.ballet, academy.ana, starts, 1.0,
                      status="completed")

    access.relength_sessions(repo, academy.ballet, 2.0)

    assert repo.get("sessions", sid)["duration_hours"] == 1.0


def test_a_cancelled_session_is_left_alone(academy):
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana,
                      db.now() + 3 * 86400, 1.0, status="cancelled")

    access.relength_sessions(repo, academy.ballet, 2.0)

    assert repo.get("sessions", sid)["duration_hours"] == 1.0


def test_a_session_that_would_run_into_another_keeps_its_length(academy):
    """
    One session at a time is academy-wide, so making a class longer can run
    it into whatever is on next. That one is named, never forced, and the
    rest of the batch still goes through -- the same treatment repeating a
    term gives a taken evening.
    """
    repo = academy.repo
    # Clear of the fixture's own two series, so the only thing in the way is
    # the one this test puts there.
    day = db.now() + 40 * 86400
    blocked = add_session(repo, academy.ballet, academy.ana, day, 1.0)
    add_session(repo, academy.flex, academy.bea, day + 3600, 1.0)
    free = add_session(repo, academy.ballet, academy.ana, day + 4 * 86400, 1.0)

    out = access.relength_sessions(repo, academy.ballet, 1.5)

    assert repo.get("sessions", blocked)["duration_hours"] == 1.0
    assert repo.get("sessions", free)["duration_hours"] == 1.5
    assert len(out["skipped"]) == 1
    # A sentence naming what is in the way, not a code.
    assert "Flexibility" in out["skipped"][0]
    assert "stayed at 1h" in out["skipped"][0]


def test_the_classs_own_sessions_are_tested_against_each_other(academy):
    """
    Back-to-back sessions of one class lengthened together would be laid
    over each other -- nothing is written until the end, so each accepted
    one has to join the occupied list as it goes.
    """
    repo = academy.repo
    day = db.now() + 7 * 86400
    first = add_session(repo, academy.ballet, academy.ana, day, 1.0)
    second = add_session(repo, academy.ballet, academy.ana, day + 3600, 1.0)

    out = access.relength_sessions(repo, academy.ballet, 1.5)

    assert out["relengthened"] == 1
    assert repo.get("sessions", first)["duration_hours"] == 1.5
    assert repo.get("sessions", second)["duration_hours"] == 1.0
    assert len(out["skipped"]) == 1


def test_the_class_edit_is_what_applies_it(client):
    """The cascade reaches the sessions through the one screen the length is
    typed on, beside the instructor cascade it matches."""
    a = client.academy
    starts = db.now() + 3 * 86400
    sid = add_session(a.repo, a.ballet, a.ana, starts, 1.0)

    r = client.put(f"/api/classes/{a.ballet}",
                   json=_class_body(a.repo, a.ballet, duration_hours=2.0))

    assert r.status_code == 200
    assert r.json()["relengthened"] >= 1
    assert r.json()["length_clashes"] == []
    assert a.repo.get("sessions", sid)["ends_at"] == access.ends_at_of(starts, 2.0)


def test_a_relengthened_class_stops_closing_early(academy):
    """
    The whole point, end to end: a session an hour into a 1.5-hour class is
    still running, and its no-show is still booked rather than written off.
    """
    repo = academy.repo
    started = db.now() - 70 * 60
    sid = add_session(repo, academy.ballet, academy.ana, started, 1.0)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})

    # As it stands the session is over and the latecomer is absent.
    access.sweep_invalidate()
    access.settle_past_sessions(repo)
    assert repo.get("sessions", sid)["status"] == "completed"
    assert repo.find_one("bookings", {"session_id": sid})["status"] == "absent"

    # The class is corrected to the length it really runs.
    access.relength_sessions(repo, academy.ballet, 1.5)
    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    # It is running again, and the slot is back -- the repair gives an
    # absence back on a session it reopens.
    assert repo.get("sessions", sid)["status"] == "scheduled"
    b = repo.find_one("bookings", {"session_id": sid})
    assert b["status"] == "booked"
    assert b["settled_at"] is None


# --------------------------------------------------- when an absence was written

def test_the_sweep_stamps_when_it_wrote_an_absence(academy):
    """
    An absence had no timestamp of its own -- `checked_in_at` stays NULL --
    so "marked absent too early" was unanswerable from the data. This is the
    stamp, read against the session's own end.
    """
    repo = academy.repo
    started = db.now() - 5 * 86400
    sid = add_session(repo, academy.ballet, academy.ana, started, 1.5)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})

    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    b = repo.find_one("bookings", {"session_id": sid})
    assert b["status"] == "absent"
    assert b["settled_at"] is not None
    assert abs(b["settled_at"] - db.now()) < 120


def test_an_absence_nobody_swept_carries_no_stamp(academy):
    """
    It means "the sweep wrote this off, then" and nothing else. A plan
    written down after the client started coming books a finished session
    straight to absent, which is not the sweep's doing.
    """
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "absent",
                             "created_at": db.now()})

    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    assert repo.find_one("bookings", {"session_id": sid})["settled_at"] is None


def test_marking_them_present_takes_the_stamp_off(academy):
    """Otherwise a client who turned up carries the time they were written
    off beside their check-in."""
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})
    access.sweep_invalidate()
    access.settle_past_sessions(repo)
    assert repo.find_one("bookings", {"session_id": sid})["settled_at"] is not None

    access.set_status(repo, sid, academy.planless, "present")

    assert repo.find_one("bookings", {"session_id": sid})["settled_at"] is None


def test_the_roster_carries_it(academy):
    """The session page is the screen this was reported from, so it is the
    screen the answer has to be on."""
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})
    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    row = repo.session_roster(sid)[0]
    assert row["settled_at"] is not None
