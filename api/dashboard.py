"""/api/dashboard — the single landing-page summary endpoint."""

from datetime import date, timedelta

from fastapi import APIRouter

import access
import db

from .helpers import rows

router = APIRouter()


@router.get("/api/dashboard")
def dashboard():
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        midnight, tomorrow = access.day_bounds()

        today_sessions = rows(conn.execute(
            "SELECT s.*, c.name AS class_name, c.colour, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s JOIN classes c ON c.id=s.class_id"
            "  LEFT JOIN instructors i ON i.id=s.instructor_id"
            " WHERE s.starts_at BETWEEN ? AND ? ORDER BY s.starts_at",
            (midnight, tomorrow)))

        recent = rows(conn.execute(
            "SELECT e.id, e.scanned_at, e.decision, e.reason, e.confirmed_at,"
            "       c.name_en, cl.name AS class_name"
            "  FROM access_events e LEFT JOIN clients c ON c.id=e.client_id"
            "  LEFT JOIN sessions s ON s.id=e.session_id"
            "  LEFT JOIN classes cl ON cl.id=s.class_id"
            " WHERE e.scanned_at >= ? ORDER BY e.scanned_at DESC LIMIT 60", (midnight,)))

        exp = access.expected_today(conn)
        intake = access.month_intake(conn)
        stats = {
            **{f"exp_{k}": v for k, v in exp.items()},
            **{f"mo_{k}": v for k, v in intake.items()},
            "scans_today": conn.execute(
                "SELECT COUNT(*) n FROM access_events WHERE scanned_at>=? AND source='scan'",
                (midnight,)).fetchone()["n"],
            "denied_today": conn.execute(
                "SELECT COUNT(*) n FROM access_events WHERE scanned_at>=? AND decision='deny'",
                (midnight,)).fetchone()["n"],
            "active_clients": conn.execute(
                "SELECT COUNT(*) n FROM clients WHERE active=1").fetchone()["n"],
            "classes": conn.execute(
                "SELECT COUNT(*) n FROM classes WHERE active=1").fetchone()["n"],
            "sessions_week": conn.execute(
                "SELECT COUNT(*) n FROM sessions WHERE starts_at BETWEEN ? AND ?"
                " AND status='scheduled'", (db.now(), db.now() + 7 * 86400)).fetchone()["n"],
        }

        today = date.today().isoformat()
        soon = (date.today() + timedelta(days=7)).isoformat()
        attention = []
        for r in conn.execute(
                "SELECT c.id, c.name_en, c.phone, s.id AS sub_id, s.plan"
                "  FROM clients c JOIN subscriptions s ON s.client_id=c.id AND s.active=1"
                " WHERE c.active=1").fetchall():
            st = access.plan_state(conn, r["sub_id"])
            if st["frozen"]:
                continue          # deliberately paused, not a problem to chase
            # plan_state owns what a plan is valid through — the stored
            # column is only its floor.
            expires = st["expires_on"]
            if st["remaining"] <= 2 or expires <= soon or st["unassigned"] > 0:
                attention.append({
                    "id": r["id"], "name_en": r["name_en"], "phone": r["phone"],
                    "plan": r["plan"], "expires_on": expires,
                    "remaining": st["remaining"], "unassigned": st["unassigned"],
                    "expired": expires < today,
                })
        attention.sort(key=lambda x: (x["remaining"], x["expires_on"]))

        return {"stats": stats, "today_sessions": today_sessions,
                "recent": recent, "attention": attention[:20], "today": today}
    finally:
        conn.close()
