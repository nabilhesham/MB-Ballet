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
    # Naming the plan is what lets a session of another class be booked: with
    # no plan in that session's class there is nothing for book() to resolve
    # on its own. Only the client profile's corrections send these two.
    subscription_id: Optional[int] = None
    allow_other_class: bool = False


class StatusIn(BaseModel):
    client_id: int
    status: str


class BulkDeleteIn(BaseModel):
    ids: list[int]
    force: bool = False


class MoveIn(BaseModel):
    to_session_id: int
    # Corrections made on the client profile may land on another class's
    # session; the booking keeps the plan that paid for it either way.
    allow_other_class: bool = False
    status: Optional[str] = None


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
        hours = body.duration_hours or cl["duration_hours"]
        # The check and the insert under one lock. Deferred, the slot could
        # be taken between deciding it was free and writing into it.
        with db.tx(conn):
            clash = access.slot_conflict(conn, body.starts_at, hours)
            if clash:
                raise HTTPException(400, access.slot_taken_message(clash))
            cur = conn.execute(
                "INSERT INTO sessions (class_id, instructor_id, starts_at, duration_hours,"
                " ends_at, notes) VALUES (?,?,?,?,?,?)",
                (body.class_id, instructor_id, body.starts_at, hours,
                 access.ends_at_of(body.starts_at, hours), body.notes))
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
        hours = body.duration_hours or cl["duration_hours"]
        # A term is generated as one change. Each date is still
        # skipped individually on a clash, but a failure partway
        # through must not leave half a term behind.
        made = 0
        skipped = []
        with db.tx(conn):
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
                    # A whole term is generated at once, so one taken evening in
                    # week 7 must not cost the other eleven. Skip it and say
                    # which, the same way a date already holding this class's own
                    # session is skipped just above.
                    clash = access.slot_conflict(conn, ts, hours)
                    if clash:
                        skipped.append(access.slot_taken_message(clash))
                        continue
                    conn.execute(
                        "INSERT INTO sessions (class_id, instructor_id, starts_at,"
                        " duration_hours, ends_at) VALUES (?,?,?,?,?)",
                        (body.class_id, instructor_id, ts, hours,
                         access.ends_at_of(ts, hours)))
                    made += 1
        return {"created": made, "skipped": skipped}
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
        # Moving a session or stretching it can walk into another one, so the
        # slot is re-checked against the values this edit is about to write —
        # ignoring the session itself, which of course overlaps where it is.
        with db.tx(conn):
            if "starts_at" in fields or "duration_hours" in fields:
                cur = one(conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)))
                clash = access.slot_conflict(
                    conn,
                    fields.get("starts_at", cur["starts_at"]),
                    fields.get("duration_hours", cur["duration_hours"]),
                    exclude_id=sid)
                if clash:
                    raise HTTPException(400, access.slot_taken_message(clash))
                # Moving or stretching a session moves its end. ends_at is
                # stored, not derived, so it has to travel with them.
                fields["ends_at"] = access.ends_at_of(
                    fields.get("starts_at", cur["starts_at"]),
                    fields.get("duration_hours", cur["duration_hours"]))
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), sid))
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
        # Cancelling frees the slot, so bringing a session back has to find it
        # still free — otherwise cancel, schedule something else, un-cancel
        # would put two classes in one slot by the back door.
        with db.tx(conn):
            if status != "cancelled":
                row = one(conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)))
                if not row:
                    raise HTTPException(404, "no such session")
                clash = access.slot_conflict(conn, row["starts_at"],
                                             row["duration_hours"], exclude_id=sid)
                if clash:
                    raise HTTPException(400, access.slot_taken_message(clash))
            conn.execute("UPDATE sessions SET status=? WHERE id=?", (status, sid))
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/api/sessions/{sid}")
def delete_session(sid: int, force: bool = False):
    """Delete one session. Deleting one is deleting a list of one."""
    conn = db.connect()
    try:
        r = access.delete_sessions(conn, [sid], force=force)
        if r["blocked"]:
            held = r["blocked"][0]["attendance"]
            raise HTTPException(400, f"{held} attendance record(s) — cancel it instead")
        return {"ok": True, "released": r["released"]}
    finally:
        conn.close()


@router.post("/api/sessions/bulk-delete")
def bulk_delete_sessions(body: BulkDeleteIn):
    """
    Delete several sessions at once, applying the same rule delete_session
    applies to one: a session carrying attendance is kept back unless force
    is set, because losing the record of who turned up is worse than a
    cluttered timetable.

    Kept-back sessions are named in the response rather than failing the
    batch — clearing a term with one taught week in the middle of it should
    remove the other eleven and say why the twelfth stayed.
    """
    conn = db.connect()
    try:
        return access.delete_sessions(conn, body.ids, force=body.force)
    finally:
        conn.close()


@router.post("/api/sessions/{sid}/book")
def book_into_session(sid: int, body: BookIn):
    conn = db.connect()
    try:
        r = access.book(conn, body.client_id, sid,
                        subscription_id=body.subscription_id,
                        allow_other_class=body.allow_other_class)
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
        r = access.move_booking(conn, cid, from_sid, body.to_session_id,
                                allow_other_class=body.allow_other_class,
                                status=body.status)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()
