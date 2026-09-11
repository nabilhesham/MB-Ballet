"""/api/classes/* — the offering: class definitions and their rosters."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import access
import db
import repo as data

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
def list_classes(status: str = "active"):
    """
    `status="archived"` is the whole other half of this list, not an overlay
    on top of the active one -- same convention as /api/instructors' status
    param.
    """
    repo = data.connect()
    try:
        active = 0 if status == "archived" else 1
        return rows(repo.raw(
            "SELECT c.*, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM sessions s WHERE s.class_id=c.id"
            "     AND s.starts_at > ? AND s.status='scheduled') AS upcoming,"
            "  (SELECT COUNT(DISTINCT b.client_id) FROM bookings b"
            "     JOIN sessions s ON s.id=b.session_id WHERE s.class_id=c.id) AS students"
            " FROM classes c LEFT JOIN instructors i ON i.id = c.instructor_id"
            " WHERE c.active=? ORDER BY c.name", (db.now(), active)))
    finally:
        repo.close()


@router.post("/api/classes")
def create_class(body: ClassIn):
    repo = data.connect()
    try:
        cur = repo.raw(
            "INSERT INTO classes (name, description, colour, duration_hours, level,"
            " instructor_id) VALUES (?,?,?,?,?,?)",
            (body.name, body.description, body.colour, body.duration_hours, body.level,
             body.instructor_id))
        return {"id": cur.lastrowid}
    finally:
        repo.close()


@router.put("/api/classes/{clid}")
def update_class(clid: int, body: ClassIn):
    repo = data.connect()
    try:
        # The class and its cascade are one change: a run that set the
        # default and then failed to apply it would leave the sessions
        # disagreeing with the class they belong to.
        with repo.begin():
            repo.raw(
                "UPDATE classes SET name=?, description=?, colour=?,"
                " duration_hours=?, level=?, instructor_id=? WHERE id=?",
                (body.name, body.description, body.colour,
                 body.duration_hours, body.level, body.instructor_id, clid))
            # The class's instructor is a default that cascades: every session
            # that hasn't happened yet is overwritten to match, whatever
            # instructor it had before — not just the ones with none. Past and
            # cancelled sessions are untouched; the "upcoming" predicate here is
            # the same one list_classes' own `upcoming` count uses.
            cascaded = repo.raw(
                "UPDATE sessions SET instructor_id=? WHERE class_id=?"
                " AND status='scheduled' AND starts_at > ?",
                (body.instructor_id, clid, db.now())).rowcount
        return {"ok": True, "cascaded_sessions": cascaded}
    finally:
        repo.close()


@router.get("/api/classes/{clid}")
def get_class(clid: int):
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        c = one(repo.raw("SELECT * FROM classes WHERE id=?", (clid,)))
        if not c:
            raise HTTPException(404, "no such class")
        c["sessions"] = rows(repo.raw(
            "SELECT s.*, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s LEFT JOIN instructors i ON i.id = s.instructor_id"
            " WHERE s.class_id=? ORDER BY s.starts_at DESC LIMIT 80", (clid,)))
        c["students"] = rows(repo.raw(
            "SELECT cl.id, cl.name_en, cl.phone, cl.photo_path,"
            "       COUNT(b.id) AS slots,"
            "       SUM(CASE WHEN b.status='present' THEN 1 ELSE 0 END) AS attended"
            "  FROM bookings b JOIN sessions s ON s.id=b.session_id"
            "  JOIN clients cl ON cl.id=b.client_id"
            " WHERE s.class_id=? AND cl.active=1"
            " GROUP BY cl.id ORDER BY cl.name_en", (clid,)))
        return c
    finally:
        repo.close()


@router.delete("/api/classes/{clid}")
def delete_class(clid: int, hard: bool = False):
    """Archive, or remove entirely. The rules live in access.delete_class()."""
    repo = data.connect()
    try:
        r = access.delete_class(repo, clid, hard=hard)
        if not r["ok"]:
            raise HTTPException(r.get("status", 400), r["error"])
        return r
    finally:
        repo.close()

@router.post("/api/classes/{clid}/unarchive")
def unarchive_class(clid: int):
    repo = data.connect()
    try:
        if not repo.raw("SELECT 1 FROM classes WHERE id=?", (clid,)).fetchone():
            raise HTTPException(404, "no such class")
        repo.raw("UPDATE classes SET active=1 WHERE id=?", (clid,))
        return {"ok": True}
    finally:
        repo.close()
