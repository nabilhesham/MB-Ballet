"""
A session is as long as *it* says, and an absence says when it was written.

Both halves of one bug, reported three times as different things: "the
session completes after one hour however the duration is 1:30 or 2 hours",
and the client who walked in at 1h05 marked absent in a room they were
sitting in.

`sessions.duration_hours` used to be copied from the class when the session
was created, and the class's length then cascaded back over it on every
class edit -- so the one number that decides when a class closes could be
rewritten behind a dozen already-scheduled sessions' backs, and a session
that could not be lengthened without running into the next one was left
behind anyway, leaving the timetable holding both answers.

Neither direction exists now. A session's length is its own from the moment
it is scheduled; the class's is the value the *form* offers and nothing
reads it afterwards. So `ends_at` can only move when somebody edits that
session, and the end-of-session event -- the only thing that writes an
absence -- is an alarm set by that one number.
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


# --------------------------------------------------- no cascade

def test_a_class_edit_leaves_its_sessions_lengths_alone(client):
    """
    The length on the class screen is what the session form will offer next
    time and nothing else. It used to cascade: saving a class rewrote
    `duration_hours` and `ends_at` on every upcoming session, which moved the
    moment each of them closes and wrote off their latecomers.
    """
    a = client.academy
    starts = db.now() + 3 * 86400
    sid = add_session(a.repo, a.ballet, a.ana, starts, 1.0)

    r = client.put(f"/api/classes/{a.ballet}",
                   json=_class_body(a.repo, a.ballet, duration_hours=2.0))

    assert r.status_code == 200
    assert "relengthened" not in r.json()
    row = a.repo.get("sessions", sid)
    assert row["duration_hours"] == 1.0
    assert row["ends_at"] == access.ends_at_of(starts, 1.0)
    # The class itself did take the new value -- it is just not a cascade.
    assert a.repo.get("classes", a.ballet)["duration_hours"] == 2.0


def test_the_instructor_still_cascades(client):
    """
    So the test above is read as "the length does not" rather than "nothing
    does". Who teaches a date is a fact about the class's staffing; how long
    a dated session runs is a fact about that session.
    """
    a = client.academy
    sid = add_session(a.repo, a.ballet, a.ana, db.now() + 3 * 86400, 1.0)

    r = client.put(f"/api/classes/{a.ballet}",
                   json=_class_body(a.repo, a.ballet, instructor_id=a.bea))

    assert r.status_code == 200
    assert r.json()["cascaded_sessions"] >= 1
    assert a.repo.get("sessions", sid)["instructor_id"] == a.bea


def test_a_session_is_created_at_the_length_it_was_given(client):
    """
    Not at the class's. The class usually runs 1.5h in the fixture; this one
    is a two-hour makeup class and stays one.
    """
    a = client.academy
    starts = db.now() + 4 * 86400
    r = client.post("/api/sessions", json={
        "class_id": a.ballet, "starts_at": starts, "duration_hours": 2.0})

    assert r.status_code == 200, r.text
    row = a.repo.get("sessions", r.json()["id"])
    assert row["duration_hours"] == 2.0
    assert row["ends_at"] == access.ends_at_of(starts, 2.0)


def test_a_session_with_no_length_is_refused_in_words(client):
    """
    It used to fall back to the class's length, which is how a session's end
    became a number nobody on that screen had chosen. There is no fallback,
    and the refusal is a sentence rather than pydantic's list of dicts.
    """
    a = client.academy
    r = client.post("/api/sessions", json={
        "class_id": a.ballet, "starts_at": db.now() + 4 * 86400})
    assert r.status_code == 422

    r = client.post("/api/sessions", json={
        "class_id": a.ballet, "starts_at": db.now() + 4 * 86400,
        "duration_hours": 0})
    assert r.status_code == 400
    assert "how long" in r.json()["detail"].lower(), r.text


def test_a_term_is_repeated_at_the_length_it_was_given(client):
    """
    Up to ninety-six sessions in one request, every one of them carrying the
    length the form sent rather than the class's at that moment.
    """
    a = client.academy
    r = client.post("/api/sessions/repeat", json={
        "class_id": a.ballet, "starts_at": db.now() + 40 * 86400,
        "weeks": 3, "duration_hours": 2.0})

    assert r.status_code == 200, r.text
    made = [s for s in a.repo.find("sessions", {"class_id": a.ballet})
            if s["duration_hours"] == 2.0]
    assert len(made) == r.json()["created"] >= 1
    for row in made:
        assert row["ends_at"] == access.ends_at_of(row["starts_at"], 2.0)


def test_editing_a_session_moves_its_own_end_and_no_other(academy):
    """
    `ends_at` travels with a start or a length, which is the only way the
    alarm moves now. The class's other sessions are untouched.
    """
    repo = academy.repo
    a, b = (add_session(repo, academy.ballet, academy.ana,
                        db.now() + d * 86400, 1.0) for d in (20, 21))
    before = repo.get("sessions", b)["ends_at"]

    from api.sessions import SessionEdit, edit_session
    edit_session(a, SessionEdit(duration_hours=2.0))

    row = repo.get("sessions", a)
    assert row["duration_hours"] == 2.0
    assert row["ends_at"] == access.ends_at_of(row["starts_at"], 2.0)
    assert repo.get("sessions", b)["ends_at"] == before


# --------------------------------------------------- when an absence was written

def test_the_event_stamps_when_it_wrote_an_absence(academy):
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

    access.settle_due_sessions(repo)

    b = repo.find_one("bookings", {"session_id": sid})
    assert b["status"] == "absent"
    assert b["settled_at"] is not None
    assert abs(b["settled_at"] - db.now()) < 120


def test_an_absence_the_event_did_not_write_carries_no_stamp(academy):
    """
    It means "the event wrote this off, then" and nothing else. A plan
    written down after the client started coming books a finished session
    straight to absent, which is not the event's doing.
    """
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "absent",
                             "created_at": db.now()})

    access.settle_due_sessions(repo)

    assert repo.find_one("bookings", {"session_id": sid})["settled_at"] is None


def test_marking_them_present_takes_the_stamp_off(academy):
    """Otherwise a client who turned up carries the time they were written
    off beside their check-in."""
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})
    access.settle_due_sessions(repo)
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
    access.settle_due_sessions(repo)

    row = repo.session_roster(sid)[0]
    assert row["settled_at"] is not None


# --------------------------------------------------- which screen wrote the stamp

def test_a_desk_check_in_and_a_roster_mark_are_told_apart(academy):
    """
    `checked_in_at` had two writers and one label, and that is the whole of
    "the CHECKED IN column is +1 of the actual time": `check_in_booking()`
    stamps the instant the client was at the door, while the session page's
    Present stamps the instant somebody took the roster -- an hour or more
    later for a class marked up at the end. With no scanner on the desk yet,
    almost every mark in the academy's database is the second kind.
    """
    repo = academy.repo
    sid = academy.today_ballet

    # The roster, taken from the session page.
    access.set_status(repo, sid, academy.dual, "present")
    b = repo.find_one("bookings", {"session_id": sid, "client_id": academy.dual})
    assert b["checked_in_by"] == "roster"
    assert b["checked_in_at"] is not None

    # And a check-in at the desk, through the one path a scan and a
    # number/name lookup share.
    assert repo.check_in_booking(b["id"], db.now()) is False  # already present
    other = repo.insert("bookings", {
        "client_id": academy.planless, "session_id": sid,
        "subscription_id": None, "status": "booked", "created_at": db.now()})
    assert repo.check_in_booking(other, db.now()) is True
    assert repo.get("bookings", other)["checked_in_by"] == "kiosk"


def test_marking_them_absent_clears_the_source_too(academy):
    """The stamp goes, so what wrote it has to go with it -- otherwise an
    absent row carries the screen that marked it present an hour ago."""
    repo = academy.repo
    sid = academy.today_ballet
    access.set_status(repo, sid, academy.dual, "present")

    access.set_status(repo, sid, academy.dual, "absent")

    b = repo.find_one("bookings", {"session_id": sid, "client_id": academy.dual})
    assert b["checked_in_at"] is None
    assert b["checked_in_by"] is None


def test_the_roster_carries_the_source(academy):
    """The session page is where this was reported from, so it is where the
    distinction has to be readable."""
    repo = academy.repo
    sid = academy.today_ballet
    access.set_status(repo, sid, academy.dual, "present")

    row = [r for r in repo.session_roster(sid) if r["id"] == academy.dual][0]
    assert row["checked_in_by"] == "roster"
