"""
Scanning, checking in, and what a slot costs.

Ported from the old test_model.py. The behaviour asserted is the same; what
changed is that the subjects are built by tests/fixtures.py rather than found
by querying whichever term was last seeded.
"""

from datetime import date, timedelta

import access
import db


def test_a_client_holds_one_card_per_class(academy):
    held = academy.conn.execute(
        "SELECT class_id FROM credentials WHERE client_id=? AND revoked_at IS NULL",
        (academy.dual,)).fetchall()
    classes = [r["class_id"] for r in held]
    assert sorted(classes) == sorted([academy.ballet, academy.flex])
    assert len(classes) == len(set(classes)), "one card per class, not two for one"


def test_the_card_decides_which_class(academy):
    conn = academy.conn
    # The ballet card finds the ballet session running later today.
    granted = access.verify(conn, academy.dual_ballet_card)
    assert granted["granted"], granted["message"]
    assert granted["session"]["id"] == academy.today_ballet

    # The flexibility card, for the same client on the same day, does not.
    refused = access.verify(conn, academy.dual_flex_card)
    assert not refused["granted"]
    assert "Evening Flexibility" in refused["message"], refused["message"]


def test_the_card_reports_its_own_classs_plan(academy):
    r = access.verify(academy.conn, academy.dual_ballet_card)
    assert r["plan"] == "12 sessions", "the ballet card must not report the flex plan"


def test_arriving_early_still_matches_the_session(academy):
    r = access.verify(academy.conn, academy.dual_ballet_card)
    assert r["granted"]
    assert r["session"]["early"] is True
    assert r["session"]["minutes_until"] > 0


def test_checking_in_consumes_exactly_one_slot(academy):
    conn = academy.conn
    before = access.plan_state(conn, academy.dual_ballet_plan)["remaining"]
    r = access.verify(conn, academy.dual_ballet_card)
    access.check_in(conn, r["event_id"])
    after = access.plan_state(conn, academy.dual_ballet_plan)["remaining"]
    assert after == before - 1


def test_a_second_scan_the_same_day_deducts_nothing(academy):
    conn = academy.conn
    first = access.verify(conn, academy.dual_ballet_card)
    access.check_in(conn, first["event_id"])
    spent = access.plan_state(conn, academy.dual_ballet_plan)["remaining"]

    second = access.verify(conn, academy.dual_ballet_card)
    assert not second["granted"]
    assert access.plan_state(conn, academy.dual_ballet_plan)["remaining"] == spent


def test_a_second_scan_reads_amber_not_red(academy):
    """Scanning twice is ordinary. It must not answer with the revoked-card red."""
    conn = academy.conn
    first = access.verify(conn, academy.dual_ballet_card)
    access.check_in(conn, first["event_id"])
    second = access.verify(conn, academy.dual_ballet_card)
    assert second.get("severity") == "warn", second


def test_undo_refunds_the_slot(academy):
    conn = academy.conn
    before = access.plan_state(conn, academy.dual_ballet_plan)["remaining"]
    r = access.verify(conn, academy.dual_ballet_card)
    access.check_in(conn, r["event_id"])
    access.undo(conn, r["event_id"])
    assert access.plan_state(conn, academy.dual_ballet_plan)["remaining"] == before


def test_only_present_and_absent_are_accepted(academy):
    """A slot is either used or it is not. There is no third state."""
    conn = academy.conn
    assert not access.set_status(conn, academy.today_ballet, academy.dual, "excused")["ok"]
    assert access.set_status(conn, academy.today_ballet, academy.dual, "absent")["ok"]
    assert access.set_status(conn, academy.today_ballet, academy.dual, "present")["ok"]


def test_an_unattended_past_session_is_swept_to_absent(academy):
    conn = academy.conn
    past = conn.execute(
        "INSERT INTO sessions (class_id,instructor_id,starts_at,duration_hours,status)"
        " VALUES (?,?,?,1.5,'scheduled')",
        (academy.ballet, academy.ana, db.now() - 4 * 3600)).lastrowid
    conn.execute(
        "INSERT INTO bookings (client_id,session_id,subscription_id,status,created_at)"
        " VALUES (?,?,NULL,'booked',?)", (academy.planless, past, db.now()))
    conn.commit()

    access.settle_past_sessions(conn)

    status = conn.execute("SELECT status FROM bookings WHERE session_id=?",
                          (past,)).fetchone()["status"]
    assert status == "absent"


def test_the_sweep_also_completes_the_session(academy):
    conn = academy.conn
    past = conn.execute(
        "INSERT INTO sessions (class_id,instructor_id,starts_at,duration_hours,status)"
        " VALUES (?,?,?,1.5,'scheduled')",
        (academy.ballet, academy.ana, db.now() - 4 * 3600)).lastrowid
    conn.commit()
    access.settle_past_sessions(conn)
    assert conn.execute("SELECT status FROM sessions WHERE id=?",
                        (past,)).fetchone()["status"] == "completed"


def test_instructor_pay_is_hours_times_rate(academy):
    totals = access.logged_hours(
        academy.conn, academy.ana,
        (date.today() - timedelta(days=30)).isoformat(), date.today().isoformat())
    assert totals["hours"] == 40.0, totals          # 10 days x 4h
    assert totals["pay"] == 40.0 * 120.0, totals


def test_correcting_a_rate_reprices_the_month(academy):
    """Pay is derived at read time, never stored, so a new rate re-prices."""
    conn = academy.conn
    a, b = (date.today() - timedelta(days=30)).isoformat(), date.today().isoformat()
    conn.execute("UPDATE instructors SET hourly_rate=200 WHERE id=?", (academy.ana,))
    conn.commit()
    assert access.logged_hours(conn, academy.ana, a, b)["pay"] == 40.0 * 200.0


def test_month_intake_counts_new_clients_and_what_they_paid(academy):
    m = access.month_intake(academy.conn, date.today().strftime("%Y-%m"))
    # Five of the six fixture clients joined today; the sixth is archived and
    # the seventh joined 120 days ago.
    assert m["new_clients"] >= 4, m
    assert m["new_revenue"] <= m["revenue"], f"{m['new_revenue']} of {m['revenue']}"


def test_an_unpriced_plan_is_counted_not_treated_as_zero(academy):
    m = access.month_intake(academy.conn, date.today().strftime("%Y-%m"))
    assert m["unpriced"] >= 1, m
    assert m["unpriced"] <= m["plans"]
