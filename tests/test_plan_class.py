"""
A plan, its sessions and its card all belong to the same class.

Ported from the old test_plan_class.py. Several of these are data invariants
still phrased as SQL; they are rewritten against the repository interface in
a later phase, once there is one.
"""

import access


def test_every_plan_names_a_class(academy):
    n = academy.repo.raw(
        "SELECT COUNT(*) n FROM subscriptions WHERE class_id IS NULL").fetchone()["n"]
    assert n == 0


def test_a_plan_lookup_never_falls_back_to_another_class(academy):
    """Falling back would let one card spend another class's balance."""
    repo = academy.repo
    assert access.active_plan(repo, academy.solo_ballet, academy.ballet) is not None
    assert access.active_plan(repo, academy.solo_ballet, academy.flex) is None


def test_no_booking_crosses_from_its_plans_class(academy):
    n = academy.repo.raw(
        "SELECT COUNT(*) n FROM bookings b JOIN sessions s ON s.id=b.session_id"
        "  JOIN subscriptions sub ON sub.id=b.subscription_id"
        " WHERE sub.class_id IS NOT NULL AND s.class_id != sub.class_id").fetchone()["n"]
    assert n == 0


def test_no_card_exists_without_a_matching_plan(academy):
    """A card that can never check anyone in reads at reception as a fault."""
    n = academy.repo.raw(
        "SELECT COUNT(*) n FROM credentials cr"
        " WHERE cr.revoked_at IS NULL AND cr.class_id IS NOT NULL"
        "   AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.client_id=cr.client_id"
        "                     AND s.class_id=cr.class_id AND s.active=1)").fetchone()["n"]
    assert n == 0


def test_two_classes_give_two_different_plans(academy):
    repo = academy.repo
    ballet = access.active_plan(repo, academy.dual, academy.ballet)
    flex = access.active_plan(repo, academy.dual, academy.flex)
    assert ballet["id"] != flex["id"]
    assert ballet["class_id"] == academy.ballet
    assert flex["class_id"] == academy.flex


def test_can_freeze_agrees_with_the_rule(academy):
    repo = academy.repo
    big = repo.raw("SELECT * FROM subscriptions WHERE id=?",
                       (academy.dual_ballet_plan,)).fetchone()
    small = repo.raw("SELECT * FROM subscriptions WHERE id=?",
                         (academy.dual_flex_plan,)).fetchone()
    assert access.can_freeze(big)[0] is True
    allowed, why = access.can_freeze(small)
    assert allowed is False
    assert str(access.FREEZE_MIN_SESSIONS) in why


def test_plan_state_reports_the_same_freeze_verdict_the_api_would(academy):
    """The button greys out for the same reason the endpoint refuses."""
    repo = academy.repo
    for r in repo.raw("SELECT id FROM subscriptions WHERE active=1").fetchall():
        state = access.plan_state(repo, r["id"])
        sub = repo.raw("SELECT * FROM subscriptions WHERE id=?", (r["id"],)).fetchone()
        assert state["can_freeze"] == access.can_freeze(sub)[0], r["id"]
