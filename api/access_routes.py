"""
/api/access/* — the reception kiosk's scan → verify → check-in flow.

Named access_routes.py, not access.py, so it never collides with the actual
access.py at the repo root that holds the business rules this thin layer
calls into.
"""

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


@router.post("/api/access/undo")
def undo(body: EventIn):
    conn = db.connect()
    try:
        return access.undo(conn, body.event_id)
    finally:
        conn.close()
