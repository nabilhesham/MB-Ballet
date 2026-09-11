"""/api/classes/* — the offering: class definitions and their rosters."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import access
import db
import repo as data


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
        return repo.classes_with_counts(active)
    finally:
        repo.close()


@router.post("/api/classes")
def create_class(body: ClassIn):
    repo = data.connect()
    try:
        return {"id": repo.insert("classes", {
            "name": body.name, "description": body.description,
            "colour": body.colour, "duration_hours": body.duration_hours,
            "level": body.level, "instructor_id": body.instructor_id})}
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
            repo.update("classes", clid, {
                "name": body.name, "description": body.description,
                "colour": body.colour, "duration_hours": body.duration_hours,
                "level": body.level, "instructor_id": body.instructor_id})
            # The class's instructor is a default that cascades: every session
            # that hasn't happened yet is overwritten to match, whatever
            # instructor it had before — not just the ones with none. Past and
            # cancelled sessions are untouched; the "upcoming" predicate here is
            # the same one list_classes' own `upcoming` count uses.
            cascaded = repo.update_where(
                "sessions",
                {"class_id": clid, "status": "scheduled",
                 "starts_at": {"gt": db.now()}},
                {"instructor_id": body.instructor_id})
        return {"ok": True, "cascaded_sessions": cascaded}
    finally:
        repo.close()


@router.get("/api/classes/{clid}")
def get_class(clid: int):
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        c = repo.get("classes", clid)
        if not c:
            raise HTTPException(404, "no such class")
        c["sessions"] = repo.class_sessions(clid, 80)
        c["students"] = repo.class_students(clid)
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
        if not repo.update("classes", clid, {"active": 1}):
            raise HTTPException(404, "no such class")
        return {"ok": True}
    finally:
        repo.close()
