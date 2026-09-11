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
def _log(repo, client_id, credential_id, session_id, decision, reason,
         source: str = "scan") -> int:
    with repo.begin():
        return repo.insert("access_events", {
            "client_id": client_id, "credential_id": credential_id,
            "session_id": session_id, "scanned_at": db.now(),
            "decision": decision, "reason": reason, "source": source})


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
    """
    When an already-fetched session row finishes.

    Derived from the row rather than read from `ends_at` on purpose: this is
    handed rows built by hand in places that have no such column, and the two
    are the same number by construction — migrate() repairs any row where
    they are not.
    """
    return ends_at_of(row["starts_at"], row["duration_hours"])


def ends_at_of(starts_at: int, duration_hours: float) -> int:
    """
    When a session starting then and lasting that long finishes.

    The single writer of `sessions.ends_at`. Every path that sets `starts_at`
    or `duration_hours` must set the column from this — create_session,
    edit_session, repeat_sessions and seed.py are the four — so the stored
    value can never disagree with the two it is derived from.
    """
    return int(starts_at + duration_hours * 3600)


def slot_conflict(repo, starts_at: int, duration_hours: float, exclude_id: int = None):
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
    return repo.slot_conflict(starts_at, ends_at_of(starts_at, duration_hours),
                              exclude_id=exclude_id)


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


def last_session_date(repo, sub_id: int):
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
    t = repo.last_session_ts(sub_id)
    return _iso_day(t) if t is not None else None


def last_of_sessions(repo, session_ids):
    """
    Same answer for sessions that have no bookings yet — what a plan about to
    be sold against them will be valid through.
    """
    if not session_ids:
        return None
    t = repo.max_starts_at(list(session_ids))
    return _iso_day(t) if t is not None else None


def refresh_expiry(repo, sub_id: int):
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
    covers = last_session_date(repo, sub_id)
    if covers is None:
        return None
    repo.update("subscriptions", sub_id, {"expires_on": covers})
    return covers


def plan_state(repo, sub_id: int) -> dict:
    """
    Where a plan stands. Used slots are counted from the bookings rather than
    tracked in a column, so the two can never drift apart.
    """
    return plan_states(repo, [sub_id]).get(sub_id, {})


def plan_states(repo, sub_ids) -> dict:
    """
    The same for many plans, in three queries rather than three per plan.

    The dashboard's attention list and the clients list both ask this of
    every active client. On a local SQLite file the N+1 is free; against a
    networked backend it is three round trips per client, which on a few
    hundred of them is the whole page. There is one implementation and the
    single-plan case goes through it, so the two cannot drift.
    """
    sub_ids = [s for s in dict.fromkeys(sub_ids) if s is not None]
    if not sub_ids:
        return {}
    subs = {s["id"]: s for s in repo.find("subscriptions", {"id": {"in": sub_ids}})}
    counts = repo.plan_counts_bulk(list(subs))
    class_ids = sorted({s["class_id"] for s in subs.values() if s["class_id"]})
    classes = {c["id"]: c for c in repo.find("classes", {"id": {"in": class_ids}})}

    out = {}
    for sid, sub in subs.items():
        c = counts[sid]
        present, absent, assigned = c["present"], c["absent"], c["assigned"]
        used = present + absent
        klass = classes.get(sub["class_id"])
        allowed, why = can_freeze(sub)
        out[sid] = {
            "id": sub["id"], "plan": sub["plan"],
            "class_id": sub["class_id"],
            "class_name": klass["name"] if klass else None,
            "class_colour": klass["colour"] if klass else None,
            "can_freeze": allowed, "freeze_blocked_because": why,
            "sessions_total": sub["sessions_total"],
            "assigned": assigned,
            "present": present, "absent": absent, "used": used,
            "remaining": max(0, sub["sessions_total"] - used),
            "unassigned": max(0, sub["sessions_total"] - assigned),
            "starts_on": sub["starts_on"],
            # The stored date is the answer, not a floor: refresh_expiry()
            # rewrites it whenever the plan's bookings change, so deriving it
            # again here could only disagree with what an edit deliberately set.
            "expires_on": sub["expires_on"],
            "active": sub["active"], "price": sub["price"],
            # NULL means unpaid. Everything that shows a paid/unpaid indicator
            # -- the profile, the payment history, the kiosk -- reads it from
            # here, so there is one answer rather than four re-derivations.
            "paid_on": sub["paid_on"],
            "notes": sub["notes"],
            "frozen": bool(sub["frozen_on"]),
            "frozen_on": sub["frozen_on"],
            "frozen_until": sub["frozen_until"],
            "frozen_days": sub["frozen_days"] or 0,
        }
    return out


def active_plan(repo, client_id: int, class_id: int = None):
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
        return repo.find_one(
            "subscriptions",
            {"client_id": client_id, "class_id": class_id, "active": 1},
            sort=[("expires_on", -1)])
    return repo.find_one("subscriptions", {"client_id": client_id, "active": 1},
                         sort=[("expires_on", 1)])


def active_plans(repo, client_id: int):
    """Every live plan, one per class."""
    plans = repo.find("subscriptions", {"client_id": client_id, "active": 1})
    # Joined in Python rather than in the query: two round trips whatever the
    # backend, and no $lookup to read. There is never more than a handful.
    classes = {c["id"]: c for c in repo.find(
        "classes", {"id": {"in": sorted({p["class_id"] for p in plans
                                         if p["class_id"]})}})}
    for pl in plans:
        k = classes.get(pl["class_id"])
        pl["class_name"] = k["name"] if k else None
        pl["colour"] = k["colour"] if k else None
    plans.sort(key=lambda pl: (pl["class_name"] or "", pl["id"]))
    return plans


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
def settle_past_sessions(repo) -> int:
    """
    Any booking whose session has finished but was never checked in becomes
    absent. Called on startup and before anything that reads attendance, so
    what is on screen is never stale.

    Dated freezes are lifted first: a plan that came out of a freeze last week
    should have its slots settled normally, and one still frozen is skipped
    entirely so a paused client never loses a session.
    """
    with repo.begin():
        lift_expired_freezes(repo)
        now = db.now()
        # The frozen plans are read first and passed in rather than joined:
        # Mongo has no cross-collection update, and there are never more than
        # a handful of them.
        frozen = [r["id"] for r in repo.find(
            "subscriptions", {"frozen_on": {"ne": None}}, fields=["id"])]
        settled = repo.settle_absences(now, frozen)
        repo.complete_finished_sessions(now)
        return settled


# ---------------------------------------------------------------- scanning
def _client_payload(repo, client, sub, class_id=None) -> dict:
    cid = client["id"]
    state = plan_state(repo, sub["id"]) if sub else {}

    recent = repo.recent_attendance(cid, 4)

    # "Next class" means the next one on the card being held. A client who
    # takes Ballet and Flexibility was being shown whichever came first
    # across both, so the Ballet card could answer with a Flexibility date —
    # true, but not what was asked. Scoped to the card's class; a
    # member-number lookup names no class and still spans everything.
    nxt = repo.next_booked_session(cid, db.now(), class_id)
    tot = repo.client_totals(cid)

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
        "visits": tot["present"],
        "absences": tot["absent"],
        "last_visit": tot["last_visit"],
        "recent": recent,
        "next_session": nxt,
        "low_balance": state.get("remaining") is not None and 0 < state["remaining"] <= 2,
        "frozen": state.get("frozen", False),
        "frozen_until": state.get("frozen_until"),
    }


def verify(repo, raw_token: str) -> dict:
    """Read-only. Works out who this is and which session they are here for."""
    settle_past_sessions(repo)

    try:
        tokens.parse(raw_token, max_age=None)
    except tokens.TokenError as e:
        _log(repo, None, None, None, "deny", f"invalid token: {e}")
        return _deny("This code was not issued by us", detail=str(e))

    cred = repo.credential_by_token(raw_token.strip().upper())
    if cred is None:
        return _deny("Card not recognised", detail="valid signature, no matching record")

    # Look the client up before the revoked check, not after. A replaced card
    # is one we know the owner of, and answering it with a blank panel headed
    # "Unknown card" told reception the person in front of them was a
    # stranger — every reissue leaves an older card in circulation that lands
    # here. Denials carry the profile wherever the client is known.
    client = repo.get("clients", cred["client_id"])
    if client is None:
        return _deny("Card not recognised", detail="no client behind this credential")
    known = {
        "client_id": client["id"], "credential_id": cred["id"],
        "name_en": client["name_en"], "photo_path": client["photo_path"],
        "card_class": cred["class_name"], "card_colour": cred["colour"],
    }
    if cred["revoked_at"]:
        _log(repo, cred["client_id"], cred["id"], None, "deny", "revoked")
        return _deny("This card was replaced",
                     detail="hand the client their new card", **known)
    if not client["active"]:
        return _deny("Client is not active", **known)

    # The card names a class, so the plan is that class's plan. This is what
    # stops a Ballet card drawing on a Flexibility balance.
    sub = active_plan(repo, client["id"], cred["class_id"])
    base = {**known, **_client_payload(repo, client, sub, cred["class_id"])}
    if sub is None and cred["class_id"]:
        _log(repo, client["id"], cred["id"], None, "deny", "no plan for that class")
        return _deny(f"No {cred['class_name']} plan",
                     detail="this card is for a class they are not enrolled in",
                     **base)
    return _decide(repo, client, cred, base, db.now())


def _decide(repo, client, cred, base, t):
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
    #
    # "Today" is the *session's* day, not the moment the attendance was
    # recorded. Those are not the same thing: set_status() stamps
    # checked_in_at with the instant someone presses Present, so marking a
    # client present this evening for yesterday's class writes today's
    # timestamp onto yesterday's booking. Filtering on that timestamp turned
    # a client with nothing on today away with "already checked in today for
    # Adult Ballet Monday" — naming a class that ran the day before, and
    # withholding the manual check-in this refusal is not supposed to reach.
    # One flat fetch of everything of theirs that runs today; the three
    # questions below are decided in Python. The window is one client and one
    # day, so this is a handful of rows -- and as a query it would be a
    # four-table join with a conditional OR and ORDER BY ABS(...), which has
    # no readable equivalent on a document store.
    today_rows = repo.client_day_bookings(cid, start, end)

    # "Today" means the session's own day, never checked_in_at: marking
    # someone present this evening for yesterday's class stamps today's time
    # onto yesterday's booking.
    present = [r for r in today_rows if r["status"] == "present"]
    done = max(present, key=lambda r: (r["starts_at"], r["session_id"])) if present else None
    if done:
        # The time is worth printing only when it is a time from today. A
        # booking marked present in advance carries an earlier day's stamp,
        # and "checked in today at 19:24" would then name an hour nobody was
        # here for; older rows may carry no stamp at all.
        at = done["checked_in_at"]
        when = (f" at {time.strftime('%H:%M', time.localtime(at))}"
                if at and start <= at <= end else "")
        _log(repo, cid, cred_id, None, "deny", "already checked in today")
        return _deny(f"Already checked in today{when} for {done['class_name']}",
                     detail="nothing was deducted", severity="warn", **base)

    # The card's own plan: freezing the ballet plan must not turn away a
    # client arriving for the flexibility class she is paid up in.
    sub = active_plan(repo, cid, cred["class_id"] if cred else None)
    if sub and sub["frozen_on"]:
        until = sub["frozen_until"]
        when = f" until {until}" if until else ""
        _log(repo, cid, cred_id, None, "deny", "plan frozen")
        return _deny(f"This plan is frozen{when}",
                     detail="unfreeze it from their profile to let them in", **base)

    candidates = [r for r in today_rows if r["session_status"] != "cancelled"]
    if cred and cred["class_id"]:
        # The card names a *plan*, not a date. So match the booking this
        # class's plan paid for, whatever session it now sits on: a booking
        # moved to another class by move_booking() is still this plan's slot,
        # and this card is still what proves it. Matching on the session's
        # own class instead would turn away a client whose Ballet slot was
        # moved onto a Flexibility date — the exact case that move exists for.
        # Bookings with no plan behind them (older rows) keep the old rule.
        want = cred["class_id"]
        candidates = [r for r in candidates
                      if r["plan_class_id"] == want
                      or (r["subscription_id"] is None
                          and r["session_class_id"] == want)]

    # Nearest to now, which is what ORDER BY ABS(starts_at - t) was for.
    row = min(candidates, key=lambda r: (abs(r["starts_at"] - t), r["session_id"])) \
        if candidates else None

    if row is None:
        cls = f" for {base.get('card_class')}" if base.get("card_class") else ""
        _log(repo, cid, cred_id, None, "deny", "no session today")
        return _deny(f"No session booked today{cls}",
                     detail="check their upcoming sessions on their profile",
                     code="no_session_today", **base)

    if row["status"] == "absent":
        # Their session has been and gone and they were swept absent, but here
        # they are. The slot is theirs and still unspent as far as attendance
        # goes, so it can be moved onto something else running today — the
        # same swap "no session today" offers, hence the same button.
        _log(repo, cid, cred_id, row["session_id"], "deny", "already absent")
        return _deny("Already marked absent for today's session",
                     detail="they can still be moved onto another session today",
                     code="absent_today", **base)

    mins = round((row["starts_at"] - t) / 60)
    event_id = _log(repo, cid, cred_id, row["session_id"], "allow", None)

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


def verify_by_client(repo, client_id: int) -> dict:
    """Member-number lookup. Same rules; no card, so no class filter."""
    settle_past_sessions(repo)
    client = repo.get("clients", client_id)
    if client is None:
        return _deny(f"No client with number {client_id:05d}")
    if not client["active"]:
        return _deny("Client is not active")

    sub = active_plan(repo, client_id)
    base = {
        "client_id": client["id"], "credential_id": None,
        "name_en": client["name_en"], "photo_path": client["photo_path"],
        "card_class": None, "card_colour": None,
        **_client_payload(repo, client, sub),
    }
    return _decide(repo, client, None, base, db.now())


def check_in(repo, event_id: int) -> dict:
    """Mark the booking present. Guarded so a double tap cannot double-spend."""
    with repo.begin():
        ev = repo.get("access_events", event_id)
        if ev is None or ev["decision"] != "allow":
            return {"ok": False, "error": "no such granted scan"}
        if ev["confirmed_at"]:
            return {"ok": False, "error": "already checked in"}

        b = repo.find_one("bookings", {"client_id": ev["client_id"],
                                       "session_id": ev["session_id"]})
        if b is None:
            return {"ok": False, "error": "booking no longer exists"}

        # Compare-and-swap, not read-then-write: False means somebody got
        # there first and nothing is deducted.
        if not repo.check_in_booking(b["id"], db.now()):
            return {"ok": False, "error": "already marked present"}

        repo.update("access_events", event_id,
                    {"confirmed_at": db.now(), "session_spent": 1})
        state = plan_state(repo, b["subscription_id"]) if b["subscription_id"] else {}
        return {"ok": True, "sessions_remaining": state.get("remaining")}


def swap_options(repo, client_id: int) -> dict:
    """
    What reception can offer someone standing at the desk with nothing booked
    today: every session running today, and every slot of their own they
    could give up for one.

    A giveable slot is a date still ahead of them, or one they were already
    marked absent for — a paid slot they lost. Each is tagged so the screen
    can say which is which, because giving up a future date and reclaiming a
    missed one are different decisions.
    """
    settle_past_sessions(repo)
    start, end = day_bounds()
    now = db.now()

    today = [dict(r) for r in repo.raw(
        "SELECT s.id, s.starts_at, s.duration_hours, c.name AS class_name, c.colour,"
        "       i.name AS instructor_name,"
        "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id = s.id) AS booked"
        "  FROM sessions s JOIN classes c ON c.id = s.class_id"
        "  LEFT JOIN instructors i ON i.id = s.instructor_id"
        " WHERE s.starts_at BETWEEN ? AND ? AND s.status != 'cancelled'"
        "   AND NOT EXISTS (SELECT 1 FROM bookings b"
        "                    WHERE b.session_id = s.id AND b.client_id = ?)"
        " ORDER BY s.starts_at", (start, end, client_id)).fetchall()]

    slots = [dict(r) for r in repo.raw(
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


def swap_and_check_in(repo, client_id: int, from_session: int, to_session: int,
                      credential_id: int = None) -> dict:
    """
    Give up one of the client's own slots for a session running today, and
    check them in to it.

    Deliberately built out of the ordinary pieces: move_booking() to change
    the date, then the same _log()/check_in() pair a scan goes through. That
    is what makes the 60-second Undo work here exactly as it does for a
    normal scan, and keeps the day's check-in count honest.

    All three under one tx(). They each open their own, but tx() is
    re-entrant, so the outermost block here is the one that commits. This
    used to be three separate transactions for a single press of the confirm
    button: a crash between the move and the check-in left the booking sitting
    on a session the client was never marked present at, and no event logged
    to say what had happened.
    """
    with repo.begin():
        moved = move_booking(repo, client_id, from_session, to_session,
                             allow_other_class=True)
        if not moved["ok"]:
            return moved
        event_id = _log(repo, client_id, credential_id, to_session, "allow",
                        "manual swap", source="manual")
        r = check_in(repo, event_id)
        r["event_id"] = event_id
        return r


def undo(repo, event_id: int) -> dict:
    with repo.begin():
        ev = repo.get("access_events", event_id)
        if not ev or not ev["session_spent"]:
            return {"ok": False, "error": "nothing to undo"}
        if db.now() - ev["confirmed_at"] > 120:
            return {"ok": False, "error": "undo window closed — change it from the session page"}
        repo.update_where("bookings",
                          {"client_id": ev["client_id"],
                           "session_id": ev["session_id"]},
                          {"status": "booked", "checked_in_at": None})
        repo.update("access_events", event_id,
                    {"confirmed_at": None, "session_spent": 0})
        return {"ok": True}


# ---------------------------------------------------------------- attendance
def set_status(repo, session_id: int, client_id: int, status: str) -> dict:
    """Present or absent. Both consume the slot; the difference is the record."""
    with repo.begin():
        if status not in ("present", "absent", "booked"):
            return {"ok": False, "error": "status must be present or absent"}
        b = repo.find_one("bookings",
                          {"session_id": session_id, "client_id": client_id})
        if b is None:
            return {"ok": False, "error": "this client is not booked into this session"}
        repo.update("bookings", b["id"], {
            "status": status,
            "checked_in_at": db.now() if status == "present" else None})
        return {"ok": True, "status": status}


def book(repo, client_id: int, session_id: int, subscription_id: int = None,
         allow_other_class: bool = False) -> dict:
    with repo.begin():
        if repo.exists("bookings", {"client_id": client_id,
                                    "session_id": session_id}):
            return {"ok": False, "error": "already booked into this session"}
        s = repo.get("sessions", session_id)
        if s is None:
            return {"ok": False, "error": "no such session"}
        klass = repo.get("classes", s["class_id"])
        cname = klass["name"] if klass else "this class"

        if subscription_id is None:
            # Spend the plan bought for this class, not whichever one runs longest.
            sub = active_plan(repo, client_id, s["class_id"])
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
        sub_row = repo.get("subscriptions", subscription_id)
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
        used = repo.count("bookings", {"subscription_id": subscription_id})
        if used >= sub_row["sessions_total"]:
            return {"ok": False,
                    "error": f"every session on their {cname} plan is already "
                             f"assigned — no free slot to book this one against"}

        repo.insert("bookings", {
            "client_id": client_id, "session_id": session_id,
            "subscription_id": subscription_id,
            "status": "absent" if session_end(s) < db.now() else "booked",
            "created_at": db.now()})
        if subscription_id is not None:
            refresh_expiry(repo, subscription_id)
        return {"ok": True}


def unbook(repo, client_id: int, session_id: int) -> dict:
    with repo.begin():
        b = repo.find_one("bookings",
                          {"client_id": client_id, "session_id": session_id})
        if b is None:
            return {"ok": False, "error": "not booked"}
        repo.delete("bookings", b["id"])
        if b["subscription_id"] is not None:
            refresh_expiry(repo, b["subscription_id"])
        return {"ok": True}


def move_booking(repo, client_id: int, from_session: int, to_session: int,
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
    with repo.begin():
        b = repo.find_one("bookings", {"client_id": client_id,
                                       "session_id": from_session})
        if b is None:
            return {"ok": False, "error": "not booked into that session"}
        if repo.exists("bookings", {"client_id": client_id,
                                    "session_id": to_session}):
            return {"ok": False, "error": "already booked into the target session"}

        src = repo.get("sessions", from_session)
        dst = repo.get("sessions", to_session)
        if dst is None:
            return {"ok": False, "error": "no such session"}
        if src["class_id"] != dst["class_id"] and not allow_other_class:
            return {"ok": False, "error": "can only move within the same class"}

        if status is not None and status not in ("present", "absent", "booked"):
            return {"ok": False, "error": "status must be present, absent or booked"}
        new_status = status or "booked"
        repo.update("bookings", b["id"], {
            "session_id": to_session, "status": new_status,
            "checked_in_at": db.now() if new_status == "present" else None})
        # Moving a booking to a different date can move the plan's last session
        # too — earlier or later — so it needs the same refresh book()/unbook() do.
        if b["subscription_id"] is not None:
            refresh_expiry(repo, b["subscription_id"])
        return {"ok": True}


def session_roster(repo, session_id: int) -> list:
    return repo.session_roster(session_id)


def cancel_session(repo, session_id: int) -> dict:
    """A class the studio is not running. Every slot goes back to the clients."""
    with repo.begin():
        n = repo.update_where("bookings", {"session_id": session_id},
                              {"status": "booked", "checked_in_at": None})
        repo.update("sessions", session_id, {"status": "cancelled"})
        return {"ok": True, "released": n}


def _assignment_error(repo, client_id, session_ids, class_id, class_label):
    """
    Why this set of sessions cannot be assigned to this client, or None.

    The class rule and the double-booking rule, asked once. add_plan() and
    edit_plan() both enforce them — selling a plan and correcting one are the
    same question about which sessions a plan may pay for — and two copies of
    it would eventually disagree.
    """
    ids = list(session_ids)
    wrong = repo.count("sessions", {"id": {"in": ids},
                                    "class_id": {"ne": class_id}})
    if wrong:
        return f"{wrong} of the chosen sessions are not {class_label}"
    clash = repo.count("bookings", {"client_id": client_id,
                                    "session_id": {"in": ids}})
    if clash:
        return "already booked into one of those sessions"
    return None


def _book_slots(repo, client_id, sub_id, session_ids):
    """
    One booking per slot.

    A session that has already finished is booked straight to absent: the
    plan is being written down after the client started coming, and those
    dates really did pass without them being marked present.
    """
    ids = list(session_ids)
    if not ids:
        return
    # One read and one write however many slots there are. Selling a plan
    # books up to twelve at once and a repeated term far more, and on a
    # networked backend each of those would otherwise be its own round trip.
    sessions = {x["id"]: x for x in repo.find("sessions", {"id": {"in": ids}})}
    now = db.now()
    repo.insert_many("bookings", [{
        "client_id": client_id, "session_id": sid, "subscription_id": sub_id,
        "status": ("absent" if sid in sessions
                   and session_end(sessions[sid]) < now else "booked"),
        "created_at": now} for sid in ids])


def add_plan(repo, client_id: int, class_id: int, plan: str, sessions_total: int,
             session_ids: list, price: float = None, starts_on: str = None,
             expires_on: str = None, paid_on: str = None, notes: str = None) -> dict:
    """
    Sell a plan for one class.

    Four rules, enforced here rather than trusted to the UI:
      - every slot is assigned to a real session up front, because a plan with
        unassigned slots is a promise nobody has written down;
      - every one of those sessions belongs to the plan's class, so a Ballet
        plan cannot quietly pay for a Flexibility session;
      - only the previous plan *for this class* is replaced, so a client taking
        two classes keeps the other one running;
      - a plan runs through the last session it pays for, unless reception
        types an end date of its own.

    Lives beside edit_plan() rather than in the route that calls it. The two
    enforce the same four rules over the same tables, and keeping selling in
    api/clients.py while correcting lived here is what let their validation
    drift apart in the first place.

    `status` on a refusal is the HTTP code the route should use, so the
    endpoint stays a translation rather than a second set of rules.
    """
    with repo.begin():
        if sessions_total < 1:
            return {"ok": False, "status": 400, "error": "a plan needs at least one session"}
        if len(session_ids) != sessions_total:
            return {"ok": False, "status": 400,
                    "error": f"assign all {sessions_total} sessions "
                             f"({len(session_ids)} chosen)"}
        if len(set(session_ids)) != len(session_ids):
            return {"ok": False, "status": 400, "error": "the same session was chosen twice"}

        klass = repo.raw("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not klass:
            return {"ok": False, "status": 404, "error": "no such class"}

        bad = _assignment_error(repo, client_id, session_ids, class_id,
                                f"{klass['name']} sessions")
        if bad:
            return {"ok": False, "status": 400, "error": bad}

        # Only this class's previous plan is retired. The client's other class
        # keeps running.
        repo.raw("UPDATE subscriptions SET active=0 WHERE client_id=? AND class_id=?",
                     (client_id, class_id))

        starts = starts_on or date.today().isoformat()
        # Validity follows the sessions the plan actually pays for: it runs
        # through the last of them. Reception can still type a date instead -- a
        # courtesy extension -- and that is what expires_on carries when set.
        expires = expires_on or last_of_sessions(repo, session_ids) or starts
        cur = repo.raw(
            "INSERT INTO subscriptions (client_id, class_id, plan, sessions_total, price,"
            " starts_on, expires_on, paid_on, notes, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (client_id, class_id, plan, sessions_total, price, starts, expires,
             paid_on or None, (notes or "").strip() or None, db.now()))
        sub_id = cur.lastrowid
        _book_slots(repo, client_id, sub_id, session_ids)
        return {"ok": True, "id": sub_id, "booked": len(session_ids),
                "class_id": class_id, "class_name": klass["name"]}


def edit_plan(repo, sub_id: int, plan: str = None, sessions_total: int = None,
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
    with repo.begin():
        sub = repo.raw("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
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
            klass = repo.raw("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
            if klass is None:
                return {"ok": False, "error": "no such class"}
            if session_ids is None:
                return {"ok": False, "error": "changing the class means reassigning the sessions"}
            held = repo.raw(
                "SELECT COUNT(*) n FROM subscriptions"
                " WHERE client_id=? AND class_id=? AND active=1 AND id!=?",
                (sub["client_id"], class_id, sub_id)).fetchone()["n"]
            if held:
                return {"ok": False,
                        "error": f"this client already has a live {klass['name']} plan"
                                 " — renew that one instead"}

        current = repo.raw(
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
                bad = _assignment_error(repo, sub["client_id"], sorted(new_ids),
                                        target_class, "this plan's class")
                if bad:
                    return {"ok": False, "error": bad}

            to_drop = current_ids - wanted
            if to_drop:
                marks = ",".join("?" * len(to_drop))
                repo.raw(
                    f"DELETE FROM bookings WHERE subscription_id=? AND session_id IN ({marks})",
                    (sub_id, *to_drop))
            _book_slots(repo, sub["client_id"], sub_id, sorted(new_ids))

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
            repo.raw(f"UPDATE subscriptions SET {sets} WHERE id=?",
                         (*fields.values(), sub_id))

        if expires_on:
            repo.raw("UPDATE subscriptions SET expires_on=? WHERE id=?",
                         (expires_on, sub_id))
        else:
            refresh_expiry(repo, sub_id)

        revoked = 0
        if moving:
            # The old class's card proved this plan. Revoke it unless another live
            # plan still stands behind that class — credentials are revoked, never
            # deleted, so the log keeps pointing at the one that was used.
            still = repo.raw(
                "SELECT 1 FROM subscriptions WHERE client_id=? AND class_id=? AND active=1",
                (sub["client_id"], sub["class_id"])).fetchone()
            if not still:
                revoked = repo.raw(
                    "UPDATE credentials SET revoked_at=? WHERE client_id=? AND class_id=?"
                    "   AND revoked_at IS NULL",
                    (db.now(), sub["client_id"], sub["class_id"])).rowcount

        return {"ok": True, "moved": moving, "cards_revoked": revoked,
                **plan_state(repo, sub_id)}


def issue_card(repo, client_id: int, class_id: int) -> dict:
    """
    Mint a card for one class, revoking that class's previous one.

    Revoke-and-insert has to be one write. Between the two statements the
    client holds no working card at all, and a crash there leaves them
    holding a revoked one with nothing issued to replace it -- which reads at
    reception as a system fault rather than as something reception can fix.

    Credentials are revoked, never deleted, so the access log keeps pointing
    at the credential that was actually used.

    A card is proof of a plan in that class. Issuing a Flexibility card to
    someone who only takes Ballet would create a credential that can never
    check anyone in.

    Returns what the card needs printing on it. Drawing the PNG stays in the
    route: it is file I/O and a matter of presentation, not a business rule.
    """
    with repo.begin():
        if not class_id:
            return {"ok": False, "status": 400,
                    "error": "a card belongs to a class — say which"}
        client = repo.raw("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            return {"ok": False, "status": 404, "error": "no such client"}
        klass = repo.raw("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not klass:
            return {"ok": False, "status": 404, "error": "no such class"}

        sub = active_plan(repo, client_id, class_id)
        if not sub:
            return {"ok": False, "status": 400,
                    "error": f"{client['name_en']} has no active {klass['name']} plan — "
                             f"add one before issuing this card"}

        # `class_id IS ?` rather than `= ?`: a card issued before cards had a
        # class carries NULL, and NULL = NULL is not true in SQL.
        old = repo.raw(
            "SELECT token FROM credentials WHERE client_id=? AND revoked_at IS NULL"
            "   AND (class_id IS ? OR class_id = ?)",
            (client_id, class_id, class_id)).fetchone()
        repo.raw(
            "UPDATE credentials SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL"
            "   AND (class_id IS ? OR class_id = ?)",
            (db.now(), client_id, class_id, class_id))

        token = tokens.issue(client_id)
        repo.raw(
            "INSERT INTO credentials (client_id, class_id, token, kind, issued_at)"
            " VALUES (?,?,?,?,?)", (client_id, class_id, token, "card", db.now()))

        state = plan_state(repo, sub["id"])
        return {"ok": True, "token": token,
                "revoked": old["token"] if old else None,
                "client_name": client["name_en"],
                "class_name": klass["name"], "class_colour": klass["colour"],
                "sessions_total": state["sessions_total"],
                "expires_on": state["expires_on"]}


def delete_plan(repo, sub_id: int) -> dict:
    """
    Remove a plan for good, along with every booking it paid for.

    The one deletion in the app that takes attendance with it, because a
    plan's bookings *are* its attendance: there is no version of removing the
    plan that keeps the record. So the counts are taken before the delete and
    returned, and the confirm dialog says what will go rather than what did.

    No refresh_expiry() here, unlike the other bulk booking deletes: the plan
    whose expiry would be recomputed is itself gone.
    """
    with repo.begin():
        sub = repo.raw("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if not sub:
            return {"ok": False, "status": 404, "error": "no such plan"}

        counts = repo.raw(
            "SELECT COUNT(*) n,"
            "       SUM(CASE WHEN status='booked' THEN 1 ELSE 0 END) upcoming,"
            "       SUM(CASE WHEN status!='booked' THEN 1 ELSE 0 END) attended"
            "  FROM bookings WHERE subscription_id=?", (sub_id,)).fetchone()
        repo.raw("DELETE FROM bookings WHERE subscription_id=?", (sub_id,))
        repo.raw("DELETE FROM subscriptions WHERE id=?", (sub_id,))

        # The card for this class now proves a plan that does not exist. Revoke it
        # unless another plan in the same class still stands behind it.
        revoked = 0
        if sub["class_id"]:
            still = repo.raw(
                "SELECT 1 FROM subscriptions WHERE client_id=? AND class_id=? AND active=1",
                (sub["client_id"], sub["class_id"])).fetchone()
            if not still:
                revoked = repo.raw(
                    "UPDATE credentials SET revoked_at=? WHERE client_id=? AND class_id=?"
                    "   AND revoked_at IS NULL",
                    (db.now(), sub["client_id"], sub["class_id"])).rowcount
        return {"ok": True, "bookings": counts["n"] or 0,
                "upcoming": counts["upcoming"] or 0,
                "attended": counts["attended"] or 0, "cards_revoked": revoked}


def delete_client(repo, client_id: int, hard: bool = False) -> dict:
    """
    Archive a client, or -- with `hard` -- remove them entirely.

    Archiving is refused while they have sessions still ahead of them. Not
    released, refused: a client with dates in the future is not finished with
    the academy, and the older behaviour deleted those bookings without
    saying so. The predicate is the same one get_client builds the profile's
    `upcoming` list from, so the number in the error is the number on the
    screen reception is looking at.

    Unused slots with no dates on them do not block it. A lapsed plan holding
    slots nobody will ever book must not make a client permanently
    un-archivable.

    The hard path is a manual cascade, ordered by hand to respect the foreign
    keys, and is refused outright once any attendance exists -- losing the
    record of who attended what is worse than a cluttered list.
    """
    with repo.begin():
        # So a booking whose session has already finished counts as history
        # rather than as something still upcoming in the guard below.
        settle_past_sessions(repo)

        client = repo.raw("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            return {"ok": False, "status": 404, "error": "no such client"}

        if hard:
            visits = repo.raw(
                "SELECT COUNT(*) n FROM bookings WHERE client_id=? AND status!='booked'",
                (client_id,)).fetchone()["n"]
            if visits:
                return {"ok": False, "status": 400,
                        "error": f"{client['name_en']} has {visits} recorded sessions "
                                 f"— archive instead"}
            for q in ("DELETE FROM bookings WHERE client_id=?",
                      "DELETE FROM credentials WHERE client_id=?",
                      "DELETE FROM subscriptions WHERE client_id=?",
                      "DELETE FROM access_events WHERE client_id=?",
                      "DELETE FROM clients WHERE id=?"):
                repo.raw(q, (client_id,))
            return {"ok": True, "action": "delete"}

        upcoming = repo.raw(
            "SELECT COUNT(*) n FROM bookings b JOIN sessions s ON s.id = b.session_id"
            " WHERE b.client_id=? AND s.starts_at >= ? AND s.status != 'cancelled'",
            (client_id, db.now())).fetchone()["n"]
        if upcoming:
            return {"ok": False, "status": 400,
                    "error": f"{client['name_en']} has {upcoming} upcoming session"
                             f"{'' if upcoming == 1 else 's'} — remove or reassign "
                             f"{'it' if upcoming == 1 else 'them'} before archiving"}

        repo.raw("UPDATE clients SET active=0 WHERE id=?", (client_id,))
        # Archiving revokes the card, so a restored client needs a new one issued.
        repo.raw("UPDATE credentials SET revoked_at=? WHERE client_id=?"
                     " AND revoked_at IS NULL", (db.now(), client_id))
        # The guard above ignores bookings whose session was cancelled, so those
        # are the ones still left to release here.
        repo.raw("DELETE FROM bookings WHERE client_id=? AND status='booked'",
                     (client_id,))
        return {"ok": True, "action": "archive"}


def delete_sessions(repo, session_ids, force: bool = False) -> dict:
    """
    Remove sessions, keeping back any that carry attendance.

    One function for both the single and the bulk case: deleting one session
    is deleting a list of one, and the two used to be near-identical copies
    in api/sessions.py that had to be kept in step by hand.

    A session someone was marked present or absent at is history. Rather than
    failing the whole batch over it, the ones kept back are named in
    `blocked` — clearing a term with one taught week in the middle of it
    should remove the other eleven and say why the twelfth stayed. `force`
    overrides, which is the caller saying they meant it.

    Deleting the bookings directly bypasses unbook(), so every plan that
    funded one needs refresh_expiry() by hand — otherwise a plan goes on
    claiming it runs through a date that no longer exists. This is one of the
    paths the docstring on refresh_expiry() is warning about.

    access_events.session_id is nulled rather than cascaded: the row is the
    record that someone scanned, which stays true after the session is gone.
    """
    with repo.begin():
        deleted, released, blocked = 0, 0, []
        for sid in session_ids:
            row = repo.raw(
                "SELECT s.id, s.starts_at, c.name AS class_name FROM sessions s"
                "  JOIN classes c ON c.id = s.class_id WHERE s.id=?", (sid,)).fetchone()
            if not row:
                continue
            held = repo.raw(
                "SELECT COUNT(*) n FROM bookings WHERE session_id=? AND status!='booked'",
                (sid,)).fetchone()["n"]
            if held and not force:
                blocked.append({**dict(row), "attendance": held})
                continue

            n = repo.raw("SELECT COUNT(*) n FROM bookings WHERE session_id=?",
                             (sid,)).fetchone()["n"]
            subs = {r["subscription_id"] for r in repo.raw(
                "SELECT DISTINCT subscription_id FROM bookings"
                " WHERE session_id=? AND subscription_id IS NOT NULL", (sid,)).fetchall()}
            repo.raw("DELETE FROM bookings WHERE session_id=?", (sid,))
            repo.raw("UPDATE access_events SET session_id=NULL WHERE session_id=?", (sid,))
            repo.raw("DELETE FROM sessions WHERE id=?", (sid,))
            for sub_id in subs:
                refresh_expiry(repo, sub_id)
            deleted += 1
            released += n

        return {"ok": True, "deleted": deleted, "released": released, "blocked": blocked}


def delete_class(repo, class_id: int, hard: bool = False) -> dict:
    """
    Archive a class, or -- with `hard` -- remove it and its whole history.

    Archiving is the one archive path that cascades a delete into another
    table. A class that stops being offered has nothing left to happen for,
    so its upcoming sessions are deleted rather than left dangling on a class
    nobody can see: the clients booked into them get the slot back as
    unassigned on their plan, the same as any other booking removal. Past
    sessions and their attendance are never touched.

    (Client and instructor archiving deliberately do not do this. A client
    with dates ahead of them is refused instead -- see delete_client.)

    Both branches delete bookings directly rather than through unbook(), so
    every plan that funded one needs refresh_expiry() by hand.
    """
    with repo.begin():
        klass = repo.raw("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not klass:
            return {"ok": False, "status": 404, "error": "no such class"}

        if hard:
            held = repo.raw(
                "SELECT COUNT(*) n FROM bookings b JOIN sessions s ON s.id=b.session_id"
                " WHERE s.class_id=? AND b.status!='booked'", (class_id,)).fetchone()["n"]
            if held:
                return {"ok": False, "status": 400,
                        "error": f"{klass['name']} has {held} attendance records "
                                 f"— archive instead"}
            subs = {r["subscription_id"] for r in repo.raw(
                "SELECT DISTINCT subscription_id FROM bookings"
                " WHERE session_id IN (SELECT id FROM sessions WHERE class_id=?)"
                "   AND subscription_id IS NOT NULL", (class_id,)).fetchall()}
            repo.raw("DELETE FROM bookings WHERE session_id IN"
                         " (SELECT id FROM sessions WHERE class_id=?)", (class_id,))
            repo.raw("DELETE FROM sessions WHERE class_id=?", (class_id,))
            repo.raw("DELETE FROM classes WHERE id=?", (class_id,))
            for sub_id in subs:
                refresh_expiry(repo, sub_id)
            return {"ok": True, "action": "delete",
                    "released_sessions": None, "released_bookings": None}

        upcoming = [r["id"] for r in repo.raw(
            "SELECT id FROM sessions WHERE class_id=? AND status='scheduled'"
            " AND starts_at > ?", (class_id, db.now())).fetchall()]
        released_bookings = 0
        if upcoming:
            marks = ",".join("?" * len(upcoming))
            subs = {r["subscription_id"] for r in repo.raw(
                f"SELECT DISTINCT subscription_id FROM bookings"
                f" WHERE session_id IN ({marks}) AND subscription_id IS NOT NULL",
                upcoming).fetchall()}
            released_bookings = repo.raw(
                f"SELECT COUNT(*) n FROM bookings WHERE session_id IN ({marks})",
                upcoming).fetchone()["n"]
            repo.raw(f"DELETE FROM bookings WHERE session_id IN ({marks})", upcoming)
            repo.raw(f"DELETE FROM sessions WHERE id IN ({marks})", upcoming)
            for sub_id in subs:
                refresh_expiry(repo, sub_id)
        repo.raw("UPDATE classes SET active=0 WHERE id=?", (class_id,))
        return {"ok": True, "action": "archive",
                "released_sessions": len(upcoming),
                "released_bookings": released_bookings}


def expected_today(repo) -> dict:
    settle_past_sessions(repo)
    start, end = day_bounds()
    r = repo.day_attendance_totals(start, end)
    expected, arrived, absent = r["expected"], r["arrived"], r["absent"]
    return {"expected": expected, "arrived": arrived, "absent": absent,
            "still_due": max(0, expected - arrived - absent)}


def month_of(when: date = None) -> str:
    """The "YYYY-MM" a date falls in. Today's, unless told otherwise."""
    return (when or date.today()).strftime("%Y-%m")


def prev_month(month: str) -> str:
    first = date.fromisoformat(month + "-01")
    return (first - timedelta(days=1)).strftime("%Y-%m")


def next_month(month: str) -> str:
    """
    The "YYYY-MM" after this one.

    Used as the open end of a month range: an ISO date sorts lexicographically
    against a bare "YYYY-MM", so `joined_on < next_month(m)` is exactly
    "in or before month m" without extracting the month from the column
    first. See month_intake().
    """
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"


def shift_month(month: str, back: int) -> str:
    """The "YYYY-MM" `back` months earlier. Used to build the window a
    period is compared against, which is why it only ever goes backwards."""
    y, m = int(month[:4]), int(month[5:7])
    total = y * 12 + (m - 1) - back
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def months_between(a: str, b: str) -> int:
    """How many calendar months the inclusive range a..b spans."""
    return ((int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))) + 1


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


def logged_hours(repo, instructor_id: int, period_from: str, period_to: str) -> dict:
    """
    Hours the salary sheet recorded for this instructor within a period, plus
    any manual corrections layered on top (see instructor_hour_adjustments) --
    never mixed into the sheet's own rows, so what the sheet actually said
    stays visible. `days`/`from`/`to` count only real salary-sheet rows; a
    correction is not a claim of an extra day worked.
    """
    sheet = repo.raw(
        "SELECT COALESCE(SUM(hours),0) h, COUNT(*) days, MIN(work_date) a, MAX(work_date) b"
        " FROM instructor_hours WHERE instructor_id=? AND work_date BETWEEN ? AND ?",
        (instructor_id, period_from, period_to)).fetchone()
    rate_row = repo.raw("SELECT hourly_rate FROM instructors WHERE id=?",
                            (instructor_id,)).fetchone()
    rate = (rate_row["hourly_rate"] or 0) if rate_row else 0
    hours = round(sheet["h"] or 0, 2)
    return {
        "hours": hours, "days": sheet["days"], "from": sheet["a"], "to": sheet["b"],
        "pay": round(hours * rate, 2),
    }


def taught_hours(repo, instructor_id: int, period_from: str, period_to: str) -> dict:
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
    t = repo.raw(
        "SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
        " WHERE instructor_id=? AND status='completed'"
        "   AND starts_at >= ? AND starts_at < ?",
        (instructor_id, start_ts, end_ts)).fetchone()
    adj = repo.raw(
        "SELECT COALESCE(SUM(delta_hours),0) d FROM instructor_hour_adjustments"
        " WHERE instructor_id=? AND adjustment_date BETWEEN ? AND ?",
        (instructor_id, period_from, period_to)).fetchone()
    scheduled = round(t["h"] or 0, 2)
    adjustment = round(adj["d"] or 0, 2)
    return {"sessions": t["n"], "scheduled": scheduled, "adjustment": adjustment,
            "hours": round(scheduled + adjustment, 2)}


def adjust_taught_hours(repo, instructor_id: int, day: str,
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
    with repo.begin():
        current = taught_hours(repo, instructor_id, day, day)
        delta = round(new_total - current["hours"], 2)
        repo.raw(
            "INSERT INTO instructor_hour_adjustments (instructor_id, adjustment_date, delta_hours,"
            " note, created_at) VALUES (?,?,?,?,?)",
            (instructor_id, day, delta, note, db.now()))
        return taught_hours(repo, instructor_id, day, day)


def month_intake(repo, month: str = None, month_to: str = None) -> dict:
    """
    Who joined in a stretch of months and what they paid. Defaults to the
    month we are in, which is what the dashboard shows until asked otherwise.

    Two numbers reception actually asks for at the end of a month, and they
    are not the same question:

      *New clients* are counted on `joined_on` — the date of their first
      payment, which is when they became a client.

      *Their revenue* is every plan those same people have bought. A
      returning client renewing is real money too, so the period's whole
      intake is reported alongside it rather than instead of it; the pair is
      what tells you whether growth came from new faces or from the regulars.

    The two are scoped differently on purpose, and that is the subtle part.
    The new-client figure is filtered by *who* — the clients who joined in the
    period — and by nothing else. It used to be filtered by when their plans
    started as well, and a client who joined on 14 August whose plan started
    on 2 September then fell through both months: out of range in August, not
    a new client in September. August read "4 new clients, 0 EGP" while three
    of those four had paid 4,100 between them. Two dates ANDed together also
    do not add up across sub-periods, so August plus September came to more
    than either month suggested, which is how it was noticed.

    So this figure follows the people, and the money follows them out of the
    period: a month's number grows as its intake renews later on. That is
    what "earned from them" means, and it is the reading that keeps the two
    cards describing the same clients. The period-bound number is the other
    one — `revenue`, every plan *sold* in the window, whoever bought it —
    which stays strictly inside it and does add up across months.

    The period is whole calendar months, never part of one, because that is
    the granularity both figures mean: "joined in September" is an answer, and
    "joined between the 8th and the 23rd" is not a question anybody asks about
    an academy that bills by the month. Months compare lexicographically as
    "YYYY-MM" strings, so a BETWEEN on the first seven characters is the whole
    of the range logic.

    `new_clients_prev` is the same span immediately before — one month back
    for a single month, three for a quarter — so the comparison is like for
    like however wide the window is opened.

    Plans whose price nobody wrote down are counted separately, never as
    zero. The roster sheets record "package", "free" and "yes" as often as an
    amount, and a month's takings reported as a clean total while half its
    plans carry no figure at all is a lie the shape of a fact.
    """
    month = month or month_of()
    month_to = month_to or month
    if month_to < month:
        month, month_to = month_to, month
    span = months_between(month, month_to)
    prev_from, prev_to = shift_month(month, span), shift_month(month_to, span)

    # A month range as a half-open range over the whole date, rather than
    # substr(col,1,7) BETWEEN a AND b. The two are equivalent — an ISO date
    # sorts lexicographically against a bare "YYYY-MM", so "2026-08-31" is
    # both >= "2026-08" and < "2026-09" while "2026-07-31" is neither — and
    # the range form is the one an index can use. substr() on the column
    # defeated ix_cli_joined and ix_sub_starts entirely.
    def joined_in(a: str, b: str) -> int:
        return repo.raw(
            "SELECT COUNT(*) n FROM clients"
            " WHERE active=1 AND joined_on >= ? AND joined_on < ?",
            (a, next_month(b))).fetchone()["n"]

    new_clients = joined_in(month, month_to)
    before = joined_in(prev_from, prev_to)

    def takings(where: str) -> tuple:
        r = repo.raw(
            "SELECT COALESCE(SUM(s.price),0) paid,"
            "       SUM(CASE WHEN s.price IS NULL THEN 1 ELSE 0 END) unpriced,"
            "       COUNT(*) plans"
            "  FROM subscriptions s JOIN clients c ON c.id=s.client_id"
            f" WHERE c.active=1 AND {where} >= ? AND {where} < ?",
            (month, next_month(month_to))).fetchone()
        return r["paid"] or 0, r["unpriced"] or 0, r["plans"] or 0

    # Every plan belonging to a client who joined in the period, whenever they
    # bought it — see the note above on why this is not also filtered by when
    # the plan started.
    new_paid, new_unpriced, new_plans = takings("c.joined_on")
    # Every plan sold in the period, whoever bought it. This one is the
    # period's own takings and stays inside the window.
    all_paid, all_unpriced, all_plans = takings("s.starts_on")

    return {
        "month": month,
        "month_to": month_to,
        "months": span,
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


def freeze_plan(repo, sub_id: int, until: str = None, reason: str = None,
                from_date: str = None) -> dict:
    """
    Pause a plan. `until` may be None, meaning it stays frozen until lifted.
    Returns how many booked sessions were released.
    """
    with repo.begin():
        sub = repo.raw("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
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

        doomed = repo.raw(
            "SELECT b.id FROM bookings b JOIN sessions s ON s.id = b.session_id"
            " WHERE b.subscription_id = ? AND b.status = 'booked'"
            f"   AND s.starts_at >= ?{window}", params).fetchall()
        released = len(doomed)
        if released:
            repo.raw(
                f"DELETE FROM bookings WHERE id IN ({','.join('?' * released)})",
                [r["id"] for r in doomed])

        repo.raw("UPDATE subscriptions SET frozen_on=?, frozen_until=? WHERE id=?",
                     (start, until, sub_id))
        repo.raw(
            "INSERT INTO freezes (subscription_id, from_date, until_date, released, reason,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (sub_id, start, until, released, reason, db.now()))
        return {"ok": True, "released": released, "frozen_on": start, "frozen_until": until}


def unfreeze_plan(repo, sub_id: int, on_date: str = None) -> dict:
    """
    Lift a freeze and push the expiry out by however long it lasted. The
    released slots are already unassigned, so the client profile will show them
    as needing dates.
    """
    with repo.begin():
        sub = repo.get("subscriptions", sub_id)
        if sub is None:
            return {"ok": False, "error": "no such plan"}
        if not sub["frozen_on"]:
            return {"ok": False, "error": "this plan is not frozen"}

        ended = on_date or date.today().isoformat()
        if ended < sub["frozen_on"]:
            ended = sub["frozen_on"]
        days = _days_between(sub["frozen_on"], ended)

        repo.update("subscriptions", sub_id, {
            "frozen_on": None, "frozen_until": None,
            "frozen_days": (sub["frozen_days"] or 0) + days,
            "expires_on": _shift_date(sub["expires_on"], days)})
        repo.update_where("freezes",
                          {"subscription_id": sub_id, "ended_on": None},
                          {"ended_on": ended, "days_added": days})

        state = plan_state(repo, sub_id)
        return {"ok": True, "days": days, "expires_on": state["expires_on"],
                "unassigned": state["unassigned"]}


def lift_expired_freezes(repo) -> int:
    """
    A freeze with an end date lifts itself. Called wherever plans are read, so
    a laptop left off over the whole freeze still comes back correct.
    """
    today = date.today().isoformat()
    # `ne: None` matters on a document store: there, a range comparison
    # against a string also matches null, because BSON orders null first.
    due = repo.find("subscriptions", {"frozen_on": {"ne": None},
                                      "frozen_until": {"ne": None, "lte": today}})
    for row in due:
        unfreeze_plan(repo, row["id"], on_date=row["frozen_until"])
    return len(due)
