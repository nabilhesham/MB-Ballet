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
import repo as data


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
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        active = 0 if status == "archived" else 1
        out = repo.search_clients(active, q)
        today = date.today().isoformat()
        # Five queries for the whole list rather than four per client. The
        # difference is invisible on a local file and is the whole page
        # against a networked backend.
        ids = [d["id"] for d in out]
        plans = repo.active_plans_for(ids)
        states = access.plan_states(repo, [p["id"] for p in plans.values()])
        cards = repo.card_counts_bulk(ids)
        for d in out:
            sub = plans.get(d["id"])
            state = states.get(sub["id"], {}) if sub else {}
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
            d["cards"] = cards[d["id"]]
        if status == "attention":
            out = [d for d in out if not d["frozen"] and (
                    d["expired"] or d["low"] or d["empty"] or not d["cards"]
                    or (d["unassigned"] or 0) > 0)]
        return out
    finally:
        repo.close()


@router.post("/api/clients")
def create_client(body: ClientIn):
    repo = data.connect()
    try:
        return {"id": repo.insert("clients", {
            "name_en": body.name_en, "phone": body.phone, "age": body.age,
            "school": body.school,
            "joined_on": body.joined_on or date.today().isoformat(),
            "notes": body.notes, "created_at": db.now()})}
    finally:
        repo.close()


@router.get("/api/clients/{cid}")
def get_client(cid: int):
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        c = repo.get("clients", cid)
        if not c:
            raise HTTPException(404, "no such client")

        # Payment history: every plan bought, newest first.
        owned = repo.find("subscriptions", {"client_id": cid},
                          sort=[("created_at", -1)], fields=["id"])
        states = access.plan_states(repo, [r["id"] for r in owned])
        c["plans"] = [states[r["id"]] for r in owned]
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

        c["cards"] = repo.client_cards(cid)
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
        c["upcoming"] = repo.client_upcoming(cid, now)

        c["history"] = repo.client_history(cid, now, 100)
        return c
    finally:
        repo.close()


@router.get("/api/clients/{cid}/plan/{pid}/sessions")
def plan_sessions(cid: int, pid: int):
    """Every session paid for by one plan — the popup on a payment row."""
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        return repo.plan_sessions(cid, pid)
    finally:
        repo.close()


@router.put("/api/clients/{cid}")
def update_client(cid: int, body: ClientIn):
    repo = data.connect()
    try:
        repo.update("clients", cid, {
            "name_en": body.name_en, "phone": body.phone, "age": body.age,
            "school": body.school, "joined_on": body.joined_on,
            "notes": body.notes})
        return {"ok": True}
    finally:
        repo.close()


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
    repo = data.connect()
    try:
        repo.update("clients", cid, {"photo_path": "/" + path})
        return {"photo_path": "/" + path}
    finally:
        repo.close()


@router.post("/api/clients/{cid}/plan")
def add_plan(cid: int, body: PlanIn):
    """Sell a plan for one class. The rules live in access.add_plan()."""
    repo = data.connect()
    try:
        r = access.add_plan(
            repo, cid, body.class_id, body.plan, body.sessions_total,
            body.session_ids, price=body.price, starts_on=body.starts_on,
            expires_on=body.expires_on, paid_on=body.paid_on, notes=body.notes)
        if not r["ok"]:
            raise HTTPException(r.get("status", 400), r["error"])
        return r
    finally:
        repo.close()


@router.post("/api/clients/{cid}/card")
def issue_card(cid: int, body: CardIn):
    """
    One card per class. Reissuing replaces only that class's card, so a client
    taking Ballet and Flexibility keeps the other one working.
    """
    repo = data.connect()
    try:
        r = access.issue_card(repo, cid, body.class_id)
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
        repo.close()


@router.delete("/api/clients/{cid}")
def delete_client(cid: int, hard: bool = False):
    """Archive, or remove entirely. The rules live in access.delete_client()."""
    repo = data.connect()
    try:
        r = access.delete_client(repo, cid, hard=hard)
        if not r["ok"]:
            raise HTTPException(r.get("status", 400), r["error"])
        return r
    finally:
        repo.close()


@router.post("/api/clients/{cid}/unarchive")
def unarchive_client(cid: int):
    """
    Brings the client and their history back. Their cards stay revoked —
    credentials are revoked rather than deleted, so a restored client needs
    one reissued.
    """
    repo = data.connect()
    try:
        if not repo.update("clients", cid, {"active": 1}):
            raise HTTPException(404, "no such client")
        return {"ok": True}
    finally:
        repo.close()
