"""
A plan, its sessions and its card all belong to the same class.

Ported from the old test_plan_class.py. Several of these are data invariants
still phrased as SQL; they are rewritten against the repository interface in
a later phase, once there is one.
"""

import access


def test_every_plan_names_a_class(academy):
    assert academy.repo.count("subscriptions", {"class_id": None}) == 0


def test_a_plan_lookup_never_falls_back_to_another_class(academy):
    """Falling back would let one card spend another class's balance."""
    repo = academy.repo
    assert access.active_plan(repo, academy.solo_ballet, academy.ballet) is not None
    assert access.active_plan(repo, academy.solo_ballet, academy.flex) is None


def test_no_booking_crosses_from_its_plans_class(academy):
    repo = academy.repo
    sessions = {s["id"]: s for s in repo.find("sessions")}
    plans = {p["id"]: p for p in repo.find("subscriptions")}
    crossed = [b for b in repo.find("bookings")
               if b["subscription_id"] is not None
               and plans[b["subscription_id"]]["class_id"] is not None
               and sessions[b["session_id"]]["class_id"]
               != plans[b["subscription_id"]]["class_id"]]
    assert crossed == []


def test_no_card_exists_without_a_matching_plan(academy):
    """A card that can never check anyone in reads at reception as a fault."""
    repo = academy.repo
    live = {(p["client_id"], p["class_id"])
            for p in repo.find("subscriptions", {"active": 1})}
    orphans = [c for c in repo.find("credentials", {"revoked_at": None})
               if c["class_id"] is not None
               and (c["client_id"], c["class_id"]) not in live]
    assert orphans == []


def test_two_classes_give_two_different_plans(academy):
    repo = academy.repo
    ballet = access.active_plan(repo, academy.dual, academy.ballet)
    flex = access.active_plan(repo, academy.dual, academy.flex)
    assert ballet["id"] != flex["id"]
    assert ballet["class_id"] == academy.ballet
    assert flex["class_id"] == academy.flex


def test_can_freeze_agrees_with_the_rule(academy):
    repo = academy.repo
    big = repo.get("subscriptions", academy.dual_ballet_plan)
    small = repo.get("subscriptions", academy.dual_flex_plan)
    assert access.can_freeze(big)[0] is True
    allowed, why = access.can_freeze(small)
    assert allowed is False
    assert str(access.FREEZE_MIN_SESSIONS) in why


def test_plan_state_reports_the_same_freeze_verdict_the_api_would(academy):
    """The button greys out for the same reason the endpoint refuses."""
    repo = academy.repo
    for sub in repo.find("subscriptions", {"active": 1}):
        state = access.plan_state(repo, sub["id"])
        assert state["can_freeze"] == access.can_freeze(sub)[0], sub["id"]
