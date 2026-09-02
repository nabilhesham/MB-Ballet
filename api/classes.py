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
            "INSERT INTO classes (name, description, colour, duration_hours, level)"
            " VALUES (?,?,?,?,?)",
            (body.name, body.description, body.colour, body.duration_hours, body.level))
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
            " duration_hours=?, level=? WHERE id=?",
            (body.name, body.description, body.colour,
             body.duration_hours, body.level, clid))
        conn.commit()
        return {"ok": True}
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
            conn.execute("UPDATE classes SET active=0 WHERE id=?", (clid,))
            action = "archive"
        conn.commit()
        return {"ok": True, "action": action}
    finally:
        conn.close()
