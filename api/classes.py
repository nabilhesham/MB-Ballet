"""/api/classes/* — the offering: class definitions and their rosters."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import access
import db

from .helpers import rows, one

router = APIRouter()


# ---------------------------------------------------------------- models
class ClassIn(BaseModel):
    name: str
    description: Optional[str] = None
    colour: str = "#87438E"
    duration_hours: float = 1.5
    level: Optional[str] = None
    # A default, not a lock: what a new session falls back to when no
    # instructor is chosen, and what every upcoming session's instructor is
    # overwritten to when this changes (see update_class below).
    instructor_id: Optional[int] = None


# ---------------------------------------------------------------- routes
@router.get("/api/classes")
def list_classes():
    conn = db.connect()
    try:
        return rows(conn.execute(
            "SELECT c.*,"
            "  (SELECT COUNT(*) FROM sessions s WHERE s.class_id=c.id"
            "     AND s.starts_at > ? AND s.status='scheduled') AS upcoming,"
            "  (SELECT COUNT(DISTINCT b.client_id) FROM bookings b"
            "     JOIN sessions s ON s.id=b.session_id WHERE s.class_id=c.id) AS students"
            " FROM classes c WHERE c.active=1 ORDER BY c.name", (db.now(),)))
    finally:
        conn.close()


@router.post("/api/classes")
def create_class(body: ClassIn):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO classes (name, description, colour, duration_hours, level,"
            " instructor_id) VALUES (?,?,?,?,?,?)",
            (body.name, body.description, body.colour, body.duration_hours, body.level,
             body.instructor_id))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.put("/api/classes/{clid}")
def update_class(clid: int, body: ClassIn):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE classes SET name=?, description=?, colour=?,"
            " duration_hours=?, level=?, instructor_id=? WHERE id=?",
            (body.name, body.description, body.colour,
             body.duration_hours, body.level, body.instructor_id, clid))
        # The class's instructor is a default that cascades: every session
        # that hasn't happened yet is overwritten to match, whatever
        # instructor it had before — not just the ones with none. Past and
        # cancelled sessions are untouched; the "upcoming" predicate here is
        # the same one list_classes' own `upcoming` count uses.
        cascaded = conn.execute(
            "UPDATE sessions SET instructor_id=? WHERE class_id=?"
            " AND status='scheduled' AND starts_at > ?",
            (body.instructor_id, clid, db.now())).rowcount
        conn.commit()
        return {"ok": True, "cascaded_sessions": cascaded}
    finally:
        conn.close()


@router.get("/api/classes/{clid}")
def get_class(clid: int):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        c = one(conn.execute("SELECT * FROM classes WHERE id=?", (clid,)))
        if not c:
            raise HTTPException(404, "no such class")
        c["sessions"] = rows(conn.execute(
            "SELECT s.*, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s LEFT JOIN instructors i ON i.id = s.instructor_id"
            " WHERE s.class_id=? ORDER BY s.starts_at DESC LIMIT 80", (clid,)))
        c["students"] = rows(conn.execute(
            "SELECT cl.id, cl.name_en, cl.phone, cl.photo_path,"
            "       COUNT(b.id) AS slots,"
            "       SUM(CASE WHEN b.status='present' THEN 1 ELSE 0 END) AS attended"
            "  FROM bookings b JOIN sessions s ON s.id=b.session_id"
            "  JOIN clients cl ON cl.id=b.client_id"
            " WHERE s.class_id=? AND cl.active=1"
            " GROUP BY cl.id ORDER BY cl.name_en", (clid,)))
        return c
    finally:
        conn.close()


@router.delete("/api/classes/{clid}")
def delete_class(clid: int, hard: bool = False):
    conn = db.connect()
    try:
        c = one(conn.execute("SELECT * FROM classes WHERE id=?", (clid,)))
        if not c:
            raise HTTPException(404, "no such class")
        held = conn.execute(
            "SELECT COUNT(*) n FROM bookings b JOIN sessions s ON s.id=b.session_id"
            " WHERE s.class_id=? AND b.status!='booked'", (clid,)).fetchone()["n"]
        if hard and held:
            raise HTTPException(400, f"{c['name']} has {held} attendance records — archive instead")
        if hard:
            # Bypasses access.unbook(), so the plans these bookings funded
            # need their expiry refreshed by hand once the bookings are gone.
            subs = {r["subscription_id"] for r in conn.execute(
                "SELECT DISTINCT subscription_id FROM bookings"
                " WHERE session_id IN (SELECT id FROM sessions WHERE class_id=?)"
                "   AND subscription_id IS NOT NULL", (clid,)).fetchall()}
            conn.execute("DELETE FROM bookings WHERE session_id IN"
                         " (SELECT id FROM sessions WHERE class_id=?)", (clid,))
            conn.execute("DELETE FROM sessions WHERE class_id=?", (clid,))
            conn.execute("DELETE FROM classes WHERE id=?", (clid,))
            for sub_id in subs:
                access.refresh_expiry(conn, sub_id)
            action = "delete"
        else:
            # Archiving a class stops it from being offered again, so its
            # upcoming sessions have nothing left to happen for — release
            # them the same way the hard-delete branch above does (just
            # scoped to sessions that haven't happened yet), so the clients
            # booked into them get the slot back as unassigned on their plan
            # instead of it silently dangling on a class nobody can see.
            # Past sessions and their attendance are never touched.
            upcoming_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM sessions WHERE class_id=? AND status='scheduled'"
                " AND starts_at > ?", (clid, db.now())).fetchall()]
            released_sessions = len(upcoming_ids)
            released_bookings = 0
            if upcoming_ids:
                marks = ",".join("?" * len(upcoming_ids))
                subs = {r["subscription_id"] for r in conn.execute(
                    f"SELECT DISTINCT subscription_id FROM bookings"
                    f" WHERE session_id IN ({marks}) AND subscription_id IS NOT NULL",
                    upcoming_ids).fetchall()}
                released_bookings = conn.execute(
                    f"SELECT COUNT(*) n FROM bookings WHERE session_id IN ({marks})",
                    upcoming_ids).fetchone()["n"]
                conn.execute(f"DELETE FROM bookings WHERE session_id IN ({marks})", upcoming_ids)
                conn.execute(f"DELETE FROM sessions WHERE id IN ({marks})", upcoming_ids)
                for sub_id in subs:
                    access.refresh_expiry(conn, sub_id)
            conn.execute("UPDATE classes SET active=0 WHERE id=?", (clid,))
            action = "archive"
        conn.commit()
        return {"ok": True, "action": action,
                "released_sessions": released_sessions if action == "archive" else None,
                "released_bookings": released_bookings if action == "archive" else None}
    finally:
        conn.close()
