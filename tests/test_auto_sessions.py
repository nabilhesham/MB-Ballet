"""
A plan's dates, worked out from when it starts and how many sessions it buys.

Stating those two answers is stating which sessions they are, so the forms
fill the ticks and the end date instead of asking for the same thing twice.
The rule lives in access.sessions_from_start() and is reached over HTTP by
/api/plans/auto-sessions, because three forms need the same answer — the
plan picker, the plan editor, and the reception kiosk, which has no session
list to work it out from at all.

What is asserted here is the edges, which is where three hand-written copies
would have parted company: attendance, the plan's own dates, a day already
gone, and a timetable too short to fill the order.
"""

from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

import access
import db
from fixtures import add_session


TODAY = date.today()


def _ts(d, hour=18):
    return int(datetime.combine(d, time(hour)).timestamp())


def _class(repo, name="Auto Test"):
    return repo.insert("classes", {
        "name": name, "description": None, "colour": "#87438E",
        "duration_hours": 1.5, "level": None, "instructor_id": None, "active": 1})


def _client(repo, name="Auto Pick", phone="01700000001"):
    return repo.insert("clients", {
        "name_en": name, "phone": phone, "email": None, "age": None, "dob": None,
        "school": None, "joined_on": TODAY.isoformat(), "photo_path": None,
        "notes": None, "created_at": db.now(), "active": 1})


def _week(repo, class_id, first_offset, n):
    """`n` weekly sessions, the first `first_offset` days from today."""
    return [add_session(repo, class_id, None,
                        _ts(TODAY + timedelta(days=first_offset + 7 * i)), 1.5)
            for i in range(n)]


# ---------------------------------------------------------------- the rule
def test_it_takes_the_first_n_from_the_start_day(repo):
    k, who = _class(repo), _client(repo)
    ids = _week(repo, k, 7, 8)
    start = (TODAY + timedelta(days=7)).isoformat()
    r = access.sessions_from_start(repo, k, who, start, 4)
    assert r["session_ids"] == ids[:4]
    assert r["short"] == 0


def test_the_end_date_is_the_last_one_picked(repo):
    """Not a guess three months out: a plan runs through the session it is
    still paying for, and that is the last of these."""
    k, who = _class(repo), _client(repo)
    ids = _week(repo, k, 7, 8)
    start = (TODAY + timedelta(days=7)).isoformat()
    r = access.sessions_from_start(repo, k, who, start, 4)
    last = repo.get("sessions", ids[3])["starts_at"]
    assert r["expires_on"] == date.fromtimestamp(last).isoformat()


def test_nothing_before_the_start_day_is_offered(repo):
    k, who = _class(repo), _client(repo)
    _week(repo, k, -21, 3)
    later = _week(repo, k, 7, 3)
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 3)
    assert r["session_ids"] == later


def test_a_day_already_gone_is_offered_and_counted(repo):
    """The one place that differs from "auto-fill earliest", which skips the
    past on purpose. Here the start day was *typed*: writing a plan down
    after the client started coming is exactly why the window reaches back.
    The count comes back so the form can say it before anything saves —
    and it counts what would *become* an absence, which is a finished
    session that is not already attendance."""
    k, who = _class(repo), _client(repo)
    past = _week(repo, k, -14, 2)
    ahead = _week(repo, k, 7, 2)
    start = (TODAY - timedelta(days=21)).isoformat()
    r = access.sessions_from_start(repo, k, who, start, 4)
    assert r["session_ids"] == past + ahead
    assert r["past"] == 2


def test_a_short_timetable_is_reported_not_padded(repo):
    k, who = _class(repo), _client(repo)
    _week(repo, k, 7, 3)
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 10)
    assert len(r["session_ids"]) == 3
    assert r["short"] == 7


def test_cancelled_sessions_are_skipped(repo):
    k, who = _class(repo), _client(repo)
    ids = _week(repo, k, 7, 4)
    repo.update("sessions", ids[1], {"status": "cancelled"})
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 3)
    assert r["session_ids"] == [ids[0], ids[2], ids[3]]


def test_another_class_is_never_borrowed_from(repo):
    k, other, who = _class(repo), _class(repo, "Other"), _client(repo)
    mine = _week(repo, k, 7, 2)
    _week(repo, other, 8, 4)
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 4)
    assert r["session_ids"] == mine


def test_a_slot_the_client_already_holds_elsewhere_is_skipped(repo):
    """`not_booked_by` is doing the work: a session they are already booked
    into on another plan is not a date this one can also buy."""
    k, who = _class(repo), _client(repo)
    ids = _week(repo, k, 7, 4)
    other = repo.insert("subscriptions", {
        "client_id": who, "class_id": k, "plan": "other", "sessions_total": 1,
        "price": None, "paid_on": None, "notes": None, "months": None,
        "days_pattern": None, "starts_on": TODAY.isoformat(),
        "expires_on": TODAY.isoformat(), "active": 1, "created_at": db.now()})
    repo.insert("bookings", {"client_id": who, "session_id": ids[1],
                             "subscription_id": other, "status": "booked",
                             "checked_in_at": None, "created_at": db.now()})
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 3)
    assert r["session_ids"] == [ids[0], ids[2], ids[3]]


# ------------------------------------------------- re-picking an existing plan
def _plan_with(repo, who, k, ids, total=None):
    sub = repo.insert("subscriptions", {
        "client_id": who, "class_id": k, "plan": "4 sessions",
        "sessions_total": total or len(ids), "price": None, "paid_on": None,
        "notes": None, "months": None, "days_pattern": None,
        "starts_on": TODAY.isoformat(), "expires_on": TODAY.isoformat(),
        "active": 1, "created_at": db.now()})
    for sid in ids:
        repo.insert("bookings", {"client_id": who, "session_id": sid,
                                 "subscription_id": sub, "status": "booked",
                                 "checked_in_at": None, "created_at": db.now()})
    return sub


def test_the_plans_own_dates_are_candidates_again(repo):
    """Without this, changing 4 sessions to 5 would skip the four it already
    had -- they are booked, so `not_booked_by` hides them -- and offer four
    different dates instead."""
    k, who = _class(repo), _client(repo)
    ids = _week(repo, k, 7, 6)
    sub = _plan_with(repo, who, k, ids[:4])
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 5, plan_id=sub)
    assert r["session_ids"] == ids[:5]


def test_attendance_is_kept_whatever_the_start_day_says(repo):
    """A session already present or absent is history: edit_plan() refuses
    any edit that drops one, so a rule that quietly excluded it would hand
    back a set the server will not accept."""
    k, who = _class(repo), _client(repo)
    past = _week(repo, k, -14, 2)
    ahead = _week(repo, k, 7, 4)
    sub = _plan_with(repo, who, k, past + ahead[:2], total=4)
    for sid in past:
        repo.update_where("bookings", {"subscription_id": sub, "session_id": sid},
                          {"status": "present"})
    # A start day well after those two, which would exclude them on its own.
    r = access.sessions_from_start(repo, k, who, (TODAY + timedelta(days=7)).isoformat(),
                                   4, plan_id=sub)
    assert set(past) <= set(r["session_ids"])
    # And they *count*: four sessions means four, not four plus history.
    assert len(r["session_ids"]) == 4
    assert r["session_ids"][-2:] == ahead[:2]


def test_the_answer_is_one_edit_plan_will_accept(repo):
    """The point of deriving them at all. What comes back has to satisfy the
    contract the server already enforces, or the form fills itself in with
    something that cannot be saved."""
    k, who = _class(repo), _client(repo)
    ids = _week(repo, k, 7, 8)
    sub = _plan_with(repo, who, k, ids[:4])
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 6, plan_id=sub)
    out = access.edit_plan(repo, sub, sessions_total=6,
                           session_ids=r["session_ids"])
    assert out["ok"], out
    assert repo.get("subscriptions", sub)["expires_on"] == r["expires_on"]


# ---------------------------------------------------------------- over HTTP
@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def test_a_session_still_running_is_not_counted_as_missed(repo):
    """"Finished", not "started". The class somebody is standing in right
    now has not been missed, and it is the commonest date of all to be
    writing a plan against."""
    k, who = _class(repo), _client(repo)
    running = add_session(repo, k, None, db.now() - 600, 1.5)
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 1)
    assert r["session_ids"] == [running]
    assert r["past"] == 0


def test_attendance_is_not_warned_about(repo):
    """An already-attended date is a record, not something about to go
    wrong — warning about it would tell reception to untick the one session
    the server will not let them untick."""
    k, who = _class(repo), _client(repo)
    past = _week(repo, k, -14, 1)
    ahead = _week(repo, k, 7, 2)
    sub = _plan_with(repo, who, k, past + ahead[:1], total=2)
    repo.update_where("bookings", {"subscription_id": sub, "session_id": past[0]},
                      {"status": "present"})
    r = access.sessions_from_start(repo, k, who, TODAY.isoformat(), 2, plan_id=sub)
    assert past[0] in r["session_ids"]
    assert r["past"] == 0


def test_the_endpoint_answers_the_forms(client, academy):
    r = client.post("/api/plans/auto-sessions", json={
        "class_id": academy.flex, "client_id": academy.planless,
        "starts_on": TODAY.isoformat(), "sessions_total": 2})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert set(body) == {"session_ids", "expires_on", "past", "short", "kept"}
    assert len(body["session_ids"]) == 2
    assert body["past"] == 0 and body["kept"] == 0


def test_a_plan_cannot_be_shrunk_below_its_own_attendance(client, academy):
    """A floor, not a miscount. The forms turn `kept` into "n sessions are
    already attended — the plan cannot go below that"; dropping one is the
    thing edit_plan() refuses outright."""
    body = client.post("/api/plans/auto-sessions", json={
        "class_id": academy.ballet, "client_id": academy.solo_ballet,
        "starts_on": TODAY.isoformat(), "sessions_total": 2,
        "plan_id": academy.solo_ballet_plan}).json()
    assert body["kept"] > 2
    assert len(body["session_ids"]) == body["kept"]


def test_a_plan_of_no_sessions_is_refused(client, academy):
    r = client.post("/api/plans/auto-sessions", json={
        "class_id": academy.ballet, "client_id": academy.solo_ballet,
        "starts_on": TODAY.isoformat(), "sessions_total": 0})
    assert r.status_code == 400
