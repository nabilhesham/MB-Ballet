"""
Scanning, checking in, and what a slot costs.

Ported from the old test_model.py. The behaviour asserted is the same; what
changed is that the subjects are built by tests/fixtures.py rather than found
by querying whichever term was last seeded.
"""

from datetime import date, timedelta

import access
import db
from fixtures import add_session, later_today


def test_a_client_holds_one_card_per_class(academy):
    held = academy.repo.find("credentials", {"client_id": academy.dual,
                                             "revoked_at": None})
    classes = [r["class_id"] for r in held]
    assert sorted(classes) == sorted([academy.ballet, academy.flex])
    assert len(classes) == len(set(classes)), "one card per class, not two for one"


def test_the_card_decides_which_class(academy):
    repo = academy.repo
    # The ballet card finds the ballet session running later today.
    granted = access.verify(repo, academy.dual_ballet_card)
    assert granted["granted"], granted["message"]
    assert granted["session"]["id"] == academy.today_ballet

    # The flexibility card, for the same client on the same day, does not.
    refused = access.verify(repo, academy.dual_flex_card)
    assert not refused["granted"]
    assert "Evening Flexibility" in refused["message"], refused["message"]


def test_the_card_reports_its_own_classs_plan(academy):
    r = access.verify(academy.repo, academy.dual_ballet_card)
    assert r["plan"] == "12 sessions", "the ballet card must not report the flex plan"


def test_arriving_early_still_matches_the_session(academy):
    r = access.verify(academy.repo, academy.dual_ballet_card)
    assert r["granted"]
    assert r["session"]["early"] is True
    assert r["session"]["minutes_until"] > 0


def test_checking_in_consumes_exactly_one_slot(academy):
    repo = academy.repo
    before = access.plan_state(repo, academy.dual_ballet_plan)["remaining"]
    r = access.verify(repo, academy.dual_ballet_card)
    access.check_in(repo, r["event_id"])
    after = access.plan_state(repo, academy.dual_ballet_plan)["remaining"]
    assert after == before - 1


def test_a_second_scan_the_same_day_deducts_nothing(academy):
    repo = academy.repo
    first = access.verify(repo, academy.dual_ballet_card)
    access.check_in(repo, first["event_id"])
    spent = access.plan_state(repo, academy.dual_ballet_plan)["remaining"]

    second = access.verify(repo, academy.dual_ballet_card)
    assert not second["granted"]
    assert access.plan_state(repo, academy.dual_ballet_plan)["remaining"] == spent


def test_a_second_scan_reads_amber_not_red(academy):
    """Scanning twice is ordinary. It must not answer with the revoked-card red."""
    repo = academy.repo
    first = access.verify(repo, academy.dual_ballet_card)
    access.check_in(repo, first["event_id"])
    second = access.verify(repo, academy.dual_ballet_card)
    assert second.get("severity") == "warn", second


def test_undo_refunds_the_slot(academy):
    repo = academy.repo
    before = access.plan_state(repo, academy.dual_ballet_plan)["remaining"]
    r = access.verify(repo, academy.dual_ballet_card)
    access.check_in(repo, r["event_id"])
    access.undo(repo, r["event_id"])
    assert access.plan_state(repo, academy.dual_ballet_plan)["remaining"] == before


def test_only_present_and_absent_are_accepted(academy):
    """A slot is either used or it is not. There is no third state."""
    repo = academy.repo
    assert not access.set_status(repo, academy.today_ballet, academy.dual, "excused")["ok"]
    assert access.set_status(repo, academy.today_ballet, academy.dual, "absent")["ok"]
    assert access.set_status(repo, academy.today_ballet, academy.dual, "present")["ok"]


def test_an_unattended_past_session_is_swept_to_absent(academy):
    repo = academy.repo
    past = add_session(repo, academy.ballet, academy.ana,
                       db.now() - 4 * 3600, 1.5, status="scheduled")
    repo.insert("bookings", {"client_id": academy.planless, "session_id": past,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})

    access.settle_past_sessions(repo)

    assert repo.find_one("bookings", {"session_id": past})["status"] == "absent"


def test_the_sweep_also_completes_the_session(academy):
    repo = academy.repo
    past = add_session(repo, academy.ballet, academy.ana,
                       db.now() - 4 * 3600, 1.5, status="scheduled")
    access.settle_past_sessions(repo)
    assert repo.get("sessions", past)["status"] == "completed"


def test_instructor_pay_is_hours_times_rate(academy):
    totals = access.logged_hours(
        academy.repo, academy.ana,
        (date.today() - timedelta(days=30)).isoformat(), date.today().isoformat())
    assert totals["hours"] == 40.0, totals          # 10 days x 4h
    assert totals["pay"] == 40.0 * 120.0, totals


def test_correcting_a_rate_reprices_the_month(academy):
    """Pay is derived at read time, never stored, so a new rate re-prices."""
    repo = academy.repo
    a, b = (date.today() - timedelta(days=30)).isoformat(), date.today().isoformat()
    repo.update("instructors", academy.ana, {"hourly_rate": 200})
    assert access.logged_hours(repo, academy.ana, a, b)["pay"] == 40.0 * 200.0


def test_month_intake_counts_new_clients_and_what_they_paid(academy):
    m = access.month_intake(academy.repo, date.today().strftime("%Y-%m"))
    # Five of the six fixture clients joined today; the sixth is archived and
    # the seventh joined 120 days ago.
    assert m["new_clients"] >= 4, m
    assert m["new_revenue"] <= m["revenue"], f"{m['new_revenue']} of {m['revenue']}"


def test_an_unpriced_plan_is_counted_not_treated_as_zero(academy):
    m = access.month_intake(academy.repo, date.today().strftime("%Y-%m"))
    assert m["unpriced"] >= 1, m
    assert m["unpriced"] <= m["plans"]


# ------------------------------------------------ the card matches the plan

def test_a_card_finds_a_slot_moved_to_another_classs_session(academy):
    """
    The scan matches the booking's *plan's* class, not the session's.

    This is what records "she missed Ballet on Tuesday and came to
    Flexibility on Wednesday instead" without selling a second plan: the slot
    keeps the plan that paid for it, so the Ballet card still opens the door
    for it. Matching on the session's own class would turn her away.
    """
    repo = academy.repo
    # A flexibility session today, which the ballet plan will be moved onto.
    target = add_session(repo, academy.flex, academy.bea,
                         later_today(2), 1.0, status="scheduled")
    moved = access.move_booking(repo, academy.dual, academy.today_ballet, target,
                                allow_other_class=True)
    assert moved["ok"], moved

    r = access.verify(repo, academy.dual_ballet_card)

    assert r["granted"] is True, r["message"]
    assert r["session"]["id"] == target
    assert r["session"]["class_name"] == "Evening Flexibility"


def test_the_other_classs_card_still_cannot_spend_that_slot(academy):
    """One card per class keeps meaning something after a cross-class move."""
    repo = academy.repo
    target = add_session(repo, academy.flex, academy.bea,
                         later_today(2), 1.0, status="scheduled")
    access.move_booking(repo, academy.dual, academy.today_ballet, target,
                        allow_other_class=True)

    r = access.verify(repo, academy.dual_flex_card)

    assert r["granted"] is False, "the flex card must not spend a ballet-funded slot"


def test_a_booking_with_no_plan_falls_back_to_the_sessions_class(academy):
    """Older rows carry no subscription_id and keep the original rule."""
    repo = academy.repo
    repo.update_where("bookings",
                      {"client_id": academy.dual, "session_id": academy.today_ballet},
                      {"subscription_id": None})
    r = access.verify(repo, academy.dual_ballet_card)
    assert r["granted"] is True, r["message"]


def test_a_cancelled_session_is_not_a_match(academy):
    repo = academy.repo
    repo.update("sessions", academy.today_ballet, {"status": "cancelled"})
    r = access.verify(repo, academy.dual_ballet_card)
    assert r["granted"] is False
    assert r.get("code") == "no_session_today", r


def test_the_nearest_session_of_the_day_is_the_one_matched(academy):
    """What ORDER BY ABS(starts_at - now) was for."""
    repo = academy.repo
    far = add_session(repo, academy.ballet, academy.ana,
                      later_today(8), 1.0, status="scheduled")
    access.book(repo, academy.dual, far, academy.dual_ballet_plan)

    r = access.verify(repo, academy.dual_ballet_card)

    assert r["granted"] is True
    assert r["session"]["id"] == academy.today_ballet, "the nearer of the two"
