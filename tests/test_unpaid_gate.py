"""
An unpaid plan is good for one session, then it is not.

A client who has genuinely forgotten their wallet gets today's class and
pays next time; nobody is turned away at the door over a payment reception
could take in a minute. From the second session it is no longer a forgotten
wallet, and the refusal is what puts the payment in front of the
receptionist while the client is standing there — which is the only moment
it is easy to collect. access.UNPAID_GRACE_SESSIONS is that number.

The refusal carries `code="unpaid_plan"`, which is the only thing the kiosk
branches on to offer its UPDATE PLAN panel — the same shape
`no_session_today` and `absent_today` already have.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import access
import db


TODAY = date.today()


def _unpaid(repo, plan_id):
    repo.update("subscriptions", plan_id, {"paid_on": None})


def _clear_history(repo, plan_id, now):
    """
    Leave the plan with nothing used — as if today were its first session.

    The past bookings are deleted rather than set back to 'booked': every
    verify() runs settle_past_sessions() first, which would immediately mark
    a finished session absent again and undo the setup.
    """
    for b in repo.find("bookings", {"subscription_id": plan_id}):
        if repo.get("sessions", b["session_id"])["starts_at"] < now:
            repo.delete("bookings", b["id"])


# ---------------------------------------------------------------- the rule
def test_a_paid_plan_is_never_asked_about_money(academy):
    """The control. The fixture's ballet plan is paid and has history."""
    r = access.verify(academy.repo, academy.dual_ballet_card)
    assert r["granted"], r["message"]


def test_the_first_session_passes_on_trust(academy):
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    _clear_history(repo, academy.dual_ballet_plan, db.now())
    r = access.verify(repo, academy.dual_ballet_card)
    assert r["granted"], r["message"]
    assert r["paid_on"] is None, "still unpaid — the kiosk shows that as a tag"


def test_the_second_one_is_refused_until_it_is_paid(academy):
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    r = access.verify(repo, academy.dual_ballet_card)
    assert not r["granted"]
    assert r["code"] == "unpaid_plan"
    assert "paid" in r["message"].lower()


def test_exactly_one_session_of_trust(academy):
    """The boundary, asserted rather than assumed. Nothing used passes; one
    used does not — and one is access.UNPAID_GRACE_SESSIONS, read here
    rather than written out, so the test moves if the number does."""
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    _clear_history(repo, academy.dual_ballet_plan, db.now())
    assert access.verify(repo, academy.dual_ballet_card)["granted"]

    # Mark exactly the grace number used, on slots that are not today's --
    # today's is the one the scan has to keep finding.
    spare = [b for b in repo.find("bookings",
                                  {"subscription_id": academy.dual_ballet_plan})
             if b["session_id"] != academy.today_ballet]
    for b in spare[:access.UNPAID_GRACE_SESSIONS]:
        repo.update("bookings", b["id"], {"status": "absent"})
    assert not access.verify(repo, academy.dual_ballet_card)["granted"]


def test_nothing_is_deducted_by_the_refusal(academy):
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    before = access.plan_state(repo, academy.dual_ballet_plan)["remaining"]
    access.verify(repo, academy.dual_ballet_card)
    assert access.plan_state(repo, academy.dual_ballet_plan)["remaining"] == before


def test_it_carries_the_plan_so_the_kiosk_can_open_it(academy):
    """The panel opens on the plan the card just proved, so every field it
    edits has to arrive with the verdict — a second round trip with a client
    at the counter is the thing the kiosk exists to avoid."""
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    r = access.verify(repo, academy.dual_ballet_card)
    assert r["plan_id"] == academy.dual_ballet_plan
    assert r["plan"] and r["sessions_total"]
    assert r["plan_starts_on"] and r["expires_on"]
    assert r["client_id"] == academy.dual


def test_a_day_they_are_not_due_is_not_a_day_to_chase_them(academy):
    """The refusal is deliberately after a session of theirs has been found.
    Somebody with nothing on today should be told that, not asked for money
    on a day they were never expected."""
    repo = academy.repo
    _unpaid(repo, academy.dual_flex_plan)
    r = access.verify(repo, academy.dual_flex_card)
    assert not r["granted"]
    assert r["code"] == "no_session_today"


def test_an_older_plans_leftover_slot_is_not_chased(academy):
    """The gate is on the card's live plan. A slot left over from an
    already-renewed plan is finished business, and not what reception would
    be collecting for."""
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    # Move today's booking onto a plan that is not the live one.
    today_b = repo.find_one("bookings", {"client_id": academy.dual,
                                         "session_id": academy.today_ballet})
    repo.update("bookings", today_b["id"],
                {"subscription_id": academy.dual_flex_plan})
    r = access.verify(repo, academy.dual_ballet_card)
    assert r["code"] != "unpaid_plan"


def test_a_frozen_plan_still_answers_frozen_first(academy):
    """Order matters: unfreezing is what a frozen plan needs, and being told
    to pay instead would send reception after the wrong thing."""
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    repo.update("subscriptions", academy.dual_ballet_plan,
                {"frozen_on": TODAY.isoformat()})
    r = access.verify(repo, academy.dual_ballet_card)
    assert "frozen" in r["message"].lower()


# ---------------------------------------------------------------- over HTTP
@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def test_paying_at_the_desk_lets_them_straight_in(client, academy):
    """The whole point of the panel: the payment is taken, and the next
    thing that happens is the check-in it was blocking. The kiosk asks the
    server to scan them again rather than checking them in itself, so the
    deduction, the Undo and the day's count are the real ones."""
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    plan = access.plan_state(repo, academy.dual_ballet_plan)
    assert not access.verify(repo, academy.dual_ballet_card)["granted"]

    r = client.post("/api/access/plan-update", json={
        "plan_id": academy.dual_ballet_plan, "plan": plan["plan"],
        "sessions_total": plan["sessions_total"],
        "starts_on": plan["starts_on"], "price": 4100,
        "paid_on": TODAY.isoformat()})
    assert r.status_code == 200, r.json()
    assert r.json()["ok"] and r.json()["paid"] is True

    assert repo.get("subscriptions", academy.dual_ballet_plan)["paid_on"] == TODAY.isoformat()
    after = access.verify(repo, academy.dual_ballet_card)
    assert after["granted"], after["message"]


def test_saving_without_a_payment_changes_nothing_about_the_refusal(client, academy):
    """Clearing PAID ON is a real answer, not a half-finished save. The plan
    is still unpaid, so the next scan refuses it again — and the kiosk says
    so rather than pretending to check them in."""
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    plan = access.plan_state(repo, academy.dual_ballet_plan)
    r = client.post("/api/access/plan-update", json={
        "plan_id": academy.dual_ballet_plan, "plan": plan["plan"],
        "sessions_total": plan["sessions_total"],
        "starts_on": plan["starts_on"], "paid_on": None})
    assert r.status_code == 200, r.json()
    assert r.json()["paid"] is False
    assert access.verify(repo, academy.dual_ballet_card)["code"] == "unpaid_plan"


def test_the_desk_edit_leaves_the_card_alone_when_only_the_money_changed(client, academy):
    """Issuing revokes the card in their hand, and there is no reason to do
    that to somebody who has just paid. The card prints the session count
    and the end date; a payment changes neither, and paid_on is
    deliberately not on the card at all."""
    repo = academy.repo
    _unpaid(repo, academy.dual_ballet_plan)
    plan = access.plan_state(repo, academy.dual_ballet_plan)
    before = [c["revoked_at"] for c in repo.find("credentials",
                                                 {"client_id": academy.dual})]
    body = client.post("/api/access/plan-update", json={
        "plan_id": academy.dual_ballet_plan, "plan": plan["plan"],
        "sessions_total": plan["sessions_total"],
        "starts_on": plan["starts_on"], "paid_on": TODAY.isoformat()}).json()
    assert body["card"] is None
    after = [c["revoked_at"] for c in repo.find("credentials",
                                                {"client_id": academy.dual})]
    assert after == before


def test_a_plan_that_is_not_there(client):
    r = client.post("/api/access/plan-update", json={
        "plan_id": 99999, "plan": "x", "sessions_total": 4,
        "starts_on": TODAY.isoformat()})
    assert r.status_code == 404
