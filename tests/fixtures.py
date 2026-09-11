"""
A deterministic academy, built from nothing.

The three scripts this replaces looked their subjects up in the live
`academy.db` — "a client holding two cards", "a plan of 12+ sessions not
already frozen". That made them unrunnable without the academy's own
workbooks, and it meant a test could pass or fail depending on which term had
last been seeded.

`build_academy()` constructs the same shapes on purpose instead: someone
holding one card per class, a freezable plan and an unfreezable one, sessions
on both sides of now, an instructor with completed sessions and a rate.

It writes with plain SQL rather than through `access.py`. The fixture must
hold still while the code under test moves — a fixture built out of the
functions being tested cannot fail independently of them.
"""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import access
import db
import tokens

# Enough room that a plan can be filled from the past and still have future
# dates left over, which is what the three-weeks-back rule needs to be
# testable at all.
#
# The offsets are chosen so neither series ever lands on today: 22 is not a
# multiple of 3 and 15 is not a multiple of 7. An earlier version started the
# ballet series 21 days back, which put a session on today at 18:00 — so a
# scan matched whichever of the two was nearer to the clock, and the test
# passed or failed depending on the time of day it ran. Today's session is
# created once, deliberately, below.
BALLET_START_DAYS_AGO = 22   # every 3 days, 18:00
BALLET_SESSIONS = 20
FLEX_START_DAYS_AGO = 15     # weekly, 17:00
FLEX_SESSIONS = 12


def _ts(d: date, hour: int, minute: int = 0) -> int:
    """Local epoch seconds. Local, not UTC — the app has no timezone concept."""
    return int(datetime.combine(d, time(hour, minute)).timestamp())


def _ins(conn, table: str, **cols) -> int:
    marks = ",".join("?" * len(cols))
    names = ",".join(cols)
    return conn.execute(
        f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(cols.values())
    ).lastrowid


def add_session(conn, class_id, instructor_id, starts_at, hours=1.5, status=None):
    """
    Create a session with its `ends_at` set.

    The one way tests make a session. `ends_at` is stored rather than derived
    (see db.py), and a row missing it is invisible to slot_conflict() and to
    the absent sweep — a silent wrong answer. Going through here means a test
    cannot create one by hand and get that wrong.
    """
    ends = access.ends_at_of(starts_at, hours)
    return _ins(conn, "sessions", class_id=class_id, instructor_id=instructor_id,
                starts_at=starts_at, duration_hours=hours, ends_at=ends,
                status=status or ("completed" if ends < db.now() else "scheduled"))


def _card(conn, client_id: int, class_id: int) -> str:
    token = tokens.issue(client_id)
    _ins(conn, "credentials", client_id=client_id, class_id=class_id,
         token=token, kind="card", issued_at=db.now())
    return token


def _plan(conn, client_id, class_id, name, total, *, price=None,
          starts_on=None, paid_on=None) -> int:
    today = date.today()
    return _ins(conn, "subscriptions",
                client_id=client_id, class_id=class_id, plan=name,
                sessions_total=total, price=price,
                paid_on=paid_on,
                starts_on=(starts_on or today).isoformat(),
                # Rewritten to the real last session by _fill() below; a
                # placeholder here only so the NOT NULL column is satisfied.
                expires_on=(today + timedelta(days=90)).isoformat(),
                active=1, created_at=db.now())


def _fill(conn, client_id, sub_id, session_ids):
    """
    Book a plan's slots and set its end date to the last one, the way
    access.refresh_expiry() would.

    Past sessions are booked straight to a settled status so the fixture does
    not leave stray 'booked' rows behind now — anything that calls
    settle_past_sessions() would sweep them, and a test watching the sweep
    needs to own the only row it can touch.
    """
    now = db.now()
    last = None
    for i, sid in enumerate(session_ids):
        s = conn.execute("SELECT starts_at, duration_hours FROM sessions WHERE id=?",
                         (sid,)).fetchone()
        ended = s["starts_at"] + s["duration_hours"] * 3600 < now
        # Alternate present/absent so counts of each are non-trivial.
        status = ("present" if i % 3 else "absent") if ended else "booked"
        _ins(conn, "bookings", client_id=client_id, session_id=sid,
             subscription_id=sub_id, status=status,
             checked_in_at=(s["starts_at"] if status == "present" else None),
             created_at=now)
        last = max(last or s["starts_at"], s["starts_at"])
    if last is not None:
        conn.execute("UPDATE subscriptions SET expires_on=? WHERE id=?",
                     (date.fromtimestamp(last).isoformat(), sub_id))


def build_academy(conn) -> SimpleNamespace:
    """
    Populate an initialised, empty database. Returns the ids by name so a
    test can say `a.dual` rather than re-querying for the subject it wants.
    """
    with db.tx(conn):
        today = date.today()
        a = SimpleNamespace(conn=conn)

        # -------------------------------------------------- instructors
        # Both carry a rate: pay is hours x rate at read time, so a zero rate
        # makes the payroll assertions vacuous.
        a.ana = _ins(conn, "instructors", name="Ana Ferrer", phone="01000000001",
                     specialty="Ballet", hourly_rate=120.0, active=1)
        a.bea = _ins(conn, "instructors", name="Bea Nasr", phone="01000000002",
                     specialty="Flexibility", hourly_rate=100.0, active=1)

        # -------------------------------------------------- classes
        a.ballet = _ins(conn, "classes", name="Ballet Level 8",
                        description="Graded ballet", colour="#87438E",
                        duration_hours=1.5, level="level 8",
                        instructor_id=a.ana, active=1)
        a.flex = _ins(conn, "classes", name="Evening Flexibility",
                      description="Conditioning", colour="#EAAECA",
                      duration_hours=1.0, level="primary",
                      instructor_id=a.bea, active=1)

        # -------------------------------------------------- sessions
        a.ballet_sessions = []
        for i in range(BALLET_SESSIONS):
            d = today - timedelta(days=BALLET_START_DAYS_AGO) + timedelta(days=3 * i)
            starts = _ts(d, 18)
            a.ballet_sessions.append(
                add_session(conn, a.ballet, a.ana, starts, 1.5))

        a.flex_sessions = []
        for i in range(FLEX_SESSIONS):
            d = today - timedelta(days=FLEX_START_DAYS_AGO) + timedelta(days=7 * i)
            starts = _ts(d, 17)
            a.flex_sessions.append(
                add_session(conn, a.flex, a.bea, starts, 1.0))

        # Neither recurring series may land on today, or a scan matches whichever
        # of two sessions is nearer the clock and the suite passes or fails by
        # the hour it runs at. Checked rather than trusted: the offsets above are
        # only correct as long as nobody edits the intervals.
        day = access.day_bounds()
        for label, series in (("ballet", a.ballet_sessions), ("flex", a.flex_sessions)):
            clash = [s for s in series if day[0] <= conn.execute(
                "SELECT starts_at FROM sessions WHERE id=?", (s,)
            ).fetchone()["starts_at"] < day[1]]
            assert not clash, (
                f"the {label} series put {len(clash)} session(s) on today; adjust "
                f"{label.upper()}_START_DAYS_AGO so the interval never divides it")

        # One ballet session later today, for the arriving-early path. Placed a
        # few hours out but kept inside today, since "one check-in per day" is
        # scoped to the session's own calendar day.
        end_of_day = _ts(today, 23, 50)
        a.today_ballet = add_session(conn, a.ballet, a.ana,
                                     min(db.now() + 3 * 3600, end_of_day), 1.5,
                                     status="scheduled")

        # -------------------------------------------------- clients
        def client(name, phone, **extra):
            return _ins(conn, "clients", name_en=name, phone=phone,
                        joined_on=extra.pop("joined_on", today.isoformat()),
                        created_at=db.now(), active=1, **extra)

        # Takes both classes — the subject the one-card-per-class rule is for.
        a.dual = client("Dana Halim", "01111111111", age=12.5, school="Manor House")
        a.solo_ballet = client("Farah Adel", "01111111112", age=9.0)
        a.solo_flex = client("Hana Sabry", "01111111113", age=4.8)
        a.lapsed = client("Injy Tarek", "01111111114", age=15.0,
                          joined_on=(today - timedelta(days=120)).isoformat())
        a.planless = client("Jana Wael", "01111111115", age=7.0)
        a.archived = client("Karim Nour", "01111111116", age=11.0)
        conn.execute("UPDATE clients SET active=0 WHERE id=?", (a.archived,))

        # -------------------------------------------------- plans
        def split(session_ids):
            now = db.now()
            past, future = [], []
            for s in session_ids:
                row = conn.execute("SELECT starts_at FROM sessions WHERE id=?",
                                   (s,)).fetchone()
                (past if row["starts_at"] < now else future).append(s)
            return past, future

        past_b, future_b = split(a.ballet_sessions)
        past_f, future_f = split(a.flex_sessions)

        # 12 sessions: at or above access.FREEZE_MIN_SESSIONS, so freezable.
        # Deliberately one slot short of fully assigned, so `unassigned` is
        # non-zero and the attention list has something to find.
        a.dual_ballet_plan = _plan(conn, a.dual, a.ballet, "12 sessions", 12,
                                   price=4100.0, paid_on=today.isoformat())
        _fill(conn, a.dual, a.dual_ballet_plan,
              past_b[-7:] + [a.today_ballet] + future_b[:3])

        # 8 sessions: below the freeze minimum, so can_freeze() must refuse it.
        # No flex session runs today, which is what makes the flex card's refusal
        # in test_the_card_decides_which_class a real assertion rather than an
        # accident of the hour the suite happens to run at.
        a.dual_flex_plan = _plan(conn, a.dual, a.flex, "8 sessions", 8, price=2000.0)
        _fill(conn, a.dual, a.dual_flex_plan, (past_f + future_f)[:8])

        a.solo_ballet_plan = _plan(conn, a.solo_ballet, a.ballet, "12 sessions", 12,
                                   price=4100.0)
        _fill(conn, a.solo_ballet, a.solo_ballet_plan, (past_b + future_b)[:12])

        # Unpriced on purpose: the ballet roster writes "yes", not an amount, and
        # a plan nobody wrote a price for must not report as zero revenue.
        a.solo_flex_plan = _plan(conn, a.solo_flex, a.flex, "4 sessions", 4,
                                 price=None)
        _fill(conn, a.solo_flex, a.solo_flex_plan, future_f[:4])

        a.lapsed_plan = _plan(conn, a.lapsed, a.ballet, "4 sessions", 4, price=900.0,
                              starts_on=today - timedelta(days=120))
        _fill(conn, a.lapsed, a.lapsed_plan, past_b[:4])

        # -------------------------------------------------- cards
        # One per class, which is what makes "the card decides the class" mean
        # anything for the dual client.
        a.dual_ballet_card = _card(conn, a.dual, a.ballet)
        a.dual_flex_card = _card(conn, a.dual, a.flex)
        a.solo_ballet_card = _card(conn, a.solo_ballet, a.ballet)

        # -------------------------------------------------- payroll
        # The salary sheet's half of the picture: one row per instructor per day.
        for i in range(10):
            d = (today - timedelta(days=i + 1)).isoformat()
            _ins(conn, "instructor_hours", instructor_id=a.ana, work_date=d,
                 hours=4.0, source="salary sheet", created_at=db.now())
            _ins(conn, "instructor_hours", instructor_id=a.bea, work_date=d,
                 hours=3.0, source="salary sheet", created_at=db.now())

        return a
