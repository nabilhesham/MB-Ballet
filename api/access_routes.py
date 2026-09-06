"""
/api/access/* — the reception kiosk's scan → verify → check-in flow.

Named access_routes.py, not access.py, so it never collides with the actual
access.py at the repo root that holds the business rules this thin layer
calls into.
"""

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import access
import db

router = APIRouter()


# ---------------------------------------------------------------- models
class ScanIn(BaseModel):
    token: str


class LookupIn(BaseModel):
    client_id: int


class EventIn(BaseModel):
    event_id: int


# ---------------------------------------------------------------- routes
@router.post("/api/access/verify")
def verify(body: ScanIn):
    conn = db.connect()
    try:
        return access.verify(conn, body.token)
    finally:
        conn.close()


@router.post("/api/access/lookup")
def lookup(body: LookupIn):
    conn = db.connect()
    try:
        return access.verify_by_client(conn, body.client_id)
    finally:
        conn.close()


@router.post("/api/access/checkin")
def checkin(body: EventIn):
    conn = db.connect()
    try:
        r = access.check_in(conn, body.event_id)
        return JSONResponse(r, status_code=200 if r["ok"] else 409)
    finally:
        conn.close()


class SwapIn(BaseModel):
    client_id: int
    from_session_id: int
    to_session_id: int
    credential_id: Optional[int] = None


@router.get("/api/access/swap-options/{client_id}")
def swap_options(client_id: int):
    """Today's sessions, and the client's own slots they could give up."""
    conn = db.connect()
    try:
        return access.swap_options(conn, client_id)
    finally:
        conn.close()


@router.post("/api/access/swap-checkin")
def swap_checkin(body: SwapIn):
    """Move one of their slots onto a session today, then check them in."""
    conn = db.connect()
    try:
        r = access.swap_and_check_in(conn, body.client_id, body.from_session_id,
                                     body.to_session_id, body.credential_id)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@router.post("/api/access/undo")
def undo(body: EventIn):
    conn = db.connect()
    try:
        return access.undo(conn, body.event_id)
    finally:
        conn.close()
