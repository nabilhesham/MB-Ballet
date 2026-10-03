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
        return repo.classes_with_counts(active, access.lapsed_cutoff())
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
        # A month after their last plan here ran out, a client stops being
        # a student of this class. Nothing is deleted — see repo/ports.py.
        c["students"] = repo.class_students(clid, access.lapsed_cutoff())
        _with_plan_state(repo, clid, c["students"])
        return c
    finally:
        repo.close()


def _with_plan_state(repo, clid: int, students: list) -> None:
    """
    Put each student's balance for **this class** onto their row, in place.

    The roster said how many slots they have held and how many they attended
    — the history — and nothing about whether they can still come. A
    receptionist looking at who is in Ballet Level 8 wants to see the one
    whose plan ran out last week and the one down to a single session, and
    was having to open each profile to find out.

    **This class's plan, never "their plan".** `repo.active_plans_for()`
    answers with a client's soonest-to-expire plan across every class, which
    on this page would print a Flexibility balance on the Ballet roster —
    true about the client and not an answer to the question the screen is
    asking. One plan per class per client is the invariant that makes
    `active_plan()` answerable at all, so the class's own live plans are
    enough, and the soonest-to-expire wins if that invariant is ever broken.

    Two round trips for the whole page, not two per student: one `find` over
    this class's live plans and one `plan_states()` behind it. A student with
    no live plan here keeps no balance fields at all, which `<BalancePill>`
    reads as "no plan" — correct for somebody on the roster from bookings
    they attended under a plan that has since gone.
    """
    if not students:
        return
    subs = repo.find("subscriptions", {"class_id": clid, "active": 1},
                     sort=[("expires_on", 1)])
    by_client = {}
    for sub in subs:
        by_client.setdefault(sub["client_id"], sub)
    states = access.plan_states(repo, [s["id"] for s in by_client.values()])
    for row in students:
        sub = by_client.get(row["id"])
        st = states.get(sub["id"]) if sub else None
        if not st:
            continue
        row.update({
            "plan": st["plan"], "remaining": st["remaining"],
            "expires_on": st["expires_on"], "unassigned": st["unassigned"],
            "frozen": st["frozen"], "frozen_until": st["frozen_until"],
        })


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
