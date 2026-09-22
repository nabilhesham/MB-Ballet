"""
Selling the next plan with the client standing at the reception desk.

The moment this exists for: somebody arrives, their plan has just run out —
either they walked in with nothing left, or they walked in with one session
and spent it on the way past — and the receptionist would otherwise have to
leave the kiosk for the admin screen with a queue at the counter.

What it must not do: **check anybody in.** Selling a plan and spending one
of its sessions are separate decisions, and a sale that consumed the first
slot would be the app making the second.

What it does not do *itself*: draw a card. The route above it issues one, so
both ways in to a renewal replace the printout that carries the plan's
figures -- the profile's picker already did. The price is that issuing
revokes the credential in the client's hand, which is why the kiosk says so
in as many words; the tests over HTTP below hold both halves.

The dates are chosen rather than picked, because every slot must be assigned
before a plan saves and a kiosk has no session picker. That makes *which*
sessions it picks a real rule with real edges, which is most of what is
asserted here.
"""

from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

import access
import db


def _ts(day, hour=18):
    return int(datetime.combine(day, time(hour)).timestamp())


def _class(repo, name="Renew Test"):
    return repo.insert("classes", {
        "name": name, "description": None, "colour": "#87438E",
        "duration_hours": 1.5, "level": None, "instructor_id": None,
        "active": 1})


def _session(repo, class_id, starts_at, hours=1.5, status="scheduled"):
    return repo.insert("sessions", {
        "class_id": class_id, "instructor_id": None, "starts_at": starts_at,
        "duration_hours": hours, "ends_at": int(starts_at + hours * 3600),
        "status": status, "notes": None})


def _client(repo, name="Renew Me", phone="01600000001"):
    return repo.insert("clients", {
        "name_en": name, "phone": phone, "age": 9.0, "school": None,
        "joined_on": date.today().isoformat(), "notes": None,
        "created_at": db.now(), "active": 1})


TODAY = date.today()


# ---------------------------------------------------------------- the dates
def test_it_picks_the_earliest_sessions_in_order(repo):
    k = _class(repo)
    who = _client(repo)
    ids = [_session(repo, k, _ts(TODAY + timedelta(days=d))) for d in (7, 1, 4, 10)]
    r = access.renew_at_desk(repo, who, k, "3 sessions", 3)
    assert r["ok"], r
    booked = [b["session_id"] for b in repo.find("bookings", {"client_id": who})]
    # days 1, 4, 7 — the three soonest, not the three created first.
    assert sorted(booked) == sorted([ids[1], ids[2], ids[0]])


def test_a_session_running_right_now_still_counts(repo):
    """
    The commonest renewal of all: they turn up for today's class, it has
    already started, and they have nothing left. Filling only from sessions
    that *start* in the future would hand back a plan that cannot let them
    into the class they are standing there for.
    """
    k = _class(repo)
    who = _client(repo)
    now = db.now()
    running = _session(repo, k, now - 600, hours=1.5)      # started 10 min ago
    later = _session(repo, k, _ts(TODAY + timedelta(days=3)))
    assert access.renewable_sessions(repo, k, who, 2) == [running, later]


def test_a_session_that_has_ended_is_not_offered(repo):
    """Booking a finished session records an absence — never automatically."""
    k = _class(repo)
    who = _client(repo)
    now = db.now()
    _session(repo, k, now - 4 * 3600, hours=1.0)           # ended 3h ago
    ahead = _session(repo, k, _ts(TODAY + timedelta(days=2)))
    assert access.renewable_sessions(repo, k, who, 5) == [ahead]


def test_cancelled_sessions_are_skipped(repo):
    k = _class(repo)
    who = _client(repo)
    _session(repo, k, _ts(TODAY + timedelta(days=1)), status="cancelled")
    good = _session(repo, k, _ts(TODAY + timedelta(days=2)))
    assert access.renewable_sessions(repo, k, who, 5) == [good]


def test_sessions_they_already_hold_a_slot_in_are_skipped(repo):
    """
    Otherwise the renewal would try to double-book them and add_plan would
    refuse the whole sale.
    """
    k = _class(repo)
    who = _client(repo)
    taken = _session(repo, k, _ts(TODAY + timedelta(days=1)))
    free = _session(repo, k, _ts(TODAY + timedelta(days=2)))
    repo.insert("bookings", {
        "client_id": who, "subscription_id": None, "session_id": taken,
        "status": "booked", "checked_in_at": None, "created_at": db.now()})
    assert access.renewable_sessions(repo, k, who, 5) == [free]


def test_another_class_is_never_borrowed_from(repo):
    k, other = _class(repo, "Mine"), _class(repo, "Theirs")
    who = _client(repo)
    mine = _session(repo, k, _ts(TODAY + timedelta(days=5)))
    _session(repo, other, _ts(TODAY + timedelta(days=1)))
    assert access.renewable_sessions(repo, k, who, 5) == [mine]


# ---------------------------------------------------------------- refusals
def test_a_short_timetable_is_refused_with_what_to_do(repo):
    k = _class(repo, "Ballet Level 8")
    who = _client(repo)
    for d in (1, 2):
        _session(repo, k, _ts(TODAY + timedelta(days=d)))
    r = access.renew_at_desk(repo, who, k, "12 sessions", 12)
    assert not r["ok"]
    assert "Only 2" in r["error"] and "Ballet Level 8" in r["error"]
    assert "schedule more" in r["error"]
    assert repo.find("subscriptions", {"client_id": who}) == []


def test_one_missing_session_is_still_a_refusal(repo):
    """All or nothing: a plan with an unassigned slot is a promise nobody wrote down."""
    k = _class(repo)
    who = _client(repo)
    for d in (1, 2, 3):
        _session(repo, k, _ts(TODAY + timedelta(days=d)))
    assert not access.renew_at_desk(repo, who, k, "4 sessions", 4)["ok"]


def test_nonsense_is_refused(repo):
    k = _class(repo)
    who = _client(repo)
    _session(repo, k, _ts(TODAY + timedelta(days=1)))
    assert access.renew_at_desk(repo, who, k, "x", 0)["status"] == 400
    assert access.renew_at_desk(repo, who, 999999, "x", 1)["status"] == 404
    assert access.renew_at_desk(repo, 999999, k, "x", 1)["status"] == 404


# ---------------------------------------------------------------- what it does not do
def test_it_checks_nobody_in(repo):
    """
    Every slot of the new plan is unspent, including one on a session running
    right now. The client scans again to use it.
    """
    k = _class(repo)
    who = _client(repo)
    _session(repo, k, db.now() - 600, hours=1.5)
    _session(repo, k, _ts(TODAY + timedelta(days=2)))
    r = access.renew_at_desk(repo, who, k, "2 sessions", 2)
    assert r["state"]["remaining"] == 2
    assert r["state"]["present"] == 0 and r["state"]["absent"] == 0
    assert {b["status"] for b in repo.find("bookings", {"client_id": who})} == {"booked"}


def test_the_sale_itself_draws_no_card(repo, academy):
    """
    A layering fact rather than a promise to the receptionist: drawing a PNG
    is presentation, so it belongs to the route and not to this module. The
    card *is* issued on a desk renewal -- over HTTP, asserted below.
    """
    a = academy
    before = repo.find("credentials", {"client_id": a.dual})
    assert any(c["revoked_at"] is None for c in before)
    for d in range(1, 15):
        _session(repo, a.ballet, _ts(TODAY + timedelta(days=30 + d)))
    assert access.renew_at_desk(repo, a.dual, a.ballet, "12 sessions", 12)["ok"]
    after = repo.find("credentials", {"client_id": a.dual})
    assert len(after) == len(before)
    assert ([c["revoked_at"] for c in after]
            == [c["revoked_at"] for c in before])


def test_it_retires_only_that_class_s_previous_plan(repo, academy):
    a = academy
    for d in range(1, 15):
        _session(repo, a.ballet, _ts(TODAY + timedelta(days=30 + d)))
    assert access.renew_at_desk(repo, a.dual, a.ballet, "12 sessions", 12)["ok"]
    assert repo.get("subscriptions", a.dual_ballet_plan)["active"] == 0
    assert repo.get("subscriptions", a.dual_flex_plan)["active"] == 1


# ---------------------------------------------------------------- over HTTP
@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def test_the_endpoint_sells_and_reports_the_new_plan(client, repo):
    a = client.academy
    for d in range(1, 15):
        _session(repo, a.ballet, _ts(TODAY + timedelta(days=30 + d)))
    r = client.post("/api/access/renew", json={
        "client_id": a.dual, "class_id": a.ballet,
        "plan": "12 sessions", "sessions_total": 12,
        "price": 4100, "paid_on": TODAY.isoformat()})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["ok"] and body["state"]["remaining"] == 12
    assert body["state"]["sessions_total"] == 12


def test_the_endpoint_issues_the_card(client, repo):
    """
    Both ways in to a renewal replace the card, because the printed card
    carries the session count and end date of the plan it was made for and a
    renewal makes both of them wrong.
    """
    a = client.academy
    for d in range(1, 15):
        _session(repo, a.ballet, _ts(TODAY + timedelta(days=30 + d)))
    live = [c for c in repo.find("credentials", {"client_id": a.dual})
            if c["class_id"] == a.ballet and c["revoked_at"] is None]
    assert live, "fixture should start with a live ballet card"

    body = client.post("/api/access/renew", json={
        "client_id": a.dual, "class_id": a.ballet,
        "plan": "12 sessions", "sessions_total": 12}).json()
    assert body["card"]["ok"] and body["card"]["revoked"]

    after = repo.find("credentials", {"client_id": a.dual})
    ballet = [c for c in after if c["class_id"] == a.ballet]
    # Exactly one live ballet card, and it is the new one. The old is
    # revoked rather than deleted -- the log has to keep pointing at the
    # credential that was actually used.
    assert len([c for c in ballet if c["revoked_at"] is None]) == 1
    assert all(c["revoked_at"] is not None for c in ballet if c["id"] == live[0]["id"])
    # The other class is untouched: one card per class, and only the renewed
    # class's plan changed.
    flex = [c for c in after
            if c["class_id"] == a.flex and c["revoked_at"] is None]
    assert len(flex) == 1


def test_the_new_card_carries_the_new_plan_s_figures(client, repo):
    """The whole reason it is reissued at all. A stored picture is a
    snapshot nothing regenerates, so it has to be redrawn on the sale."""
    import cards
    import images
    a = client.academy
    for d in range(1, 15):
        _session(repo, a.ballet, _ts(TODAY + timedelta(days=30 + d)))
    # cards.class_slug() is the one place the variant is derived; asking it
    # here rather than writing the slug out keeps this test from being the
    # second copy of that rule.
    slug = cards.class_slug(repo.get("classes", a.ballet)["name"])
    before = repo.find_one("images", {"kind": images.CARD, "owner_id": a.dual,
                                      "variant": slug})
    client.post("/api/access/renew", json={
        "client_id": a.dual, "class_id": a.ballet,
        "plan": "12 sessions", "sessions_total": 12})
    after = repo.find_one("images", {"kind": images.CARD, "owner_id": a.dual,
                                     "variant": slug})
    assert after is not None
    assert before is None or after["data"] != before["data"]


def test_a_refused_sale_leaves_the_card_alone(client, repo):
    """Nothing was sold, so the card in their hand must go on working."""
    a = client.academy
    before = repo.find("credentials", {"client_id": a.dual})
    assert client.post("/api/access/renew", json={
        "client_id": a.dual, "class_id": a.ballet,
        "plan": "99 sessions", "sessions_total": 99}).status_code == 400
    after = repo.find("credentials", {"client_id": a.dual})
    assert ([c["revoked_at"] for c in after]
            == [c["revoked_at"] for c in before])


def test_the_endpoint_passes_the_refusal_through(client, repo):
    a = client.academy
    r = client.post("/api/access/renew", json={
        "client_id": a.dual, "class_id": a.ballet,
        "plan": "99 sessions", "sessions_total": 99})
    assert r.status_code == 400
    assert "Only" in r.json()["error"]


# ---------------------------------------------------------------- the offer
def test_a_scan_carries_the_plan_s_class_so_the_kiosk_can_renew_it(client):
    """
    The kiosk cannot offer to renew what it cannot name: a plan is bought for
    one class, so "renew" with no class is not a question the model answers.
    """
    a = client.academy
    v = client.post("/api/access/lookup", json={"client_id": a.dual}).json()
    assert v["plan_class_id"] == a.ballet or v["plan_class_id"] == a.flex
    assert v["plan_class_name"]
    assert v["plan_id"]


def test_an_exhausted_plan_is_what_the_offer_keys_off(repo, academy):
    """
    The kiosk shows the button when sessions_remaining is 0. That figure has
    to be reachable from a refusal as well as a grant, since the client with
    nothing left is refused.
    """
    a = academy
    sub = access.plan_state(repo, a.lapsed_plan)
    assert sub["remaining"] == 0
    v = access.verify_by_client(repo, a.lapsed)
    assert v["granted"] is False
    assert v["sessions_remaining"] == 0
    assert v["plan_class_id"] == a.ballet
