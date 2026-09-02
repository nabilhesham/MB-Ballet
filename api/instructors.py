"""/api/instructors/* — instructor roster, hours and pay."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import access
import db

from .helpers import rows, one

router = APIRouter()


# ---------------------------------------------------------------- models
class InstructorIn(BaseModel):
    name: str
    phone: Optional[str] = None
    specialty: Optional[str] = None
    hourly_rate: float = 0


# ---------------------------------------------------------------- routes
@router.get("/api/instructors")
def list_instructors():
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        data = rows(conn.execute(
            "SELECT * FROM instructors WHERE active=1 ORDER BY name"))
        for i in data:
            t = conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
                " WHERE instructor_id=? AND status='completed'", (i["id"],)).fetchone()
            i["sessions_taught"] = t["n"]
            i["hours_taught"] = round(t["h"], 2)
            i["earned"] = round(t["h"] * (i["hourly_rate"] or 0), 2)
        return data
    finally:
        conn.close()


@router.post("/api/instructors")
def create_instructor(body: InstructorIn):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO instructors (name, phone, specialty, hourly_rate)"
            " VALUES (?,?,?,?)",
            (body.name, body.phone, body.specialty, body.hourly_rate))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.put("/api/instructors/{iid}")
def update_instructor(iid: int, body: InstructorIn):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE instructors SET name=?, phone=?, specialty=?, hourly_rate=?"
            " WHERE id=?",
            (body.name, body.phone, body.specialty, body.hourly_rate, iid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/api/instructors/{iid}")
def get_instructor(iid: int):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        i = one(conn.execute("SELECT * FROM instructors WHERE id=?", (iid,)))
        if not i:
            raise HTTPException(404, "no such instructor")

        i["sessions"] = rows(conn.execute(
            "SELECT s.id, s.starts_at, s.duration_hours, s.status,"
            "       c.name AS class_name, c.colour,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s JOIN classes c ON c.id = s.class_id"
            " WHERE s.instructor_id = ? ORDER BY s.starts_at DESC LIMIT 200", (iid,)))

        t = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
            " WHERE instructor_id=? AND status='completed'", (iid,)).fetchone()
        up = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
            " WHERE instructor_id=? AND status='scheduled' AND starts_at >= ?",
            (iid, db.now())).fetchone()
        # Hours the salary sheet recorded, which is what payroll is actually
        # paid on. Sessions taught is the app's own count and the two are
        # deliberately shown side by side: a gap between them is either a
        # class that never made it onto the timetable or an hour nobody
        # billed for, and both are worth seeing.
        logged = conn.execute(
            "SELECT COALESCE(SUM(hours),0) h, COUNT(*) days, MIN(work_date) a,"
            "       MAX(work_date) b FROM instructor_hours WHERE instructor_id=?",
            (iid,)).fetchone()
        rate = i["hourly_rate"] or 0
        i["logged"] = {
            "hours": round(logged["h"], 2),
            "days": logged["days"],
            "from": logged["a"], "to": logged["b"],
            "pay": round(logged["h"] * rate, 2),
        }
        i["totals"] = {
            "sessions_taught": t["n"],
            "hours_taught": round(t["h"], 2),
            "hourly_rate": rate,
            "earned": round(t["h"] * rate, 2),
            "upcoming": up["n"],
            "upcoming_hours": round(up["h"], 2),
            "upcoming_value": round(up["h"] * rate, 2),
        }
        return i
    finally:
        conn.close()


@router.delete("/api/instructors/{iid}")
def archive_instructor(iid: int):
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) n FROM sessions WHERE instructor_id=? AND status='scheduled'"
            "   AND starts_at >= ?", (iid, db.now())).fetchone()["n"]
        if n:
            raise HTTPException(400, f"Still assigned to {n} upcoming session(s) — reassign first")
        conn.execute("UPDATE instructors SET active=0 WHERE id=?", (iid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
