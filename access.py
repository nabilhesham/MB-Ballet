"""
Access decisions and attendance.

The model in one paragraph: a client buys a plan of N sessions, and at that
moment is booked into N specific sessions. Each booking is one paid slot. A
booking is 'booked' until the session happens, then 'present' or 'absent'.
There is no third state — a slot is either used or it is not, and both present
and absent consume it, because the place was reserved either way.

Scanning is therefore a lookup rather than a decision: the card carries a
class, that class has a session today, and the client already has a booking in
it.
"""

import time
from datetime import date, datetime, timedelta, time as _t

import db
import tokens

# Only plans of this size or larger may be frozen. Short packs are meant to be
# used inside their window; freezing a 4-session pack for two months makes the
# expiry date meaningless. Change the number here if the policy changes.
FREEZE_MIN_SESSIONS = 12


# ---------------------------------------------------------------- helpers
def _log(conn, client_id, credential_id, session_id, decision, reason,
         source: str = "scan") -> int:
    cur = conn.execute(
        "INSERT INTO access_events (client_id, credential_id, session_id, scanned_at,"
        " decision, reason, source) VALUES (?,?,?,?,?,?,?)",
        (client_id, credential_id, session_id, db.now(), decision, reason, source),
    )
    conn.commit()
    return cur.lastrowid


def _deny(message, detail=None, severity="stop", code=None, **base):
    """
    A refusal. `severity` separates "something is wrong" from "nothing to do
    here" — a client scanning twice has not done anything wrong, and the kiosk
    reads a red STOP at them for it. "warn" is the amber middle: refused, but
    routine. Nothing is deducted either way.

    `code` names the refusal for the one screen that needs to act on which
    refusal it was, rather than only show it: "no_session_today" is what puts
    the manual check-in button on the kiosk.
    """
    return {**base, "granted": False, "severity": severity, "code": code,
            "message": message, "detail": detail}


def day_bounds(ts: int = None):
    """Midnight-to-midnight around a timestamp, in local time."""
    d = date.fromtimestamp(ts or db.now())
    start = int(datetime.combine(d, _t.min).timestamp())
    return start, start + 86400


def session_end(row) -> int:
    return int(row["starts_at"] + row["duration_hours"] * 3600)


def slot_conflict(conn, starts_at: int, duration_hours: float, exclude_id: int = None):
    """
    The session already occupying this slot, or None.

    One session at a time, academy-wide — the rule is on the level of the app,
    not of a class or an instructor: whatever is running, nothing else runs
    beside it. Checked here rather than at each call site so create, edit,
    repeat and un-cancel cannot drift apart on what "taken" means.

    Intervals are half-open. A session ending at 16:01 and one starting at
    16:01 do not clash, because back-to-back is how a timetable is built; only
    a real overlap does.

    A cancelled session occupies nothing and never blocks — cancelling is how
    reception frees a slot up.

    Note this deliberately says nothing about the past: a datetime that has
    already been and gone is still a slot, and entering a second session into
    it is refused the same way. History that predates the rule is left alone
    (seed.py does not call this — see CLAUDE.md), so the database can still
    hold overlaps the UI would now refuse to create.
    """
    ends_at = starts_at + duration_hours * 3600
    sql = ("SELECT s.id, s.starts_at, s.duration_hours, c.name AS class_name"
           "  FROM sessions s JOIN classes c ON c.id = s.class_id"
           " WHERE s.status != 'cancelled'"
           "   AND s.starts_at < ? AND s.starts_at + s.duration_hours * 3600 > ?")
    params = [ends_at, starts_at]
    if exclude_id is not None:
        sql += " AND s.id != ?"
        params.append(exclude_id)
    return conn.execute(sql + " ORDER BY s.starts_at LIMIT 1", params).fetchone()


def slot_taken_message(row) -> str:
    """One sentence a receptionist can act on, worded the same everywhere."""
    starts = time.localtime(row["starts_at"])
    ends = time.localtime(session_end(row))
    return (f"{time.strftime('%a %d %b', starts)} "
            f"{time.strftime('%H:%M', starts)}–{time.strftime('%H:%M', ends)} "
            f"is already taken by {row['class_name']}")


# ---------------------------------------------------------------- plans
def _iso_day(ts: int) -> str:
    """The local calendar day a unix timestamp falls on."""
    return date.fromtimestamp(ts).isoformat()


def last_session_date(conn, sub_id: int):
    """
    The date of the last session this plan is paying for, or None if it is
    paying for nothing yet.

    A plan cannot have run out before the last session it funds, so this is
    what its validity is read against. Every booking counts regardless of
    status: present and absent both spent a slot the plan paid for, a
    booking still 'booked' is a date the client has been promised, and a
    session marked cancelled still keeps its bookings (cancel_session()
    resets them to 'booked' rather than releasing them) — the slot is still
    owed, so it must still count.
    """
    t = conn.execute(
        "SELECT MAX(s.starts_at) t FROM bookings b JOIN sessions s ON s.id=b.session_id"
        " WHERE b.subscription_id=?", (sub_id,)).fetchone()["t"]
    return _iso_day(t) if t is not None else None


def last_of_sessions(conn, session_ids):
    """
    Same answer for sessions that have no bookings yet — what a plan about to
    be sold against them will be valid through.
    """
    if not session_ids:
        return None
    marks = ",".join("?" * len(session_ids))
    t = conn.execute(f"SELECT MAX(starts_at) t FROM sessions WHERE id IN ({marks})",
                     tuple(session_ids)).fetchone()["t"]
    return _iso_day(t) if t is not None else None


def refresh_expiry(conn, sub_id: int):
    """
    Rewrite a plan's end date to the last session it now pays for.

    Called from every path that changes which sessions a plan's slots point
    at — book(), unbook(), and the bulk deletes that remove bookings without
    going through either. This is what makes adding a session in July push
    the plan out to July, and removing it pull the plan back, with nobody
    editing the field by hand — and it is why a date typed by hand in
    edit_plan() stands only until the sessions move under it.

    freeze_plan() is the one deliberate exception: it deletes future bookings
    on purpose, and unfreeze_plan()'s day-shift is the date that has to
    survive until the released slots are reassigned. It must not call this.

    A plan with no bookings left keeps whatever is stored: the column is NOT
    NULL, and "no dates yet" is not the same as "expired".
    """
    covers = last_session_date(conn, sub_id)
    if covers is None:
        return None
    conn.execute("UPDATE subscriptions SET expires_on=? WHERE id=?", (covers, sub_id))
    return covers


def plan_state(conn, sub_id: int) -> dict:
    """
    Where a plan stands. Used slots are counted from the bookings rather than
    tracked in a column, so the two can never drift apart.
    """
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if sub is None:
        return {}
    c = conn.execute(
        "SELECT COUNT(*) assigned,"
        "       SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) present,"
        "       SUM(CASE WHEN status='absent'  THEN 1 ELSE 0 END) absent"
        "  FROM bookings WHERE subscription_id=?", (sub_id,)).fetchone()
    present, absent = c["present"] or 0, c["absent"] or 0
    used = present + absent
    klass = conn.execute("SELECT name, colour FROM classes WHERE id=?",
                         (sub["class_id"],)).fetchone() if sub["class_id"] else None
    allowed, why = can_freeze(sub)
    # The stored date is the answer, not a floor: refresh_expiry() rewrites it
    # whenever the plan's bookings change, so deriving it again here would
    # only be able to disagree with what an edit deliberately set.
    expires = sub["expires_on"]
    return {
        "id": sub["id"], "plan": sub["plan"],
        "class_id": sub["class_id"],
        "class_name": klass["name"] if klass else None,
        "class_colour": klass["colour"] if klass else None,
        "can_freeze": allowed, "freeze_blocked_because": why,
        "sessions_total": sub["sessions_total"],
        "assigned": c["assigned"] or 0,
        "present": present, "absent": absent, "used": used,
        "remaining": max(0, sub["sessions_total"] - used),
        "unassigned": max(0, sub["sessions_total"] - (c["assigned"] or 0)),
        "starts_on": sub["starts_on"], "expires_on": expires,
        "active": sub["active"], "price": sub["price"],
        # NULL means unpaid. Everything that shows a paid/unpaid indicator —
        # the profile, the payment history, the kiosk — reads it from here,
        # so there is one answer rather than four re-derivations.
        "paid_on": sub["paid_on"],
        "notes": sub["notes"],
        "frozen": bool(sub["frozen_on"]),
        "frozen_on": sub["frozen_on"],
        "frozen_until": sub["frozen_until"],
        "frozen_days": sub["frozen_days"] or 0,
    }


def active_plan(conn, client_id: int, class_id: int = None):
    """
    The client's live plan, for a class if one is given.

    Asking for a class returns that class's plan or nothing at all — never
    another class's. Falling back would be worse than answering "no plan":
    it would let a Ballet card spend the flexibility balance, which is the
    exact confusion one card per class exists to prevent.

    Asking without a class is only meaningful for someone taking a single
    class. It returns the soonest to expire, which is the one needing
    attention.
    """
    if class_id:
        return conn.execute(
            "SELECT * FROM subscriptions WHERE client_id=? AND class_id=? AND active=1"
            " ORDER BY expires_on DESC LIMIT 1", (client_id, class_id)).fetchone()
    return conn.execute(
        "SELECT * FROM subscriptions WHERE client_id=? AND active=1"
        " ORDER BY expires_on ASC LIMIT 1", (client_id,)).fetchone()


def active_plans(conn, client_id: int):
    """Every live plan, one per class."""
    return conn.execute(
        "SELECT s.*, c.name AS class_name, c.colour FROM subscriptions s"
        "  LEFT JOIN classes c ON c.id = s.class_id"
        " WHERE s.client_id=? AND s.active=1 ORDER BY c.name", (client_id,)).fetchall()


def can_freeze(sub) -> tuple:
    """
    (allowed, reason). Kept here so the API and the UI cannot disagree about
    it — the button is greyed out for the same reason the endpoint refuses.
    """
    if sub is None:
        return False, "no active plan"
    if sub["sessions_total"] < FREEZE_MIN_SESSIONS:
        return False, (f"only plans of {FREEZE_MIN_SESSIONS} sessions or more can be "
                       f"frozen — this one has {sub['sessions_total']}")
    if sub["frozen_on"]:
        return False, "already frozen"
    return True, ""


# ---------------------------------------------------------------- auto-absent
def settle_past_sessions(conn) -> int:
    """
    Any booking whose session has finished but was never checked in becomes
    absent. Called on startup and before anything that reads attendance, so
    what is on screen is never stale.

    Dated freezes are lifted first: a plan that came out of a freeze last week
    should have its slots settled normally, and one still frozen is skipped
    entirely so a paused client never loses a session.
    """
    lift_expired_freezes(conn)
    now = db.now()
    cur = conn.execute(
        "UPDATE bookings SET status='absent'"
        " WHERE status='booked'"
        "   AND (subscription_id IS NULL OR subscription_id NOT IN ("
        "        SELECT id FROM subscriptions WHERE frozen_on IS NOT NULL))"
        "   AND session_id IN ("
        "   SELECT id FROM sessions WHERE status != 'cancelled'"
        "      AND starts_at + duration_hours*3600 < ?)", (now,))
    conn.execute(
        "UPDATE sessions SET status='completed'"
        " WHERE status='scheduled' AND starts_at + duration_hours*3600 < ?", (now,))
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------- scanning
def _client_payload(conn, client, sub, class_id=None) -> dict:
    cid = client["id"]
    state = plan_state(conn, sub["id"]) if sub else {}

    recent = [dict(r) for r in conn.execute(
        "SELECT b.status, b.checked_in_at, s.id AS session_id, s.starts_at,"
        "       c.name AS class_name, c.colour"
        "  FROM bookings b JOIN sessions s ON s.id = b.session_id"
        "  JOIN classes c ON c.id = s.class_id"
        " WHERE b.client_id = ? AND b.status != 'booked'"
        " ORDER BY s.starts_at DESC LIMIT 4", (cid,)).fetchall()]

    # "Next class" means the next one on the card being held. A client who
    # takes Ballet and Flexibility was being shown whichever came first
    # across both, so the Ballet card could answer with a Flexibility date —
    # true, but not what was asked. Scoped to the card's class; a
    # member-number lookup names no class and still spans everything.
    nxt_params = [cid, db.now()]
    class_clause = ""
    if class_id:
        class_clause = " AND s.class_id = ?"
        nxt_params.append(class_id)
    nxt = conn.execute(
        "SELECT s.starts_at, c.name AS class_name FROM bookings b"
        "  JOIN sessions s ON s.id = b.session_id"
        "  JOIN classes c ON c.id = s.class_id"
        " WHERE b.client_id = ? AND b.status = 'booked' AND s.starts_at > ?"
        f"{class_clause} ORDER BY s.starts_at LIMIT 1", nxt_params).fetchone()

    tot = conn.execute(
        "SELECT SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) present,"
        "       SUM(CASE WHEN status='absent' THEN 1 ELSE 0 END) absent,"
        "       MAX(CASE WHEN status='present' THEN checked_in_at END) last_visit"
        "  FROM bookings WHERE client_id=?", (cid,)).fetchone()

    return {
        "phone": client["phone"], "age": client["age"], "school": client["school"],
        "plan": state.get("plan"),
        "sessions_total": state.get("sessions_total"),
        "sessions_remaining": state.get("remaining"),
        "expires_on": state.get("expires_on"),
        # Shown as a tag at reception. It never blocks a check-in — the
        # receptionist is the one who decides what to do about it.
        "paid_on": state.get("paid_on"),
        # Both kinds of note reach the desk: the client's own, and the one
        # about the plan being spent. This is the moment they are worth
        # anything — nobody looks them up afterwards.
        "client_notes": client["notes"],
        "plan_notes": state.get("notes"),
        "visits": tot["present"] or 0,
        "absences": tot["absent"] or 0,
        "last_visit": tot["last_visit"],
        "recent": recent,
        "next_session": dict(nxt) if nxt else None,
        "low_balance": state.get("remaining") is not None and 0 < state["remaining"] <= 2,
        "frozen": state.get("frozen", False),
        "frozen_until": state.get("frozen_until"),
    }


def verify(conn, raw_token: str) -> dict:
    """Read-only. Works out who this is and which session they are here for."""
    settle_past_sessions(conn)

    try:
        tokens.parse(raw_token, max_age=None)
    except tokens.TokenError as e:
        _log(conn, None, None, None, "deny", f"invalid token: {e}")
        return _deny("This code was not issued by us", detail=str(e))

    cred = conn.execute(
        "SELECT cr.*, c.name AS class_name, c.colour FROM credentials cr"
        "  LEFT JOIN classes c ON c.id = cr.class_id"
        " WHERE cr.token=?", (raw_token.strip().upper(),)).fetchone()
    if cred is None:
        return _deny("Card not recognised", detail="valid signature, no matching record")

    # Look the client up before the revoked check, not after. A replaced card
    # is one we know the owner of, and answering it with a blank panel headed
    # "Unknown card" told reception the person in front of them was a
    # stranger — every reissue leaves an older card in circulation that lands
    # here. Denials carry the profile wherever the client is known.
    client = conn.execute("SELECT * FROM clients WHERE id=?", (cred["client_id"],)).fetchone()
    if client is None:
        return _deny("Card not recognised", detail="no client behind this credential")
    known = {
        "client_id": client["id"], "credential_id": cred["id"],
        "name_en": client["name_en"], "photo_path": client["photo_path"],
        "card_class": cred["class_name"], "card_colour": cred["colour"],
    }
    if cred["revoked_at"]:
        _log(conn, cred["client_id"], cred["id"], None, "deny", "revoked")
        return _deny("This card was replaced",
                     detail="hand the client their new card", **known)
    if not client["active"]:
        return _deny("Client is not active", **known)

    # The card names a class, so the plan is that class's plan. This is what
    # stops a Ballet card drawing on a Flexibility balance.
    sub = active_plan(conn, client["id"], cred["class_id"])
    base = {**known, **_client_payload(conn, client, sub, cred["class_id"])}
    if sub is None and cred["class_id"]:
        _log(conn, client["id"], cred["id"], None, "deny", "no plan for that class")
        return _deny(f"No {cred['class_name']} plan",
                     detail="this card is for a class they are not enrolled in",
                     **base)
    return _decide(conn, client, cred, base, db.now())


def _decide(conn, client, cred, base, t):
    """
    Shared by card scans and member-number lookups.

    Finds today's booking for the class on the card. Arriving early is fine —
    a client who turns up an hour before their class is still arriving for it,
    and making reception wait for the exact start time helps nobody.
    """
    start, end = day_bounds(t)
    cid = client["id"]
    cred_id = cred["id"] if cred else None

    # Already in today? Say so and stop — a second scan must never cost a slot.
    done = conn.execute(
        "SELECT b.checked_in_at, c.name AS class_name FROM bookings b"
        "  JOIN sessions s ON s.id = b.session_id"
        "  JOIN classes c ON c.id = s.class_id"
        " WHERE b.client_id = ? AND b.status = 'present'"
        "   AND b.checked_in_at BETWEEN ? AND ?"
        " ORDER BY b.checked_in_at DESC LIMIT 1", (cid, start, end)).fetchone()
    if done:
        when = time.strftime("%H:%M", time.localtime(done["checked_in_at"]))
        _log(conn, cid, cred_id, None, "deny", "already checked in today")
        return _deny(f"Already checked in today at {when} for {done['class_name']}",
                     detail="nothing was deducted", severity="warn", **base)

    # The card's own plan: freezing the ballet plan must not turn away a
    # client arriving for the flexibility class she is paid up in.
    sub = active_plan(conn, cid, cred["class_id"] if cred else None)
    if sub and sub["frozen_on"]:
        until = sub["frozen_until"]
        when = f" until {until}" if until else ""
        _log(conn, cid, cred_id, None, "deny", "plan frozen")
        return _deny(f"This plan is frozen{when}",
                     detail="unfreeze it from their profile to let them in", **base)

    params = [cid, start, end]
    class_clause = ""
    if cred and cred["class_id"]:
        # The card names a *plan*, not a date. So match the booking this
        # class's plan paid for, whatever session it now sits on: a booking
        # moved to another class by move_booking() is still this plan's slot,
        # and this card is still what proves it. Matching on the session's
        # own class instead would turn away a client whose Ballet slot was
        # moved onto a Flexibility date — the exact case that move exists for.
        # Bookings with no plan behind them (older rows) keep the old rule.
        class_clause = (" AND (sub.class_id = ?"
                        "      OR (b.subscription_id IS NULL AND s.class_id = ?))")
        params += [cred["class_id"], cred["class_id"]]

    row = conn.execute(
        "SELECT b.id AS booking_id, b.status, s.id AS session_id, s.starts_at,"
        "       s.duration_hours, c.name AS class_name, c.colour,"
        "       i.name AS instructor_name"
        "  FROM bookings b JOIN sessions s ON s.id = b.session_id"
        "  JOIN classes c ON c.id = s.class_id"
        "  LEFT JOIN subscriptions sub ON sub.id = b.subscription_id"
        "  LEFT JOIN instructors i ON i.id = s.instructor_id"
        " WHERE b.client_id = ? AND s.starts_at BETWEEN ? AND ?"
        f"   AND s.status != 'cancelled'{class_clause}"
        " ORDER BY ABS(s.starts_at - ?) LIMIT 1", (*params, t)).fetchone()

    if row is None:
        cls = f" for {base.get('card_class')}" if base.get("card_class") else ""
        _log(conn, cid, cred_id, None, "deny", "no session today")
        return _deny(f"No session booked today{cls}",
                     detail="check their upcoming sessions on their profile",
                     code="no_session_today", **base)

    if row["status"] == "absent":
        # Their session has been and gone and they were swept absent, but here
        # they are. The slot is theirs and still unspent as far as attendance
        # goes, so it can be moved onto something else running today — the
        # same swap "no session today" offers, hence the same button.
        _log(conn, cid, cred_id, row["session_id"], "deny", "already absent")
        return _deny("Already marked absent for today's session",
                     detail="they can still be moved onto another session today",
                     code="absent_today", **base)

    mins = round((row["starts_at"] - t) / 60)
    event_id = _log(conn, cid, cred_id, row["session_id"], "allow", None)

    return {
        **base, "granted": True, "event_id": event_id,
        "booking_id": row["booking_id"], "message": "Allowed",
        "session": {
            "id": row["session_id"], "class_name": row["class_name"],
            "instructor_name": row["instructor_name"],
            "starts_at": row["starts_at"], "duration_hours": row["duration_hours"],
            "colour": row["colour"], "minutes_until": mins, "early": mins > 0,
        },
    }


def verify_by_client(conn, client_id: int) -> dict:
    """Member-number lookup. Same rules; no card, so no class filter."""
    settle_past_sessions(conn)
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if client is None:
        return _deny(f"No client with number {client_id:05d}")
    if not client["active"]:
        return _deny("Client is not active")

    sub = active_plan(conn, client_id)
    base = {
        "client_id": client["id"], "credential_id": None,
        "name_en": client["name_en"], "photo_path": client["photo_path"],
        "card_class": None, "card_colour": None,
        **_client_payload(conn, client, sub),
    }
    return _decide(conn, client, None, base, db.now())


def check_in(conn, event_id: int) -> dict:
    """Mark the booking present. Guarded so a double tap cannot double-spend."""
    ev = conn.execute("SELECT * FROM access_events WHERE id=?", (event_id,)).fetchone()
    if ev is None or ev["decision"] != "allow":
        return {"ok": False, "error": "no such granted scan"}
    if ev["confirmed_at"]:
        return {"ok": False, "error": "already checked in"}

    b = conn.execute("SELECT * FROM bookings WHERE client_id=? AND session_id=?",
                     (ev["client_id"], ev["session_id"])).fetchone()
    if b is None:
        return {"ok": False, "error": "booking no longer exists"}

    cur = conn.execute(
        "UPDATE bookings SET status='present', checked_in_at=?"
        " WHERE id=? AND status != 'present'", (db.now(), b["id"]))
    if cur.rowcount == 0:
        return {"ok": False, "error": "already marked present"}

    conn.execute("UPDATE access_events SET confirmed_at=?, session_spent=1 WHERE id=?",
                 (db.now(), event_id))
    conn.commit()
    state = plan_state(conn, b["subscription_id"]) if b["subscription_id"] else {}
    return {"ok": True, "sessions_remaining": state.get("remaining")}


def swap_options(conn, client_id: int) -> dict:
    """
    What reception can offer someone standing at the desk with nothing booked
    today: every session running today, and every slot of their own they
    could give up for one.

    A giveable slot is a date still ahead of them, or one they were already
    marked absent for — a paid slot they lost. Each is tagged so the screen
    can say which is which, because giving up a future date and reclaiming a
    missed one are different decisions.
    """
    settle_past_sessions(conn)
    start, end = day_bounds()
    now = db.now()

    today = [dict(r) for r in conn.execute(
        "SELECT s.id, s.starts_at, s.duration_hours, c.name AS class_name, c.colour,"
        "       i.name AS instructor_name,"
        "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id = s.id) AS booked"
        "  FROM sessions s JOIN classes c ON c.id = s.class_id"
        "  LEFT JOIN instructors i ON i.id = s.instructor_id"
        " WHERE s.starts_at BETWEEN ? AND ? AND s.status != 'cancelled'"
        "   AND NOT EXISTS (SELECT 1 FROM bookings b"
        "                    WHERE b.session_id = s.id AND b.client_id = ?)"
        " ORDER BY s.starts_at", (start, end, client_id)).fetchall()]

    slots = [dict(r) for r in conn.execute(
        "SELECT b.session_id, b.status, s.starts_at, c.name AS class_name, c.colour,"
        "       sub.plan AS plan_name, pc.name AS plan_class"
        "  FROM bookings b JOIN sessions s ON s.id = b.session_id"
        "  JOIN classes c ON c.id = s.class_id"
        "  LEFT JOIN subscriptions sub ON sub.id = b.subscription_id"
        "  LEFT JOIN classes pc ON pc.id = sub.class_id"
        " WHERE b.client_id = ? AND s.status != 'cancelled'"
        "   AND ((b.status = 'booked' AND s.starts_at > ?) OR b.status = 'absent')",
        (client_id, now)).fetchall()]
    for s in slots:
        s["tag"] = "upcoming" if s["status"] == "booked" else "absent"
    # Dates still ahead first, soonest first — the slot reception gives up by
    # default. Missed ones after, most recent first, since an absence from
    # last week is likelier to be the one being reclaimed than one from May.
    slots.sort(key=lambda s: (s["tag"] != "upcoming",
                              s["starts_at"] if s["tag"] == "upcoming" else -s["starts_at"]))
    return {"today": today, "slots": slots}


def swap_and_check_in(conn, client_id: int, from_session: int, to_session: int,
                      credential_id: int = None) -> dict:
    """
    Give up one of the client's own slots for a session running today, and
    check them in to it.

    Deliberately built out of the ordinary pieces: move_booking() to change
    the date, then the same _log()/check_in() pair a scan goes through. That
    is what makes the 60-second Undo work here exactly as it does for a
    normal scan, and keeps the day's check-in count honest.
    """
    moved = move_booking(conn, client_id, from_session, to_session,
                         allow_other_class=True)
    if not moved["ok"]:
        return moved
    event_id = _log(conn, client_id, credential_id, to_session, "allow",
                    "manual swap", source="manual")
    r = check_in(conn, event_id)
    r["event_id"] = event_id
    return r


def undo(conn, event_id: int) -> dict:
    ev = conn.execute("SELECT * FROM access_events WHERE id=?", (event_id,)).fetchone()
    if not ev or not ev["session_spent"]:
        return {"ok": False, "error": "nothing to undo"}
    if db.now() - ev["confirmed_at"] > 120:
        return {"ok": False, "error": "undo window closed — change it from the session page"}
    conn.execute("UPDATE bookings SET status='booked', checked_in_at=NULL"
                 " WHERE client_id=? AND session_id=?", (ev["client_id"], ev["session_id"]))
    conn.execute("UPDATE access_events SET confirmed_at=NULL, session_spent=0 WHERE id=?",
                 (event_id,))
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- attendance
def set_status(conn, session_id: int, client_id: int, status: str) -> dict:
    """Present or absent. Both consume the slot; the difference is the record."""
    if status not in ("present", "absent", "booked"):
        return {"ok": False, "error": "status must be present or absent"}
    b = conn.execute("SELECT * FROM bookings WHERE session_id=? AND client_id=?",
                     (session_id, client_id)).fetchone()
    if b is None:
        return {"ok": False, "error": "this client is not booked into this session"}
    conn.execute("UPDATE bookings SET status=?, checked_in_at=? WHERE id=?",
                 (status, db.now() if status == "present" else None, b["id"]))
    conn.commit()
    return {"ok": True, "status": status}


def book(conn, client_id: int, session_id: int, subscription_id: int = None,
         allow_other_class: bool = False) -> dict:
    if conn.execute("SELECT 1 FROM bookings WHERE client_id=? AND session_id=?",
                    (client_id, session_id)).fetchone():
        return {"ok": False, "error": "already booked into this session"}
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if s is None:
        return {"ok": False, "error": "no such session"}
    klass = conn.execute("SELECT name FROM classes WHERE id=?", (s["class_id"],)).fetchone()
    cname = klass["name"] if klass else "this class"

    if subscription_id is None:
        # Spend the plan bought for this class, not whichever one runs longest.
        sub = active_plan(conn, client_id, s["class_id"])
        if sub is None:
            # No plan for this class means no slot to spend, and a booking
            # with no plan behind it is a session nobody paid for. This used
            # to fall through and insert one with subscription_id NULL —
            # which is how a client ended up in a class they were not
            # enrolled in. A session can only be booked against the plan
            # that pays for its class.
            return {"ok": False,
                    "error": f"no active {cname} plan — add one for that class "
                             f"before booking them into this session"}
        subscription_id = sub["id"]

    # A booking spends one of the plan's paid slots, whether that plan was
    # named here or just resolved above. A slot spent twice is a session
    # nobody paid for, so this is checked no matter which caller asked.
    sub_row = conn.execute(
        "SELECT sessions_total, frozen_on, class_id FROM subscriptions WHERE id=?",
        (subscription_id,)).fetchone()
    if sub_row is None:
        return {"ok": False, "error": "no such plan"}
    if sub_row["class_id"] != s["class_id"] and not allow_other_class:
        # Selling and topping up a plan stay class-locked. Only the
        # after-the-fact corrections on the client profile pass
        # allow_other_class, and they must name the plan explicitly — there
        # is no plan in this session's class for active_plan() to find.
        return {"ok": False, "error": f"that plan is not a {cname} plan"}
    if sub_row["frozen_on"]:
        # Freezing is what released this slot in the first place; it is
        # not available again until the plan is unfrozen.
        return {"ok": False, "error": "this plan is frozen"}
    used = conn.execute(
        "SELECT COUNT(*) n FROM bookings WHERE subscription_id=?",
        (subscription_id,)).fetchone()["n"]
    if used >= sub_row["sessions_total"]:
        return {"ok": False,
                "error": f"every session on their {cname} plan is already "
                         f"assigned — no free slot to book this one against"}

    conn.execute(
        "INSERT INTO bookings (client_id, session_id, subscription_id, status, created_at)"
        " VALUES (?,?,?,?,?)",
        (client_id, session_id, subscription_id,
         "absent" if session_end(s) < db.now() else "booked", db.now()))
    if subscription_id is not None:
        refresh_expiry(conn, subscription_id)
    conn.commit()
    return {"ok": True}


def unbook(conn, client_id: int, session_id: int) -> dict:
    b = conn.execute("SELECT * FROM bookings WHERE client_id=? AND session_id=?",
                     (client_id, session_id)).fetchone()
    if b is None:
        return {"ok": False, "error": "not booked"}
    conn.execute("DELETE FROM bookings WHERE id=?", (b["id"],))
    if b["subscription_id"] is not None:
        refresh_expiry(conn, b["subscription_id"])
    conn.commit()
    return {"ok": True}


def move_booking(conn, client_id: int, from_session: int, to_session: int,
                 allow_other_class: bool = False, status: str = None) -> dict:
    """
    Point an existing booking at a different session.

    The booking keeps the plan that paid for it — only the date it sits on
    changes. `allow_other_class` lets that date belong to another class,
    which is how reception records "she missed Ballet on Tuesday but came to
    Flexibility on Wednesday instead". The slot is still the Ballet plan's,
    and the Ballet card is still what opens the door for it (see _decide,
    which matches on the plan's class rather than the session's).

    Selling a plan stays class-locked — add_plan() and edit_plan() are
    untouched. This is a correction made after the fact, not a way to buy
    one class and spend it on another.

    `status` writes the final attendance state in the same breath, so
    "mark present on the session they actually attended" is one transaction
    rather than a move that settle_past_sessions() could flip to absent
    before the status lands.
    """
    b = conn.execute("SELECT * FROM bookings WHERE client_id=? AND session_id=?",
                     (client_id, from_session)).fetchone()
    if b is None:
        return {"ok": False, "error": "not booked into that session"}
    if conn.execute("SELECT 1 FROM bookings WHERE client_id=? AND session_id=?",
                    (client_id, to_session)).fetchone():
        return {"ok": False, "error": "already booked into the target session"}

    src = conn.execute("SELECT class_id FROM sessions WHERE id=?", (from_session,)).fetchone()
    dst = conn.execute("SELECT class_id FROM sessions WHERE id=?", (to_session,)).fetchone()
    if dst is None:
        return {"ok": False, "error": "no such session"}
    if src["class_id"] != dst["class_id"] and not allow_other_class:
        return {"ok": False, "error": "can only move within the same class"}

    if status is not None and status not in ("present", "absent", "booked"):
        return {"ok": False, "error": "status must be present, absent or booked"}
    new_status = status or "booked"
    conn.execute("UPDATE bookings SET session_id=?, status=?, checked_in_at=? WHERE id=?",
                 (to_session, new_status,
                  db.now() if new_status == "present" else None, b["id"]))
    # Moving a booking to a different date can move the plan's last session
    # too — earlier or later — so it needs the same refresh book()/unbook() do.
    if b["subscription_id"] is not None:
        refresh_expiry(conn, b["subscription_id"])
    conn.commit()
    return {"ok": True}


def session_roster(conn, session_id: int) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT b.id AS booking_id, b.status, b.checked_in_at,"
        "       cl.id, cl.name_en, cl.phone, cl.photo_path"
        "  FROM bookings b JOIN clients cl ON cl.id = b.client_id"
        " WHERE b.session_id = ? ORDER BY cl.name_en", (session_id,)).fetchall()]


def cancel_session(conn, session_id: int) -> dict:
    """A class the studio is not running. Every slot goes back to the clients."""
    n = conn.execute(
        "UPDATE bookings SET status='booked', checked_in_at=NULL WHERE session_id=?",
        (session_id,)).rowcount
    conn.execute("UPDATE sessions SET status='cancelled' WHERE id=?", (session_id,))
    conn.commit()
    return {"ok": True, "released": n}


def edit_plan(conn, sub_id: int, plan: str = None, sessions_total: int = None,
             expires_on: str = None, session_ids: list = None,
             paid_on: str = None, clear_paid_on: bool = False,
             notes: str = None, class_id: int = None) -> dict:
    """
    Change a plan's name, size, sessions, class or end date after it has been
    sold.

    Frozen plans are refused outright: freezing already owns this plan's
    bookings and its expiry (see freeze_plan()'s comment on why it skips
    refresh_expiry()), and editing underneath a freeze would fight
    unfreeze_plan()'s day-shift.

    Changing sessions_total requires session_ids too — the picker always
    accompanies the count, the same contract add_plan() uses, so the two can
    never drift out of step with each other. When session_ids is given it is
    the plan's *complete* new set of dates, not a delta: every slot assigned
    up front, same as add_plan(). Sessions already present/absent are
    attendance history and can never be dropped; only 'booked' ones may be
    removed.

    A typed expires_on is a deliberate override and is written as given;
    leaving it out re-derives the date from whatever sessions the plan holds
    after this edit, via refresh_expiry() — so an edit that only adds or
    drops sessions moves the date automatically, in either direction.

    clear_paid_on marks the plan unpaid again. It exists because the route
    drops None fields before calling this, so paid_on=None cannot mean
    "erase it" — the same reason edit_session() carries clear_instructor.

    Moving a plan to another class is a correction of "this was written down
    against the wrong class". It carries its slots with it: the new class's
    sessions have to be picked in the same call, exactly as changing the
    count does, and the old class's *upcoming* dates are dropped.

    What it never touches is attendance. A session already present or absent
    stays on the plan exactly as it was, still pointing at the old class's
    date, protected by the same rule that stops any edit dropping one — so a
    client who attended four Grade 6 sessions before the plan was corrected
    keeps those four, and only what is still ahead of them moves. The plan's
    own class is what a card proves, not the session's (see _decide()), so
    the new class's card finds those older slots too.

    The one refusal left is a client who already has a live plan in the class
    it is moving to: one plan per class per client is what makes
    active_plan() answer at all, so renew that one instead of ending up with
    two. The card for the class it left proves a plan that is no longer
    there, so it is revoked unless another live plan holds that class up —
    issuing the new class's card is the caller's next step.
    """
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if sub is None:
        return {"ok": False, "error": "no such plan"}
    if sub["frozen_on"]:
        return {"ok": False, "error": "unfreeze this plan before editing it"}
    if sessions_total is not None and sessions_total < 1:
        return {"ok": False, "error": "a plan needs at least one session"}
    if sessions_total is not None and session_ids is None:
        return {"ok": False, "error": "changing the number of sessions means reassigning them"}

    moving = class_id is not None and class_id != sub["class_id"]
    if moving:
        klass = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if klass is None:
            return {"ok": False, "error": "no such class"}
        if session_ids is None:
            return {"ok": False, "error": "changing the class means reassigning the sessions"}
        held = conn.execute(
            "SELECT COUNT(*) n FROM subscriptions"
            " WHERE client_id=? AND class_id=? AND active=1 AND id!=?",
            (sub["client_id"], class_id, sub_id)).fetchone()["n"]
        if held:
            return {"ok": False,
                    "error": f"this client already has a live {klass['name']} plan"
                             " — renew that one instead"}

    current = conn.execute(
        "SELECT session_id, status FROM bookings WHERE subscription_id=?",
        (sub_id,)).fetchall()
    current_ids = {b["session_id"] for b in current}
    attended_ids = {b["session_id"] for b in current if b["status"] != "booked"}

    total = sessions_total if sessions_total is not None else sub["sessions_total"]
    target_class = class_id if moving else sub["class_id"]

    if session_ids is not None:
        if len(set(session_ids)) != len(session_ids):
            return {"ok": False, "error": "the same session was chosen twice"}
        if len(session_ids) != total:
            return {"ok": False,
                    "error": f"assign all {total} sessions ({len(session_ids)} chosen)"}
        wanted = set(session_ids)
        if not attended_ids <= wanted:
            return {"ok": False, "error": "an already-attended session cannot be removed"}

        new_ids = wanted - current_ids
        if new_ids:
            marks = ",".join("?" * len(new_ids))
            wrong = conn.execute(
                f"SELECT COUNT(*) n FROM sessions WHERE id IN ({marks}) AND class_id != ?",
                (*new_ids, target_class)).fetchone()["n"]
            if wrong:
                return {"ok": False,
                        "error": f"{wrong} of the chosen sessions are not this plan's class"}
            clash = conn.execute(
                f"SELECT COUNT(*) n FROM bookings WHERE client_id=? AND session_id IN ({marks})",
                (sub["client_id"], *new_ids)).fetchone()["n"]
            if clash:
                return {"ok": False, "error": "already booked into one of those sessions"}

        to_drop = current_ids - wanted
        if to_drop:
            marks = ",".join("?" * len(to_drop))
            conn.execute(
                f"DELETE FROM bookings WHERE subscription_id=? AND session_id IN ({marks})",
                (sub_id, *to_drop))
        for sid in new_ids:
            s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            status = "absent" if s and session_end(s) < db.now() else "booked"
            conn.execute(
                "INSERT INTO bookings (client_id, session_id, subscription_id, status,"
                " created_at) VALUES (?,?,?,?,?)",
                (sub["client_id"], sid, sub_id, status, db.now()))

    fields = {}
    if moving:
        fields["class_id"] = class_id
    if plan is not None:
        fields["plan"] = plan
    if sessions_total is not None:
        fields["sessions_total"] = sessions_total
    if clear_paid_on:
        fields["paid_on"] = None
    elif paid_on is not None:
        fields["paid_on"] = paid_on
    if notes is not None:
        # "" is a real value here — it clears the note — so this checks for
        # None rather than falsiness, unlike paid_on which needs its own flag
        # because the route drops None before we ever see it.
        fields["notes"] = notes.strip() or None
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE subscriptions SET {sets} WHERE id=?",
                     (*fields.values(), sub_id))

    if expires_on:
        conn.execute("UPDATE subscriptions SET expires_on=? WHERE id=?",
                     (expires_on, sub_id))
    else:
        refresh_expiry(conn, sub_id)

    revoked = 0
    if moving:
        # The old class's card proved this plan. Revoke it unless another live
        # plan still stands behind that class — credentials are revoked, never
        # deleted, so the log keeps pointing at the one that was used.
        still = conn.execute(
            "SELECT 1 FROM subscriptions WHERE client_id=? AND class_id=? AND active=1",
            (sub["client_id"], sub["class_id"])).fetchone()
        if not still:
            revoked = conn.execute(
                "UPDATE credentials SET revoked_at=? WHERE client_id=? AND class_id=?"
                "   AND revoked_at IS NULL",
                (db.now(), sub["client_id"], sub["class_id"])).rowcount

    conn.commit()
    return {"ok": True, "moved": moving, "cards_revoked": revoked,
            **plan_state(conn, sub_id)}


def expected_today(conn) -> dict:
    settle_past_sessions(conn)
    start, end = day_bounds()
    r = conn.execute(
        "SELECT COUNT(*) expected,"
        "       SUM(CASE WHEN b.status='present' THEN 1 ELSE 0 END) arrived,"
        "       SUM(CASE WHEN b.status='absent'  THEN 1 ELSE 0 END) absent"
        "  FROM bookings b JOIN sessions s ON s.id = b.session_id"
        " WHERE s.starts_at BETWEEN ? AND ? AND s.status != 'cancelled'",
        (start, end)).fetchone()
    expected, arrived, absent = r["expected"] or 0, r["arrived"] or 0, r["absent"] or 0
    return {"expected": expected, "arrived": arrived, "absent": absent,
            "still_due": max(0, expected - arrived - absent)}


def month_of(when: date = None) -> str:
    """The "YYYY-MM" a date falls in. Today's, unless told otherwise."""
    return (when or date.today()).strftime("%Y-%m")


def prev_month(month: str) -> str:
    first = date.fromisoformat(month + "-01")
    return (first - timedelta(days=1)).strftime("%Y-%m")


def month_bounds(when: date = None) -> tuple:
    """The first and last day of a date's calendar month, both ISO. Today's
    month unless told otherwise -- the instructor view's default period."""
    d = when or date.today()
    first = d.replace(day=1)
    next_first = (first.replace(year=d.year + 1, month=1) if d.month == 12
                  else first.replace(month=d.month + 1))
    last = next_first - timedelta(days=1)
    return first.isoformat(), last.isoformat()


def date_range_ts(period_from: str, period_to: str) -> tuple:
    """An inclusive [from, to] ISO-date pair as a half-open unix timestamp
    range, for filtering a starts_at column against a picked date range."""
    start = int(datetime.combine(date.fromisoformat(period_from), _t.min).timestamp())
    end = int(datetime.combine(date.fromisoformat(period_to), _t.min).timestamp()) + 86400
    return start, end


def logged_hours(conn, instructor_id: int, period_from: str, period_to: str) -> dict:
    """
    Hours the salary sheet recorded for this instructor within a period, plus
    any manual corrections layered on top (see instructor_hour_adjustments) --
    never mixed into the sheet's own rows, so what the sheet actually said
    stays visible. `days`/`from`/`to` count only real salary-sheet rows; a
    correction is not a claim of an extra day worked.
    """
    sheet = conn.execute(
        "SELECT COALESCE(SUM(hours),0) h, COUNT(*) days, MIN(work_date) a, MAX(work_date) b"
        " FROM instructor_hours WHERE instructor_id=? AND work_date BETWEEN ? AND ?",
        (instructor_id, period_from, period_to)).fetchone()
    rate_row = conn.execute("SELECT hourly_rate FROM instructors WHERE id=?",
                            (instructor_id,)).fetchone()
    rate = (rate_row["hourly_rate"] or 0) if rate_row else 0
    hours = round(sheet["h"] or 0, 2)
    return {
        "hours": hours, "days": sheet["days"], "from": sheet["a"], "to": sheet["b"],
        "pay": round(hours * rate, 2),
    }


def taught_hours(conn, instructor_id: int, period_from: str, period_to: str) -> dict:
    """
    Hours actually taught in a period: what the timetable says, plus any
    manual corrections (`instructor_hour_adjustments`).

    This is the figure reception edits and the one pay is worked out from —
    an instructor who stayed an extra hour taught it whether or not a session
    row says so. The corrections used to be layered onto the salary sheet's
    total instead; they belong here, and only one of the two figures may
    carry them or a single correction would be counted twice.

    `scheduled` and `adjustment` are returned apart from their sum so the
    screen can show what was corrected rather than a number that silently
    disagrees with the sessions listed beneath it.
    """
    start_ts, end_ts = date_range_ts(period_from, period_to)
    t = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
        " WHERE instructor_id=? AND status='completed'"
        "   AND starts_at >= ? AND starts_at < ?",
        (instructor_id, start_ts, end_ts)).fetchone()
    adj = conn.execute(
        "SELECT COALESCE(SUM(delta_hours),0) d FROM instructor_hour_adjustments"
        " WHERE instructor_id=? AND adjustment_date BETWEEN ? AND ?",
        (instructor_id, period_from, period_to)).fetchone()
    scheduled = round(t["h"] or 0, 2)
    adjustment = round(adj["d"] or 0, 2)
    return {"sessions": t["n"], "scheduled": scheduled, "adjustment": adjustment,
            "hours": round(scheduled + adjustment, 2)}


def adjust_taught_hours(conn, instructor_id: int, day: str,
                        new_total: float, note: str = None) -> dict:
    """
    Reception's "edit the hours taught" action, for **one day**.

    A day, not a range, because a correction belongs to the day it happened
    on: dated that way the deltas accumulate into a real daily history, and
    any wider range that contains the day picks it up by summing. Spread
    across a month there would be no telling which day the extra hour was.

    Recorded as one new dated row rather than by rewriting a session's
    duration or a salary-sheet row, so the correction stays its own auditable
    fact and what the timetable and the sheet actually said stays visible.
    """
    current = taught_hours(conn, instructor_id, day, day)
    delta = round(new_total - current["hours"], 2)
    conn.execute(
        "INSERT INTO instructor_hour_adjustments (instructor_id, adjustment_date, delta_hours,"
        " note, created_at) VALUES (?,?,?,?,?)",
        (instructor_id, day, delta, note, db.now()))
    conn.commit()
    return taught_hours(conn, instructor_id, day, day)


def month_intake(conn, month: str = None) -> dict:
    """
    Who joined this month and what they paid.

    Two numbers reception actually asks for at the end of a month, and they
    are not the same question:

      *New clients* are counted on `joined_on` — the date of their first
      payment, which is when they became a client.

      *Their revenue* is the plans those same people bought this month. A
      returning client renewing is real money too, so the month's whole
      intake is reported alongside it rather than instead of it; the pair is
      what tells you whether growth came from new faces or from the regulars.

    Plans whose price nobody wrote down are counted separately, never as
    zero. The roster sheets record "package", "free" and "yes" as often as an
    amount, and a month's takings reported as a clean total while half its
    plans carry no figure at all is a lie the shape of a fact.
    """
    month = month or month_of()
    like = month + "-%"

    new_clients = conn.execute(
        "SELECT COUNT(*) n FROM clients WHERE active=1 AND joined_on LIKE ?",
        (like,)).fetchone()["n"]
    before = conn.execute(
        "SELECT COUNT(*) n FROM clients WHERE active=1 AND joined_on LIKE ?",
        (prev_month(month) + "-%",)).fetchone()["n"]

    def takings(only_new: bool) -> tuple:
        joined = " AND c.joined_on LIKE ?" if only_new else ""
        args = (like, like) if only_new else (like,)
        r = conn.execute(
            "SELECT COALESCE(SUM(s.price),0) paid,"
            "       SUM(CASE WHEN s.price IS NULL THEN 1 ELSE 0 END) unpriced,"
            "       COUNT(*) plans"
            "  FROM subscriptions s JOIN clients c ON c.id=s.client_id"
            " WHERE c.active=1 AND s.starts_on LIKE ?" + joined, args).fetchone()
        return r["paid"] or 0, r["unpriced"] or 0, r["plans"] or 0

    new_paid, new_unpriced, new_plans = takings(True)
    all_paid, all_unpriced, all_plans = takings(False)

    return {
        "month": month,
        "new_clients": new_clients,
        "new_clients_prev": before,
        "new_revenue": round(new_paid, 2),
        "new_plans": new_plans,
        "new_unpriced": new_unpriced,
        "revenue": round(all_paid, 2),
        "plans": all_plans,
        "unpriced": all_unpriced,
    }


# ======================================================================
# Freezing a plan
#
# A client goes away for a month and asks to pause. Three things have to
# happen, and missing any one of them makes the freeze worthless:
#
#   1. The sessions they are booked into during the pause are released. If
#      they stayed booked, settle_past_sessions() would mark every one absent
#      and the client would come back to a plan with nothing left in it.
#   2. Those slots go back to unassigned, so on return they are reassigned to
#      real dates through the normal picker.
#   3. The expiry date moves out by the number of days paused. They paid for
#      a window of validity, and the pause should not eat it.
#
# A freeze either has an end date or runs until someone lifts it. The dated
# kind lifts itself the first time anything reads the plan afterwards.
# ======================================================================

def _days_between(a: str, b: str) -> int:
    return max(0, (date.fromisoformat(b) - date.fromisoformat(a)).days)


def _shift_date(iso: str, days: int) -> str:
    from datetime import timedelta
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def freeze_plan(conn, sub_id: int, until: str = None, reason: str = None,
                from_date: str = None) -> dict:
    """
    Pause a plan. `until` may be None, meaning it stays frozen until lifted.
    Returns how many booked sessions were released.
    """
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if sub is None:
        return {"ok": False, "error": "no such plan"}
    if not sub["active"]:
        return {"ok": False, "error": "this plan is not active"}
    allowed, why = can_freeze(sub)
    if not allowed:
        return {"ok": False, "error": why}

    start = from_date or date.today().isoformat()
    if until and until <= start:
        return {"ok": False, "error": "the end of the freeze must be after it starts"}

    # Release future bookings inside the freeze. Anything already marked
    # present or absent is history and stays untouched.
    #
    # Deliberately does NOT call refresh_expiry() after this delete, unlike
    # book()/unbook()/move_booking(): the expiry needs to stay put at whatever
    # it already was so unfreeze_plan()'s day-shift has a real date to shift
    # from, not one that just collapsed back to an earlier remaining session.
    cutoff_from = int(datetime.combine(date.fromisoformat(start), _t.min).timestamp())
    params = [sub_id, cutoff_from]
    window = ""
    if until:
        window = " AND s.starts_at < ?"
        params.append(int(datetime.combine(date.fromisoformat(until), _t.min).timestamp()))

    doomed = conn.execute(
        "SELECT b.id FROM bookings b JOIN sessions s ON s.id = b.session_id"
        " WHERE b.subscription_id = ? AND b.status = 'booked'"
        f"   AND s.starts_at >= ?{window}", params).fetchall()
    released = len(doomed)
    if released:
        conn.execute(
            f"DELETE FROM bookings WHERE id IN ({','.join('?' * released)})",
            [r["id"] for r in doomed])

    conn.execute("UPDATE subscriptions SET frozen_on=?, frozen_until=? WHERE id=?",
                 (start, until, sub_id))
    conn.execute(
        "INSERT INTO freezes (subscription_id, from_date, until_date, released, reason,"
        " created_at) VALUES (?,?,?,?,?,?)",
        (sub_id, start, until, released, reason, db.now()))
    conn.commit()
    return {"ok": True, "released": released, "frozen_on": start, "frozen_until": until}


def unfreeze_plan(conn, sub_id: int, on_date: str = None) -> dict:
    """
    Lift a freeze and push the expiry out by however long it lasted. The
    released slots are already unassigned, so the client profile will show them
    as needing dates.
    """
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if sub is None:
        return {"ok": False, "error": "no such plan"}
    if not sub["frozen_on"]:
        return {"ok": False, "error": "this plan is not frozen"}

    ended = on_date or date.today().isoformat()
    if ended < sub["frozen_on"]:
        ended = sub["frozen_on"]
    days = _days_between(sub["frozen_on"], ended)

    conn.execute(
        "UPDATE subscriptions SET frozen_on=NULL, frozen_until=NULL,"
        " frozen_days = frozen_days + ?, expires_on = ? WHERE id=?",
        (days, _shift_date(sub["expires_on"], days), sub_id))
    conn.execute(
        "UPDATE freezes SET ended_on=?, days_added=? WHERE subscription_id=? AND ended_on IS NULL",
        (ended, days, sub_id))
    conn.commit()

    state = plan_state(conn, sub_id)
    return {"ok": True, "days": days, "expires_on": state["expires_on"],
            "unassigned": state["unassigned"]}


def lift_expired_freezes(conn) -> int:
    """
    A freeze with an end date lifts itself. Called wherever plans are read, so
    a laptop left off over the whole freeze still comes back correct.
    """
    today = date.today().isoformat()
    due = conn.execute(
        "SELECT id, frozen_until FROM subscriptions"
        " WHERE frozen_on IS NOT NULL AND frozen_until IS NOT NULL AND frozen_until <= ?",
        (today,)).fetchall()
    for row in due:
        unfreeze_plan(conn, row["id"], on_date=row["frozen_until"])
    return len(due)
