"""/api/plans/* — freezing, unfreezing and removing a subscription."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import access
import db
import repo as data


router = APIRouter()


# ---------------------------------------------------------------- models
class FreezeIn(BaseModel):
    until: Optional[str] = None          # None = frozen until lifted by hand
    reason: Optional[str] = None


class PlanEdit(BaseModel):
    plan: Optional[str] = None
    class_id: Optional[int] = None
    sessions_total: Optional[int] = None
    expires_on: Optional[str] = None
    session_ids: Optional[list[int]] = None
    paid_on: Optional[str] = None
    notes: Optional[str] = None
    # When the plan began. Not bookkeeping alone -- it is one of the two
    # answers a plan's dates are derived from, so every form that offers it
    # re-picks the sessions when it changes. See access.sessions_from_start().
    starts_on: Optional[str] = None
    price: Optional[float] = None


class AutoSessionsIn(BaseModel):
    """Which sessions a start day and a session count come to."""
    class_id: int
    client_id: int
    starts_on: str
    sessions_total: int
    # The plan being edited, when there is one. It is what makes its own
    # already-booked dates candidates again instead of obstacles, and what
    # keeps its attended ones in the answer.
    plan_id: Optional[int] = None


# ---------------------------------------------------------------- routes
@router.post("/api/plans/auto-sessions")
def auto_sessions(body: AutoSessionsIn):
    """
    The dates a plan gets when reception states when it starts and how many
    sessions it buys — the answer, not a suggestion to be re-derived.

    Three forms ask it: the plan picker, the plan editor, and the kiosk's
    update panel, which has no session list at all. The rule is one function
    in access.py for the usual reason — three copies of "the first four
    sessions from 1 October" would agree on the obvious case and part company
    on attendance, on the plan's own dates, and on a day already gone. See
    access.sessions_from_start().
    """
    if body.sessions_total < 1:
        raise HTTPException(400, "A plan needs at least one session.")
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        return access.sessions_from_start(
            repo, body.class_id, body.client_id, body.starts_on,
            body.sessions_total, plan_id=body.plan_id)
    finally:
        repo.close()


@router.put("/api/plans/{pid}")
def edit_plan(pid: int, body: PlanEdit, clear_paid_on: bool = False):
    """
    Partial update — only the fields sent are touched, via exclude_none. That
    is also why marking a plan unpaid again needs its own flag: a plain
    paid_on=null is indistinguishable from "wasn't sent" once exclude_none
    drops it, so it would silently never reach the database. Pass
    ?clear_paid_on=true instead of paid_on to blank it back to unpaid — the
    same shape edit_session() uses for clear_instructor.
    """
    repo = data.connect()
    try:
        r = access.edit_plan(repo, pid, clear_paid_on=clear_paid_on,
                             **body.model_dump(exclude_none=True))
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        repo.close()


@router.post("/api/plans/{pid}/freeze")
def freeze_plan(pid: int, body: FreezeIn):
    repo = data.connect()
    try:
        r = access.freeze_plan(repo, pid, until=body.until, reason=body.reason)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        repo.close()


@router.post("/api/plans/{pid}/unfreeze")
def unfreeze_plan(pid: int):
    repo = data.connect()
    try:
        r = access.unfreeze_plan(repo, pid)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        repo.close()


@router.get("/api/plans/{pid}/freezes")
def plan_freezes(pid: int):
    repo = data.connect()
    try:
        return repo.find("freezes", {"subscription_id": pid},
                         sort=[("created_at", -1)])
    finally:
        repo.close()


@router.delete("/api/plans/{pid}")
def delete_plan(pid: int):
    """
    Remove a plan for good, along with every booking it paid for — so the
    client drops off the sessions it had them down for.

    This is the one deletion in the app that can take attendance with it,
    because a plan's bookings *are* its attendance; there is no way to keep
    the record and remove the plan. The counts come back in the response so
    the screen can say plainly what went, and the confirm dialog says it
    before rather than after. Archiving is the alternative and is what the
    Freeze/renew flow is for.

    No refresh_expiry() here, unlike the other bulk booking deletes: the plan
    whose expiry would be recomputed is itself gone.
    """
    repo = data.connect()
    try:
        r = access.delete_plan(repo, pid)
        if not r["ok"]:
            raise HTTPException(r.get("status", 400), r["error"])
        return r
    finally:
        repo.close()
