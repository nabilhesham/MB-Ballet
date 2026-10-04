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


def test_a_second_scan_carries_the_code_the_door_opens_on(academy):
    """
    The kiosk opens the door on this one refusal and no other -- somebody who
    stepped out and came back has a spent slot proving they were admitted
    today, and used to stand at a locked door through the amber verdict. The
    code is the only thing reception.html branches on, so losing it here is a
    door that silently stops opening.
    """
    repo = academy.repo
    first = access.verify(repo, academy.dual_ballet_card)
    access.check_in(repo, first["event_id"])

    second = access.verify(repo, academy.dual_ballet_card)
    assert second["code"] == "already_checked_in", second


def test_another_refusal_does_not_carry_it(academy):
    """
    The flexibility card on a day that class does not run: a real refusal,
    with its own code and its own remedy (the swap). If a second refusal ever
    started answering `already_checked_in`, the door would open for it.
    """
    r = access.verify(academy.repo, academy.dual_flex_card)
    assert not r["granted"]
    assert r["code"] != "already_checked_in", r


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


# ------------------------------------------------- a class that is still running
#
# The failure these hold happened on the academy's own screen: a 3:30 class
# showed as `completed` at twenty past three with two students already marked
# absent, in a room they were sitting in. The cause is a stored `ends_at`
# earlier than the session's own duration says -- the sweep then completes it
# and settles its no-shows mid-class, and nothing afterwards puts either back.
def test_a_short_stored_end_does_not_end_the_class_early(academy):
    """
    The column is the fast path for "has this ended?", not a second opinion on
    it. One hour written against a 1.5-hour class ends the class half an hour
    early, every single time, with nothing on screen to say why.
    """
    repo = academy.repo
    started = db.now() - 70 * 60                      # 1h10 into a 1.5h class
    sid = add_session(repo, academy.ballet, academy.ana, started, 1.5,
                      status="scheduled")
    repo.update("sessions", sid, {"ends_at": started + 3600})   # an hour short
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})

    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    assert repo.get("sessions", sid)["status"] == "scheduled"
    assert repo.find_one("bookings", {"session_id": sid})["status"] == "booked"
    # And the column is corrected, so the next pass works from the duration.
    assert repo.get("sessions", sid)["ends_at"] == access.ends_at_of(started, 1.5)


def test_a_session_still_running_is_put_back_to_scheduled(academy):
    """
    The repair for the state already in the database. `completed` on a class
    that is visibly running is what the dashboard prints instead of `now`, and
    it cannot clear itself without this.
    """
    repo = academy.repo
    started = db.now() - 20 * 60
    sid = add_session(repo, academy.ballet, academy.ana, started, 1.5,
                      status="completed")

    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    assert repo.get("sessions", sid)["status"] == "scheduled"


def test_reopening_gives_the_no_shows_their_slots_back(academy):
    """
    The half that matters at the counter: while the slot reads `absent` the
    kiosk stops checking a latecomer in the ordinary way and offers the swap
    instead, which is different work for the same person at the same door.
    """
    repo = academy.repo
    started = db.now() - 20 * 60
    sid = add_session(repo, academy.ballet, academy.ana, started, 1.5,
                      status="completed")
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})
    repo.update_where("bookings", {"session_id": sid}, {"status": "absent"})

    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    assert repo.find_one("bookings", {"session_id": sid})["status"] == "booked"


def test_an_attendance_already_taken_survives_the_repair(academy):
    """Present is a fact about somebody who turned up. Only absences, which
    the sweep itself writes, are given back."""
    repo = academy.repo
    started = db.now() - 20 * 60
    sid = add_session(repo, academy.ballet, academy.ana, started, 1.5,
                      status="completed")
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "present",
                             "checked_in_at": db.now(), "created_at": db.now()})

    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    assert repo.find_one("bookings", {"session_id": sid})["status"] == "present"


def test_a_finished_session_is_left_completed(academy):
    """The repair is about a class that is still running. One that is over
    stays over -- otherwise every settled session would reopen for ever."""
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana,
                      db.now() - 4 * 3600, 1.5, status="completed")
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "absent",
                             "created_at": db.now()})

    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    assert repo.get("sessions", sid)["status"] == "completed"
    assert repo.find_one("bookings", {"session_id": sid})["status"] == "absent"


def test_a_cancelled_session_is_never_reopened(academy):
    """Cancelling is how reception frees a slot up. Its end is still kept
    honest, because un-cancelling asks slot_conflict() about it."""
    repo = academy.repo
    started = db.now() - 20 * 60
    sid = add_session(repo, academy.ballet, academy.ana, started, 1.5,
                      status="cancelled")
    repo.update("sessions", sid, {"ends_at": started + 60})

    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    row = repo.get("sessions", sid)
    assert row["status"] == "cancelled"
    assert row["ends_at"] == access.ends_at_of(started, 1.5)


# ---------------------------------------------------------------- sweep cadence
#
# The absent rule has always been the session's own end (`ends_at < now`).
# What was an hour is how often the background loop in server.py woke up to
# apply it — and on a laptop sitting on the kiosk screen nothing else reads
# attendance, so that loop was the only thing applying it. A no-show became
# absent somewhere in the hour after their class instead of when it finished.
# access.seconds_to_next_sweep() is what the loop waits now.
def test_the_sweep_waits_for_the_session_to_end_not_an_hour(academy):
    """A class finishing in ten minutes is waited for, not slept past."""
    repo = academy.repo
    add_session(repo, academy.ballet, academy.ana,
                db.now() + 600 - int(1.5 * 3600), 1.5, status="scheduled")
    access.sweep_invalidate()
    access.settle_past_sessions(repo)          # recomputes the deadline

    wait = access.seconds_to_next_sweep()
    assert 540 <= wait <= 600, wait            # ~10 minutes, nothing like 3600


def test_a_session_ending_this_second_does_not_spin_the_loop(academy):
    """
    next_sweep_deadline answers `ends_at >= now` and settle_absences wants
    `<`, so the pass that lands exactly on a session's end has nothing to do
    and would be asked to wait zero seconds. The floor is what stops that
    becoming a busy loop.
    """
    repo = academy.repo
    add_session(repo, academy.ballet, academy.ana,
                db.now() - int(1.5 * 3600), 1.5, status="scheduled")
    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    assert access.seconds_to_next_sweep() >= access.SWEEP_WAIT_MIN


def test_a_quiet_timetable_still_gets_the_hourly_pass(academy):
    """
    With nothing ending for days the deadline is tomorrow's midnight — the
    freeze boundary — and the loop still wakes hourly as a heartbeat for what
    a deadline cannot see: a suspended laptop, a clock jump, a write made
    while it was already asleep.
    """
    repo = academy.repo
    repo.update_where("sessions", {}, {"status": "cancelled"})
    access.sweep_invalidate()
    access.settle_past_sessions(repo)

    assert access.seconds_to_next_sweep() == access.SWEEP_WAIT_MAX
