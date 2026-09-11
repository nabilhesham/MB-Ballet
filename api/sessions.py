"""
/api/sessions/* — dated occurrences, their rosters, and booking moves.

`move_booking` lives here rather than under api/clients.py despite its URL
(`/api/clients/{cid}/move-booking/{from_sid}`) because it is a booking
operation through and through — same access.move_booking() call, same
JSONResponse-with-ok pattern as book/unbook right above it.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import access
import db
import repo as data


router = APIRouter()


# ---------------------------------------------------------------- models
class SessionIn(BaseModel):
    class_id: int
    instructor_id: Optional[int] = None
    starts_at: int
    duration_hours: Optional[float] = None
    notes: Optional[str] = None


class SessionEdit(BaseModel):
    class_id: Optional[int] = None
    instructor_id: Optional[int] = None
    starts_at: Optional[int] = None
    duration_hours: Optional[float] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class RepeatIn(BaseModel):
    class_id: int
    instructor_id: Optional[int] = None
    starts_at: int
    weeks: int = 8
    weekdays: list[int] = []
    duration_hours: Optional[float] = None


class BookIn(BaseModel):
    client_id: int
    # Naming the plan is what lets a session of another class be booked: with
    # no plan in that session's class there is nothing for book() to resolve
    # on its own. Only the client profile's corrections send these two.
    subscription_id: Optional[int] = None
    allow_other_class: bool = False


class StatusIn(BaseModel):
    client_id: int
    status: str


class BulkDeleteIn(BaseModel):
    ids: list[int]
    force: bool = False


class MoveIn(BaseModel):
    to_session_id: int
    # Corrections made on the client profile may land on another class's
    # session; the booking keeps the plan that paid for it either way.
    allow_other_class: bool = False
    status: Optional[str] = None


# ---------------------------------------------------------------- routes
@router.get("/api/sessions")
def list_sessions(start: int = 0, end: int = 0, class_id: int = 0, available_for: int = 0):
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        if not start:
            start = db.now() - 7 * 86400
        if not end:
            end = start + 28 * 86400
        # Half-open, where this was BETWEEN ... AND. `available_for` drops
        # sessions the client already holds a slot in.
        return repo.sessions_in_range(start, end + 1, class_id=class_id,
                                      not_booked_by=available_for)
    finally:
        repo.close()


@router.post("/api/sessions")
def create_session(body: SessionIn):
    repo = data.connect()
    try:
        cl = repo.get("classes", body.class_id)
        if not cl:
            raise HTTPException(404, "no such class")
        # No instructor named explicitly -> fall back to the class's default,
        # if it has one. Naming one, even a different one, always wins.
        instructor_id = body.instructor_id if body.instructor_id is not None else cl["instructor_id"]
        hours = body.duration_hours or cl["duration_hours"]
        # The check and the insert under one lock. Deferred, the slot could
        # be taken between deciding it was free and writing into it.
        with repo.begin():
            clash = access.slot_conflict(repo, body.starts_at, hours)
            if clash:
                raise HTTPException(400, access.slot_taken_message(clash))
            new_id = repo.insert("sessions", {
                "class_id": body.class_id, "instructor_id": instructor_id,
                "starts_at": body.starts_at, "duration_hours": hours,
                "ends_at": access.ends_at_of(body.starts_at, hours),
                "notes": body.notes})
        return {"id": new_id}
    finally:
        repo.close()


@router.post("/api/sessions/repeat")
def repeat_sessions(body: RepeatIn):
    repo = data.connect()
    try:
        cl = repo.get("classes", body.class_id)
        if not cl:
            raise HTTPException(404, "no such class")
        instructor_id = body.instructor_id if body.instructor_id is not None else cl["instructor_id"]
        base = datetime.fromtimestamp(body.starts_at)
        weekdays = body.weekdays or [base.weekday()]
        hours = body.duration_hours or cl["duration_hours"]
        # A term is generated as one change. Each date is still
        # skipped individually on a clash, but a failure partway
        # through must not leave half a term behind.
        made = 0
        skipped = []
        with repo.begin():
            for w in range(body.weeks):
                monday = base - timedelta(days=base.weekday()) + timedelta(weeks=w)
                for wd in weekdays:
                    when = monday.replace(hour=base.hour, minute=base.minute,
                                          second=0, microsecond=0) + timedelta(days=wd)
                    ts = int(when.timestamp())
                    if ts < body.starts_at:
                        continue
                    if repo.exists("sessions",
                                   {"class_id": body.class_id, "starts_at": ts}):
                        continue
                    # A whole term is generated at once, so one taken evening in
                    # week 7 must not cost the other eleven. Skip it and say
                    # which, the same way a date already holding this class's own
                    # session is skipped just above.
                    clash = access.slot_conflict(repo, ts, hours)
                    if clash:
                        skipped.append(access.slot_taken_message(clash))
                        continue
                    repo.insert("sessions", {
                        "class_id": body.class_id, "instructor_id": instructor_id,
                        "starts_at": ts, "duration_hours": hours,
                        "ends_at": access.ends_at_of(ts, hours)})
                    made += 1
        return {"created": made, "skipped": skipped}
    finally:
        repo.close()


@router.get("/api/sessions/{sid}")
def get_session(sid: int):
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        s = repo.session_detail(sid)
        if not s:
            raise HTTPException(404, "no such session")
        s["roster"] = access.session_roster(repo, sid)
        return s
    finally:
        repo.close()


@router.put("/api/sessions/{sid}")
def edit_session(sid: int, body: SessionEdit, clear_instructor: bool = False):
    """
    Partial update — only the fields sent are touched, via exclude_none. That
    is also why clearing the instructor needs its own flag: a plain
    instructor_id=null is indistinguishable from "wasn't sent" once
    exclude_none drops it, so it silently never reached the database. Pass
    ?clear_instructor=true instead of instructor_id to blank it back to none.
    """
    repo = data.connect()
    try:
        if not repo.exists("sessions", {"id": sid}):
            raise HTTPException(404, "no such session")
        fields = body.model_dump(exclude_none=True)
        if clear_instructor:
            fields["instructor_id"] = None
        if not fields:
            return {"ok": True}
        # Moving a session or stretching it can walk into another one, so the
        # slot is re-checked against the values this edit is about to write —
        # ignoring the session itself, which of course overlaps where it is.
        with repo.begin():
            if "starts_at" in fields or "duration_hours" in fields:
                cur = repo.get("sessions", sid)
                clash = access.slot_conflict(
                    repo,
                    fields.get("starts_at", cur["starts_at"]),
                    fields.get("duration_hours", cur["duration_hours"]),
                    exclude_id=sid)
                if clash:
                    raise HTTPException(400, access.slot_taken_message(clash))
                # Moving or stretching a session moves its end. ends_at is
                # stored, not derived, so it has to travel with them.
                fields["ends_at"] = access.ends_at_of(
                    fields.get("starts_at", cur["starts_at"]),
                    fields.get("duration_hours", cur["duration_hours"]))
            repo.update("sessions", sid, fields)
        return {"ok": True, "changed": list(fields)}
    finally:
        repo.close()


@router.post("/api/sessions/{sid}/cancel")
def cancel_the_session(sid: int):
    repo = data.connect()
    try:
        return access.cancel_session(repo, sid)
    finally:
        repo.close()


@router.put("/api/sessions/{sid}/status/{status}")
def set_session_status(sid: int, status: str):
    if status not in ("scheduled", "completed", "cancelled"):
        raise HTTPException(400, "bad status")
    repo = data.connect()
    try:
        # Cancelling frees the slot, so bringing a session back has to find it
        # still free — otherwise cancel, schedule something else, un-cancel
        # would put two classes in one slot by the back door.
        with repo.begin():
            if status != "cancelled":
                row = repo.get("sessions", sid)
                if not row:
                    raise HTTPException(404, "no such session")
                clash = access.slot_conflict(repo, row["starts_at"],
                                             row["duration_hours"], exclude_id=sid)
                if clash:
                    raise HTTPException(400, access.slot_taken_message(clash))
            repo.update("sessions", sid, {"status": status})
        return {"ok": True}
    finally:
        repo.close()


@router.delete("/api/sessions/{sid}")
def delete_session(sid: int, force: bool = False):
    """Delete one session. Deleting one is deleting a list of one."""
    repo = data.connect()
    try:
        r = access.delete_sessions(repo, [sid], force=force)
        if r["blocked"]:
            held = r["blocked"][0]["attendance"]
            raise HTTPException(400, f"{held} attendance record(s) — cancel it instead")
        return {"ok": True, "released": r["released"]}
    finally:
        repo.close()


@router.post("/api/sessions/bulk-delete")
def bulk_delete_sessions(body: BulkDeleteIn):
    """
    Delete several sessions at once, applying the same rule delete_session
    applies to one: a session carrying attendance is kept back unless force
    is set, because losing the record of who turned up is worse than a
    cluttered timetable.

    Kept-back sessions are named in the response rather than failing the
    batch — clearing a term with one taught week in the middle of it should
    remove the other eleven and say why the twelfth stayed.
    """
    repo = data.connect()
    try:
        return access.delete_sessions(repo, body.ids, force=body.force)
    finally:
        repo.close()


@router.post("/api/sessions/{sid}/book")
def book_into_session(sid: int, body: BookIn):
    repo = data.connect()
    try:
        r = access.book(repo, body.client_id, sid,
                        subscription_id=body.subscription_id,
                        allow_other_class=body.allow_other_class)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        repo.close()


@router.delete("/api/sessions/{sid}/book/{cid}")
def unbook_from_session(sid: int, cid: int):
    repo = data.connect()
    try:
        r = access.unbook(repo, cid, sid)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        repo.close()


@router.post("/api/sessions/{sid}/status-of")
def set_attend_status(sid: int, body: StatusIn):
    repo = data.connect()
    try:
        r = access.set_status(repo, sid, body.client_id, body.status)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        repo.close()


@router.post("/api/clients/{cid}/move-booking/{from_sid}")
def move_booking(cid: int, from_sid: int, body: MoveIn):
    repo = data.connect()
    try:
        r = access.move_booking(repo, cid, from_sid, body.to_session_id,
                                allow_other_class=body.allow_other_class,
                                status=body.status)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        repo.close()
