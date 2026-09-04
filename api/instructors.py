"""/api/instructors/* — instructor roster, hours and pay."""

import os
import shutil
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

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


class HoursAdjustIn(BaseModel):
    from_: str = Field(alias="from")
    to: str
    new_total: float
    note: Optional[str] = None


# ---------------------------------------------------------------- routes
@router.get("/api/instructors")
def list_instructors(status: str = "active"):
    """
    `status="archived"` is the whole other half of this list, not an overlay
    on top of the active one. /api/classes and /api/clients read the same
    way; /api/clients additionally carries "attention", which filters within
    the active half rather than choosing a half.
    """
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        active = 0 if status == "archived" else 1
        data = rows(conn.execute(
            "SELECT * FROM instructors WHERE active=? ORDER BY name", (active,)))
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
def get_instructor(iid: int, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    """
    Everything on this page is scoped to a period, defaulting to this
    calendar month when no from/to is given — reception picking a different
    range recalculates every figure, including what's "upcoming", from the
    same query params.
    """
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        i = one(conn.execute("SELECT * FROM instructors WHERE id=?", (iid,)))
        if not i:
            raise HTTPException(404, "no such instructor")

        period_from, period_to = (from_, to) if from_ and to else access.month_bounds()
        if period_to < period_from:
            raise HTTPException(400, "the end of the range must not be before its start")
        start_ts, end_ts = access.date_range_ts(period_from, period_to)

        i["sessions"] = rows(conn.execute(
            "SELECT s.id, s.starts_at, s.duration_hours, s.status,"
            "       c.name AS class_name, c.colour,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s JOIN classes c ON c.id = s.class_id"
            " WHERE s.instructor_id = ? AND s.starts_at >= ? AND s.starts_at < ?"
            " ORDER BY s.starts_at DESC LIMIT 200", (iid, start_ts, end_ts)))

        t = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
            " WHERE instructor_id=? AND status='completed'"
            "   AND starts_at >= ? AND starts_at < ?", (iid, start_ts, end_ts)).fetchone()
        # "Upcoming" is bounded by the picked range too, not just by now: a
        # past range has none (nothing in it is still ahead), and a future
        # range only counts what's still ahead within that window.
        up_start = max(start_ts, db.now())
        up = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
            " WHERE instructor_id=? AND status='scheduled' AND starts_at >= ? AND starts_at < ?",
            (iid, up_start, end_ts)).fetchone()
        # Hours the salary sheet recorded, which is what payroll is actually
        # paid on. Sessions taught is the app's own count and the two are
        # deliberately shown side by side: a gap between them is either a
        # class that never made it onto the timetable or an hour nobody
        # billed for, and both are worth seeing.
        rate = i["hourly_rate"] or 0
        i["logged"] = access.logged_hours(conn, iid, period_from, period_to)
        i["period_from"], i["period_to"] = period_from, period_to
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


@router.post("/api/instructors/{iid}/hours-adjustment")
def adjust_hours(iid: int, body: HoursAdjustIn):
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM instructors WHERE id=?", (iid,)).fetchone():
            raise HTTPException(404, "no such instructor")
        if body.to < body.from_:
            raise HTTPException(400, "the end of the range must not be before its start")
        return access.adjust_logged_hours(conn, iid, body.from_, body.to, body.new_total, body.note)
    finally:
        conn.close()


@router.post("/api/instructors/{iid}/photo")
async def upload_photo(iid: int, file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "use jpg, png or webp")
    path = f"photos/instructor_{iid:05d}{ext}"
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    conn = db.connect()
    try:
        conn.execute("UPDATE instructors SET photo_path=? WHERE id=?", ("/" + path, iid))
        conn.commit()
        return {"photo_path": "/" + path}
    finally:
        conn.close()


@router.post("/api/instructors/{iid}/unarchive")
def unarchive_instructor(iid: int):
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM instructors WHERE id=?", (iid,)).fetchone():
            raise HTTPException(404, "no such instructor")
        conn.execute("UPDATE instructors SET active=1 WHERE id=?", (iid,))
        conn.commit()
        return {"ok": True}
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
