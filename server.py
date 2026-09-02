"""
MB Ballet Academy — local server.

    ENTRY_SECRET=... python server.py
    open http://127.0.0.1:8000
"""

import asyncio
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import access
import cards
import db
import tokens

# --------------------------------------------------------------------------
# Paths.
#
# When packaged as a single .exe, PyInstaller unpacks the bundled files into a
# temporary folder that is wiped on exit — so static assets are read from there,
# but the database, photos, cards and .env must live next to the .exe or the
# academy loses its records every time the program closes.
# --------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    APP_DIR = BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(APP_DIR)
STATIC_DIR = os.path.join(BUNDLE_DIR, "static")

# These must exist before the StaticFiles mounts below, which run at import
# time and raise if their directory is missing.
for _d in ("photos", "cards"):
    os.makedirs(os.path.join(APP_DIR, _d), exist_ok=True)

app = FastAPI(title="MB Ballet Academy")


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
    expires_on: str
    session_ids: list[int] = []


class InstructorIn(BaseModel):
    name: str
    phone: Optional[str] = None
    specialty: Optional[str] = None
    hourly_rate: float = 0


class ClassIn(BaseModel):
    name: str
    description: Optional[str] = None
    colour: str = "#87438E"
    duration_hours: float = 1.5
    level: Optional[str] = None


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


class ScanIn(BaseModel):
    token: str


class LookupIn(BaseModel):
    client_id: int


class EventIn(BaseModel):
    event_id: int


class StatusIn(BaseModel):
    client_id: int
    status: str


class BookIn(BaseModel):
    client_id: int


class MoveIn(BaseModel):
    to_session_id: int


class CardIn(BaseModel):
    class_id: Optional[int] = None


class FreezeIn(BaseModel):
    until: Optional[str] = None          # None = frozen until lifted by hand
    reason: Optional[str] = None


# ---------------------------------------------------------------- helpers
def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None


@app.on_event("startup")
def _startup():
    if not os.environ.get("ENTRY_SECRET") and os.path.exists(".env"):
        for line in open(".env"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)

    if not os.environ.get("ENTRY_SECRET"):
        import secrets
        key = secrets.token_urlsafe(32)
        with open(".env", "w") as f:
            f.write(f"ENTRY_SECRET={key}\n")
        os.environ["ENTRY_SECRET"] = key
        print("  A new security key was created and saved to .env.")
        print("  Keep a backup of that file — losing it invalidates every card.\n")

    db.init()
    os.makedirs("photos", exist_ok=True)
    os.makedirs("cards", exist_ok=True)
    asyncio.create_task(_settle_loop())


async def _settle_loop():
    """
    Mark past no-shows absent, hourly. The same call runs on every read that
    depends on attendance, so this is only a safety net for a screen left open
    overnight.
    """
    await asyncio.sleep(15)
    while True:
        try:
            conn = db.connect()
            try:
                n = access.settle_past_sessions(conn)
                if n:
                    print(f"[settle] {n} booking(s) marked absent")
            finally:
                conn.close()
        except Exception as e:
            print(f"[settle] skipped: {e}")
        await asyncio.sleep(3600)


# ================================================================ clients
@app.get("/api/clients")
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


@app.post("/api/clients")
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


@app.get("/api/clients/{cid}")
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


@app.get("/api/clients/{cid}/plan/{pid}/sessions")
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


@app.put("/api/clients/{cid}")
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


@app.post("/api/clients/{cid}/photo")
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


@app.post("/api/clients/{cid}/plan")
def add_plan(cid: int, body: PlanIn):
    """
    Sell a plan for one class.

    Three rules are enforced here rather than trusted to the UI:
      - every slot is assigned to a real session up front, because a plan with
        unassigned slots is a promise nobody has written down;
      - every one of those sessions belongs to the plan's class, so a Ballet
        plan cannot quietly pay for a Flexibility session;
      - only the previous plan *for this class* is replaced, so a client taking
        two classes keeps the other one running.
    """
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
        cur = conn.execute(
            "INSERT INTO subscriptions (client_id, class_id, plan, sessions_total, price,"
            " starts_on, expires_on, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (cid, body.class_id, body.plan, body.sessions_total, body.price, starts,
             body.expires_on, db.now()))
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


@app.post("/api/plans/{pid}/freeze")
def freeze_plan(pid: int, body: FreezeIn):
    conn = db.connect()
    try:
        r = access.freeze_plan(conn, pid, until=body.until, reason=body.reason)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@app.post("/api/plans/{pid}/unfreeze")
def unfreeze_plan(pid: int):
    conn = db.connect()
    try:
        r = access.unfreeze_plan(conn, pid)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@app.get("/api/plans/{pid}/freezes")
def plan_freezes(pid: int):
    conn = db.connect()
    try:
        return rows(conn.execute(
            "SELECT * FROM freezes WHERE subscription_id=? ORDER BY created_at DESC", (pid,)))
    finally:
        conn.close()


@app.delete("/api/plans/{pid}")
def delete_plan(pid: int):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM bookings WHERE subscription_id=?", (pid,))
        conn.execute("DELETE FROM subscriptions WHERE id=?", (pid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/clients/{cid}/card")
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
        path = cards.build_card(cid, c["name_en"], token, state["plan"],
                                state["expires_on"],
                                class_name=klass["name"], colour=klass["colour"])
        return {"token": token, "card_url": "/" + path,
                "revoked": old["token"] if old else None}
    finally:
        conn.close()


@app.delete("/api/clients/{cid}")
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


# ================================================================ instructors
@app.get("/api/instructors")
def list_instructors():
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        data = rows(conn.execute(
            "SELECT * FROM instructors WHERE active=1 ORDER BY name"))
        for i in data:
            t = conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
                " WHERE instructor_id=? AND status='completed'", (i["id"],)).fetchone()
            i["sessions_taught"] = t["n"]
            i["hours_taught"] = round(t["h"], 2)
            i["earned"] = round(t["h"] * (i["hourly_rate"] or 0), 2)
        return data
    finally:
        conn.close()


@app.post("/api/instructors")
def create_instructor(body: InstructorIn):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO instructors (name, phone, specialty, hourly_rate)"
            " VALUES (?,?,?,?)",
            (body.name, body.phone, body.specialty, body.hourly_rate))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@app.put("/api/instructors/{iid}")
def update_instructor(iid: int, body: InstructorIn):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE instructors SET name=?, phone=?, specialty=?, hourly_rate=?"
            " WHERE id=?",
            (body.name, body.phone, body.specialty, body.hourly_rate, iid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/instructors/{iid}")
def get_instructor(iid: int):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        i = one(conn.execute("SELECT * FROM instructors WHERE id=?", (iid,)))
        if not i:
            raise HTTPException(404, "no such instructor")

        i["sessions"] = rows(conn.execute(
            "SELECT s.id, s.starts_at, s.duration_hours, s.status,"
            "       c.name AS class_name, c.colour,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s JOIN classes c ON c.id = s.class_id"
            " WHERE s.instructor_id = ? ORDER BY s.starts_at DESC LIMIT 200", (iid,)))

        t = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
            " WHERE instructor_id=? AND status='completed'", (iid,)).fetchone()
        up = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
            " WHERE instructor_id=? AND status='scheduled' AND starts_at >= ?",
            (iid, db.now())).fetchone()
        # Hours the salary sheet recorded, which is what payroll is actually
        # paid on. Sessions taught is the app's own count and the two are
        # deliberately shown side by side: a gap between them is either a
        # class that never made it onto the timetable or an hour nobody
        # billed for, and both are worth seeing.
        logged = conn.execute(
            "SELECT COALESCE(SUM(hours),0) h, COUNT(*) days, MIN(work_date) a,"
            "       MAX(work_date) b FROM instructor_hours WHERE instructor_id=?",
            (iid,)).fetchone()
        rate = i["hourly_rate"] or 0
        i["logged"] = {
            "hours": round(logged["h"], 2),
            "days": logged["days"],
            "from": logged["a"], "to": logged["b"],
            "pay": round(logged["h"] * rate, 2),
        }
        i["totals"] = {
            "sessions_taught": t["n"],
            "hours_taught": round(t["h"], 2),
            "hourly_rate": rate,
            "earned": round(t["h"] * rate, 2),
            "upcoming": up["n"],
            "upcoming_hours": round(up["h"], 2),
            "upcoming_value": round(up["h"] * rate, 2),
        }
        return i
    finally:
        conn.close()


@app.delete("/api/instructors/{iid}")
def archive_instructor(iid: int):
    conn = db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) n FROM sessions WHERE instructor_id=? AND status='scheduled'"
            "   AND starts_at >= ?", (iid, db.now())).fetchone()["n"]
        if n:
            raise HTTPException(400, f"Still assigned to {n} upcoming session(s) — reassign first")
        conn.execute("UPDATE instructors SET active=0 WHERE id=?", (iid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ================================================================ classes
@app.get("/api/classes")
def list_classes():
    conn = db.connect()
    try:
        return rows(conn.execute(
            "SELECT c.*,"
            "  (SELECT COUNT(*) FROM sessions s WHERE s.class_id=c.id"
            "     AND s.starts_at > ? AND s.status='scheduled') AS upcoming,"
            "  (SELECT COUNT(DISTINCT b.client_id) FROM bookings b"
            "     JOIN sessions s ON s.id=b.session_id WHERE s.class_id=c.id) AS students"
            " FROM classes c WHERE c.active=1 ORDER BY c.name", (db.now(),)))
    finally:
        conn.close()


@app.post("/api/classes")
def create_class(body: ClassIn):
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO classes (name, description, colour, duration_hours, level)"
            " VALUES (?,?,?,?,?)",
            (body.name, body.description, body.colour, body.duration_hours, body.level))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@app.put("/api/classes/{clid}")
def update_class(clid: int, body: ClassIn):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE classes SET name=?, description=?, colour=?,"
            " duration_hours=?, level=? WHERE id=?",
            (body.name, body.description, body.colour,
             body.duration_hours, body.level, clid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/classes/{clid}")
def get_class(clid: int):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        c = one(conn.execute("SELECT * FROM classes WHERE id=?", (clid,)))
        if not c:
            raise HTTPException(404, "no such class")
        c["sessions"] = rows(conn.execute(
            "SELECT s.*, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s LEFT JOIN instructors i ON i.id = s.instructor_id"
            " WHERE s.class_id=? ORDER BY s.starts_at DESC LIMIT 80", (clid,)))
        c["students"] = rows(conn.execute(
            "SELECT cl.id, cl.name_en, cl.phone, cl.photo_path,"
            "       COUNT(b.id) AS slots,"
            "       SUM(CASE WHEN b.status='present' THEN 1 ELSE 0 END) AS attended"
            "  FROM bookings b JOIN sessions s ON s.id=b.session_id"
            "  JOIN clients cl ON cl.id=b.client_id"
            " WHERE s.class_id=? AND cl.active=1"
            " GROUP BY cl.id ORDER BY cl.name_en", (clid,)))
        return c
    finally:
        conn.close()


@app.delete("/api/classes/{clid}")
def delete_class(clid: int, hard: bool = False):
    conn = db.connect()
    try:
        c = one(conn.execute("SELECT * FROM classes WHERE id=?", (clid,)))
        if not c:
            raise HTTPException(404, "no such class")
        held = conn.execute(
            "SELECT COUNT(*) n FROM bookings b JOIN sessions s ON s.id=b.session_id"
            " WHERE s.class_id=? AND b.status!='booked'", (clid,)).fetchone()["n"]
        if hard and held:
            raise HTTPException(400, f"{c['name']} has {held} attendance records — archive instead")
        if hard:
            conn.execute("DELETE FROM bookings WHERE session_id IN"
                         " (SELECT id FROM sessions WHERE class_id=?)", (clid,))
            conn.execute("DELETE FROM sessions WHERE class_id=?", (clid,))
            conn.execute("DELETE FROM classes WHERE id=?", (clid,))
            action = "delete"
        else:
            conn.execute("UPDATE classes SET active=0 WHERE id=?", (clid,))
            action = "archive"
        conn.commit()
        return {"ok": True, "action": action}
    finally:
        conn.close()


# ================================================================ sessions
@app.get("/api/sessions")
def list_sessions(start: int = 0, end: int = 0, class_id: int = 0, available_for: int = 0):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        if not start:
            start = db.now() - 7 * 86400
        if not end:
            end = start + 28 * 86400
        sql = (
            "SELECT s.*, c.name AS class_name, c.colour, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s JOIN classes c ON c.id=s.class_id"
            "  LEFT JOIN instructors i ON i.id=s.instructor_id"
            " WHERE s.starts_at BETWEEN ? AND ?")
        params = [start, end]
        if class_id:
            sql += " AND s.class_id = ?"
            params.append(class_id)
        if available_for:
            # Sessions this client is not already booked into.
            sql += (" AND NOT EXISTS (SELECT 1 FROM bookings b"
                    " WHERE b.session_id = s.id AND b.client_id = ?)")
            params.append(available_for)
        sql += " ORDER BY s.starts_at"
        return rows(conn.execute(sql, params))
    finally:
        conn.close()


@app.post("/api/sessions")
def create_session(body: SessionIn):
    conn = db.connect()
    try:
        cl = one(conn.execute("SELECT * FROM classes WHERE id=?", (body.class_id,)))
        if not cl:
            raise HTTPException(404, "no such class")
        cur = conn.execute(
            "INSERT INTO sessions (class_id, instructor_id, starts_at, duration_hours, notes)"
            " VALUES (?,?,?,?,?)",
            (body.class_id, body.instructor_id, body.starts_at,
             body.duration_hours or cl["duration_hours"], body.notes))
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


@app.post("/api/sessions/repeat")
def repeat_sessions(body: RepeatIn):
    conn = db.connect()
    try:
        cl = one(conn.execute("SELECT * FROM classes WHERE id=?", (body.class_id,)))
        if not cl:
            raise HTTPException(404, "no such class")
        base = datetime.fromtimestamp(body.starts_at)
        weekdays = body.weekdays or [base.weekday()]
        made = 0
        for w in range(body.weeks):
            monday = base - timedelta(days=base.weekday()) + timedelta(weeks=w)
            for wd in weekdays:
                when = monday.replace(hour=base.hour, minute=base.minute,
                                      second=0, microsecond=0) + timedelta(days=wd)
                ts = int(when.timestamp())
                if ts < body.starts_at:
                    continue
                if conn.execute("SELECT 1 FROM sessions WHERE class_id=? AND starts_at=?",
                                (body.class_id, ts)).fetchone():
                    continue
                conn.execute(
                    "INSERT INTO sessions (class_id, instructor_id, starts_at, duration_hours)"
                    " VALUES (?,?,?,?)",
                    (body.class_id, body.instructor_id, ts,
                     body.duration_hours or cl["duration_hours"]))
                made += 1
        conn.commit()
        return {"created": made}
    finally:
        conn.close()


@app.get("/api/sessions/{sid}")
def get_session(sid: int):
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        s = one(conn.execute(
            "SELECT s.*, c.name AS class_name, c.colour, i.name AS instructor_name"
            "  FROM sessions s JOIN classes c ON c.id=s.class_id"
            "  LEFT JOIN instructors i ON i.id=s.instructor_id WHERE s.id=?", (sid,)))
        if not s:
            raise HTTPException(404, "no such session")
        s["roster"] = access.session_roster(conn, sid)
        return s
    finally:
        conn.close()


@app.put("/api/sessions/{sid}")
def edit_session(sid: int, body: SessionEdit):
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
            raise HTTPException(404, "no such session")
        fields = body.model_dump(exclude_none=True)
        if not fields:
            return {"ok": True}
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), sid))
        conn.commit()
        return {"ok": True, "changed": list(fields)}
    finally:
        conn.close()


@app.post("/api/sessions/{sid}/cancel")
def cancel_the_session(sid: int):
    conn = db.connect()
    try:
        return access.cancel_session(conn, sid)
    finally:
        conn.close()


@app.put("/api/sessions/{sid}/status/{status}")
def set_session_status(sid: int, status: str):
    if status not in ("scheduled", "completed", "cancelled"):
        raise HTTPException(400, "bad status")
    conn = db.connect()
    try:
        conn.execute("UPDATE sessions SET status=? WHERE id=?", (status, sid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/sessions/{sid}")
def delete_session(sid: int, force: bool = False):
    conn = db.connect()
    try:
        used = conn.execute(
            "SELECT COUNT(*) n FROM bookings WHERE session_id=? AND status!='booked'",
            (sid,)).fetchone()["n"]
        if used and not force:
            raise HTTPException(400, f"{used} attendance record(s) — cancel it instead")
        n = conn.execute("SELECT COUNT(*) n FROM bookings WHERE session_id=?",
                         (sid,)).fetchone()["n"]
        conn.execute("DELETE FROM bookings WHERE session_id=?", (sid,))
        conn.execute("UPDATE access_events SET session_id=NULL WHERE session_id=?", (sid,))
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        conn.commit()
        return {"ok": True, "released": n}
    finally:
        conn.close()


@app.post("/api/sessions/{sid}/book")
def book_into_session(sid: int, body: BookIn):
    conn = db.connect()
    try:
        r = access.book(conn, body.client_id, sid)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@app.delete("/api/sessions/{sid}/book/{cid}")
def unbook_from_session(sid: int, cid: int):
    conn = db.connect()
    try:
        r = access.unbook(conn, cid, sid)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@app.post("/api/sessions/{sid}/status-of")
def set_attend_status(sid: int, body: StatusIn):
    conn = db.connect()
    try:
        r = access.set_status(conn, sid, body.client_id, body.status)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


@app.post("/api/clients/{cid}/move-booking/{from_sid}")
def move_booking(cid: int, from_sid: int, body: MoveIn):
    conn = db.connect()
    try:
        r = access.move_booking(conn, cid, from_sid, body.to_session_id)
        return JSONResponse(r, status_code=200 if r["ok"] else 400)
    finally:
        conn.close()


# ================================================================ access
@app.post("/api/access/verify")
def verify(body: ScanIn):
    conn = db.connect()
    try:
        return access.verify(conn, body.token)
    finally:
        conn.close()


@app.post("/api/access/lookup")
def lookup(body: LookupIn):
    conn = db.connect()
    try:
        return access.verify_by_client(conn, body.client_id)
    finally:
        conn.close()


@app.post("/api/access/checkin")
def checkin(body: EventIn):
    conn = db.connect()
    try:
        r = access.check_in(conn, body.event_id)
        return JSONResponse(r, status_code=200 if r["ok"] else 409)
    finally:
        conn.close()


@app.post("/api/access/undo")
def undo(body: EventIn):
    conn = db.connect()
    try:
        return access.undo(conn, body.event_id)
    finally:
        conn.close()


# ================================================================ dashboard
@app.get("/api/dashboard")
def dashboard():
    conn = db.connect()
    try:
        access.settle_past_sessions(conn)
        midnight, tomorrow = access.day_bounds()

        today_sessions = rows(conn.execute(
            "SELECT s.*, c.name AS class_name, c.colour, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s JOIN classes c ON c.id=s.class_id"
            "  LEFT JOIN instructors i ON i.id=s.instructor_id"
            " WHERE s.starts_at BETWEEN ? AND ? ORDER BY s.starts_at",
            (midnight, tomorrow)))

        recent = rows(conn.execute(
            "SELECT e.id, e.scanned_at, e.decision, e.reason, e.confirmed_at,"
            "       c.name_en, cl.name AS class_name"
            "  FROM access_events e LEFT JOIN clients c ON c.id=e.client_id"
            "  LEFT JOIN sessions s ON s.id=e.session_id"
            "  LEFT JOIN classes cl ON cl.id=s.class_id"
            " WHERE e.scanned_at >= ? ORDER BY e.scanned_at DESC LIMIT 60", (midnight,)))

        exp = access.expected_today(conn)
        intake = access.month_intake(conn)
        stats = {
            **{f"exp_{k}": v for k, v in exp.items()},
            **{f"mo_{k}": v for k, v in intake.items()},
            "scans_today": conn.execute(
                "SELECT COUNT(*) n FROM access_events WHERE scanned_at>=? AND source='scan'",
                (midnight,)).fetchone()["n"],
            "denied_today": conn.execute(
                "SELECT COUNT(*) n FROM access_events WHERE scanned_at>=? AND decision='deny'",
                (midnight,)).fetchone()["n"],
            "active_clients": conn.execute(
                "SELECT COUNT(*) n FROM clients WHERE active=1").fetchone()["n"],
            "classes": conn.execute(
                "SELECT COUNT(*) n FROM classes WHERE active=1").fetchone()["n"],
            "sessions_week": conn.execute(
                "SELECT COUNT(*) n FROM sessions WHERE starts_at BETWEEN ? AND ?"
                " AND status='scheduled'", (db.now(), db.now() + 7 * 86400)).fetchone()["n"],
        }

        today = date.today().isoformat()
        soon = (date.today() + timedelta(days=7)).isoformat()
        attention = []
        for r in conn.execute(
                "SELECT c.id, c.name_en, c.phone, s.id AS sub_id, s.plan, s.expires_on"
                "  FROM clients c JOIN subscriptions s ON s.client_id=c.id AND s.active=1"
                " WHERE c.active=1").fetchall():
            st = access.plan_state(conn, r["sub_id"])
            if st["frozen"]:
                continue          # deliberately paused, not a problem to chase
            if st["remaining"] <= 2 or r["expires_on"] <= soon or st["unassigned"] > 0:
                attention.append({
                    "id": r["id"], "name_en": r["name_en"], "phone": r["phone"],
                    "plan": r["plan"], "expires_on": r["expires_on"],
                    "remaining": st["remaining"], "unassigned": st["unassigned"],
                    "expired": r["expires_on"] < today,
                })
        attention.sort(key=lambda x: (x["remaining"], x["expires_on"]))

        return {"stats": stats, "today_sessions": today_sessions,
                "recent": recent, "attention": attention[:20], "today": today}
    finally:
        conn.close()


# ================================================================ static
app.mount("/photos", StaticFiles(directory="photos"), name="photos")
app.mount("/cards", StaticFiles(directory="cards"), name="cards")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/reception")
def reception():
    return FileResponse(os.path.join(STATIC_DIR, "reception.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
