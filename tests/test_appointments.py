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
