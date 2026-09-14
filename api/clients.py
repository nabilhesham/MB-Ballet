"""/api/clients/* — client profiles, their plans and cards."""

import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

import access
import cards
import db
import images
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
        # Where the stored card can be fetched, so the profile can offer it
        # for download and print.
        #
        # ?v=issued_at is not decoration. The URL is derived from the client
        # and the class, so reissuing hands back the same one and the browser
        # kept serving the card it had already cached — an edited end date
        # never appeared on it, the stored image being right and the picture
        # old. The stamp changes on every issue, which is exactly when the
        # image changes.
        #
        # `card_url` is None when no image is stored, and the profile offers
        # Reissue in place of Download/Print rather than two links that 404.
        # A credential can outlive its picture: one issued before cards moved
        # into the database, on an install whose cards/ folder was not beside
        # academy.db when it first started, or one carried across a backend
        # migration without its images. Regenerating the PNG here instead
        # would be worse -- the card is a print snapshot of what plan_state()
        # said at issue time, and a silently redrawn one would carry today's
        # figures under the old issue date.
        #
        # One query for every card this client has a picture for, not an
        # exists() each: a client holds one per class, and against a networked
        # backend each of those is a round trip on a page that already has a
        # budget (tests/test_query_budget.py).
        stored = {r["variant"] for r in repo.find(
            "images", {"kind": images.CARD, "owner_id": cid}, fields=["variant"])}
        for cd in c["cards"]:
            slug = cards.class_slug(cd["class_name"])
            cd["card_url"] = (
                images.url(images.CARD, cid, variant=slug, stamp=cd["issued_at"])
                if slug in stored else None)

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
    """
    The photo goes into the database, not into photos/ — see images.py.

    `photo_path` still holds the thing an `<img src>` points at, so nothing
    that renders a face had to change; what it holds is now a URL this app
    answers rather than a file on the disk. It is stamped with the upload
    time for the reason it always was: the URL is derived from who the photo
    belongs to, so without the stamp a re-upload leaves the browser showing
    the picture it already had and the new one looks like it never saved.
    """
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "use jpg, png or webp")
    blob, mime = images.shrink(await file.read(),
                               file.content_type or "image/jpeg")
    repo = data.connect()
    try:
        now = db.now()
        with repo.begin():
            images.store(repo, images.CLIENT_PHOTO, cid, blob, mime, now=now)
            url = images.url(images.CLIENT_PHOTO, cid, stamp=now)
            repo.update("clients", cid, {"photo_path": url})
        return {"photo_path": url}
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
        png = cards.build_card(cid, r["client_name"], r["token"],
                               r["sessions_total"], r["expires_on"],
                               class_name=r["class_name"],
                               colour=r["class_colour"])
        # Stamped with the issue time, for the reason get_client gives above.
        now = db.now()
        slug = cards.class_slug(r["class_name"])
        images.store(repo, images.CARD, cid, png, "image/png",
                     variant=slug, now=now)
        return {"token": r["token"],
                "card_url": images.url(images.CARD, cid, variant=slug, stamp=now),
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
