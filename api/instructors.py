"""/api/instructors/* — instructor roster, hours and pay."""

import glob
import os
import shutil
from datetime import date
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

import access
import db
import repo as data


router = APIRouter()


# ---------------------------------------------------------------- models
class InstructorIn(BaseModel):
    name: str
    phone: Optional[str] = None
    specialty: Optional[str] = None
    hourly_rate: float = 0


class HoursAdjustIn(BaseModel):
    # One day, not a range: a correction has to land on the day it happened
    # on, or the daily history it builds up means nothing.
    day: str
    new_total: float
    note: Optional[str] = None


# ---------------------------------------------------------------- routes
@router.get("/api/instructors")
def list_instructors(status: str = "active"):
    """
    `status="archived"` is the whole other half of this list, not an overlay
    on top of the active one. /api/classes and /api/clients read the same
    way; /api/clients additionally carries "attention", which filters within
    the active half rather than choosing a half.
    """
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        active = 0 if status == "archived" else 1
        out = repo.find("instructors", {"active": active}, sort=[("name", 1)])
        # Two queries for the list rather than one per instructor.
        totals = repo.taught_totals_bulk([i["id"] for i in out])
        for i in out:
            t = totals[i["id"]]
            i["sessions_taught"] = t["sessions"]
            i["hours_taught"] = t["hours"]
            i["earned"] = round(t["hours"] * (i["hourly_rate"] or 0), 2)
        return out
    finally:
        repo.close()


@router.post("/api/instructors")
def create_instructor(body: InstructorIn):
    repo = data.connect()
    try:
        return {"id": repo.insert("instructors", {
            "name": body.name, "phone": body.phone,
            "specialty": body.specialty, "hourly_rate": body.hourly_rate})}
    finally:
        repo.close()


@router.put("/api/instructors/{iid}")
def update_instructor(iid: int, body: InstructorIn):
    repo = data.connect()
    try:
        repo.update("instructors", iid, {
            "name": body.name, "phone": body.phone,
            "specialty": body.specialty, "hourly_rate": body.hourly_rate})
        return {"ok": True}
    finally:
        repo.close()


@router.get("/api/instructors/{iid}")
def get_instructor(iid: int, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    """
    Everything on this page is scoped to a period, defaulting to **today**
    when no from/to is given — the question asked most often is "what did she
    do today", and a day is also the only period whose hours can be edited
    (see adjust_taught_hours). A wider range is picked with the from/to
    inputs and recalculates every figure, including what's "upcoming", from
    the same query params.
    """
    repo = data.connect()
    try:
        access.settle_past_sessions(repo)
        i = repo.get("instructors", iid)
        if not i:
            raise HTTPException(404, "no such instructor")

        today = date.today().isoformat()
        period_from, period_to = (from_, to) if from_ and to else (today, today)
        if period_to < period_from:
            raise HTTPException(400, "the end of the range must not be before its start")
        start_ts, end_ts = access.date_range_ts(period_from, period_to)

        i["sessions"] = repo.instructor_sessions(iid, start_ts, end_ts, 200)

        t = repo.taught_totals_bulk([iid], start_ts, end_ts)[iid]
        # "Upcoming" is bounded by the picked range too, not just by now: a
        # past range has none (nothing in it is still ahead), and a future
        # range only counts what's still ahead within that window.
        up_start = max(start_ts, db.now())
        up = repo.taught_totals_bulk([iid], up_start, end_ts,
                                     status="scheduled")[iid]
        # Hours the salary sheet recorded, which is what payroll is actually
        # paid on. Sessions taught is the app's own count and the two are
        # deliberately shown side by side: a gap between them is either a
        # class that never made it onto the timetable or an hour nobody
        # billed for, and both are worth seeing.
        rate = i["hourly_rate"] or 0
        i["logged"] = access.logged_hours(repo, iid, period_from, period_to)
        # Hours taught carries reception's corrections, so pay follows the
        # corrected figure rather than the raw timetable.
        taught = access.taught_hours(repo, iid, period_from, period_to)
        i["period_from"], i["period_to"] = period_from, period_to
        # A day is the only period whose hours may be edited — a correction
        # has to land on the day it happened on to be worth anything later.
        i["is_single_day"] = period_from == period_to
        i["totals"] = {
            "sessions_taught": t["n"],
            "hours_taught": taught["hours"],
            "hours_scheduled": taught["scheduled"],
            "hours_adjustment": taught["adjustment"],
            "hourly_rate": rate,
            "earned": round(taught["hours"] * rate, 2),
            "upcoming": up["n"],
            "upcoming_hours": round(up["h"], 2),
            "upcoming_value": round(up["h"] * rate, 2),
        }
        return i
    finally:
        repo.close()


@router.post("/api/instructors/{iid}/hours-adjustment")
def adjust_hours(iid: int, body: HoursAdjustIn):
    repo = data.connect()
    try:
        if not repo.exists("instructors", {"id": iid}):
            raise HTTPException(404, "no such instructor")
        if body.new_total < 0:
            raise HTTPException(400, "hours cannot be negative")
        return access.adjust_taught_hours(repo, iid, body.day, body.new_total, body.note)
    finally:
        repo.close()


@router.post("/api/instructors/{iid}/photo")
async def upload_photo(iid: int, file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "use jpg, png or webp")
    # Timestamped for the same reason as the client photo: a stable filename
    # let the browser keep showing the cached previous picture.
    path = f"photos/instructor_{iid:05d}_{db.now()}{ext}"
    for old in glob.glob(f"photos/instructor_{iid:05d}*"):
        try:
            os.remove(old)
        except OSError:
            pass
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    repo = data.connect()
    try:
        repo.update("instructors", iid, {"photo_path": "/" + path})
        return {"photo_path": "/" + path}
    finally:
        repo.close()


@router.post("/api/instructors/{iid}/unarchive")
def unarchive_instructor(iid: int):
    repo = data.connect()
    try:
        if not repo.update("instructors", iid, {"active": 1}):
            raise HTTPException(404, "no such instructor")
        return {"ok": True}
    finally:
        repo.close()


@router.delete("/api/instructors/{iid}")
def archive_instructor(iid: int):
    repo = data.connect()
    try:
        # The guard and the archive under one lock, so a session cannot be
        # booked into the gap between deciding there are none and archiving.
        with repo.begin():
            n = repo.count("sessions", {
                "instructor_id": iid, "status": "scheduled",
                "starts_at": {"gte": db.now()}})
            if n:
                raise HTTPException(
                    400, f"Still assigned to {n} upcoming session(s) — reassign first")
            repo.update("instructors", iid, {"active": 0})
        return {"ok": True}
    finally:
        repo.close()
