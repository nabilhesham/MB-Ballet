"""
/api/sessions/* — dated occurrences, their rosters, and booking moves.

`move_booking` lives here rather than under api/clients.py despite its URL
(`/api/clients/{cid}/move-booking/{from_sid}`) because it is a booking
operation through and through — same access.move_booking() call, same
JSONResponse-with-ok pattern as book/unbook right above it.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import access
import db

from .helpers import rows, one

router = APIRouter()


# ---------------------------------------------------------------- models
class SessionIn(BaseModel):
    class_id: int
    instructor_id: Optional[int] = None
    starts_at: int
    duration_hours: Optional[float] = None
    notes: Optional[str] = None


class SessionEdit(BaseModel):
    class_id: Optional[int] = None
    instructor_id: Optional[int] = None
    starts_at: Optional[int] = None
    duration_hours: Optional[float] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class RepeatIn(BaseModel):
    class_id: int
    instructor_id: Optional[int] = None
    starts_at: int
    weeks: int = 8
    weekdays: list[int] = []
    duration_hours: Optional[float] = None


class BookIn(BaseModel):
    client_id: int


class StatusIn(BaseModel):
    client_id: int
    status: str


class MoveIn(BaseModel):
    to_session_id: int


# ---------------------------------------------------------------- routes
@router.get("/api/sessions")
def list_sessions(start: int = 0, end: int = 0, class_id: int = 0, available_for: int = 0):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        if not start:
            start = db.now() - 7 * 86400
        if not end:
            end = start + 28 * 86400
        sql = (
            "SELECT s.*, c.name AS class_name, c.colour, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s JOIN classes c ON c.id=s.class_id"
            "  LEFT JOIN instructors i ON i.id=s.instructor_id"
            " WHERE s.starts_at BETWEEN ? AND ?")
        params = [start, end]
        if class_id:
            sql += " AND s.class_id = ?"
            params.append(class_id)
        if available_for:
            # Sessions this client is not already booked into.
            sql += (" AND NOT EXISTS (SELECT 1 FROM bookings b"
                    " WHERE b.session_id = s.id AND b.client_id = ?)")
            params.append(available_for)
        sql += " ORDER BY s.starts_at"
        return rows(conn.execute(sql, params))
    finally:
        conn.close()


@router.post("/api/sessions")
def create_session(body: SessionIn):
    conn = db.connect()
    try:
        cl = one(conn.execute("SELECT * FROM classes WHERE id=?", (body.class_id,)))
        if not cl:
            raise HTTPException(404, "no such class")
        # No instructor named explicitly -> fall back to the class's default,
        # if it has one. Naming one, even a different one, always wins.
        instructor_id = body.instructor_id if body.instructor_id is not None else cl["instructor_id"]
        cur = conn.execute(
            "INSERT INTO sessions (class_id, instructor_id, starts_at, duration_hours, notes)"
            " VALUES (?,?,?,?,?)",
            (body.class_id, instructor_id, body.starts_at,
             body.duration_hours or cl["duration_hours"], body.notes))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.post("/api/sessions/repeat")
def repeat_sessions(body: RepeatIn):
    conn = db.connect()
    try:
        cl = one(conn.execute("SELECT * FROM classes WHERE id=?", (body.class_id,)))
        if not cl:
            raise HTTPException(404, "no such class")
        instructor_id = body.instructor_id if body.instructor_id is not None else cl["instructor_id"]
        base = datetime.fromtimestamp(body.starts_at)
        weekdays = body.weekdays or [base.weekday()]
        made = 0
        for w in range(body.weeks):
            monday = base - timedelta(days=base.weekday()) + timedelta(weeks=w)
            for wd in weekdays:
                when = monday.replace(hour=base.hour, minute=base.minute,
                                      second=0, microsecond=0) + timedelta(days=wd)
                ts = int(when.timestamp())
                if ts < body.starts_at:
                    continue
                if conn.execute("SELECT 1 FROM sessions WHERE class_id=? AND starts_at=?",
                                (body.class_id, ts)).fetchone():
                    continue
                conn.execute(
                    "INSERT INTO sessions (class_id, instructor_id, starts_at, duration_hours)"
                    " VALUES (?,?,?,?)",
                    (body.class_id, instructor_id, ts,
                     body.duration_hours or cl["duration_hours"]))
                made += 1
        conn.commit()
        return {"created": made}
    finally:
        conn.close()


@router.get("/api/sessions/{sid}")
def get_session(sid: int):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        s = one(conn.execute(
            "SELECT s.*, c.name AS class_name, c.colour, i.name AS instructor_name"
            "  FROM sessions s JOIN classes c ON c.id=s.class_id"
            "  LEFT JOIN instructors i ON i.id=s.instructor_id WHERE s.id=?", (sid,)))
        if not s:
            raise HTTPException(404, "no such session")
        s["roster"] = access.session_roster(conn, sid)
        return s
    finally:
        conn.close()


@router.put("/api/sessions/{sid}")
def edit_session(sid: int, body: SessionEdit, clear_instructor: bool = False):
    """
    Partial update — only the fields sent are touched, via exclude_none. That
    is also why clearing the instructor needs its own flag: a plain
    instructor_id=null is indistinguishable from "wasn't sent" once
    exclude_none drops it, so it silently never reached the database. Pass
    ?clear_instructor=true instead of instructor_id to blank it back to none.
    """
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "no such session")
        fields = body.model_dump(exclude_none=True)
        if clear_instructor:
            fields["instructor_id"] = None
        if not fields:
            return {"ok": True}
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), sid))
        conn.commit()
        return {"ok": True, "changed": list(fields)}
    finally:
        conn.close()


@router.post("/api/sessions/{sid}/cancel")
def cancel_the_session(sid: int):
    conn = db.connect()
    try:
        return access.cancel_session(conn, sid)
    finally:
        conn.close()


@router.put("/api/sessions/{sid}/status/{status}")
def set_session_status(sid: int, status: str):
    if status not in ("scheduled", "completed", "cancelled"):
        raise HTTPException(400, "bad status")
    conn = db.connect()
    try:
        conn.execute("UPDATE sessions SET status=? WHERE id=?", (status, sid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/api/sessions/{sid}")
def delete_session(sid: int, force: bool = False):
    conn = db.connect()
    try:
        used = conn.execute(
            "SELECT COUNT(*) n FROM bookings WHERE session_id=? AND status!='booked'",
            (sid,)).fetchone()["n"]
        if used and not force:
            raise HTTPException(400, f"{used} attendance record(s) — cancel it instead")
        n = conn.execute("SELECT COUNT(*) n FROM bookings WHERE session_id=?",
                         (sid,)).fetchone()["n"]
        # Deleting bookings this way bypasses access.unbook(), so the plans
        # they funded need the same expiry refresh by hand.
        subs = {r["subscription_id"] for r in conn.execute(
            "SELECT DISTINCT subscription_id FROM bookings"
            " WHERE session_id=? AND subscription_id IS NOT NULL", (sid,)).fetchall()}
        conn.execute("DELETE FROM bookings WHERE session_id=?", (sid,))
        conn.execute("UPDATE access_events SET session_id=NULL WHERE session_id=?", (sid,))
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        for sub_id in subs:
            access.refresh_expiry(conn, sub_id)
        conn.commit()
        return {"ok": True, "released": n}
    finally:
        conn.close()


@router.post("/api/sessions/{sid}/book")
def book_into_session(sid: int, body: BookIn):
    conn = db.connect()
    try:
        r = access.book(conn, body.client_id, sid)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@router.delete("/api/sessions/{sid}/book/{cid}")
def unbook_from_session(sid: int, cid: int):
    conn = db.connect()
    try:
        r = access.unbook(conn, cid, sid)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@router.post("/api/sessions/{sid}/status-of")
def set_attend_status(sid: int, body: StatusIn):
    conn = db.connect()
    try:
        r = access.set_status(conn, sid, body.client_id, body.status)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@router.post("/api/clients/{cid}/move-booking/{from_sid}")
def move_booking(cid: int, from_sid: int, body: MoveIn):
    conn = db.connect()
    try:
        r = access.move_booking(conn, cid, from_sid, body.to_session_id)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()
