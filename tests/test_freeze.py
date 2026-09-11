"""
Freezing: what it releases, what it refuses, and what it puts back.

Ported from the old test_freeze.py. One change of method: to test that
unfreezing extends the expiry, the original rewrote `subscriptions.frozen_on`
by hand to fabricate a ten-day-old freeze. `freeze_plan()` already takes a
`from_date`, so these tests age the freeze through the API instead of
reaching behind it.
"""

from datetime import date, timedelta

import access
import db
from fixtures import add_session


def test_freezing_releases_future_bookings(academy):
    repo = academy.repo
    plan = academy.dual_ballet_plan
    before = access.plan_state(repo, plan)
    booked = repo.count("bookings", {"subscription_id": plan, "status": "booked"})

    r = access.freeze_plan(repo, plan, reason="travelling")
    after = access.plan_state(repo, plan)

    assert r["ok"], r
    assert r["released"] == booked, f"{booked} booked -> {r['released']} released"
    assert after["unassigned"] == before["unassigned"] + r["released"]
    assert after["remaining"] == before["remaining"], "a freeze owes the same sessions"
    assert after["frozen"] is True
    assert after["frozen_until"] is None, "open-ended unless given an end date"


def test_a_frozen_plan_cannot_be_scanned(academy):
    repo = academy.repo
    access.freeze_plan(repo, academy.dual_ballet_plan)
    v = access.verify(repo, academy.dual_ballet_card)
    assert not v["granted"], v
    assert v.get("frozen") is True


def test_the_sweep_leaves_a_frozen_clients_session_alone(academy):
    """A paused client must never lose a session to the absent sweep."""
    repo = academy.repo
    plan = academy.dual_ballet_plan
    access.freeze_plan(repo, plan)

    past = add_session(repo, academy.ballet, academy.ana,
                       db.now() - 4 * 3600, 1.5, status="scheduled")
    repo.insert("bookings", {"client_id": academy.dual, "session_id": past,
                             "subscription_id": plan, "status": "booked",
                             "created_at": db.now()})

    access.settle_past_sessions(repo)

    status = repo.find_one("bookings", {"session_id": past,
                                       "client_id": academy.dual})["status"]
    assert status == "booked"


def test_unfreezing_extends_the_expiry_by_the_frozen_days(academy):
    repo = academy.repo
    plan = academy.dual_ballet_plan
    before = access.plan_state(repo, plan)["expires_on"]

    access.freeze_plan(repo, plan,
                       from_date=(date.today() - timedelta(days=10)).isoformat())
    u = access.unfreeze_plan(repo, plan)
    after = access.plan_state(repo, plan)

    assert u["ok"], u
    assert u["days"] == 10, u
    assert after["expires_on"] == (
        date.fromisoformat(before) + timedelta(days=10)).isoformat()
    assert after["frozen"] is False
    assert after["frozen_days"] == 10


def test_a_dated_freeze_lifts_itself_once_the_date_passes(academy):
    repo = academy.repo
    plan = academy.dual_ballet_plan
    access.freeze_plan(repo, plan,
                       from_date=(date.today() - timedelta(days=8)).isoformat(),
                       until=(date.today() - timedelta(days=1)).isoformat())
    assert access.plan_state(repo, plan)["frozen"] is True

    lifted = access.lift_expired_freezes(repo)

    assert lifted == 1
    assert access.plan_state(repo, plan)["frozen"] is False


def test_a_plan_cannot_be_frozen_twice(academy):
    repo = academy.repo
    access.freeze_plan(repo, academy.dual_ballet_plan)
    again = access.freeze_plan(repo, academy.dual_ballet_plan)
    assert not again["ok"]
    assert "already frozen" in again["error"]


def test_an_unfrozen_plan_cannot_be_unfrozen(academy):
    r = access.unfreeze_plan(academy.repo, academy.dual_ballet_plan)
    assert not r["ok"]
    assert "not frozen" in r["error"]


def test_a_freeze_ending_before_it_starts_is_refused(academy):
    r = access.freeze_plan(academy.repo, academy.dual_ballet_plan,
                           until=(date.today() - timedelta(days=3)).isoformat())
    assert not r["ok"]
    assert "after it starts" in r["error"]


def test_only_plans_of_twelve_or_more_can_be_frozen(academy):
    """Short packs are meant to be used inside their window."""
    repo = academy.repo
    r = access.freeze_plan(repo, academy.dual_flex_plan)      # 8 sessions
    assert not r["ok"]
    assert str(access.FREEZE_MIN_SESSIONS) in r["error"], r


def test_freezing_one_class_leaves_the_other_alone(academy):
    repo = academy.repo
    access.freeze_plan(repo, academy.dual_ballet_plan)
    assert access.plan_state(repo, academy.dual_flex_plan)["frozen"] is False


def test_freeze_history_is_kept(academy):
    repo = academy.repo
    plan = academy.dual_ballet_plan
    access.freeze_plan(repo, plan, reason="first")
    access.unfreeze_plan(repo, plan)
    access.freeze_plan(repo, plan, reason="second")

    rows = repo.find("freezes", {"subscription_id": plan}, sort=[("id", 1)])
    assert len(rows) == 2
    finished = [r for r in rows if r["ended_on"]]
    assert finished and all(r["days_added"] is not None for r in finished)
