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
import cards
import db
import repo as data

router = APIRouter()


# ---------------------------------------------------------------- models
class ScanIn(BaseModel):
    token: str


class LookupIn(BaseModel):
    client_id: int


class EventIn(BaseModel):
    event_id: int


class RenewIn(BaseModel):
    """
    A renewal sold at the desk. Deliberately no `session_ids`: the kiosk has
    no session picker, and access.renew_at_desk() chooses the dates. See its
    docstring for why that is the rule rather than a shortcut.

    No `class_id` for the card either -- it is the plan's own class, because
    a card is one per class and this is the plan it now stands for.
    """
    client_id: int
    class_id: int
    plan: str
    sessions_total: int
    price: Optional[float] = None
    paid_on: Optional[str] = None


# ---------------------------------------------------------------- routes
@router.post("/api/access/verify")
def verify(body: ScanIn):
    repo = data.connect()
    try:
        return access.verify(repo, body.token)
    finally:
        repo.close()


@router.post("/api/access/lookup")
def lookup(body: LookupIn):
    repo = data.connect()
    try:
        return access.verify_by_client(repo, body.client_id)
    finally:
        repo.close()


@router.post("/api/access/checkin")
def checkin(body: EventIn):
    repo = data.connect()
    try:
        r = access.check_in(repo, body.event_id)
        return JSONResponse(r, status_code=200 if r["ok"] else 409)
    finally:
        repo.close()


class SwapIn(BaseModel):
    client_id: int
    from_session_id: int
    to_session_id: int
    credential_id: Optional[int] = None


@router.get("/api/access/swap-options/{client_id}")
def swap_options(client_id: int):
    """Today's sessions, and the client's own slots they could give up."""
    repo = data.connect()
    try:
        return access.swap_options(repo, client_id)
    finally:
        repo.close()


@router.post("/api/access/swap-checkin")
def swap_checkin(body: SwapIn):
    """Move one of their slots onto a session today, then check them in."""
    repo = data.connect()
    try:
        r = access.swap_and_check_in(repo, body.client_id, body.from_session_id,
                                     body.to_session_id, body.credential_id)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        repo.close()


@router.post("/api/access/undo")
def undo(body: EventIn):
    repo = data.connect()
    try:
        return access.undo(repo, body.event_id)
    finally:
        repo.close()


@router.post("/api/access/renew")
def renew(body: RenewIn):
    """
    Sell the next plan without leaving the kiosk, and print the card for it.

    The second way in to a renewal; the first is the client's profile, which
    keeps its full session picker. This one exists for the moment it is
    actually needed -- a client at the counter whose plan has just run out.

    **The card is issued here, exactly as the profile's renewal issues one.**
    The printed card carries the session count and the end date of the plan
    it was made for, so a renewal leaves the old one reading last month's
    figures; both ways in therefore replace it, and reception prints the new
    one and hands it over before the client walks away.

    The cost is real and the kiosk says so out loud: issuing **revokes** the
    previous credential, and the card being revoked is the one in the
    client's hand. Between the sale and the printout that card does not
    scan. That is the trade -- a wrong number on a card that works, against
    a right one that has to be printed -- and it is reception's to manage,
    with the client standing in front of them.

    It still checks nobody in. Selling a plan and spending one of its
    sessions are separate decisions; see access.renew_at_desk().
    """
    repo = data.connect()
    try:
        r = access.renew_at_desk(repo, body.client_id, body.class_id,
                                 body.plan.strip(), body.sessions_total,
                                 price=body.price, paid_on=body.paid_on)
        if not r.get("ok"):
            return JSONResponse(r, status_code=r.get("status", 400))
        # The sale stands whatever happens here. A card that could not be
        # drawn is one press of Reissue on the profile away; unselling a
        # plan somebody has just paid for is not, and a 500 over a picture
        # would leave the kiosk claiming nothing happened when the money is
        # already in the till. So the failure is reported beside the
        # verdict, not raised.
        try:
            card = cards.issue(repo, body.client_id, body.class_id)
            r["card"] = {"ok": bool(card.get("ok")),
                         "revoked": bool(card.get("revoked")),
                         "error": None if card.get("ok") else card.get("error")}
        except Exception as exc:                       # noqa: BLE001
            r["card"] = {"ok": False, "revoked": False, "error": str(exc)}
        return JSONResponse(r, status_code=200)
    finally:
        repo.close()


class PlanUpdateIn(BaseModel):
    """
    A plan corrected at the kiosk, with the client standing there.

    No `session_ids` and no `class_id`, for the same reason `RenewIn` has
    neither: the kiosk has no session picker, so the dates are derived from
    the start day and the count (see access.sessions_from_start()), and the
    class is the one the scanned card proves — changing that is a correction
    of a different kind and belongs on the profile.
    """
    plan_id: int
    plan: str
    sessions_total: int
    starts_on: str
    expires_on: Optional[str] = None
    price: Optional[float] = None
    paid_on: Optional[str] = None
    notes: Optional[str] = None


@router.post("/api/access/plan-update")
def plan_update(body: PlanUpdateIn):
    """
    Fix a plan from the kiosk — in practice, take the payment for one.

    This is where an unpaid plan's refusal leads. The card was scanned, the
    plan behind it has had its one session on trust, and the receptionist is
    looking at the client: that is the moment the money is actually
    collectable, and walking to the admin screen for it is how it stops
    being collected. Every field the profile's Edit offers is here except
    the two the kiosk cannot answer (see PlanUpdateIn).

    **It does not check anybody in.** Saving a payment and spending a
    session are still separate, exactly as selling a plan and spending one
    are — the kiosk re-reads the client afterwards and the ordinary scan
    path does the rest, which is what makes the Undo, the deduction and the
    day's count identical to a real scan rather than a second
    implementation of them.
    """
    repo = data.connect()
    try:
        sub = repo.get("subscriptions", body.plan_id)
        if sub is None:
            return JSONResponse({"ok": False, "error": "no such plan"},
                                status_code=404)

        extra = {}
        # The dates are re-derived only when one of the two answers they come
        # from has actually changed. Left alone otherwise, so taking a payment
        # never quietly moves the sessions somebody already agreed.
        resequenced = (body.starts_on != sub["starts_on"]
                       or body.sessions_total != sub["sessions_total"])
        if resequenced:
            auto = access.sessions_from_start(
                repo, sub["class_id"], sub["client_id"], body.starts_on,
                body.sessions_total, plan_id=body.plan_id)
            if auto["short"]:
                # Said as the thing to do about it rather than as a rule that
                # was broken, the same way a short timetable is refused on a
                # renewal. Nothing is saved.
                have = body.sessions_total - auto["short"]
                return JSONResponse({"ok": False, "error": (
                    f"Only {have} session{'' if have == 1 else 's'} are "
                    f"scheduled from {body.starts_on} — schedule more, or "
                    f"put a smaller number on the plan.")}, status_code=400)
            extra["session_ids"] = auto["session_ids"]
            extra["expires_on"] = body.expires_on or auto["expires_on"]
            # Sent only when it moved. edit_plan() reads a count on its own
            # as "reassign these", and rightly refuses it without the dates
            # -- so passing the unchanged number through would turn a
            # payment into that refusal.
            extra["sessions_total"] = body.sessions_total
        elif body.expires_on and body.expires_on != sub["expires_on"]:
            extra["expires_on"] = body.expires_on

        r = access.edit_plan(
            repo, body.plan_id, plan=body.plan.strip(),
            starts_on=body.starts_on, price=body.price, notes=body.notes,
            paid_on=body.paid_on or None, clear_paid_on=not body.paid_on,
            **extra)
        if not r.get("ok"):
            return JSONResponse(r, status_code=r.get("status", 400))

        # The card prints the session count and the end date, and nothing
        # regenerates it -- so it is replaced when one of those changed, and
        # left alone when only the money did. That second case is the common
        # one here and is worth protecting: issuing revokes the card in the
        # client's hand, and there is no reason to do that to somebody who
        # has just paid. paid_on is deliberately not on the card at all (a
        # print snapshot would read UNPAID for the card's whole life), so
        # nothing about it goes stale.
        reprinted = resequenced or bool(extra.get("expires_on"))
        card = None
        if reprinted:
            try:
                out = cards.issue(repo, sub["client_id"], sub["class_id"])
                card = {"ok": bool(out.get("ok")),
                        "error": None if out.get("ok") else out.get("error")}
            except Exception as exc:                   # noqa: BLE001
                card = {"ok": False, "error": str(exc)}
        return JSONResponse({**r, "client_id": sub["client_id"],
                             "paid": bool(body.paid_on), "card": card},
                            status_code=200)
    finally:
        repo.close()
