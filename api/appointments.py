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
    notes: Optional[str] = None


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
    if not body.name.strip():
        raise HTTPException(400, "A name is required.")
    try:
        date.fromisoformat(body.on_date)
    except (TypeError, ValueError):
        raise HTTPException(400, "Pick the date of the appointment.")
    repo = data.connect()
    try:
        return {"id": repo.insert("appointments", {
            "name": body.name.strip(), "phone": (body.phone or "").strip() or None,
            "age": body.age, "on_date": body.on_date,
            "notes": (body.notes or "").strip() or None,
            "created_at": db.now()})}
    finally:
        repo.close()
