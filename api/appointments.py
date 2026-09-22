"""
/api/appointments/* — enquiries: somebody who has asked to come in.

An appointment is deliberately **not** a client. Most of these are strangers
who rang up, and the whole value of the list is the ones who have not been
written down as clients yet — so the row carries its own name, age and
mobile, which is all reception has when the phone rings. If the person turns
up and enrols, a client is created separately and this row stays as the
record of the enquiry.

That also means none of the client identity rules apply here. The same
family rings twice about two children and the same person reschedules, so
there is no uniqueness rule on the number or on anything else, and the
mobile is optional rather than mandatory — see access.phone_required() for
why a *client* may not be without one.
"""

import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import repo as data


router = APIRouter()


class AppointmentIn(BaseModel):
    name: str
    phone: Optional[str] = None
    # REAL, not int, for the same reason clients.age is: the youngest
    # children are 4.8 and 3.5, and an int field *rejects* a decimal rather
    # than rounding it. See the clients note in CLAUDE.md.
    age: Optional[float] = None
    on_date: str
    # HH:MM, beside the day rather than folded into it. Optional, because
    # "sometime Tuesday" is what half of these enquiries actually are and
    # midnight is not a truthful stand-in for it. See db.py's appointments
    # table for why the two halves stay separate columns.
    on_time: Optional[str] = None
    notes: Optional[str] = None


_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _checked(body: AppointmentIn) -> dict:
    """
    The row a create or an edit writes, or an HTTPException saying why not.

    One function because the two routes ask exactly the same questions, and
    a second copy of them would eventually let an edit write something a
    create refuses -- which is the shape the client duplicate rule takes
    too (see access.duplicate_client, enforced on both POST and PUT).
    """
    if not body.name.strip():
        raise HTTPException(400, "A name is required.")
    try:
        date.fromisoformat(body.on_date)
    except (TypeError, ValueError):
        raise HTTPException(400, "Pick the date of the appointment.")
    at = (body.on_time or "").strip()
    # An <input type="time"> can hand back seconds; the column holds HH:MM.
    if len(at) == 8 and at[5] == ":":
        at = at[:5]
    if at and not _TIME.match(at):
        raise HTTPException(400, "The time should look like 16:30.")
    return {"name": body.name.strip(),
            "phone": (body.phone or "").strip() or None,
            "age": body.age, "on_date": body.on_date, "on_time": at or None,
            "notes": (body.notes or "").strip() or None}


@router.get("/api/appointments")
def list_appointments(q: str = "", date_from: str = "", date_to: str = ""):
    """
    The enquiry list, newest appointment first.

    `q` matches the name or the mobile, server-side, the same way
    /api/clients does — the search box is next to the table and must filter
    the same rows the table was just given.

    `date_from`/`date_to` are inclusive ISO days. They are a filter on the
    date somebody is expected, which is the only date on the row; both are
    optional and either one alone works. Expressed as an ordinary filter
    rather than a port method because the dialect already reaches it — an
    `$or` of two `like`s and a range on one column is exactly what the
    twelve primitives are for.
    """
    flt = {}
    if q:
        flt["$or"] = [{"name": {"like": f"%{q}%"}},
                      {"phone": {"like": f"%{q}%"}}]
    when = {}
    if date_from:
        when["gte"] = date_from
    if date_to:
        when["lte"] = date_to
    if when:
        flt["on_date"] = when

    repo = data.connect()
    try:
        # Soonest-last: the list is read to find who is coming, and the
        # newest enquiry is the one just taken down. The tiebreak on id is
        # added by the dialect, so both backends agree on equal dates.
        return repo.find("appointments", flt, sort=[("on_date", -1)])
    finally:
        repo.close()


@router.post("/api/appointments")
def create_appointment(body: AppointmentIn):
    repo = data.connect()
    try:
        return {"id": repo.insert("appointments",
                                  {**_checked(body), "created_at": db.now()})}
    finally:
        repo.close()


@router.put("/api/appointments/{aid}")
def update_appointment(aid: int, body: AppointmentIn):
    """
    Correct an enquiry: the time moved, the name was misheard, a mobile
    finally arrived. Every field the form shows, since an enquiry is a note
    taken over the phone and any part of it can be wrong.
    """
    repo = data.connect()
    try:
        if repo.get("appointments", aid) is None:
            raise HTTPException(404, "No such appointment.")
        repo.update("appointments", aid, _checked(body))
        return repo.get("appointments", aid)
    finally:
        repo.close()


@router.delete("/api/appointments/{aid}")
def delete_appointment(aid: int):
    """
    Gone for good, not archived -- the one place in this app where that is
    the right answer.

    The deletion policy exists because losing who attended what is worse
    than a cluttered list. An enquiry has no attendance, no plan, no card
    and nothing pointing at it: it is a note that somebody rang up. A
    cancelled call kept for ever as a greyed-out row would make the list
    worse at the one job it has, which is showing who is expected.
    """
    repo = data.connect()
    try:
        if repo.get("appointments", aid) is None:
            raise HTTPException(404, "No such appointment.")
        repo.delete("appointments", aid)
        return {"ok": True}
    finally:
        repo.close()
