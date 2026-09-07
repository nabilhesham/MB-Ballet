"""/api/plans/* — freezing, unfreezing and removing a subscription."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import access
import db

from .helpers import rows, one

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


# ---------------------------------------------------------------- routes
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
    conn = db.connect()
    try:
        r = access.edit_plan(conn, pid, clear_paid_on=clear_paid_on,
                             **body.model_dump(exclude_none=True))
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@router.post("/api/plans/{pid}/freeze")
def freeze_plan(pid: int, body: FreezeIn):
    conn = db.connect()
    try:
        r = access.freeze_plan(conn, pid, until=body.until, reason=body.reason)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@router.post("/api/plans/{pid}/unfreeze")
def unfreeze_plan(pid: int):
    conn = db.connect()
    try:
        r = access.unfreeze_plan(conn, pid)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@router.get("/api/plans/{pid}/freezes")
def plan_freezes(pid: int):
    conn = db.connect()
    try:
        return rows(conn.execute(
            "SELECT * FROM freezes WHERE subscription_id=? ORDER BY created_at DESC", (pid,)))
    finally:
        conn.close()


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
    conn = db.connect()
    try:
        sub = one(conn.execute("SELECT * FROM subscriptions WHERE id=?", (pid,)))
        if not sub:
            raise HTTPException(404, "no such plan")
        counts = conn.execute(
            "SELECT COUNT(*) n,"
            "       SUM(CASE WHEN status='booked' THEN 1 ELSE 0 END) upcoming,"
            "       SUM(CASE WHEN status!='booked' THEN 1 ELSE 0 END) attended"
            "  FROM bookings WHERE subscription_id=?", (pid,)).fetchone()
        conn.execute("DELETE FROM bookings WHERE subscription_id=?", (pid,))
        conn.execute("DELETE FROM subscriptions WHERE id=?", (pid,))
        # The card for this class proves a plan that no longer exists. Revoke
        # it unless another plan in the same class is still live — credentials
        # are revoked rather than deleted, so the log keeps pointing at it.
        revoked = 0
        if sub["class_id"]:
            still = conn.execute(
                "SELECT 1 FROM subscriptions WHERE client_id=? AND class_id=? AND active=1",
                (sub["client_id"], sub["class_id"])).fetchone()
            if not still:
                revoked = conn.execute(
                    "UPDATE credentials SET revoked_at=? WHERE client_id=? AND class_id=?"
                    "   AND revoked_at IS NULL",
                    (db.now(), sub["client_id"], sub["class_id"])).rowcount
        conn.commit()
        return {"ok": True, "bookings": counts["n"] or 0,
                "upcoming": counts["upcoming"] or 0,
                "attended": counts["attended"] or 0, "cards_revoked": revoked}
    finally:
        conn.close()
