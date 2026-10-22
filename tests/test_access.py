"""
Scanning, checking in, and what a slot costs.

Ported from the old test_model.py. The behaviour asserted is the same; what
changed is that the subjects are built by tests/fixtures.py rather than found
by querying whichever term was last seeded.
"""

import re
from datetime import date, datetime, time, timedelta

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


def test_the_second_scan_sends_the_instant_not_a_rendered_time(academy):
    """
    One instant, one renderer.

    This refusal used to format the time with the *server's* timezone
    (`time.strftime`), while every screen in the app renders a stamp with the
    browser's. A server process an hour off the browser -- the app under WSL
    with no TZ while the browser carries Egypt's summer offset is exactly
    that shape -- therefore printed one check-in as two different times on
    two screens, with the clock perfectly correct, and it reads as the app
    adding an hour when it saves.
    """
    repo = academy.repo
    first = access.verify(repo, academy.dual_ballet_card)
    access.check_in(repo, first["event_id"])

    second = access.verify(repo, academy.dual_ballet_card)

    # The epoch travels, and it is the one on the booking.
    assert isinstance(second["checked_in_at"], int)
    assert abs(second["checked_in_at"] - db.now()) < 120
    # The sentence names no clock time at all -- the kiosk appends it.
    assert not re.search(r"\d{1,2}:\d{2}", second["message"]), second["message"]
    assert "Already checked in today" in second["message"]


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


def test_a_finished_session_writes_off_its_no_show(academy):
    """The event, end to end: it ended, they never came, the slot is spent."""
    repo = academy.repo
    past = add_session(repo, academy.ballet, academy.ana,
                       db.now() - 4 * 3600, 1.5, status="scheduled")
    repo.insert("bookings", {"client_id": academy.planless, "session_id": past,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})

    access.settle_due_sessions(repo)

    b = repo.find_one("bookings", {"session_id": past})
    assert b["status"] == "absent"
    assert b["settled_at"] == db.now()
    assert repo.get("sessions", past)["status"] == "completed"


def test_a_session_still_running_writes_nobody_off(academy):
    """
    Twenty minutes into a 1.5-hour class. Nothing about the length of the
    class it belongs to is consulted, so there is no version of this where
    the slot is spent at the one-hour mark.
    """
    repo = academy.repo
    running = add_session(repo, academy.ballet, academy.ana,
                          db.now() - 20 * 60, 1.5, status="scheduled")
    repo.insert("bookings", {"client_id": academy.planless,
                             "session_id": running,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})

    assert access.settle_due_sessions(repo) == 0

    b = repo.find_one("bookings", {"session_id": running})
    assert b["status"] == "booked"
    assert b["settled_at"] is None
    assert repo.get("sessions", running)["status"] == "scheduled"


def test_a_read_settles_nothing_on_the_way_past(academy):
    """
    Reading a screen does not settle anything any more, which is the other
    half of "the event is the only mechanism". The sweep used to run at the
    top of nine read endpoints; a read that still settled would hide the
    watcher never firing.
    """
    repo = academy.repo
    past = add_session(repo, academy.ballet, academy.ana,
                       db.now() - 4 * 3600, 1.5, status="scheduled")
    repo.insert("bookings", {"client_id": academy.planless, "session_id": past,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})

    access.verify_by_client(repo, academy.planless)
    access.session_roster(repo, past)

    assert repo.find_one("bookings", {"session_id": past})["status"] == "booked"


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


# ------------------------------------------------- when the event is set for
#
# The event is an alarm, and `ends_at` is the time it is set to. Everything
# below is about that one number: who writes it, that it is the session's own
# length and never its class's, and that the watcher sleeps until it rather
# than polling an hour at a time.
#
# This replaced a repair (`access._repair_running_sessions`), which existed
# because the sweep re-asked "has this ended?" on every pass against a column
# a class edit could silently rewrite -- so a 3:30 class showed `completed` at
# twenty past three with two students marked absent in a room they were
# sitting in. Nothing rewrites the column behind a session's back any more:
# the class's length does not cascade, so the only way to move an end is to
# edit the session.
def test_the_stored_end_comes_from_the_sessions_own_length(academy):
    repo = academy.repo
    started = db.now() + 86400
    sid = add_session(repo, academy.ballet, academy.ana, started, 2.5,
                      status="scheduled")
    assert repo.get("sessions", sid)["ends_at"] == access.ends_at_of(started, 2.5)


def test_the_event_is_set_by_the_stored_end_not_by_an_hour(academy):
    """
    A class finishing in ten minutes is waited for, not slept past. The
    background loop used to wake on a flat hour, so a no-show became absent
    somewhere in the hour after their class rather than when it finished --
    and on a laptop sitting on the kiosk that loop is the only thing there is.
    """
    repo = academy.repo
    add_session(repo, academy.ballet, academy.ana,
                db.now() + 600 - int(1.5 * 3600), 1.5, status="scheduled")

    wait = access.seconds_to_next_end(repo)
    assert 540 <= wait <= 601, wait            # ~10 minutes, nothing like 3600


def test_a_two_hour_class_is_waited_for_in_full(academy):
    """
    The same question asked of a length longer than an hour, since "it marks
    them absent after one hour even though the session is longer" is the
    report all of this comes from. Ninety minutes into a two-hour class the
    alarm is still half an hour out.

    Compared against the next midnight too, for the reason the heartbeat test
    below gives: the deadline is the *earlier* of the next end and the freeze
    boundary, so run inside the last half hour of the day this is legitimately
    nearer than the class's end and a flat assertion fails by the clock.
    """
    import time as _time
    from datetime import date, datetime, time as _t, timedelta

    repo = academy.repo
    repo.update_where("sessions", {}, {"status": "cancelled"})
    add_session(repo, academy.ballet, academy.ana,
                db.now() - 90 * 60, 2.0, status="scheduled")

    midnight = datetime.combine(date.today() + timedelta(days=1), _t.min).timestamp()
    expected = min(30 * 60, midnight - _time.time())

    wait = access.seconds_to_next_end(repo)
    assert abs(wait - expected) <= 2, (wait, expected)
    # Whatever the hour, it is nothing like the flat 3600 the loop used to
    # sleep, and nothing like the one hour the report complains about.
    assert wait > 60, wait


def test_a_session_ending_this_second_does_not_spin_the_loop(academy):
    """
    next_session_end() answers `ends_at >= now` and due_sessions() wants `<`,
    so the pass that lands exactly on a session's end has nothing to do and
    would be asked to wait zero seconds. The floor is what stops that
    becoming a busy loop.
    """
    repo = academy.repo
    add_session(repo, academy.ballet, academy.ana,
                db.now() - int(1.5 * 3600), 1.5, status="scheduled")

    assert access.seconds_to_next_end(repo) >= access.WATCH_WAIT_MIN


def test_the_wait_lands_just_past_the_end_not_on_it(academy):
    """
    Sleeping *to* the deadline made the first pass find nothing (`>= now`
    against `<`) and the event fired a floor-length late -- 5s of a slot
    still reading `booked` after the class was over, measured against a
    running server. One second past the end is what makes it exact.
    """
    repo = academy.repo
    repo.update_where("sessions", {}, {"status": "cancelled"})
    ends_in = 600
    add_session(repo, academy.ballet, academy.ana,
                db.now() + ends_in - int(1.5 * 3600), 1.5, status="scheduled")

    assert access.seconds_to_next_end(repo) == ends_in + access.WATCH_LAG


def test_a_quiet_timetable_still_gets_the_hourly_heartbeat(academy):
    """
    With nothing ending for days the deadline is tomorrow's midnight — the
    freeze boundary — and the loop still wakes hourly as a heartbeat for what
    a sleeping process cannot see: a suspended laptop, a clock jump.

    **The hour is a ceiling, not the answer**, which is why this compares
    against the midnight too: run inside the last hour of the day and the
    deadline is nearer than the cap, so asserting the flat hour made the
    suite pass or fail by the clock it was started at — the same hazard the
    fixture guards for recurring sessions.
    """
    import time as _time
    from datetime import date, datetime, time as _t, timedelta

    repo = academy.repo
    repo.update_where("sessions", {}, {"status": "cancelled"})

    midnight = datetime.combine(date.today() + timedelta(days=1), _t.min).timestamp()
    expected = min(access.WATCH_WAIT_MAX, midnight - _time.time())

    assert abs(access.seconds_to_next_end(repo) - expected) <= 2
