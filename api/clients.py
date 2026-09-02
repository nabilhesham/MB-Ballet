"""/api/clients/* — client profiles, their plans and cards."""

import os
import shutil
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

import access
import cards
import db
import tokens

from .helpers import rows, one

router = APIRouter()


# ---------------------------------------------------------------- models
class ClientIn(BaseModel):
    name_en: str
    phone: Optional[str] = None
    age: Optional[int] = None
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
    session_ids: list[int] = []


class CardIn(BaseModel):
    class_id: Optional[int] = None


# ---------------------------------------------------------------- routes
@router.get("/api/clients")
def list_clients(q: str = "", status: str = "all"):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        like = f"%{q}%"
        data = rows(conn.execute(
            "SELECT c.* FROM clients c"
            " WHERE c.active = 1 AND (? = '' OR c.name_en LIKE ? OR c.phone LIKE ?"
            "   OR c.school LIKE ?)"
            " ORDER BY c.name_en", (q, like, like, like)))
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
        conn.commit()
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
            "SELECT cr.id, cr.token, cr.class_id, cl.name AS class_name, cl.colour"
            "  FROM credentials cr LEFT JOIN classes cl ON cl.id = cr.class_id"
            " WHERE cr.client_id=? AND cr.revoked_at IS NULL"
            " ORDER BY cl.name", (cid,)))
        # The PNG the card was written to, so the profile can offer it for
        # download and print without guessing at the filename in the browser.
        for cd in c["cards"]:
            cd["card_url"] = "/" + cards.card_path(cid, cd["class_name"])

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
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/api/clients/{cid}/photo")
async def upload_photo(cid: int, file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "use jpg, png or webp")
    path = f"photos/client_{cid:05d}{ext}"
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    conn = db.connect()
    try:
        conn.execute("UPDATE clients SET photo_path=? WHERE id=?", ("/" + path, cid))
        conn.commit()
        return {"photo_path": "/" + path}
    finally:
        conn.close()


@router.post("/api/clients/{cid}/plan")
def add_plan(cid: int, body: PlanIn):
    """
    Sell a plan for one class.

    Four rules are enforced here rather than trusted to the UI:
      - every slot is assigned to a real session up front, because a plan with
        unassigned slots is a promise nobody has written down;
      - every one of those sessions belongs to the plan's class, so a Ballet
        plan cannot quietly pay for a Flexibility session;
      - only the previous plan *for this class* is replaced, so a client taking
        two classes keeps the other one running;
      - a plan runs through the last session it pays for, unless reception
        types an end date of its own.
    """
    if body.sessions_total < 1:
        raise HTTPException(400, "a plan needs at least one session")
    if len(body.session_ids) != body.sessions_total:
        raise HTTPException(
            400, f"assign all {body.sessions_total} sessions "
                 f"({len(body.session_ids)} chosen)")
    if len(set(body.session_ids)) != len(body.session_ids):
        raise HTTPException(400, "the same session was chosen twice")

    conn = db.connect()
    try:
        klass = one(conn.execute("SELECT * FROM classes WHERE id=?", (body.class_id,)))
        if not klass:
            raise HTTPException(404, "no such class")

        marks = ",".join("?" * len(body.session_ids))
        wrong = conn.execute(
            f"SELECT COUNT(*) n FROM sessions WHERE id IN ({marks}) AND class_id != ?",
            (*body.session_ids, body.class_id)).fetchone()["n"]
        if wrong:
            raise HTTPException(
                400, f"{wrong} of the chosen sessions are not {klass['name']} sessions")

        clash = conn.execute(
            f"SELECT COUNT(*) n FROM bookings WHERE client_id=? AND session_id IN ({marks})",
            (cid, *body.session_ids)).fetchone()["n"]
        if clash:
            raise HTTPException(400, "already booked into one of those sessions")

        conn.execute("UPDATE subscriptions SET active=0 WHERE client_id=? AND class_id=?",
                     (cid, body.class_id))
        starts = body.starts_on or date.today().isoformat()
        # Validity follows the sessions the plan actually pays for: it runs
        # through the last of them. Reception can still type a date instead
        # — a courtesy extension — and that's what expires_on carries when set.
        expires = body.expires_on or access.last_of_sessions(conn, body.session_ids) or starts
        cur = conn.execute(
            "INSERT INTO subscriptions (client_id, class_id, plan, sessions_total, price,"
            " starts_on, expires_on, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (cid, body.class_id, body.plan, body.sessions_total, body.price, starts,
             expires, db.now()))
        sub_id = cur.lastrowid
        for sid in body.session_ids:
            s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            status = "absent" if s and access.session_end(s) < db.now() else "booked"
            conn.execute(
                "INSERT INTO bookings (client_id, session_id, subscription_id, status,"
                " created_at) VALUES (?,?,?,?,?)", (cid, sid, sub_id, status, db.now()))
        conn.commit()
        return {"id": sub_id, "booked": len(body.session_ids),
                "class_id": body.class_id, "class_name": klass["name"]}
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
        c = one(conn.execute("SELECT * FROM clients WHERE id=?", (cid,)))
        if not c:
            raise HTTPException(404, "no such client")
        if not body.class_id:
            raise HTTPException(400, "a card belongs to a class — say which")
        klass = one(conn.execute("SELECT * FROM classes WHERE id=?", (body.class_id,)))
        if not klass:
            raise HTTPException(404, "no such class")

        # A card is proof of a plan in that class. Issuing a Flexibility card
        # to someone who only takes Ballet would create a credential that can
        # never check anyone in, and reads at reception as a system fault.
        sub = access.active_plan(conn, cid, body.class_id)
        if not sub:
            raise HTTPException(
                400, f"{c['name_en']} has no active {klass['name']} plan — "
                     f"add one before issuing this card")

        old = one(conn.execute(
            "SELECT token FROM credentials WHERE client_id=? AND revoked_at IS NULL"
            "   AND (class_id IS ? OR class_id = ?)",
            (cid, body.class_id, body.class_id)))
        conn.execute(
            "UPDATE credentials SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL"
            "   AND (class_id IS ? OR class_id = ?)",
            (db.now(), cid, body.class_id, body.class_id))

        token = tokens.issue(cid)
        conn.execute(
            "INSERT INTO credentials (client_id, class_id, token, kind, issued_at)"
            " VALUES (?,?,?,?,?)", (cid, body.class_id, token, "card", db.now()))
        conn.commit()

        state = access.plan_state(conn, sub["id"])
        path = cards.build_card(cid, c["name_en"], token, state["sessions_total"],
                                state["expires_on"],
                                class_name=klass["name"], colour=klass["colour"])
        return {"token": token, "card_url": "/" + path,
                "revoked": old["token"] if old else None}
    finally:
        conn.close()


@router.delete("/api/clients/{cid}")
def delete_client(cid: int, hard: bool = False):
    conn = db.connect()
    try:
        c = one(conn.execute("SELECT * FROM clients WHERE id=?", (cid,)))
        if not c:
            raise HTTPException(404, "no such client")
        visits = conn.execute(
            "SELECT COUNT(*) n FROM bookings WHERE client_id=? AND status!='booked'",
            (cid,)).fetchone()["n"]
        if hard and visits:
            raise HTTPException(400, f"{c['name_en']} has {visits} recorded sessions — archive instead")
        if hard:
            for q in ("DELETE FROM bookings WHERE client_id=?",
                      "DELETE FROM credentials WHERE client_id=?",
                      "DELETE FROM subscriptions WHERE client_id=?",
                      "DELETE FROM access_events WHERE client_id=?",
                      "DELETE FROM clients WHERE id=?"):
                conn.execute(q, (cid,))
            action = "delete"
        else:
            conn.execute("UPDATE clients SET active=0 WHERE id=?", (cid,))
            conn.execute("UPDATE credentials SET revoked_at=? WHERE client_id=?"
                         " AND revoked_at IS NULL", (db.now(), cid))
            conn.execute("DELETE FROM bookings WHERE client_id=? AND status='booked'", (cid,))
            action = "archive"
        conn.commit()
        return {"ok": True, "action": action}
    finally:
        conn.close()
