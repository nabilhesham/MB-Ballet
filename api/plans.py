"""/api/plans/* — freezing, unfreezing and removing a subscription."""

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import access
import db

from .helpers import rows

router = APIRouter()


# ---------------------------------------------------------------- models
class FreezeIn(BaseModel):
    until: Optional[str] = None          # None = frozen until lifted by hand
    reason: Optional[str] = None


class PlanEdit(BaseModel):
    plan: Optional[str] = None
    sessions_total: Optional[int] = None
    expires_on: Optional[str] = None
    session_ids: Optional[list[int]] = None
    paid_on: Optional[str] = None


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
    conn = db.connect()
    try:
        conn.execute("DELETE FROM bookings WHERE subscription_id=?", (pid,))
        conn.execute("DELETE FROM subscriptions WHERE id=?", (pid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
