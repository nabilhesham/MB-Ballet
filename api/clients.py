"""/api/clients/* — client profiles, their plans and cards."""

import glob
import os
import shutil
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

import access
import cards
import db

from .helpers import rows, one

router = APIRouter()


# ---------------------------------------------------------------- models
class ClientIn(BaseModel):
    name_en: str
    phone: Optional[str] = None
    # Float, not int: the roster sheets carry "4.8" and reception needs to
    # type 3.5 for the youngest children. An int field here does not round a
    # decimal, it rejects the whole request — which is what it did until now.
    age: Optional[float] = None
    school: Optional[str] = None
    joined_on: Optional[str] = None
    notes: Optional[str] = None


class PlanIn(BaseModel):
    class_id: int
    plan: str
    sessions_total: int
    price: Optional[float] = None
    starts_on: Optional[str] = None
    # Blank means "through the last session chosen" — the rule, rather than
    # a date someone typed. A value here is a deliberate override.
    expires_on: Optional[str] = None
    # The day the money arrived. Blank is a real answer — the plan is unpaid,
    # and shows as such until someone edits a date in.
    paid_on: Optional[str] = None
    # About this purchase, not about the person — clients.notes covers that.
    notes: Optional[str] = None
    session_ids: list[int] = []


class CardIn(BaseModel):
    class_id: Optional[int] = None


# ---------------------------------------------------------------- routes
@router.get("/api/clients")
def list_clients(q: str = "", status: str = "all"):
    """
    `status` does two unrelated jobs. "archived" picks which half of the list
    to read — the same convention /api/instructors and /api/classes use.
    "attention" is a filter *within* the active half, applied further down
    once each row has been enriched with the plan state it needs; it is not a
    third value of the same switch.
    """
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        like = f"%{q}%"
        active = 0 if status == "archived" else 1
        data = rows(conn.execute(
            "SELECT c.* FROM clients c"
            " WHERE c.active = ? AND (? = '' OR c.name_en LIKE ? OR c.phone LIKE ?"
            "   OR c.school LIKE ?)"
            " ORDER BY c.name_en", (active, q, like, like, like)))
        today = date.today().isoformat()
        for d in data:
            sub = access.active_plan(conn, d["id"])
            state = access.plan_state(conn, sub["id"]) if sub else {}
            d.update({
                "plan": state.get("plan"),
                "sessions_total": state.get("sessions_total"),
                "remaining": state.get("remaining"),
                "unassigned": state.get("unassigned"),
                "expires_on": state.get("expires_on"),
                "frozen": state.get("frozen", False),
                "frozen_until": state.get("frozen_until"),
            })
            d["expired"] = bool(d["expires_on"] and d["expires_on"] < today
                                and not d["frozen"])
            d["low"] = d["remaining"] is not None and 0 < d["remaining"] <= 2
            d["empty"] = d["remaining"] is not None and d["remaining"] <= 0
            d["cards"] = conn.execute(
                "SELECT COUNT(*) n FROM credentials WHERE client_id=? AND revoked_at IS NULL",
                (d["id"],)).fetchone()["n"]
        if status == "attention":
            data = [d for d in data if not d["frozen"] and (
                    d["expired"] or d["low"] or d["empty"] or not d["cards"]
                    or (d["unassigned"] or 0) > 0)]
        return data
    finally:
        conn.close()


@router.post("/api/clients")
def create_client(body: ClientIn):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO clients (name_en, phone, age, school, joined_on, notes, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (body.name_en, body.phone, body.age, body.school,
             body.joined_on or date.today().isoformat(), body.notes, db.now()))
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@router.get("/api/clients/{cid}")
def get_client(cid: int):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        c = one(conn.execute("SELECT * FROM clients WHERE id=?", (cid,)))
        if not c:
            raise HTTPException(404, "no such client")

        # Payment history: every plan bought, newest first.
        c["plans"] = [access.plan_state(conn, r["id"]) for r in conn.execute(
            "SELECT id FROM subscriptions WHERE client_id=? ORDER BY created_at DESC",
            (cid,)).fetchall()]
        # One live plan per class. The profile is organised around these: each
        # gets its own card, its own sessions and its own freeze state.
        c["active_plans"] = [p for p in c["plans"] if p["active"]]
        c["active_plan"] = c["active_plans"][0] if c["active_plans"] else None
        c["classes_enrolled"] = [
            {"class_id": p["class_id"], "class_name": p["class_name"],
             "colour": p["class_colour"], "plan_id": p["id"],
             # How many of this plan's paid slots have no session yet — the
             # only number that says whether a new one can be added here.
             # Frozen carries separately: a freeze is what creates unassigned
             # slots in the first place, and they stay off-limits to booking
             # until the plan is active again — the same reason the existing
             # "assign remaining sessions" link hides itself while frozen.
             "unassigned": p["unassigned"], "frozen": p["frozen"]}
            for p in c["active_plans"] if p["class_id"]]

        c["cards"] = rows(conn.execute(
            "SELECT cr.id, cr.token, cr.class_id, cr.issued_at,"
            "       cl.name AS class_name, cl.colour"
            "  FROM credentials cr LEFT JOIN classes cl ON cl.id = cr.class_id"
            " WHERE cr.client_id=? AND cr.revoked_at IS NULL"
            " ORDER BY cl.name", (cid,)))
        # The PNG the card was written to, so the profile can offer it for
        # download and print without guessing at the filename in the browser.
        #
        # ?v=issued_at is not decoration. Reissuing overwrites the same path,
        # so the browser kept serving the card it had already cached and an
        # edited end date never appeared on it — the file was right and the
        # picture was old. The stamp changes on every issue, which is exactly
        # when the image changes.
        for cd in c["cards"]:
            cd["card_url"] = f"/{cards.card_path(cid, cd['class_name'])}?v={cd['issued_at']}"

        now = db.now()
        c["upcoming"] = rows(conn.execute(
            "SELECT b.id AS booking_id, b.status, s.id AS session_id, s.starts_at,"
            "       s.duration_hours, s.class_id, cl.name AS class_name, cl.colour,"
            "       i.name AS instructor_name"
            "  FROM bookings b JOIN sessions s ON s.id = b.session_id"
            "  JOIN classes cl ON cl.id = s.class_id"
            "  LEFT JOIN instructors i ON i.id = s.instructor_id"
            " WHERE b.client_id=? AND s.starts_at >= ? AND s.status != 'cancelled'"
            " ORDER BY s.starts_at", (cid, now)))

        c["history"] = rows(conn.execute(
            "SELECT b.id AS booking_id, b.status, b.checked_in_at, b.subscription_id,"
            "       s.id AS session_id, s.starts_at, cl.name AS class_name, cl.colour,"
            "       i.name AS instructor_name"
            "  FROM bookings b JOIN sessions s ON s.id = b.session_id"
            "  JOIN classes cl ON cl.id = s.class_id"
            "  LEFT JOIN instructors i ON i.id = s.instructor_id"
            " WHERE b.client_id=? AND s.starts_at < ?"
            " ORDER BY s.starts_at DESC LIMIT 100", (cid, now)))
        return c
    finally:
        conn.close()


@router.get("/api/clients/{cid}/plan/{pid}/sessions")
def plan_sessions(cid: int, pid: int):
    """Every session paid for by one plan — the popup on a payment row."""
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        return rows(conn.execute(
            "SELECT b.status, b.checked_in_at, s.id AS session_id, s.starts_at,"
            "       s.duration_hours, cl.name AS class_name, cl.colour,"
            "       i.name AS instructor_name"
            "  FROM bookings b JOIN sessions s ON s.id = b.session_id"
            "  JOIN classes cl ON cl.id = s.class_id"
            "  LEFT JOIN instructors i ON i.id = s.instructor_id"
            " WHERE b.client_id=? AND b.subscription_id=?"
            " ORDER BY s.starts_at", (cid, pid)))
    finally:
        conn.close()


@router.put("/api/clients/{cid}")
def update_client(cid: int, body: ClientIn):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE clients SET name_en=?, phone=?, age=?, school=?, joined_on=?, notes=?"
            " WHERE id=?",
            (body.name_en, body.phone, body.age, body.school, body.joined_on,
             body.notes, cid))
        return {"ok": True}
    finally:
        conn.close()


@router.post("/api/clients/{cid}/photo")
async def upload_photo(cid: int, file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "use jpg, png or webp")
    # A fresh filename per upload. Writing back to the same path meant the
    # browser kept serving the cached old picture after a re-upload, so a new
    # photo looked like it had not saved at all — reloading the profile did
    # not help, because the URL had not changed. The previous files are
    # removed so the folder does not fill up with every photo ever taken.
    path = f"photos/client_{cid:05d}_{db.now()}{ext}"
    for old in glob.glob(f"photos/client_{cid:05d}*"):
        try:
            os.remove(old)
        except OSError:
            pass
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    conn = db.connect()
    try:
        conn.execute("UPDATE clients SET photo_path=? WHERE id=?", ("/" + path, cid))
        return {"photo_path": "/" + path}
    finally:
        conn.close()


@router.post("/api/clients/{cid}/plan")
def add_plan(cid: int, body: PlanIn):
    """Sell a plan for one class. The rules live in access.add_plan()."""
    conn = db.connect()
    try:
        r = access.add_plan(
            conn, cid, body.class_id, body.plan, body.sessions_total,
            body.session_ids, price=body.price, starts_on=body.starts_on,
            expires_on=body.expires_on, paid_on=body.paid_on, notes=body.notes)
        if not r["ok"]:
            raise HTTPException(r.get("status", 400), r["error"])
        return r
    finally:
        conn.close()


@router.post("/api/clients/{cid}/card")
def issue_card(cid: int, body: CardIn):
    """
    One card per class. Reissuing replaces only that class's card, so a client
    taking Ballet and Flexibility keeps the other one working.
    """
    conn = db.connect()
    try:
        r = access.issue_card(conn, cid, body.class_id)
        if not r["ok"]:
            raise HTTPException(r.get("status", 400), r["error"])
        # Drawing the PNG is file I/O and presentation, so it stays here
        # rather than in access.py.
        path = cards.build_card(cid, r["client_name"], r["token"],
                                r["sessions_total"], r["expires_on"],
                                class_name=r["class_name"],
                                colour=r["class_colour"])
        # Stamped with the issue time: card_path() gives one stable filename
        # per client per class, so a reissue overwrites a URL the browser has
        # already cached and the old picture keeps being shown.
        return {"token": r["token"], "card_url": f"/{path}?v={db.now()}",
                "revoked": r["revoked"]}
    finally:
        conn.close()


@router.delete("/api/clients/{cid}")
def delete_client(cid: int, hard: bool = False):
    """Archive, or remove entirely. The rules live in access.delete_client()."""
    conn = db.connect()
    try:
        r = access.delete_client(conn, cid, hard=hard)
        if not r["ok"]:
            raise HTTPException(r.get("status", 400), r["error"])
        return r
    finally:
        conn.close()


@router.post("/api/clients/{cid}/unarchive")
def unarchive_client(cid: int):
    """
    Brings the client and their history back. Their cards stay revoked —
    credentials are revoked rather than deleted, so a restored client needs
    one reissued.
    """
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM clients WHERE id=?", (cid,)).fetchone():
            raise HTTPException(404, "no such client")
        conn.execute("UPDATE clients SET active=1 WHERE id=?", (cid,))
        return {"ok": True}
    finally:
        conn.close()
