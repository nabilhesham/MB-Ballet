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
import images
import identity
import tokens

# Only plans of this size or larger may be frozen. Short packs are meant to be
# used inside their window; freezing a 4-session pack for two months makes the
# expiry date meaningless. Change the number here if the policy changes.
FREEZE_MIN_SESSIONS = 12


# ---------------------------------------------------------------- helpers
def _log(repo, client_id, credential_id, session_id, decision, reason,
         source: str = "scan") -> int:
    # No begin() of its own. It is one insert, so outside a block it
    # autocommits -- and inside one (swap_and_check_in) it joins whatever
    # block the caller opened, which is what re-entrancy already gave it.
    # The wrapper was not free: on a document store a commit is its own round
    # trip, and every scan, granted or refused, ends here with a client
    # waiting at the desk.
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

    **A time in a refusal travels as an epoch in `base`, never as text.**
    `message` is a sentence reception reads aloud, and formatting a clock
    time into it here would render it in the *server's* timezone while every
    other screen renders the same instant in the browser's -- one check-in
    printed as two different times. `already_checked_in` carries
    `checked_in_at` for that reason and the kiosk appends it.
    """
    return {**base, "granted": False, "severity": severity, "code": code,
            "message": message, "detail": detail}


# How many sessions an unpaid plan is allowed on trust. One: a client who has
# genuinely forgotten their wallet gets today's class and pays next time, and
# nobody is turned away at the door over a payment reception can take in a
# minute. From the second session it is no longer a forgotten wallet, and the
# refusal is what puts the payment in front of the receptionist while the
# client is standing there -- which is the only moment it is easy to collect.
UNPAID_GRACE_SESSIONS = 1


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


def repeat_sessions(repo, class_id: int, instructor_id, candidates: list,
                    duration_hours: float) -> dict:
    """
    Create a whole term's sessions, checking every candidate date against the
    same two rules one-at-a-time creation uses, in three round trips instead
    of three per date.

    It used to be a loop of exists() + slot_conflict() + insert() per date --
    up to ~288 round trips for a 96-session term, which against a networked
    backend is minutes. The rules are unchanged; only the number of questions
    asked is.

    Three things the loop got for free and this has to do deliberately:

    - **A date already holding this class's own session is skipped**, whatever
      that session's status -- a cancelled one still counts as "already
      entered", which is why the duplicate query carries no status filter
      while the overlap query excludes cancelled.
    - **Sessions created here conflict with each other.** Each insert used to
      be visible to the next slot_conflict(); batched, nothing is written
      until the end, so accepted slots are accumulated and tested against
      alongside the pre-existing ones. Without this a term could lay two
      sessions over each other.
    - **A clash skips one date, never the batch.** One taken evening in week
      seven must not cost the other eleven.
    """
    if not candidates:
        return {"created": 0, "skipped": []}
    span_from = min(candidates)
    span_to = max(candidates) + int(duration_hours * 3600)

    # 1. This class's own sessions on any of the candidate dates. No status
    #    filter: see the docstring.
    taken = {r["starts_at"] for r in repo.find(
        "sessions", {"class_id": class_id, "starts_at": {"in": candidates}},
        fields=["id", "starts_at"])}

    # 2. Everything that could overlap the span, academy-wide. Same predicate
    #    as repo.slot_conflict(), widened from one slot to the whole term.
    #    The ends_at comparison picks up filters.py's automatic null guard,
    #    so a NULL ends_at occupies nothing here exactly as it does there.
    existing = repo.find("sessions", {"status": {"ne": "cancelled"},
                                      "starts_at": {"lt": span_to},
                                      "ends_at": {"gt": span_from}})

    occupied = [(r["starts_at"], ends_at_of(r["starts_at"], r["duration_hours"]),
                 r["id"], r["class_id"]) for r in existing]
    made, clashes = [], []
    for ts in candidates:
        if ts in taken:
            continue
        end = ends_at_of(ts, duration_hours)
        # Half-open, matching repo.slot_conflict().
        hits = [o for o in occupied if o[0] < end and o[1] > ts]
        if hits:
            # Earliest wins, which is what repo.slot_conflict() would have
            # returned for this slot. The id is the tiebreak and must be an
            # int for a not-yet-inserted session too, or a tie between one of
            # those and a stored row would compare None with an int.
            clash = min(hits, key=lambda o: (o[0], o[2]))
            clashes.append({"starts_at": clash[0],
                            "duration_hours": (clash[1] - clash[0]) / 3600,
                            "class_id": clash[3]})
            continue
        occupied.append((ts, end, -1, class_id))
        made.append({"class_id": class_id, "instructor_id": instructor_id,
                     "starts_at": ts, "duration_hours": duration_hours,
                     "ends_at": end})

    # 3. One insert for the term. Mongo allocates the whole id block with a
    #    single counter increment (repo/mongo/__init__.py).
    if made:
        repo.insert_many("sessions", made)

    # Names for the message, one lookup rather than one per clash. A clash
    # against a session created in this very batch carries class_id, so the
    # id set covers it too.
    names = {}
    if clashes:
        names = {c["id"]: c["name"] for c in repo.find(
            "classes", {"id": {"in": sorted({c["class_id"] for c in clashes})}})}
    return {"created": len(made),
            "skipped": [slot_taken_message({**c, "class_name": names.get(c["class_id"])})
                        for c in clashes]}


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


def refresh_expiries(repo, sub_ids):
    """
    refresh_expiry() for many plans, in two round trips rather than two or
    three per plan.

    The invariant is the one refresh_expiry() documents and must not weaken:
    a plan whose bookings have gone entirely keeps whatever end date is
    stored, because "no dates yet" is not the same as "expired". Here that
    falls out of last_session_ts_bulk() omitting such a plan rather than
    mapping it to None.
    """
    sub_ids = [s for s in dict.fromkeys(sub_ids) if s is not None]
    if not sub_ids:
        return {}
    covers = repo.last_session_ts_bulk(sub_ids)
    out = {}
    for sub_id, t in covers.items():
        day = _iso_day(t)
        repo.update("subscriptions", sub_id, {"expires_on": day})
        out[sub_id] = day
    return out


def plan_state(repo, sub_id: int) -> dict:
    """
    Where a plan stands. Used slots are counted from the bookings rather than
    tracked in a column, so the two can never drift apart.
    """
    return plan_states(repo, [sub_id]).get(sub_id, {})


def plan_states(repo, sub_ids) -> dict:
    """
    The same for many plans, in **one** query rather than three per plan.

    The dashboard's attention list and the clients list both ask this of
    every active client, and the reception scan path asks it for one plan
    with a client standing at the desk. On a local SQLite file the N+1 is
    free; against a networked backend it was three round trips per call.
    repo.plan_rows() answers the subscription, its class and its booking
    counts together. There is one implementation and the single-plan case
    goes through it, so the two cannot drift.
    """
    sub_ids = [s for s in dict.fromkeys(sub_ids) if s is not None]
    if not sub_ids:
        return {}
    subs = repo.plan_rows(sub_ids)

    out = {}
    for sid, sub in subs.items():
        present, absent, assigned = sub["present"], sub["absent"], sub["assigned"]
        used = present + absent
        allowed, why = can_freeze(sub)
        out[sid] = {
            "id": sub["id"], "plan": sub["plan"],
            "class_id": sub["class_id"],
            "class_name": sub["class_name"],
            "class_colour": sub["class_colour"],
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


# ---------------------------------------------------------------- identity
def phone_required(phone) -> str | None:
    """
    The sentence to refuse with when there is no usable number, or None.

    The mobile number identifies the client, so it is mandatory — a client
    with no number cannot be told apart from the next client with no number,
    and duplicate_client() below has nothing to compare, which means two of
    them are not duplicates of each other and never will be. Making it
    required is what closes that.

    "Usable" is doing work here. A required field that accepts "n/a" is not
    required in any sense that matters: it would be satisfied by something
    carrying no identity, and several clients could hold the same placeholder
    without any of them conflicting. identity.looks_like_a_number() is the
    test, and identity.MIN_DIGITS records where the line is and why.

    The seed does **not** go through this. `seed.py` inserts clients
    directly, and it must: the roster sheets are the business record, and
    refusing to import a student because nobody wrote her number down would
    lose her. So a seeded database can legitimately hold a client with no
    number, and editing that client from the profile is where reception is
    asked for one.
    """
    if not str(phone or "").strip():
        return "A mobile number is required — it is what identifies a client."
    if not identity.looks_like_a_number(phone):
        return ("That does not look like a mobile number. It identifies the "
                "client, so it has to be the real one.")
    return None


def duplicate_client(repo, name, phone, exclude_id=None) -> str | None:
    """
    The sentence to refuse a client with, or None if this pair is free.

    Identity is the **mobile number and the name together**. A shared mobile
    is not a duplicate: a parent enrols two children on one number, which at
    a children's ballet academy is the ordinary case rather than the
    exception. What is a duplicate is the same name on the same number --
    the same person entered twice.

    That matters because two profiles for one person is not an untidiness
    problem: their sessions, plans and cards divide between the two records,
    so a card scans against a balance that is half what they bought, and the
    missing half is invisible because the other profile looks healthy.

    Like can_freeze(), it is the single answer to the question, so the form
    and the endpoint cannot drift -- both refuse with this sentence.

    Names are compared through identity.name_key(): whitespace collapsed,
    case folded, and deliberately nothing cleverer. Reception can see two
    rows and decide; a rule that decided "Mohamed" and "Mohammed" were one
    person would also decide two real cousins were.

    A blank number is never a conflict -- there is nothing to compare, and
    two blanks are not duplicates of each other. That is why
    phone_required() exists and why both routes call it *first*.

    `exclude_id` is the client being edited: they are not a duplicate of
    themselves.
    """
    key = identity.phone_key(phone)
    if not key:
        return None
    want = identity.name_key(name)
    same = [c for c in repo.clients_by_phone_key(key)
            if c["id"] != exclude_id and identity.name_key(c["name_en"]) == want]
    if not same:
        return None
    c = same[0]
    who = f"{c['name_en']}, member {c['id']}"
    if not c["active"]:
        # Archived, so the match is somebody deliberately put away rather
        # than somebody on the list. "Already exists" would send reception
        # looking for a client they cannot find, and the only way out of
        # that is a second profile -- the thing this refusal prevents.
        return (f"{who}, already has this mobile number and is archived. "
                f"Restore them from the Archived list instead of adding "
                f"them again.")
    return (f"{who}, already has this name and this mobile number. "
            f"A different person on the same number is fine -- change the "
            f"name if this is a second client.")


# ---------------------------------------------------------------- lapsed
# How long after a plan ends a client still counts as a student of that
# class. Membership is derived from bookings and bookings are never deleted,
# so without this a client who stopped coming last year stays on the class
# page for ever and the roster slowly stops describing who actually attends.
LAPSED_GRACE_MONTHS = 1


def lapsed_cutoff(when: date = None) -> str:
    """
    The ISO date a plan must have ended on or after to still count.

    One calendar month back, not thirty days: "a month after it ran out" is
    what reception means, and a month is what the plans are sold in. The day
    is clamped to the shorter month, so 31 March answers 28 February rather
    than raising.
    """
    import calendar
    d = when or date.today()
    y, m = d.year, d.month - LAPSED_GRACE_MONTHS
    while m < 1:
        y -= 1
        m += 12
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


def plan_end(repo, sub) -> str:
    """
    The last day a plan covers: the later of its `expires_on` and the date
    of the last session it pays for.

    refresh_expiry() normally keeps those two equal, so this matters in
    exactly one case -- a date typed into ENDS ON that sits *earlier* than a
    session the plan still funds. The plan cannot have finished before a
    session it is paying for, so the session wins. Whichever is later is
    what the lapsed cutoff is measured from.
    """
    last = last_session_date(repo, sub["id"])
    return max(x for x in (sub.get("expires_on"), last, "") if x is not None)


# ------------------------------------------------------- end-of-session event
#
# **A session's no-shows are written off by an event at that session's own
# end, and by nothing else.** There is one moment it can happen and the
# process waits for it: server.py's watcher sleeps until `next_session_end()`
# and calls `session_ended()` for each session that has arrived. No window is
# scanned, no read endpoint settles anything on the way past, and no cutoff
# is compared against a clock anywhere below this line.
#
# That replaced a sweep, and the two things the sweep had grown are both
# gone with it:
#
# * It ran at the top of nine read endpoints -- about 400ms of every admin
#   page against Atlas -- so it had to be given a cached deadline to skip
#   itself. An event needs neither: nothing is stale between two moments
#   when nothing can have happened.
# * Deciding *whether* a session had ended meant comparing `ends_at` against
#   `now` on every pass, which made every way of `ends_at` being wrong a way
#   of writing somebody off in a room they were sitting in. The comparison
#   happens once now, when the watcher picks its next deadline, and the only
#   input is the session's own stored end.
#
# The one thing an event cannot see is a write that moves the moment -- a
# session created in the past, an edited start or length, a new freeze. The
# middleware in server.py wakes the watcher on any non-GET request, which is
# the single place in the app where "something may have changed" is knowable,
# so a write endpoint added later is covered without anyone remembering to.

# The watcher's bounds, and the second it waits past its deadline.
#
# `next_session_end()` answers `ends_at >= now` while `due_sessions()` wants
# `<`, so a pass landing *exactly* on a session's end finds nothing to do.
# Sleeping to the deadline therefore fired the event one floor-length late --
# measured at 5s against a running server, which is 5s of a slot reading
# `booked` after the class is over. WATCH_LAG is the fix: wake a second
# after the moment, not on it, and the comparison is satisfied on the first
# pass. The floor is then only a spin guard and can be the same second.
#
# The ceiling is a heartbeat for what a sleeping process cannot see -- a
# suspended laptop, a clock jump -- and never delays an event: each wake
# recomputes the deadline, so the nearest one still wins to the second.
WATCH_LAG = 1
WATCH_WAIT_MIN = 1
WATCH_WAIT_MAX = 3600


def _next_midnight(now: int) -> int:
    """
    The start of tomorrow, local time.

    The watcher's deadline is capped at this because one job in it is about a
    *date* rather than an instant: lift_expired_freezes() compares
    `frozen_until` against today, so a freeze ending tomorrow becomes due at
    tomorrow's midnight, which no session's end need coincide with.
    """
    return int(datetime.combine(
        date.fromtimestamp(now) + timedelta(days=1), _t.min).timestamp())


def _frozen_plan_ids(repo) -> list:
    """The plans a settle must skip. One read, passed down."""
    return [r["id"] for r in repo.find("subscriptions", {"frozen_on": {"ne": None}})]


def session_ended(repo, session_id: int, frozen=None) -> int:
    """
    One session's duration has run out: write off whoever never checked in,
    and close the session. Returns how many slots were settled.

    **This is the event, and it is the only thing that creates an absence.**
    Being called *is* the statement that the session has finished, so nothing
    here re-derives that from `ends_at`, a duration, or a date -- which is
    what makes a wrong stored end a wrong *alarm time* rather than a client
    marked absent while the class is still being taught.

    A frozen plan is skipped so a paused client never loses a session;
    `frozen` is those ids when the caller has already read them, because
    working through a backlog must not be one read of that collection per
    session. The status goes to `completed` whether or not there was anybody
    to settle. Running it twice is harmless: the second pass finds no
    `booked` slots and a session already `completed`.
    """
    with repo.begin():
        if frozen is None:
            frozen = _frozen_plan_ids(repo)
        settled = repo.settle_session_absences(session_id, db.now(), frozen)
        repo.update("sessions", session_id, {"status": "completed"})
        return settled


def settle_due_sessions(repo) -> int:
    """
    Fire the event for every session whose end has already passed and which
    nobody has closed. Returns how many slots were settled in total.

    Normally there is nothing to do, because the watcher fires each event on
    time. This is the backlog: the laptop was off overnight, the process was
    restarted, or a write has just put a session in the past. It is what
    startup runs, and what each wake of the watcher runs before working out
    when to sleep until.

    Dated freezes are lifted first -- a plan that came out of a freeze last
    week should have its slots settled normally, and one still frozen is
    skipped entirely. The frozen plans are read once for the whole backlog
    rather than once per session.

    Each session is its own transaction deliberately: a backlog is not one
    change, and one session that cannot be settled must not take the other
    forty with it.
    """
    frozen = _frozen_plan_ids(repo)
    with repo.begin():
        lifted = lift_expired_freezes(repo, repo.find(
            "subscriptions", {"frozen_on": {"ne": None}}))
    still_frozen = [i for i in frozen if i not in lifted]
    return sum(session_ended(repo, s["id"], still_frozen)
               for s in repo.due_sessions(db.now()))


def seconds_to_next_end(repo) -> float:
    """
    How long until the next session end -- or the next midnight, whichever is
    sooner, since a dated freeze comes due on one of those.

    Read after the backlog has been worked through, never before: a session
    settled a moment ago must not still be the answer.
    """
    now = db.now()
    nxt = repo.next_session_end(now)
    due = min(_next_midnight(now), nxt) if nxt else _next_midnight(now)
    return max(WATCH_WAIT_MIN, min(due - now + WATCH_LAG, WATCH_WAIT_MAX))


# ---------------------------------------------------------------- scanning
#
# The four questions a scan asks about a person, all decided from the one
# flat list of their bookings that `repo.client_bookings()` returns. They
# were four port methods, and on a networked backend each one re-fetched the
# same bookings and re-joined the same sessions behind them -- thirteen round
# trips to answer four questions about rows already in hand, with a client
# standing at the desk. See the note in repo/ports.py.
def _recent_attendance(rows, limit) -> list:
    """Their last few settled sessions, newest first."""
    done = [r for r in rows if r["status"] != "booked"]
    done.sort(key=lambda r: (-r["starts_at"], -r["session_id"]))
    return [{"status": r["status"], "checked_in_at": r["checked_in_at"],
             "session_id": r["session_id"], "starts_at": r["starts_at"],
             "class_name": r["class_name"], "colour": r["colour"]}
            for r in done[:limit]]


def _next_booked_session(rows, after, class_id=None):
    """
    The next session they are booked into, or None. Scoped to a class when
    the card names one — a Ballet card answering with a Flexibility date is
    true but not the question asked.
    """
    ahead = [r for r in rows
             if r["status"] == "booked" and r["starts_at"] > after
             and (not class_id or r["session_class_id"] == class_id)]
    if not ahead:
        return None
    r = min(ahead, key=lambda x: (x["starts_at"], x["session_id"]))
    return {"starts_at": r["starts_at"], "class_name": r["class_name"]}


def _client_totals(rows) -> dict:
    """`{"present", "absent", "last_visit"}` across their whole history."""
    present = [r for r in rows if r["status"] == "present"]
    return {"present": len(present),
            "absent": sum(1 for r in rows if r["status"] == "absent"),
            "last_visit": max((r["starts_at"] for r in present), default=None)}


def _client_payload(repo, client, sub, rows, class_id=None) -> dict:
    state = plan_state(repo, sub["id"]) if sub else {}

    recent = _recent_attendance(rows, 4)

    # "Next class" means the next one on the card being held. A client who
    # takes Ballet and Flexibility was being shown whichever came first
    # across both, so the Ballet card could answer with a Flexibility date —
    # true, but not what was asked. Scoped to the card's class; a
    # member-number lookup names no class and still spans everything.
    nxt = _next_booked_session(rows, db.now(), class_id)
    tot = _client_totals(rows)

    return {
        "phone": client["phone"], "age": client["age"], "school": client["school"],
        "plan": state.get("plan"),
        # Which plan, and for which class. The kiosk needs both to offer a
        # renewal at the desk: a plan is bought for one class, so "renew"
        # with no class named is not a question the model can answer.
        "plan_id": state.get("id"),
        "plan_class_id": state.get("class_id"),
        "plan_class_name": state.get("class_name"),
        "sessions_total": state.get("sessions_total"),
        "sessions_remaining": state.get("remaining"),
        "expires_on": state.get("expires_on"),
        # The rest of the plan as it stands, for the kiosk's update panel:
        # it opens on the plan the card just proved, so every field has to
        # arrive with the verdict rather than costing a second round trip
        # with a client waiting at the counter.
        "plan_starts_on": state.get("starts_on"),
        "plan_price": state.get("price"),
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
    """
    Read-only. Works out who this is and which session they are here for.

    It settles nothing on the way past, and used not to: the sweep ran here
    first so a no-show's slot could not still read `booked`. The end-of-
    session event has already done that by the time anybody scans -- see the
    note above session_ended() -- which takes four round trips off the one
    path with a client standing at the desk.
    """
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
    rows = repo.client_bookings(client["id"])
    base = {**known, **_client_payload(repo, client, sub, rows, cred["class_id"])}
    if sub is None and cred["class_id"]:
        _log(repo, client["id"], cred["id"], None, "deny", "no plan for that class")
        return _deny(f"No {cred['class_name']} plan",
                     detail="this card is for a class they are not enrolled in",
                     **base)
    return _decide(repo, client, cred, base, db.now(), rows, sub)


def _decide(repo, client, cred, base, t, rows, sub):
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
    today_rows = [r for r in rows if start <= r["starts_at"] < end]

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
        # **The epoch travels; the kiosk renders it.** This used to be
        # `time.strftime(... time.localtime(at))` -- the *server's* timezone
        # -- which made this the one place in the app that printed a clock
        # time from anywhere but the browser in front of the person. Every
        # other screen renders an instant with `toLocaleTimeString`, so a
        # server process one hour off the browser (the app under WSL with no
        # TZ while Chrome has Egypt's summer offset is exactly that shape)
        # printed one check-in as two different times on two screens, with
        # nothing wrong with the clock at all -- and it reads as the app
        # adding an hour when it saves. One instant, one renderer.
        when = at if at and start <= at <= end else None
        _log(repo, cid, cred_id, None, "deny", "already checked in today")
        # The code is what puts the door on this one refusal -- see the note
        # in reception.html's show(). A client who is already checked in today
        # has a spent slot proving they belong inside; stepping out and coming
        # back is not a second visit to be charged for, and it is not a reason
        # to make them wait at a locked door either.
        return _deny(f"Already checked in today for {done['class_name']}",
                     detail="nothing was deducted", severity="warn",
                     code="already_checked_in", checked_in_at=when, **base)

    # The card's own plan: freezing the ballet plan must not turn away a
    # client arriving for the flexibility class she is paid up in. Passed in
    # rather than looked up again — both callers have already asked
    # active_plan() this exact question, with the same class, to build `base`.
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

    # Not paid for, and the trust has run out.
    #
    # Deliberately here, *after* a session of theirs has been found: a client
    # with nothing on today should be told that, not chased for money on a
    # day they were never due. And deliberately before the absent branch
    # below, whose MANUAL CHECK-IN spends a slot exactly like a scan does --
    # letting that through would be letting them in unpaid by another door.
    #
    # The count is of this plan's own used slots, taken from the bookings
    # already in hand rather than re-queried. It is gated on the booking
    # belonging to the card's live plan: an older, already-renewed plan's
    # leftover slot is finished business and not what reception would be
    # collecting for.
    if (sub and not sub["paid_on"] and row["subscription_id"] == sub["id"]):
        used = sum(1 for r in rows
                   if r["subscription_id"] == sub["id"]
                   and r["status"] in ("present", "absent"))
        if used >= UNPAID_GRACE_SESSIONS:
            _log(repo, cid, cred_id, row["session_id"], "deny", "plan not paid")
            spent = f"{used} session{'' if used == 1 else 's'}"
            return _deny(
                f"Not paid for yet — {spent} already taken",
                detail=("take the payment and put the date on the plan; "
                        "they are checked in as soon as it saves"),
                code="unpaid_plan", **base)

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
    client = repo.get("clients", client_id)
    if client is None:
        return _deny(f"No client with number {client_id:05d}")
    if not client["active"]:
        return _deny("Client is not active")

    sub = active_plan(repo, client_id)
    rows = repo.client_bookings(client_id)
    base = {
        "client_id": client["id"], "credential_id": None,
        "name_en": client["name_en"], "photo_path": client["photo_path"],
        "card_class": None, "card_colour": None,
        **_client_payload(repo, client, sub, rows),
    }
    return _decide(repo, client, None, base, db.now(), rows, sub)


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
    start, end = day_bounds()
    now = db.now()

    today = [s for s in repo.sessions_in_range(start, end, not_booked_by=client_id)
             if s["status"] != "cancelled"]

    slots = repo.giveable_slots(client_id, now)
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
            # **This stamp is when somebody pressed Present, not when the
            # client arrived**, and `checked_in_by` is what finally says so.
            # One column labelled CHECKED IN was covering both meanings:
            # `check_in_booking()` writes the instant the client was at the
            # door, and this writes the instant the roster was taken -- which
            # for a class marked up at the end is an hour or more later. With
            # no scanner on the desk yet, almost every mark in the academy's
            # database is this one, and the gap was reported three times as
            # the app adding an hour to a check-in.
            "checked_in_at": db.now() if status == "present" else None,
            "checked_in_by": "roster" if status == "present" else None,
            # An absence marked by hand is reception's, not the sweep's, so
            # the stamp saying "the sweep wrote this off" comes off the row
            # either way -- otherwise a client marked present keeps the time
            # they were written off beside their check-in.
            "settled_at": None})
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
    rather than a move the end-of-session event could flip to absent before
    the status lands.
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


def renewable_sessions(repo, class_id: int, client_id: int, want: int) -> list:
    """
    The earliest `want` sessions of a class this client could still attend.

    What "still" means is **not finished yet**, not "starts in the future":
    the session someone is standing at the desk for has usually already
    started by the time they scan. Dropping it would make the commonest
    renewal — a client arriving for today's class with nothing left — hand
    back a plan that cannot let them into the class they came for.

    Cancelled sessions are dropped (a slot on one is meaningless, the same
    filter lib/planSessions.js applies), and so is anything they already
    hold a slot in, which is what `not_booked_by` is for.
    """
    now = db.now()
    rows = repo.sessions_in_range(day_bounds(now)[0], None, class_id=class_id,
                                  not_booked_by=client_id)
    live = [s for s in rows
            if s["status"] != "cancelled" and (s["ends_at"] or 0) > now]
    live.sort(key=lambda s: (s["starts_at"], s["id"]))
    return [s["id"] for s in live[:want]]


def sessions_from_start(repo, class_id: int, client_id: int, start_day: str,
                       want: int, plan_id: int = None) -> dict:
    """
    A plan's dates worked out from two answers: when it starts, and how many
    sessions it buys. The first `want` sessions of that class from that day
    onwards, and the end date that follows from the last of them.

    **It lives here rather than in each form**, because there are three of
    them — the client profile's plan picker and its Edit, and the reception
    kiosk's update panel, which has no session list to tick at all. Written
    once in each would be three answers to "which four sessions is 1 October
    plus four", and they would drift on the edges below rather than on the
    obvious part.

    The edges, all of which are decisions rather than details:

    * **Already-attended sessions are kept, whatever the start day says**,
      and they count toward `want`. They are attendance history; `edit_plan()`
      refuses any edit that drops one, so a rule that quietly excluded them
      would produce a set the server will not accept. That is also why they
      are counted rather than added on top — four sessions means four.
    * **This plan's own upcoming slots are candidates again**, not
      obstacles. `not_booked_by` rightly hides dates the client already
      holds, but the plan's own are exactly the ones being re-picked: without
      them, changing 4 sessions to 5 would skip the four it already had and
      offer four *different* dates.
    * **A day already gone is offered.** This is the one place that differs
      from "auto-fill earliest", which skips the past on purpose because
      creating absences in bulk is not a decision to take by accident. Here
      the receptionist has *typed* the start day, and writing a plan down
      after the client started coming is precisely why the window reaches
      three weeks back. The count comes back in `past` so the form can say
      how many will be recorded as absent before anything is saved.
    * **A short timetable is reported, not padded.** `short` is how many
      fewer than `want` exist; the caller decides whether that blocks a save.
    * **A plan cannot go below its own attendance**, so asking for fewer
      sessions than it has already used answers with those sessions and
      `kept` says how many they are. That is a floor, not a miscount: the
      forms turn it into "n sessions are already attended — the plan cannot
      go below that" rather than silently dropping one.
    """
    lo = int(datetime.combine(date.fromisoformat(start_day), _t.min).timestamp())

    mine = repo.plan_sessions(client_id, plan_id) if plan_id else []
    kept = [r for r in mine if r["status"] != "booked"]
    reuse_ids = [r["session_id"] for r in mine if r["status"] == "booked"]

    pool = [s for s in repo.sessions_in_range(lo, None, class_id=class_id,
                                              not_booked_by=client_id)
            if s["status"] != "cancelled"]
    if reuse_ids:
        pool += [s for s in repo.find("sessions", {"id": {"in": sorted(reuse_ids)}})
                 if s["class_id"] == class_id and s["status"] != "cancelled"
                 and s["starts_at"] >= lo]

    seen = {r["session_id"] for r in kept}
    fill = []
    for s in sorted(pool, key=lambda s: (s["starts_at"], s["id"])):
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        fill.append(s)

    room = max(0, want - len(kept))
    taken = fill[:room]
    chosen = ([{"id": r["session_id"], "starts_at": r["starts_at"]} for r in kept]
              + [{"id": s["id"], "starts_at": s["starts_at"]} for s in taken])
    chosen.sort(key=lambda r: (r["starts_at"], r["id"]))

    # `past` is what the form warns about, so it counts the sessions that
    # would *become* absences and nothing else: finished, and not already
    # attendance. Two distinctions that both matter -- an already-attended
    # date is a record, not a warning, and "finished" is the test the three
    # write paths actually book by (session_end, not starts_at), so the
    # class somebody is standing in right now is not counted as missed.
    now = db.now()
    return {
        "session_ids": [r["id"] for r in chosen],
        "expires_on": _iso_day(chosen[-1]["starts_at"]) if chosen else None,
        "past": sum(1 for s in taken if session_end(s) <= now),
        "short": max(0, want - len(chosen)),
        "kept": len(kept),
    }


def renew_at_desk(repo, client_id: int, class_id: int, plan: str,
                  sessions_total: int, price: float = None,
                  paid_on: str = None) -> dict:
    """
    Sell a client their next plan with them standing at the reception desk.

    The same sale `add_plan()` makes, with the dates chosen rather than
    picked. That is the whole reason this exists: every slot must be
    assigned to a real session before a plan saves, and a receptionist with
    a queue at the counter cannot tick twelve dates on a kiosk that has no
    tables and no modals. So the terms come from the desk and the dates come
    from the rule — the earliest sessions of that class they could still
    attend, which is what `PlanPicker`'s "auto-fill earliest" button already
    means on the admin side.

    Reception can correct any of those dates afterwards from the client's
    profile; what it cannot do is sell a plan that promises nothing.

    **The sale is all this does — no card, and no check-in.**

    The card is issued by the route above it, `POST /api/access/renew`,
    because drawing a PNG is presentation and this module holds the rules.
    Both ways in to a renewal issue one: the printed card carries the
    session count and the end date of the plan it was made for, so a
    renewal leaves the old one reading last month's figures. The cost is
    that issuing revokes the previous credential — the card in the client's
    hand stops scanning until the new one is printed — and the kiosk says
    so where reception can act on it. See that route's docstring.

    **It checks nobody in either.** The new plan's slots are booked but not
    spent, so a client who had nothing left scans again to use one. That
    keeps the deduction where it has always been — a scan, or a deliberate
    press — rather than something a sale does on its own.
    """
    client = repo.get("clients", client_id)
    if client is None or not client["active"]:
        return {"ok": False, "status": 404, "error": "no such client"}
    klass = repo.get("classes", class_id)
    if not klass:
        return {"ok": False, "status": 404, "error": "no such class"}
    if sessions_total < 1:
        return {"ok": False, "status": 400,
                "error": "a plan needs at least one session"}

    ids = renewable_sessions(repo, class_id, client_id, sessions_total)
    if len(ids) < sessions_total:
        # Said as the thing to do about it rather than as a rule that was
        # broken: the timetable is short, and the fix is to schedule more or
        # to sell a smaller plan.
        return {"ok": False, "status": 400,
                "error": (f"Only {len(ids)} {klass['name']} session"
                          f"{'' if len(ids) == 1 else 's'} are scheduled — "
                          f"schedule more, or sell a shorter plan.")}

    out = add_plan(repo, client_id, class_id, plan, sessions_total, ids,
                   price=price, paid_on=paid_on)
    if not out.get("ok"):
        return out
    return {**out, "state": plan_state(repo, out["id"])}


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

        klass = repo.get("classes", class_id)
        if not klass:
            return {"ok": False, "status": 404, "error": "no such class"}

        bad = _assignment_error(repo, client_id, session_ids, class_id,
                                f"{klass['name']} sessions")
        if bad:
            return {"ok": False, "status": 400, "error": bad}

        # Only this class's previous plan is retired. The client's other class
        # keeps running.
        repo.update_where("subscriptions",
                          {"client_id": client_id, "class_id": class_id},
                          {"active": 0})

        starts = starts_on or date.today().isoformat()
        # Validity follows the sessions the plan actually pays for: it runs
        # through the last of them. Reception can still type a date instead -- a
        # courtesy extension -- and that is what expires_on carries when set.
        expires = expires_on or last_of_sessions(repo, session_ids) or starts
        sub_id = repo.insert("subscriptions", {
            "client_id": client_id, "class_id": class_id, "plan": plan,
            "sessions_total": sessions_total, "price": price,
            "starts_on": starts, "expires_on": expires,
            "paid_on": paid_on or None,
            "notes": (notes or "").strip() or None, "created_at": db.now()})
        _book_slots(repo, client_id, sub_id, session_ids)
        return {"ok": True, "id": sub_id, "booked": len(session_ids),
                "class_id": class_id, "class_name": klass["name"]}


def edit_plan(repo, sub_id: int, plan: str = None, sessions_total: int = None,
             expires_on: str = None, session_ids: list = None,
             paid_on: str = None, clear_paid_on: bool = False,
             notes: str = None, class_id: int = None, starts_on: str = None,
             price: float = None) -> dict:
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

    starts_on and price are corrections of what was written down. starts_on
    matters beyond bookkeeping: it is one of the two answers
    sessions_from_start() derives a plan's dates from, so the forms re-pick
    the sessions when it changes.

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
        sub = repo.get("subscriptions", sub_id)
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
            klass = repo.get("classes", class_id)
            if klass is None:
                return {"ok": False, "error": "no such class"}
            if session_ids is None:
                return {"ok": False, "error": "changing the class means reassigning the sessions"}
            held = repo.count("subscriptions", {
                "client_id": sub["client_id"], "class_id": class_id,
                "active": 1, "id": {"ne": sub_id}})
            if held:
                return {"ok": False,
                        "error": f"this client already has a live {klass['name']} plan"
                                 " — renew that one instead"}

        current = repo.find("bookings", {"subscription_id": sub_id},
                            fields=["session_id", "status"])
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
                repo.delete_where("bookings", {"subscription_id": sub_id,
                                               "session_id": {"in": sorted(to_drop)}})
            _book_slots(repo, sub["client_id"], sub_id, sorted(new_ids))

        fields = {}
        if moving:
            fields["class_id"] = class_id
        if plan is not None:
            fields["plan"] = plan
        if sessions_total is not None:
            fields["sessions_total"] = sessions_total
        if starts_on is not None:
            # A correction of when the plan began, which is also what the
            # session dates are worked out from -- see sessions_from_start().
            fields["starts_on"] = starts_on
        if price is not None:
            # Editable because a plan is often written down before the amount
            # is settled, and because the desk takes the money with the client
            # standing there. NULL still means "nobody wrote it down" and is
            # reached by clearing the field, which the route turns into a 0
            # only if somebody types one -- see PlanEdit.
            fields["price"] = price
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
            repo.update("subscriptions", sub_id, fields)

        if expires_on:
            repo.update("subscriptions", sub_id, {"expires_on": expires_on})
        else:
            refresh_expiry(repo, sub_id)

        revoked = 0
        if moving:
            # The old class's card proved this plan. Revoke it unless another live
            # plan still stands behind that class — credentials are revoked, never
            # deleted, so the log keeps pointing at the one that was used.
            still = repo.exists("subscriptions", {
                "client_id": sub["client_id"], "class_id": sub["class_id"],
                "active": 1})
            if not still:
                revoked = repo.update_where(
                    "credentials",
                    {"client_id": sub["client_id"], "class_id": sub["class_id"],
                     "revoked_at": None},
                    {"revoked_at": db.now()})

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
        client = repo.get("clients", client_id)
        if not client:
            return {"ok": False, "status": 404, "error": "no such client"}
        klass = repo.get("classes", class_id)
        if not klass:
            return {"ok": False, "status": 404, "error": "no such class"}

        sub = active_plan(repo, client_id, class_id)
        if not sub:
            return {"ok": False, "status": 400,
                    "error": f"{client['name_en']} has no active {klass['name']} plan — "
                             f"add one before issuing this card"}

        # `class_id IS ?` rather than `= ?`: a card issued before cards had a
        # class carries NULL, and NULL = NULL is not true in SQL.
        # `(class_id IS ? OR class_id = ?)` collapses to plain equality here:
        # class_id is guaranteed non-null by the guard above, and `x IS 5`
        # differs from `x = 5` only when the parameter itself is NULL.
        live = {"client_id": client_id, "revoked_at": None, "class_id": class_id}
        old = repo.find_one("credentials", live)
        repo.update_where("credentials", live, {"revoked_at": db.now()})

        token = tokens.issue(client_id)
        repo.insert("credentials", {
            "client_id": client_id, "class_id": class_id, "token": token,
            "kind": "card", "issued_at": db.now()})

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

    Deleting the last live plan for a class also takes the client **out of
    that class**, which is what a receptionist means by deleting a plan: the
    class page lists "students with a booking", so a slot of theirs still
    sitting on a date next week left them on the roster of a class they had
    just been removed from. Only slots still ahead of them go, and only when
    no other live plan of theirs stands behind that class. Those slots are
    usually funded by an older, already-renewed plan, so each one is given
    back to whatever plan paid for it -- which is why this branch *does* call
    refresh_expiry(), on the other plans rather than on this one.

    Attendance in the class is never touched, even though this is the one
    deletion that takes attendance with it: what it takes is *this plan's*,
    because a plan's bookings are its attendance. A session another plan
    already paid for and the client already attended belongs to that plan's
    history, and is on screen in the payment list underneath.

    No refresh_expiry() for the deleted plan itself, unlike the other bulk
    booking deletes: the plan whose expiry would be recomputed is gone.
    """
    with repo.begin():
        sub = repo.get("subscriptions", sub_id)
        if not sub:
            return {"ok": False, "status": 404, "error": "no such plan"}

        # Counted before the delete, so the dialog says what will go
        # rather than what did.
        c = repo.plan_counts(sub_id)
        attended = c["present"] + c["absent"]
        total = c["assigned"]
        repo.delete_where("bookings", {"subscription_id": sub_id})
        repo.delete("subscriptions", sub_id)

        revoked = released = 0
        if sub["class_id"]:
            still = repo.exists("subscriptions",
                                {"client_id": sub["client_id"],
                                 "class_id": sub["class_id"], "active": 1})
            if not still:
                revoked = repo.update_where(
                    "credentials",
                    {"client_id": sub["client_id"], "class_id": sub["class_id"],
                     "revoked_at": None},
                    {"revoked_at": db.now()})
                released = release_from_class(repo, sub["client_id"],
                                              sub["class_id"])
        return {"ok": True, "bookings": total,
                "upcoming": total - attended,
                "attended": attended, "cards_revoked": revoked,
                "released": released}


def release_from_class(repo, client_id: int, class_id: int) -> int:
    """
    Take a client off a class's upcoming sessions. Returns how many slots.

    Membership is derived from bookings, so this is what "remove them from
    the class" actually consists of. Only what is still ahead of them: a
    booking already marked present or absent is the record of a session that
    happened, and belongs to the plan that paid for it.

    Every slot goes back to its own plan as unassigned, which is why the
    expiry of each one is refreshed -- these bookings are deleted directly
    rather than through unbook(), the same rule delete_session and
    delete_class follow.

    Not called on its own anywhere yet; delete_plan is its one caller. It is
    a named function rather than an inline block because "which bookings put
    someone in a class" is a question two screens already ask differently,
    and a third phrasing of it would eventually disagree with both.
    """
    theirs = [b for b in repo.find("bookings",
                                   {"client_id": client_id, "status": "booked"},
                                   fields=["id", "session_id", "subscription_id"])]
    if not theirs:
        return 0
    # The session's class, not the plan's: this is about who is standing in
    # the room, which is the same question the class page's student list asks.
    ahead = {x["id"] for x in repo.find(
        "sessions",
        {"id": {"in": [b["session_id"] for b in theirs]}, "class_id": class_id,
         "status": "scheduled", "starts_at": {"gt": db.now()}}, fields=["id"])}
    drop = [b for b in theirs if b["session_id"] in ahead]
    if not drop:
        return 0
    repo.delete_where("bookings", {"id": {"in": [b["id"] for b in drop]}})
    for sub_id in {b["subscription_id"] for b in drop if b["subscription_id"]}:
        refresh_expiry(repo, sub_id)
    return len(drop)


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
        # The one read path that still works the backlog of session ends
        # itself, rather than leaving it to the event. The hard-delete guard
        # below counts bookings that are no longer `booked`, so a no-show
        # nobody has written off yet reads as a client with no history at
        # all -- and this is a destructive, irreversible path that nobody is
        # waiting on. It costs one indexed read that normally finds nothing.
        settle_due_sessions(repo)

        client = repo.get("clients", client_id)
        if not client:
            return {"ok": False, "status": 404, "error": "no such client"}

        if hard:
            visits = repo.count("bookings", {"client_id": client_id,
                                             "status": {"ne": "booked"}})
            if visits:
                return {"ok": False, "status": 400,
                        "error": f"{client['name_en']} has {visits} recorded sessions "
                                 f"— archive instead"}
            # Ordered by hand to respect the foreign keys.
            for coll in ("bookings", "credentials", "subscriptions", "access_events"):
                repo.delete_where(coll, {"client_id": client_id})
            # Their photo and every card ever printed for them. These used to
            # be files nothing ever removed, so a hard delete left them on the
            # disk; now they are rows, and rows nobody can reach are the
            # bulkiest thing in the database.
            images.drop(repo, images.CLIENT_PHOTO, client_id)
            images.drop(repo, images.CARD, client_id)
            repo.delete("clients", client_id)
            return {"ok": True, "action": "delete"}

        # The same list the profile shows, so the number in the error is the
        # number reception is looking at.
        upcoming = len(repo.client_upcoming(client_id, db.now()))
        if upcoming:
            return {"ok": False, "status": 400,
                    "error": f"{client['name_en']} has {upcoming} upcoming session"
                             f"{'' if upcoming == 1 else 's'} — remove or reassign "
                             f"{'it' if upcoming == 1 else 'them'} before archiving"}

        repo.update("clients", client_id, {"active": 0})
        # Archiving revokes the card, so a restored client needs a new one.
        repo.update_where("credentials",
                          {"client_id": client_id, "revoked_at": None},
                          {"revoked_at": db.now()})
        # The guard above ignores bookings whose session was cancelled, so
        # those are the ones still left to release here.
        repo.delete_where("bookings", {"client_id": client_id,
                                       "status": "booked"})
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
    session_ids = list(dict.fromkeys(session_ids))
    if not session_ids:
        return {"ok": True, "deleted": 0, "released": 0, "blocked": []}

    with repo.begin():
        # A fixed number of round trips rather than seven-plus per session:
        # clearing a 96-session term used to be several hundred. What each
        # session is, and every booking across all of them, in two queries.
        rows = {r["id"]: r for r in repo.find(
            "sessions", {"id": {"in": session_ids}})}
        names = {c["id"]: c["name"] for c in repo.find(
            "classes", {"id": {"in": sorted({r["class_id"] for r in rows.values()})}})}
        bookings = repo.find("bookings", {"session_id": {"in": session_ids}})

        held, total, subs_by_session = {}, {}, {}
        for b in bookings:
            sid = b["session_id"]
            total[sid] = total.get(sid, 0) + 1
            # Anything that is no longer merely 'booked' is attendance.
            if b["status"] != "booked":
                held[sid] = held.get(sid, 0) + 1
            if b["subscription_id"] is not None:
                subs_by_session.setdefault(sid, set()).add(b["subscription_id"])

        # Iterated in the order asked for, so `blocked` reads the same way it
        # always did.
        doomed, blocked, released = [], [], 0
        for sid in session_ids:
            r = rows.get(sid)
            if not r:
                continue
            if held.get(sid) and not force:
                blocked.append({"id": sid, "starts_at": r["starts_at"],
                                "class_name": names.get(r["class_id"]),
                                "attendance": held[sid]})
                continue
            doomed.append(sid)
            released += total.get(sid, 0)

        if doomed:
            repo.delete_where("bookings", {"session_id": {"in": doomed}})
            # Nulled rather than cascaded: the event records that someone
            # scanned, which stays true after the session is gone.
            repo.update_where("access_events", {"session_id": {"in": doomed}},
                              {"session_id": None})
            repo.delete_where("sessions", {"id": {"in": doomed}})
            # Only the plans that actually lost a booking -- a plan behind a
            # session held back by `force` still runs through its own date.
            refresh_expiries(repo, {sub for sid in doomed
                                    for sub in subs_by_session.get(sid, ())})

        return {"ok": True, "deleted": len(doomed), "released": released,
                "blocked": blocked}


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
        klass = repo.get("classes", class_id)
        if not klass:
            return {"ok": False, "status": 404, "error": "no such class"}

        # The class's own sessions, which both branches work from. A subquery
        # in a DELETE has no document-store equivalent; the id list does.
        mine = [x["id"] for x in repo.find("sessions", {"class_id": class_id},
                                           fields=["id"])]

        if hard:
            held = repo.count("bookings", {"session_id": {"in": mine},
                                           "status": {"ne": "booked"}})
            if held:
                return {"ok": False, "status": 400,
                        "error": f"{klass['name']} has {held} attendance records "
                                 f"— archive instead"}
            subs = {x for x in repo.distinct("bookings", "subscription_id",
                                             {"session_id": {"in": mine}})
                    if x is not None}
            repo.delete_where("bookings", {"session_id": {"in": mine}})
            repo.delete_where("sessions", {"class_id": class_id})
            repo.delete("classes", class_id)
            refresh_expiries(repo, subs)
            return {"ok": True, "action": "delete",
                    "released_sessions": None, "released_bookings": None}

        upcoming = [x["id"] for x in repo.find(
            "sessions",
            {"class_id": class_id, "status": "scheduled",
             "starts_at": {"gt": db.now()}}, fields=["id"])]
        released_bookings = 0
        if upcoming:
            subs = {x for x in repo.distinct("bookings", "subscription_id",
                                             {"session_id": {"in": upcoming}})
                    if x is not None}
            released_bookings = repo.count("bookings",
                                           {"session_id": {"in": upcoming}})
            repo.delete_where("bookings", {"session_id": {"in": upcoming}})
            repo.delete_where("sessions", {"id": {"in": upcoming}})
            refresh_expiries(repo, subs)
        repo.update("classes", class_id, {"active": 0})
        return {"ok": True, "action": "archive",
                "released_sessions": len(upcoming),
                "released_bookings": released_bookings}


def expected_today(repo) -> dict:
    """
    Today's expected/arrived/absent counts.

    Deliberately does NOT settle: it used to, and its only route caller
    (api/dashboard.py) already settles at the top of the request, so the
    landing page paid for two full sweeps -- each one a pair of
    collection-wide updates -- to answer one question. Callers settle.
    """
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


def logged_hours(repo, instructor_id: int, period_from: str, period_to: str,
                 rate: float = None) -> dict:
    """
    Hours the salary sheet recorded for this instructor within a period, plus
    any manual corrections layered on top (see instructor_hour_adjustments) --
    never mixed into the sheet's own rows, so what the sheet actually said
    stays visible. `days`/`from`/`to` count only real salary-sheet rows; a
    correction is not a claim of an extra day worked.
    """
    sheet = repo.salary_hours(instructor_id, period_from, period_to)
    if rate is None:
        # Only fetched when the caller does not already hold the row. The
        # instructor page does, and this was a second round trip for a value
        # it had read three lines earlier.
        who = repo.get("instructors", instructor_id)
        rate = (who["hourly_rate"] or 0) if who else 0
    return {**sheet, "pay": round(sheet["hours"] * rate, 2)}


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
    t = repo.taught_totals_bulk([instructor_id], start_ts, end_ts)[instructor_id]
    adjustment = repo.adjustments_sum(instructor_id, period_from, period_to)
    scheduled = t["hours"]
    return {"sessions": t["sessions"], "scheduled": scheduled,
            "adjustment": adjustment,
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
        repo.insert("instructor_hour_adjustments", {
            "instructor_id": instructor_id, "adjustment_date": day,
            "delta_hours": delta, "note": note, "created_at": db.now()})
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
    # Both windows in one pass. They are two counts over the same collection
    # and the dashboard shows them side by side, so asking twice is a round
    # trip spent on arithmetic. That pass is a scan rather than two index
    # ranges, which on a few hundred clients is nothing next to the trip it
    # saves; the note above still governs `takings()` below, where the range
    # really is what an index serves.
    new_clients, before = repo.joined_counts([
        (month, next_month(month_to)),
        (prev_from, next_month(prev_to)),
    ])

    def takings(date_field: str) -> tuple:
        r = repo.takings(date_field, month, next_month(month_to))
        return r["paid"], r["unpriced"], r["plans"]

    # Every plan belonging to a client who joined in the period, whenever they
    # bought it — see the note above on why this is not also filtered by when
    # the plan started.
    new_paid, new_unpriced, new_plans = takings("joined_on")
    # Every plan sold in the period, whoever bought it. This one is the
    # period's own takings and stays inside the window.
    all_paid, all_unpriced, all_plans = takings("starts_on")

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
# What needs doing on one particular day
#
# "Needs attention" used to be a standing condition -- two sessions or
# fewer left, expiring inside a week, slots with no dates written down.
# That is a fair description of a client and a poor description of a day's
# work: the same twenty names sat on it for a fortnight, so it stopped
# being read, and the day a plan actually ran out looked exactly like the
# four days either side of it.
#
# These two questions are about a day instead, and both are a renewal
# conversation that can be had while the client is still in the building:
#
#   renew    -- the plan ends on this day
#   ran_out  -- the last slot they hold falls on this day, so they finish
#               today with nothing for next week
#
# There was a third, "one session left". It is gone: a warning about a day
# still to come rather than a thing to do on this one, and on a screen whose
# job is renewals it padded every day with people still coming next week.
#
# The day is passed in rather than read off the clock, so the Cards screen
# can ask about any date and opens on today. Same shape as
# `sessions_in_range(start, end)` and `settle_session_absences(sid, now, ...)`.
# ======================================================================

def next_day(iso: str) -> str:
    """The day after an ISO day. The upper bound of a one-day range."""
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def day_attention(repo, day: str, states: dict) -> dict:
    """
    Which of these plans want something doing about them on `day`.

    `states` is what `plan_states()` already gave the caller -- both callers
    are holding it before they get here, and asking for it again would be a
    round trip spent on rows in hand.

    Returns `{sub_id: {"renew", "ran_out", "remaining_on"}}` for the plans
    that match either, and nothing at all for the rest, so a caller filters
    on membership.

    **Two questions, not three.** "One session left" was a third and is
    gone: it is a warning about a day still to come rather than a thing to
    do on this one, and on a screen whose job is renewals it padded every
    day's list with people who are still coming next week. What is left is
    the two that are finished business by the end of the day -- the plan
    ends, or the last slot they hold falls on it -- and those are what a
    renewal conversation is actually about.

    **A frozen plan is never in it.** It is deliberately paused; a paused
    plan "running out" is not something anybody acts on, and it is the same
    exclusion the standing rule made.

    Remaining is counted **as of that day**, from where each slot actually
    falls: everything on or before `day` is spent, everything after is still
    theirs. For today that is the figure as the day ends, which is the one
    worth acting on while they are at the desk -- a client whose last
    session is this evening needs the conversation now, not tomorrow.
    Unassigned slots stay theirs, so a plan with dates still to write down
    can never read as run out.
    """
    if not states:
        return {}
    slots = repo.plan_slot_times(list(states))
    out = {}
    for sid, st in states.items():
        if st["frozen"]:
            continue
        days = [_iso_day(t) for t in slots.get(sid, ())]
        total = st["sessions_total"] or 0
        remaining_on = max(0, total - sum(1 for d in days if d <= day))
        remaining_before = max(0, total - sum(1 for d in days if d < day))
        # "Ran out on this day", not "is at zero": the difference is whether
        # a client who finished last month reappears on every list for ever.
        # The slot that took them to nothing has to be this day's own.
        ran_out = remaining_on == 0 and remaining_before > 0
        # The plan's own end date, not "has expired by now". Every other
        # question on this screen is about one day, and "expired at some
        # point before this" is the standing condition the day filter
        # replaced -- it would put every lapsed client on every later day.
        renew = st["expires_on"] == day
        if renew or ran_out:
            out[sid] = {"renew": renew, "ran_out": ran_out,
                        "remaining_on": remaining_on}
    return out


def day_income(repo, day: str) -> dict:
    """
    What was actually taken on one day: `{"paid", "plans", "unpriced"}`.

    Keyed on `paid_on`, never on when the plan was typed in. Reception
    writes a plan down when the client asks for it and dates the payment for
    the day it is due, so a plan entered on Tuesday for Thursday's money
    belongs to Thursday -- open the dashboard on Thursday and there it is.
    Dating it by entry would put Thursday's cash in Tuesday's till and take
    it out again the next morning.

    An unpaid plan has no date at all and falls out of every day rather than
    counting as zero in one, which is the rule the month's figures already
    follow.
    """
    plans = repo.takings("paid_on", day, next_day(day))
    shop = repo.shop_takings(day, next_day(day))
    return {"paid": round(plans["paid"] + shop["paid"], 2),
            "plans": plans["plans"],
            "unpriced": plans["unpriced"],
            "plan_paid": round(plans["paid"], 2),
            "shop_paid": round(shop["paid"], 2),
            "shop_sales": shop["sales"],
            "shop_items": shop["items"],
            "shop_unpriced": shop["unpriced"]}


# ---------------------------------------------------------------- the shop
#
# The academy sells leotards, shoes and hairnets over the counter, and until
# there was a products table that was money the till figure could not see.
# A sale is deliberately built out of the same pieces a plan is: a `paid_on`
# dated the day the money arrives, a price that is *nullable* because a thing
# nobody wrote a price for must not be reported as zero, and a figure that
# counts the unpriced separately rather than folding them in.

# ---- sizes ----------------------------------------------------------
#
# A leotard comes in sizes and a hairnet does not, so sizes are per product
# rather than a thing every product has. They are represented uniformly all
# the same: a product with no sizes has exactly **one** `product_stock` row
# whose `size` is NULL. That is what keeps one code path -- a sale always
# names a size (possibly NULL), the shelf count is always the sum of the
# rows, and there is no sizeless branch to drift from the sized one.
#
# Sizes are numeric because the academy's are: 34, 36, 38, and the halves in
# between for shoes. REAL rather than INTEGER for the same reason
# `clients.age` is -- 34.5 rounded to 34 is the distinction the fitting was
# made on.


def stock_rows(repo, product_id: int) -> list:
    """
    This product's shelf, one row per size, no sizes first then ascending.

    The order is what every screen shows, so it is decided once here rather
    than in each of them: NULL ("no size") is not a number and cannot sort
    among the numbers, and putting it first is what makes a sizeless
    product read as a single quantity instead of a stray row under the
    list.
    """
    rows = repo.find("product_stock", {"product_id": product_id})
    return sorted(rows, key=lambda r: (r["size"] is not None,
                                       r["size"] if r["size"] is not None else 0,
                                       r["id"]))


def _sync_stock(repo, product_id: int, rows=None) -> int:
    """
    **The one writer of `products.stock`**, which is the sum of the rows.

    Stored rather than derived because the shop list is one round trip and
    the figure on every card is the answer to "have we got any left"; one
    writer because two is how `sessions_used` became a column nothing could
    trust. Every path that changes a quantity ends here -- selling,
    unselling, and rewriting the sizes -- so there is nowhere for the total
    and the rows to disagree.

    Always called inside the caller's transaction; it does not open one.
    """
    rows = stock_rows(repo, product_id) if rows is None else rows
    total = sum(int(r["qty"] or 0) for r in rows)
    repo.update("products", product_id, {"stock": total})
    return total


def _size_key(v):
    """
    A size as it is stored and compared: a float, or None for "no size".

    `""` and `None` both mean no size -- a form sends the empty string and
    an API caller sends neither -- and 36 and "36" are the same shelf, which
    is the comparison that matters when a sale has to find the row it came
    off.
    """
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return float(v)


def set_product_sizes(repo, product_id: int, sizes) -> dict:
    """
    Rewrite which sizes a product comes in and how many of each.

    `sizes` is a list of `{"size": <number or None>, "qty": n}`. An empty
    list, or one entry with no size, both mean "this product has no sizes":
    the result either way is the single NULL row described above, so a
    product can gain sizes and lose them again without the rest of the app
    knowing which it ever was.

    **A size with sales behind it may not simply vanish.** Its quantity can
    go to zero -- that is "none left", a real answer and the normal end of a
    run -- but removing the row would leave a sale pointing at a shelf that
    does not exist, and `unsell_product()` would then have nowhere to put the
    thing back. Such a size is kept at zero and **named** in `kept`, the same
    treatment `repeat_sessions()` gives a date it cannot use: the screen says
    what it did instead of the edit silently doing less than it said.

    A quantity below what has already been sold is not refused. Stock is what
    is on the shelf *now*, and a sale is a receipt for something that left
    it; the two are different questions, and reception counting four in the
    box after selling six is a fact rather than a contradiction.
    """
    with repo.begin():
        p = repo.get("products", product_id)
        if p is None:
            return {"ok": False, "error": "no such product"}

        wanted = {}
        for entry in (sizes or []):
            try:
                key = _size_key(entry.get("size"))
                qty = int(entry.get("qty") or 0)
            except (TypeError, ValueError, AttributeError):
                return {"ok": False,
                        "error": "a size needs a number and an amount"}
            if qty < 0:
                return {"ok": False,
                        "error": "an amount cannot be negative. Zero is "
                                 "'none left', which is a real answer."}
            if key is not None and key <= 0:
                return {"ok": False, "error": "a size is a number above zero"}
            if key in wanted:
                said = "no size" if key is None else f"{key:g}"
                return {"ok": False,
                        "error": f"{said} is listed twice — one row per size"}
            wanted[key] = qty
        if not wanted:
            wanted = {None: 0}
        if len(wanted) > 1 and None in wanted:
            return {"ok": False,
                    "error": "a product either comes in sizes or it does "
                             "not — remove the sizeless row, or the sizes"}

        have = {_size_key(r["size"]): r for r in stock_rows(repo, product_id)}
        sold = _sold_by_size(repo, product_id)

        kept = []
        for key, row in have.items():
            if key in wanted:
                if int(row["qty"] or 0) != wanted[key]:
                    repo.update("product_stock", row["id"], {"qty": wanted[key]})
                continue
            if sold.get(key):
                n = sold[key]
                said = "no size" if key is None else f"size {key:g}"
                kept.append(f"{said} is kept at 0 — {n} already sold, and a "
                            f"sale has to be able to go back on the shelf")
                if int(row["qty"] or 0):
                    repo.update("product_stock", row["id"], {"qty": 0})
                continue
            repo.delete("product_stock", row["id"])

        for key, qty in wanted.items():
            if key not in have:
                repo.insert("product_stock", {"product_id": product_id,
                                              "size": key, "qty": qty})

        rows = stock_rows(repo, product_id)
        return {"ok": True, "stock": _sync_stock(repo, product_id, rows),
                "sizes": rows, "kept": kept}


def _sold_by_size(repo, product_id: int) -> dict:
    """How many of each size have been sold. One read."""
    out = {}
    for sale in repo.find("product_sales", {"product_id": product_id}):
        key = _size_key(sale["size"])
        out[key] = out.get(key, 0) + int(sale["qty"] or 0)
    return out


def ensure_stock_rows(repo) -> int:
    """
    Give every product that has no shelf rows the one NULL row that means
    "no sizes", carrying whatever `products.stock` already said.

    **A startup step rather than a `db.migrate()` one**, and for the reason
    written down beside `backfill_checkin_source.py`: `migrate()` only ever
    runs against SQLite, and the academy's own database may be on Atlas. It
    goes through `repo`, so it works on whichever backend is configured.

    **Idempotent by construction** -- a product is touched only when it has
    no rows at all -- which is the same shape `images.import_legacy_files()`
    has, and the reason this is safe to do on every start. The standing cost
    is one read of a small collection; the alternative was a script somebody
    has to remember to run, on an app where a product with no rows cannot be
    sold at all.

    Returns how many products it filled in, for the startup banner.
    """
    have = {r["product_id"] for r in repo.find("product_stock",
                                               fields=["product_id"])}
    missing = [p for p in repo.find("products", fields=["id", "stock"])
               if p["id"] not in have]
    if not missing:
        return 0
    with repo.begin():
        repo.insert_many("product_stock",
                         [{"product_id": p["id"], "size": None,
                           "qty": int(p["stock"] or 0)} for p in missing])
    return len(missing)


def sell_product(repo, product_id: int, client_id: int, qty=1,
                 paid_on: str = None, notes: str = None, size=None) -> dict:
    """
    Sell `qty` of a product to a client, and take it off the shelf.

    **The sale names a size, and the size is a shelf rather than a label.**
    `size=None` is "no size", which is the only thing a sizeless product has
    and therefore what it is always sold as; a product that *does* come in
    sizes refuses a sale that does not say which, because the stock figure
    the sale decrements belongs to one of them and guessing would take it
    off the wrong shelf. The number is recorded on the sale as well, so
    removing the sale puts the thing back where it came from -- and so that
    changing a product's sizes next term cannot rewrite what was handed over
    last month, exactly as `unit_price` protects what was charged for it.

    **The price is copied, not referenced.** `unit_price` is whatever the
    product cost at this moment; re-pricing it next term must not rewrite
    what today's till took. That is the opposite of instructor pay, which is
    `hours x rate` at read time precisely so a corrected rate re-prices the
    month -- the difference is whether the stored number is a receipt or an
    estimate, and a receipt is what this is.

    **Stock is checked before the insert**, the same count-then-insert shape
    `book()` uses for a plan's slots and under the same single-writer
    reality: it is enough for one laptop and is not a claim about concurrent
    writers.

    `paid_on` left out means **today** -- a sale over the counter is paid as
    it happens, which is the common case and the one a receptionist should
    not have to type. An explicit empty string means unpaid, and falls out
    of every day's till rather than counting as zero in one, exactly as an
    unpaid plan does.
    """
    with repo.begin():
        p = repo.get("products", product_id)
        if p is None or not p["active"]:
            return {"ok": False, "error": "no such product"}
        c = repo.get("clients", client_id)
        if c is None or not c["active"]:
            return {"ok": False, "error": "no such client"}
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 0
        if qty < 1:
            return {"ok": False, "error": "how many? one or more"}

        try:
            key = _size_key(size)
        except (TypeError, ValueError):
            return {"ok": False, "error": "a size is a number"}
        rows = stock_rows(repo, product_id)
        shelves = {_size_key(r["size"]): r for r in rows}
        sized = sorted(k for k in shelves if k is not None)
        if not rows:
            # Every product has a shelf: create_product() makes one and
            # ensure_stock_rows() fills in any that predate sizes, so this
            # is a product written straight into the table by something that
            # did neither. Named rather than guessed at, since inventing one
            # from `products.stock` is the second code path the single NULL
            # row exists to avoid.
            return {"ok": False,
                    "error": f"{p['name']} has no stock recorded at all — "
                             f"set how many there are first"}
        if key is None and sized:
            said = ", ".join(f"{k:g}" for k in sized)
            return {"ok": False,
                    "error": f"which size? {p['name']} comes in {said}"}
        if key not in shelves:
            said = ", ".join(f"{k:g}" for k in sized) or "no sizes"
            return {"ok": False,
                    "error": f"{p['name']} does not come in size {key:g} — "
                             f"it comes in {said}"}

        shelf = shelves[key]
        left = int(shelf["qty"] or 0)
        if qty > left:
            of_it = p["name"] if key is None else f"{p['name']} in {key:g}"
            return {"ok": False,
                    "error": f"only {left} {of_it} left in stock — "
                             f"add stock first, or sell {left or 'none'}"}

        day = _iso_day(db.now()) if paid_on is None else (paid_on or None)
        sale_id = repo.insert("product_sales", {
            "product_id": product_id, "client_id": client_id, "qty": qty,
            "unit_price": p["price"], "size": key, "paid_on": day,
            "notes": notes, "created_at": db.now()})
        repo.update("product_stock", shelf["id"], {"qty": left - qty})
        shelf["qty"] = left - qty
        return {"ok": True, "sale_id": sale_id, "size": key,
                "size_left": left - qty,
                "stock": _sync_stock(repo, product_id, rows),
                "paid": None if p["price"] is None else round(p["price"] * qty, 2)}


def unsell_product(repo, sale_id: int) -> dict:
    """
    Undo a sale: the row goes, the stock comes back, and the money leaves
    today's till.

    **This is a real delete, and it is the appointments exception rather than
    a hole in the deletion policy.** That policy protects the record of who
    attended what; a sale has no attendance, no plan, no card and nothing
    pointing at it. What it has instead is a wrong number in a day's takings,
    and a greyed-out row that still had to be subtracted somewhere would be
    worse at the only job the figure has. The stock is given back for the
    same reason the money is: the thing is still in the box.

    **It goes back on the shelf it came off**, which is why the sale records
    its size at all. A size whose row has since been removed is put back as
    a row of its own rather than added to another one -- `set_product_sizes()`
    keeps any size with sales behind it for exactly this reason, so arriving
    here means the shelf was removed before that rule existed, and inventing
    the number onto a different size would be worse than a size nobody
    stocks any more showing one in it.
    """
    with repo.begin():
        s = repo.get("product_sales", sale_id)
        if s is None:
            return {"ok": False, "error": "no such sale"}
        pid, qty = s["product_id"], int(s["qty"] or 0)
        key = _size_key(s["size"])
        repo.delete("product_sales", sale_id)
        if repo.get("products", pid) is not None:
            shelf = next((r for r in stock_rows(repo, pid)
                          if _size_key(r["size"]) == key), None)
            if shelf is None:
                repo.insert("product_stock", {"product_id": pid,
                                              "size": key, "qty": qty})
            else:
                repo.update("product_stock", shelf["id"],
                            {"qty": int(shelf["qty"] or 0) + qty})
            _sync_stock(repo, pid)
        return {"ok": True, "product_id": pid, "qty": qty, "size": key,
                "paid_on": s["paid_on"]}


def archive_product(repo, product_id: int, hard: bool = False) -> dict:
    """
    Stop selling a product. Archived by default, like everything else with
    history behind it: a sale points at it, and the client profile that
    lists that sale has to go on naming what was bought.

    `hard` is refused outright while any sale exists, for exactly that
    reason -- the same refusal `?hard=true` meets elsewhere when attendance
    exists.
    """
    with repo.begin():
        p = repo.get("products", product_id)
        if p is None:
            return {"ok": False, "error": "no such product"}
        sold = repo.count("product_sales", {"product_id": product_id})
        if hard:
            if sold:
                return {"ok": False,
                        "error": f"{sold} sale{'' if sold == 1 else 's'} name "
                                 f"this product — archive it instead, so the "
                                 f"clients who bought it still show what they "
                                 f"bought"}
            # The shelf rows go with it -- they are not history, they are
            # how many of a thing that no longer exists are in a box.
            repo.delete_where("product_stock", {"product_id": product_id})
            repo.delete("products", product_id)
            return {"ok": True, "deleted": True}
        repo.update("products", product_id, {"active": 0})
        return {"ok": True, "archived": True, "sales": sold}


# ======================================================================
# Freezing a plan
#
# A client goes away for a month and asks to pause. Three things have to
# happen, and missing any one of them makes the freeze worthless:
#
#   1. The sessions they are booked into during the pause are released. If
#      they stayed booked, each one's end-of-session event would mark it
#      absent and the client would come back to a plan with nothing left.
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
        sub = repo.get("subscriptions", sub_id)
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
        cutoff_to = (int(datetime.combine(date.fromisoformat(until), _t.min).timestamp())
                     if until else None)

        # The plan's still-booked slots, then their dates. Bounded by the
        # plan's size -- a dozen or two -- so filtering the window in Python
        # costs nothing and needs no join.
        booked = repo.find("bookings", {"subscription_id": sub_id,
                                        "status": "booked"})
        when = {x["id"]: x["starts_at"] for x in repo.find(
            "sessions", {"id": {"in": [b["session_id"] for b in booked]}},
            fields=["id", "starts_at"])}
        doomed = [b["id"] for b in booked
                  if when.get(b["session_id"], -1) >= cutoff_from
                  and (cutoff_to is None or when[b["session_id"]] < cutoff_to)]
        released = len(doomed)
        if released:
            repo.delete_where("bookings", {"id": {"in": doomed}})

        repo.update("subscriptions", sub_id,
                    {"frozen_on": start, "frozen_until": until})
        repo.insert("freezes", {
            "subscription_id": sub_id, "from_date": start, "until_date": until,
            "released": released, "reason": reason, "created_at": db.now()})
        return {"ok": True, "released": released, "frozen_on": start, "frozen_until": until}


def _apply_unfreeze(repo, sub: dict, on_date: str = None) -> int:
    """
    The two writes that lift a freeze, for a plan row already in hand.
    Returns how many days the freeze ran for.

    Shared by unfreeze_plan() and lift_expired_freezes() so the date
    arithmetic exists once: the expiry is pushed out by exactly as long as
    the plan was paused, which is the whole point of freezing.

    Deliberately no refresh_expiry() here -- see the note on that function.
    A freeze deletes future bookings on purpose, and this shift needs the
    stored expiry to still be where it was.
    """
    ended = on_date or date.today().isoformat()
    if ended < sub["frozen_on"]:
        ended = sub["frozen_on"]
    days = _days_between(sub["frozen_on"], ended)

    repo.update("subscriptions", sub["id"], {
        "frozen_on": None, "frozen_until": None,
        "frozen_days": (sub["frozen_days"] or 0) + days,
        "expires_on": _shift_date(sub["expires_on"], days)})
    repo.update_where("freezes",
                      {"subscription_id": sub["id"], "ended_on": None},
                      {"ended_on": ended, "days_added": days})
    return days


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

        days = _apply_unfreeze(repo, sub, on_date)
        state = plan_state(repo, sub_id)
        return {"ok": True, "days": days, "expires_on": state["expires_on"],
                "unassigned": state["unassigned"]}


def lift_expired_freezes(repo, frozen=None) -> set:
    """
    A freeze with an end date lifts itself. Returns the ids it lifted.

    A freeze comes due on a *date* rather than at a session's end, so this is
    the one job in settle_due_sessions() that is not an event: the watcher
    caps its sleep at the next local midnight for it, and a laptop left off
    over the whole freeze still comes back correct because startup runs the
    same pass.

    It must not go through unfreeze_plan(): that would re-read a row already
    in hand and then build a plan_state() nothing here looks at -- four round
    trips per due freeze instead of the two writes it actually takes.

    `frozen` is every currently frozen plan, when the caller has already read
    them -- settle_due_sessions() has them in hand, and two reads of one
    collection is one round trip more than the answer costs.
    """
    today = date.today().isoformat()
    if frozen is None:
        # `ne: None` matters on a document store: there, a range comparison
        # against a string also matches null, because BSON orders null first.
        frozen = repo.find("subscriptions", {"frozen_on": {"ne": None}})
    due = [r for r in frozen
           if r["frozen_until"] is not None and r["frozen_until"] <= today]
    for row in due:
        _apply_unfreeze(repo, row, on_date=row["frozen_until"])
    return {r["id"] for r in due}
