"""
When a client stops being a student of a class.

Membership is derived from bookings and bookings are never deleted, so
without a cutoff a client who stopped coming last year stays on the class
page for ever and the roster slowly stops describing who actually attends.
One calendar month after their last plan in the class ran out, they drop off
it.

What this is *not* is a deletion. Every booking and every attendance mark
stays exactly where it is — the client's own profile and payment history
still show the class, and restoring them is a matter of selling another
plan. These tests assert that too, because a read filter that quietly
deleted rows would pass every other assertion here.
"""

from datetime import date, datetime, time, timedelta

import access
import db


def _ts(d, hour=18):
    return int(datetime.combine(d, time(hour)).timestamp())


def _class(repo, name="Lapse Test"):
    return repo.insert("classes", {
        "name": name, "description": None, "colour": "#87438E",
        "duration_hours": 1.5, "level": None, "instructor_id": None,
        "active": 1})


def _session(repo, class_id, day):
    starts = _ts(day)
    return repo.insert("sessions", {
        "class_id": class_id, "instructor_id": None, "starts_at": starts,
        "duration_hours": 1.5, "ends_at": starts + 5400,
        "status": "scheduled", "notes": None})


def _client(repo, name, phone):
    return repo.insert("clients", {
        "name_en": name, "phone": phone, "age": 10.0, "school": None,
        "joined_on": date.today().isoformat(), "notes": None,
        "created_at": db.now(), "active": 1})


def _plan_on(repo, client_id, class_id, last_day, *, expires_on=None,
             status="present"):
    """
    A one-session plan whose last session is `last_day`, booked. Returns the
    plan id. `expires_on` defaults to that same day, which is what
    access.refresh_expiry() would have written.
    """
    sub = repo.insert("subscriptions", {
        "client_id": client_id, "class_id": class_id, "plan": "1 session",
        "sessions_total": 1, "sessions_used": 0, "price": None,
        "payment_note": None, "paid_on": None, "notes": None, "months": None,
        "days_pattern": None, "starts_on": last_day.isoformat(),
        "expires_on": (expires_on or last_day).isoformat(),
        "active": 1, "created_at": db.now()})
    repo.insert("bookings", {
        "client_id": client_id, "subscription_id": sub,
        "session_id": _session(repo, class_id, last_day),
        "status": status, "checked_in_at": None, "created_at": db.now()})
    return sub


def _ids(repo, class_id):
    return {r["id"] for r in
            repo.class_students(class_id, access.lapsed_cutoff())}


TODAY = date.today()


# ------------------------------------------------------------ the cutoff
def test_a_plan_that_ended_two_months_ago_drops_the_client(repo):
    k = _class(repo)
    who = _client(repo, "Long Gone", "01500000001")
    _plan_on(repo, who, k, TODAY - timedelta(days=62))
    assert who not in _ids(repo, k)


def test_a_plan_that_ended_two_weeks_ago_keeps_them(repo):
    """Inside the grace month: they have probably just not renewed yet."""
    k = _class(repo)
    who = _client(repo, "Just Lapsed", "01500000002")
    _plan_on(repo, who, k, TODAY - timedelta(days=14))
    assert who in _ids(repo, k)


def test_a_live_plan_keeps_them(repo):
    k = _class(repo)
    who = _client(repo, "Current", "01500000003")
    _plan_on(repo, who, k, TODAY + timedelta(days=20), status="booked")
    assert who in _ids(repo, k)


def test_the_boundary_is_inclusive(repo):
    """
    Ending exactly on the cutoff still counts. An off-by-one here drops a
    client on the wrong day, which nobody would notice.
    """
    k = _class(repo)
    on = _client(repo, "On The Line", "01500000004")
    _plan_on(repo, on, k, date.fromisoformat(access.lapsed_cutoff()))
    just_before = _client(repo, "A Day Early", "01500000005")
    _plan_on(repo, just_before, k,
             date.fromisoformat(access.lapsed_cutoff()) - timedelta(days=1))
    ids = _ids(repo, k)
    assert on in ids
    assert just_before not in ids


def test_one_live_plan_outweighs_an_ancient_one(repo):
    """
    A client who took the class two years ago, stopped, and came back is a
    student. The rule is "every plan of theirs here has lapsed", not "their
    first one did".
    """
    k = _class(repo)
    who = _client(repo, "Returned", "01500000006")
    _plan_on(repo, who, k, TODAY - timedelta(days=700))
    _plan_on(repo, who, k, TODAY + timedelta(days=10), status="booked")
    assert who in _ids(repo, k)


def test_the_last_session_wins_over_an_earlier_typed_end_date(repo):
    """
    access.plan_end(): a plan cannot have finished before a session it is
    still paying for, so an ENDS ON typed earlier than the last session does
    not lapse the client early.
    """
    k = _class(repo)
    who = _client(repo, "Typed Early", "01500000007")
    _plan_on(repo, who, k, TODAY - timedelta(days=5),
             expires_on=TODAY - timedelta(days=200))
    assert who in _ids(repo, k)


def test_a_booking_with_no_plan_falls_back_to_its_session(repo):
    """
    Older rows carry subscription_id NULL. The session's own date is the
    only end they have — the same fallback _decide() makes.
    """
    k = _class(repo)
    recent = _client(repo, "No Plan Recent", "01500000008")
    old = _client(repo, "No Plan Old", "01500000009")
    for who, day in ((recent, TODAY - timedelta(days=3)),
                     (old, TODAY - timedelta(days=300))):
        repo.insert("bookings", {
            "client_id": who, "subscription_id": None,
            "session_id": _session(repo, k, day), "status": "present",
            "checked_in_at": None, "created_at": db.now()})
    ids = _ids(repo, k)
    assert recent in ids
    assert old not in ids


# ------------------------------------------------------------ what it does not do
def test_nothing_is_deleted(repo):
    """
    The whole rule is a read filter. A lapsed client's bookings and
    attendance are the record of sessions that happened and must survive it
    untouched — their own profile and payment history still show them.
    """
    k = _class(repo)
    who = _client(repo, "Gone But Recorded", "01500000010")
    sub = _plan_on(repo, who, k, TODAY - timedelta(days=90))
    before = repo.find("bookings", {"client_id": who})
    assert who not in _ids(repo, k)
    after = repo.find("bookings", {"client_id": who})
    assert [b["id"] for b in after] == [b["id"] for b in before]
    assert [b["status"] for b in after] == ["present"]
    assert repo.get("subscriptions", sub) is not None


def test_the_class_list_count_agrees_with_the_class_page(repo):
    """
    A Classes list saying 12 students beside a class page showing 8 is worse
    than either number alone, so both apply the same cutoff.
    """
    k = _class(repo, "Counted")
    _plan_on(repo, _client(repo, "In One", "01500000011"),
             k, TODAY - timedelta(days=2))
    _plan_on(repo, _client(repo, "In Two", "01500000012"),
             k, TODAY + timedelta(days=2), status="booked")
    _plan_on(repo, _client(repo, "Out One", "01500000013"),
             k, TODAY - timedelta(days=400))
    _plan_on(repo, _client(repo, "Out Two", "01500000014"),
             k, TODAY - timedelta(days=95))

    listed = [c for c in repo.classes_with_counts(1, access.lapsed_cutoff())
              if c["id"] == k][0]
    assert listed["students"] == 2
    assert len(_ids(repo, k)) == 2


def test_an_archived_client_is_still_left_out(repo):
    """The pre-existing active=1 filter is not lost to the new one."""
    k = _class(repo)
    who = _client(repo, "Archived Current", "01500000015")
    _plan_on(repo, who, k, TODAY + timedelta(days=10), status="booked")
    assert who in _ids(repo, k)
    repo.update("clients", who, {"active": 0})
    assert who not in _ids(repo, k)
