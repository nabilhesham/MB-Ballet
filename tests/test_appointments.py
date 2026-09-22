"""
Enquiries: somebody who has asked to come in and is not a client yet.

The list is filtered server-side on both axes — the name/mobile search and
the date range — which is the thing worth testing. A client-side text search
over a server-filtered range would silently be searching only the rows the
range let through, and the two boxes would mean different things.

None of the client identity rules reach here, and that is deliberate rather
than an omission: the same family rings twice about two children and the
same person reschedules, so the mobile is optional and may repeat.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


TODAY = date.today()


def _book(client, name, phone=None, on=None, age=None, notes=None):
    return client.post("/api/appointments", json={
        "name": name, "phone": phone,
        "on_date": (on or TODAY).isoformat(), "age": age, "notes": notes})


def _names(client, **params):
    r = client.get("/api/appointments", params=params)
    assert r.status_code == 200, r.json()
    return [a["name"] for a in r.json()]


# ---------------------------------------------------------------- creating
def test_an_appointment_is_created_and_listed(client):
    r = _book(client, "Walk In", "01200000001", age=4.8, notes="asked about ballet")
    assert r.status_code == 200
    assert r.json()["id"]
    [row] = client.get("/api/appointments").json()
    assert row["name"] == "Walk In"
    assert row["phone"] == "01200000001"
    assert row["age"] == 4.8
    assert row["notes"] == "asked about ballet"


def test_the_age_keeps_its_decimal(client):
    """
    REAL, not INTEGER, the same as clients.age. An int field rejects 3.5
    outright rather than rounding it, and the youngest classes are placed on
    exactly that distinction.
    """
    _book(client, "Little One", age=3.5)
    assert client.get("/api/appointments").json()[0]["age"] == 3.5


def test_a_name_is_required(client):
    for bad in ("", "   "):
        r = client.post("/api/appointments",
                        json={"name": bad, "on_date": TODAY.isoformat()})
        assert r.status_code == 400
        assert "name" in r.json()["detail"].lower()


def test_a_real_date_is_required(client):
    for bad in ("", "not-a-date", "2026-13-40"):
        r = client.post("/api/appointments", json={"name": "X", "on_date": bad})
        assert r.status_code == 400
    assert client.get("/api/appointments").json() == []


def test_the_mobile_is_optional_and_may_repeat(client):
    """
    The client rules do not apply here. A parent ringing about two children
    is two enquiries on one number, and a blank is fine — reception may only
    have a first name.
    """
    assert _book(client, "No Number").status_code == 200
    assert _book(client, "Child One", "01211111111").status_code == 200
    assert _book(client, "Child Two", "01211111111").status_code == 200
    assert len(client.get("/api/appointments").json()) == 3


def test_an_appointment_is_not_a_client(client):
    """It creates no client, and the clients list is untouched."""
    before = len(client.get("/api/clients").json())
    _book(client, "Enquiry Only", "01222222222")
    assert len(client.get("/api/clients").json()) == before


# ---------------------------------------------------------------- searching
def test_the_search_matches_the_name(client):
    _book(client, "Yasmin Fahmy", "01233333333")
    _book(client, "Omar Zaki", "01244444444")
    assert _names(client, q="yasmin") == ["Yasmin Fahmy"]
    assert _names(client, q="ZAK") == ["Omar Zaki"]


def test_the_search_matches_the_mobile(client):
    _book(client, "Yasmin Fahmy", "01233333333")
    _book(client, "Omar Zaki", "01244444444")
    assert _names(client, q="0124444") == ["Omar Zaki"]


def test_the_search_matches_either_field(client):
    """One box, two columns — an $or, not two round trips."""
    _book(client, "Sara Nabil", "01255555555")
    _book(client, "Someone Else", "01266666666")
    assert _names(client, q="sara") == ["Sara Nabil"]
    assert sorted(_names(client, q="012")) == ["Sara Nabil", "Someone Else"]


def test_a_blank_search_matches_everyone(client):
    _book(client, "One")
    _book(client, "Two")
    assert len(_names(client, q="")) == 2


# ---------------------------------------------------------------- the range
def test_the_range_is_inclusive_at_both_ends(client):
    _book(client, "Before", on=TODAY - timedelta(days=5))
    _book(client, "On From", on=TODAY - timedelta(days=2))
    _book(client, "Inside", on=TODAY)
    _book(client, "On To", on=TODAY + timedelta(days=2))
    _book(client, "After", on=TODAY + timedelta(days=5))
    got = _names(client,
                 date_from=(TODAY - timedelta(days=2)).isoformat(),
                 date_to=(TODAY + timedelta(days=2)).isoformat())
    assert sorted(got) == ["Inside", "On From", "On To"]


def test_either_bound_works_alone(client):
    _book(client, "Old", on=TODAY - timedelta(days=10))
    _book(client, "New", on=TODAY + timedelta(days=10))
    assert _names(client, date_from=TODAY.isoformat()) == ["New"]
    assert _names(client, date_to=TODAY.isoformat()) == ["Old"]


def test_one_day_matches_that_day(client):
    """
    Picking the same date for both bounds matches that day, which is the
    commonest thing reception will do. The column is an ISO day rather than
    a timestamp precisely so this needs no end-of-day arithmetic — see the
    Sessions list, where it does.
    """
    _book(client, "Yesterday", on=TODAY - timedelta(days=1))
    _book(client, "Today", on=TODAY)
    _book(client, "Tomorrow", on=TODAY + timedelta(days=1))
    assert _names(client, date_from=TODAY.isoformat(),
                  date_to=TODAY.isoformat()) == ["Today"]


def test_the_search_and_the_range_apply_together(client):
    """
    Both server-side and ANDed. If one were client-side it would be
    filtering whatever the other had already dropped.
    """
    _book(client, "Nour Early", "01277777777", on=TODAY - timedelta(days=30))
    _book(client, "Nour Soon", "01277777777", on=TODAY + timedelta(days=1))
    _book(client, "Other Soon", "01288888888", on=TODAY + timedelta(days=1))
    assert _names(client, q="nour", date_from=TODAY.isoformat()) == ["Nour Soon"]


def test_the_list_is_newest_first(client):
    _book(client, "Middle", on=TODAY)
    _book(client, "Latest", on=TODAY + timedelta(days=3))
    _book(client, "Earliest", on=TODAY - timedelta(days=3))
    assert _names(client) == ["Latest", "Middle", "Earliest"]


# --------------------------------------------------------------- time of day
#
# The time is stored beside the day rather than folded into it. Only the day
# is ever filtered on, and a day column is what lets the range need no
# end-of-day arithmetic -- so the tests below check that adding a time
# changed nothing about the filter that was already there.

def test_the_time_is_kept_beside_the_day(client):
    aid = client.post("/api/appointments", json={
        "name": "Nour", "on_date": TODAY.isoformat(), "on_time": "16:30"}).json()["id"]
    row = next(a for a in client.get("/api/appointments").json() if a["id"] == aid)
    assert row["on_date"] == TODAY.isoformat()
    assert row["on_time"] == "16:30"


def test_the_time_is_optional(client):
    """"Sometime Tuesday" is a real answer, and midnight is not a truthful
    stand-in for it."""
    for body in ({"name": "A", "on_date": TODAY.isoformat()},
                 {"name": "B", "on_date": TODAY.isoformat(), "on_time": None},
                 {"name": "C", "on_date": TODAY.isoformat(), "on_time": ""}):
        assert client.post("/api/appointments", json=body).status_code == 200
    assert all(a["on_time"] is None for a in client.get("/api/appointments").json())


def test_seconds_from_a_time_input_are_trimmed(client):
    """<input type="time"> can hand back HH:MM:SS; the column holds HH:MM."""
    aid = client.post("/api/appointments", json={
        "name": "Nour", "on_date": TODAY.isoformat(), "on_time": "09:05:00"}).json()["id"]
    row = next(a for a in client.get("/api/appointments").json() if a["id"] == aid)
    assert row["on_time"] == "09:05"


@pytest.mark.parametrize("bad", ["4pm", "25:00", "16:70", "16", "noon"])
def test_a_time_that_is_not_a_time_is_refused(client, bad):
    r = client.post("/api/appointments", json={
        "name": "Nour", "on_date": TODAY.isoformat(), "on_time": bad})
    assert r.status_code == 400
    assert "16:30" in r.json()["detail"]


def test_the_range_still_ignores_the_time(client):
    """The filter is on the day, whatever hour is written against it."""
    _book(client, "Early", on=TODAY)
    client.post("/api/appointments", json={
        "name": "Late", "on_date": TODAY.isoformat(), "on_time": "23:45"})
    got = _names(client, date_from=TODAY.isoformat(), date_to=TODAY.isoformat())
    assert sorted(got) == ["Early", "Late"]


# ------------------------------------------------------------- edit and delete
def test_an_appointment_is_edited(client):
    aid = _book(client, "Nour Adel", phone="01000000001").json()["id"]
    moved = (TODAY + timedelta(days=3)).isoformat()
    r = client.put(f"/api/appointments/{aid}", json={
        "name": "Nour Adele", "phone": "01000000002", "age": 4.5,
        "on_date": moved, "on_time": "17:00", "notes": "moved by phone"})
    assert r.status_code == 200, r.json()
    row = r.json()
    assert (row["name"], row["phone"], row["age"]) == ("Nour Adele", "01000000002", 4.5)
    assert (row["on_date"], row["on_time"]) == (moved, "17:00")
    assert row["notes"] == "moved by phone"
    # And the list agrees -- one row, not a second one alongside it.
    rows = client.get("/api/appointments").json()
    assert [a["name"] for a in rows] == ["Nour Adele"]


def test_an_edit_can_clear_the_time_and_the_notes(client):
    """Both are optional, so both have to be clearable -- an edit that can
    only ever add a value is how a wrong one becomes permanent."""
    aid = client.post("/api/appointments", json={
        "name": "Nour", "on_date": TODAY.isoformat(), "on_time": "16:30",
        "notes": "asked about ballet"}).json()["id"]
    row = client.put(f"/api/appointments/{aid}", json={
        "name": "Nour", "on_date": TODAY.isoformat(), "on_time": "", "notes": ""}).json()
    assert row["on_time"] is None and row["notes"] is None


def test_an_edit_is_held_to_the_same_rules_as_a_create(client):
    """Refusing something at creation and allowing it a minute later leaves
    exactly the state the refusal exists to prevent."""
    aid = _book(client, "Nour").json()["id"]
    for bad, why in (({"name": "  ", "on_date": TODAY.isoformat()}, "name"),
                     ({"name": "Nour", "on_date": "not-a-date"}, "date"),
                     ({"name": "Nour", "on_date": TODAY.isoformat(),
                       "on_time": "4pm"}, "time")):
        assert client.put(f"/api/appointments/{aid}", json=bad).status_code == 400, why
    # Untouched by any of them.
    assert client.get("/api/appointments").json()[0]["name"] == "Nour"


def test_editing_something_that_is_not_there(client):
    r = client.put("/api/appointments/999", json={
        "name": "Nour", "on_date": TODAY.isoformat()})
    assert r.status_code == 404


def test_an_appointment_is_deleted_for_good(client):
    """Deleted, not archived. An enquiry has no attendance, no plan and no
    card behind it -- there is nothing the deletion policy is protecting."""
    keep = _book(client, "Keep").json()["id"]
    drop = _book(client, "Drop").json()["id"]
    assert client.delete(f"/api/appointments/{drop}").status_code == 200
    assert [a["id"] for a in client.get("/api/appointments").json()] == [keep]
    # Gone for good: a second delete has nothing to find.
    assert client.delete(f"/api/appointments/{drop}").status_code == 404


def test_deleting_an_appointment_touches_no_client(client):
    """The row carries its own name and mobile and points at nobody. A client
    who happens to share them is not part of this."""
    before = len(client.get("/api/clients").json())
    aid = _book(client, "Dana Halim", phone="01129200365").json()["id"]
    client.delete(f"/api/appointments/{aid}")
    assert len(client.get("/api/clients").json()) == before
