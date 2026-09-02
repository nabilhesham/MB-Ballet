"""
Load MB Ballet Academy from the academy's own spreadsheets.

    ENTRY_SECRET=... python seed.py --force

Wipes academy.db and rebuilds it from the workbooks listed in SHEETS below.
Never run this once the database has moved ahead of the sheets.

Add a new term's workbook by adding a line to SHEETS. Nothing else needs
changing: the readers in sheets.py work from the header rows, so a block that
gains a column or drops one still imports.

    python seed.py --force --dry-run     parse and report, write nothing
    python seed.py --force --no-cards    skip the card PNGs (much faster)
"""

import os
import sys
from datetime import date, datetime, time, timedelta

import access
import cards
import db
import sheets

# ---------------------------------------------------------------- the sheets
# Real client data. Drop the term's workbooks in sheets/ and list them here.
#   kind    "roster"  one block per class group, one row per student
#           "salary"  instructors across the top, working days down the side
#   family  the class these groups belong to; also the card colour family
SHEETS = [
    {"kind": "roster", "path": "sheets/ballet.xlsx",
     "family": "Ballet", "duration_hours": 1.5, "colour": "#87438E"},
    {"kind": "roster", "path": "sheets/flexibility.xlsx",
     "family": "Flexibility", "duration_hours": 1.0, "colour": "#C9679C"},
    {"kind": "salary", "path": "sheets/August_Salaries_2026.xlsx"},
]

# Shades of the logo's purple and pink, so two class groups sitting next to
# each other in the timetable are tellable apart without inventing a new
# palette. Cycled per group within a family.
SHADES = {
    "Ballet":      ["#87438E", "#9C5AA3", "#6E3474", "#B07AB5", "#5A2A60"],
    "Flexibility": ["#C9679C", "#EAAECA", "#A94E7F", "#D98CB4"],
}

# How far past the last sheet entry to lay down the weekly timetable. A pack of
# N once-a-week sessions needs N weeks of calendar in front of it before every
# paid slot can be pointed at a real session, and the largest pack sold is 12.
WEEKS_AHEAD = 12

# The ballet sheet sells months, not session packs: one class a week.
SESSIONS_PER_MONTH = 4


def parse_sheets(warn):
    """Read every workbook in SHEETS. Returns (rosters, payrolls)."""
    rosters, payrolls = [], []
    for spec in SHEETS:
        path = spec["path"]
        if not os.path.exists(path):
            warn(f"{path} is missing — skipped")
            continue
        if spec["kind"] == "roster":
            r = sheets.read_roster(path, spec["family"],
                                   spec.get("duration_hours", 1.5))
            r.colour = spec.get("colour")
            rosters.append(r)
            warn(*r.warnings)
        else:
            p = sheets.read_salaries(path)
            payrolls.append(p)
            warn(*p.warnings)
    return rosters, payrolls


# ---------------------------------------------------------------- instructors
def seed_instructors(conn, payrolls, rosters, warn):
    """
    Instructors come from the salary sheet, which is the only place their rate
    is written down. Anyone named on a roster block but absent from payroll is
    still created — they teach here, their rate just is not known yet.
    """
    ids = {}
    for p in payrolls:
        for pay in p.instructors:
            key = pay.name.lower()
            if key in ids:
                continue
            cur = conn.execute(
                "INSERT INTO instructors (name, hourly_rate, specialty)"
                " VALUES (?,?,?)", (pay.name, pay.hourly_rate, None))
            ids[key] = cur.lastrowid

    def match(name):
        """
        The salary sheet writes "karma dorra"; the roster block heading says
        "With Captain Karma". Reception uses first names, so a unique first
        name is enough to be the same person — an ambiguous one is not.
        """
        low = name.lower().strip()
        if low in ids:
            return ids[low]
        first = low.split()[0]
        hits = [k for k in ids if k.split()[0] == first]
        return ids[hits[0]] if len(hits) == 1 else None

    for r in rosters:
        for g in r.groups:
            if not g.instructor:
                continue
            iid = match(g.instructor)
            if iid is None:
                cur = conn.execute(
                    "INSERT INTO instructors (name, hourly_rate) VALUES (?,?)",
                    (g.instructor, 0))
                iid = ids[g.instructor.lower()] = cur.lastrowid
                warn(f"{g.instructor}: teaches {g.class_name} but is not on the "
                     f"salary sheet — created with no hourly rate")
            g.instructor_id = iid

    for p in payrolls:
        for pay in p.instructors:
            iid = ids[pay.name.lower()]
            for when, hours in sorted(pay.days.items()):
                conn.execute(
                    "INSERT OR IGNORE INTO instructor_hours (instructor_id,"
                    " work_date, hours, source, created_at) VALUES (?,?,?,?,?)",
                    (iid, when.isoformat(), hours,
                     os.path.basename(p.path), db.now()))
    conn.commit()
    return ids


# ---------------------------------------------------------------- the timetable
def seed_classes_and_sessions(conn, rosters, warn):
    """
    One class per block of the roster, and its weekly sessions.

    The sheet's blocks are the real classes: "grade 6" and "level 8" meet at
    different hours with different students, and a single "Ballet" class would
    make one card open both doors. The instructor goes on each session, not on
    the class, so a substitute for one Wednesday stays a fact about that
    Wednesday.
    """
    class_of, sessions_of = {}, {}
    shade_used = {}

    for r in rosters:
        for g in r.groups:
            palette = SHADES.get(g.family, [r.colour or "#87438E"])
            n = shade_used.get(g.family, 0)
            shade_used[g.family] = n + 1
            desc = g.title.strip()
            cur = conn.execute(
                "INSERT INTO classes (name, description, colour, duration_hours,"
                " level) VALUES (?,?,?,?,?)",
                (g.class_name, desc, palette[n % len(palette)],
                 g.duration_hours, g.level))
            cid = class_of[id(g)] = cur.lastrowid

            weekdays = g.grid_weekdays()
            dates = [s.on for st in g.students for s in st.slots]
            dates += [st.paid_date for st in g.students if st.paid_date]
            if not dates and not weekdays:
                sessions_of[cid] = {}
                warn(f"{g.class_name}: nothing dated in the sheet — "
                     f"no sessions generated")
                continue

            first = min(dates) if dates else date.today()
            last = max(max(dates) if dates else date.today(),
                       date.today() + timedelta(weeks=WEEKS_AHEAD))
            if not weekdays and g.weekday is not None:
                weekdays = [g.weekday]

            by_date = {}
            for wd in weekdays or []:
                d = first - timedelta(days=(first.weekday() - wd) % 7)
                while d <= last:
                    by_date.setdefault(d, None)
                    d += timedelta(weeks=1)
            # A makeup class the sheet records on a day the group does not
            # normally meet is still a session that happened. Adding it keeps
            # the attendance mark attached to a real date.
            for d in dates:
                by_date.setdefault(d, None)

            for d in sorted(by_date):
                starts = int(datetime.combine(d, time(g.hour, g.minute)).timestamp())
                status = ("completed" if starts + g.duration_hours * 3600 < db.now()
                          else "scheduled")
                cur = conn.execute(
                    "INSERT INTO sessions (class_id, instructor_id, starts_at,"
                    " duration_hours, status) VALUES (?,?,?,?,?)",
                    (cid, getattr(g, "instructor_id", None), starts,
                     g.duration_hours, status))
                by_date[d] = (cur.lastrowid, starts)
            sessions_of[cid] = by_date
    conn.commit()
    return class_of, sessions_of


# ---------------------------------------------------------------- the students
def _identity(student):
    """
    One person, however many blocks they appear in.

    The phone number is the identity: the same student is "rodaina hesham" on
    the flexibility sheet and "rodina hesham" on the ballet one, and merging
    them is what makes her two cards belong to one client rather than two.
    """
    return sheets.phone_key(student.phone) or f"name:{sheets.name_key(student.name)}"


def _plan(student, family):
    """Plan size, name and expiry window, as the two sheets each express them."""
    if family == "Ballet":
        months = student.months or 1
        total = months * SESSIONS_PER_MONTH
        name = f"{months} month" + ("s" if months > 1 else "")
        if student.months is None:
            total, name = max(1, len(student.slots)), "Single class"
    else:
        months = None
        total = student.sessions or max(1, len(student.slots))
        name = f"{total} session" + ("s" if total > 1 else "")
    return total, name, months


def seed_clients(conn, rosters, class_of, sessions_of, warn):
    clients, subs = {}, []
    today = date.today()

    for r in rosters:
        for g in r.groups:
            cid_class = class_of[id(g)]
            grid = sessions_of.get(cid_class, {})

            for st in g.students:
                key = _identity(st)
                joined = st.paid_date or (st.slots[0].on if st.slots else today)

                if key in clients:
                    client_id = clients[key]
                    # Later blocks fill in what the first one left blank.
                    conn.execute(
                        "UPDATE clients SET age=COALESCE(age,?), school=COALESCE(school,?),"
                        " dob=COALESCE(dob,?), phone=COALESCE(phone,?),"
                        " joined_on=MIN(joined_on,?) WHERE id=?",
                        (st.age, st.school, st.dob, st.phone, joined.isoformat(),
                         client_id))
                else:
                    cur = conn.execute(
                        "INSERT INTO clients (name_en, phone, age, dob, school,"
                        " joined_on, notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (st.name, st.phone, st.age, st.dob, st.school,
                         joined.isoformat(), st.note, db.now()))
                    client_id = clients[key] = cur.lastrowid

                total, plan_name, months = _plan(st, g.family)
                attended = len(st.slots)
                if attended > total:
                    warn(f"{st.name} ({g.class_name}): {attended} sessions marked "
                         f"but the plan holds {total} — plan widened to fit")
                    total = attended

                starts = st.paid_date or joined
                expires = starts + timedelta(days=30 * (months or 1))
                cur = conn.execute(
                    "INSERT INTO subscriptions (client_id, class_id, plan,"
                    " sessions_total, price, payment_note, months, days_pattern,"
                    " starts_on, expires_on, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (client_id, cid_class, plan_name, total, st.price,
                     st.paid_raw, months, st.days, starts.isoformat(),
                     expires.isoformat(), db.now()))
                sub_id = cur.lastrowid
                subs.append(sub_id)

                used = _book_attendance(conn, client_id, sub_id, st, grid,
                                        g.class_name, warn)
                _book_forward(conn, client_id, sub_id, st, grid, total - used)
                _fit_expiry(conn, sub_id, expires)
        conn.commit()
    return clients, subs


def _fit_expiry(conn, sub_id, expires):
    """
    Stretch the expiry to cover the sessions the plan is actually paying for.

    access.last_session_date() owns the rule now (plan_state() applies it
    live, on every read); the week added on top here is a seed-only concern
    -- the sheet gives a payment date, and several rows were paid weeks
    before the student started, so the plain arithmetic expires a plan
    halfway through the classes it bought.
    """
    covers = access.last_session_date(conn, sub_id)
    if covers is None:
        return
    end = date.fromisoformat(covers) + timedelta(days=7)
    if end > expires:
        conn.execute("UPDATE subscriptions SET expires_on=? WHERE id=?",
                     (end.isoformat(), sub_id))


def _book_attendance(conn, client_id, sub_id, student, grid, class_name, warn):
    """Turn the dates written across the row into settled bookings."""
    booked = 0
    seen = set()
    for slot in student.slots:
        if slot.on in seen:
            # The same date written into two columns. It was one class, so it
            # costs one slot; counting it twice would overstate what she used.
            warn(f"{student.name} ({class_name}): {slot.on} is written twice — "
                 f"counted once")
            continue
        seen.add(slot.on)
        entry = grid.get(slot.on)
        if not entry:
            continue
        session_id, starts = entry
        status = "present" if slot.present else "absent"
        conn.execute(
            "INSERT OR IGNORE INTO bookings (client_id, session_id, subscription_id,"
            " status, checked_in_at, created_at) VALUES (?,?,?,?,?,?)",
            (client_id, session_id, sub_id, status,
             starts + 600 if slot.present else None, db.now()))
        booked += 1
    return booked


def _book_forward(conn, client_id, sub_id, student, grid, remaining):
    """
    Put the rest of the plan on the calendar.

    Every paid slot must point at a real session — an unassigned slot is a
    promise nobody has written down — so the leftovers go onto the next
    sessions this student's own days actually fall on.
    """
    if remaining <= 0:
        return
    after = max([s.on for s in student.slots] + [date.today() - timedelta(days=1)])
    wanted = set(student.weekdays)
    for when in sorted(d for d in grid if d > after):
        if remaining <= 0:
            break
        if wanted and when.weekday() not in wanted:
            continue
        session_id, _ = grid[when]
        cur = conn.execute(
            "INSERT OR IGNORE INTO bookings (client_id, session_id,"
            " subscription_id, status, created_at) VALUES (?,?,?,?,?)",
            (client_id, session_id, sub_id, "booked", db.now()))
        if cur.rowcount:
            remaining -= 1


# ---------------------------------------------------------------- the cards
def seed_cards(conn, make_pngs=True):
    """One card per client per class they hold a plan in."""
    import tokens
    n = 0
    for r in conn.execute(
            "SELECT s.client_id, s.class_id, s.plan, s.expires_on, c.name_en,"
            "       cl.name AS class_name, cl.colour"
            "  FROM subscriptions s JOIN clients c ON c.id=s.client_id"
            "  JOIN classes cl ON cl.id=s.class_id"
            " GROUP BY s.client_id, s.class_id").fetchall():
        token = tokens.issue(r["client_id"])
        conn.execute(
            "INSERT INTO credentials (client_id, class_id, token, kind, issued_at)"
            " VALUES (?,?,?,?,?)",
            (r["client_id"], r["class_id"], token, "card", db.now()))
        if make_pngs:
            cards.build_card(r["client_id"], r["name_en"], token, r["plan"],
                             r["expires_on"], class_name=r["class_name"],
                             colour=r["colour"])
        n += 1
    conn.commit()
    return n


# ---------------------------------------------------------------- entry point
def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    make_pngs = "--no-cards" not in argv

    if not dry:
        if os.path.exists("academy.db") and "--force" not in argv:
            print("academy.db exists. Re-run with --force to wipe it.")
            return
        if not os.environ.get("ENTRY_SECRET"):
            print("ENTRY_SECRET is not set — cards cannot be signed.\n"
                  "  set -a; source .env; set +a")
            return

    warnings = []

    def warn(*msgs):
        warnings.extend(m for m in msgs if m)

    rosters, payrolls = parse_sheets(warn)
    if not rosters and not payrolls:
        print("Nothing to seed. Put the academy's workbooks in sheets/ and list\n"
              "them in SHEETS at the top of seed.py. Expected:")
        for spec in SHEETS:
            print(f"  {spec['path']}")
        return

    if dry:
        for r in rosters:
            print(f"\n{r.path}")
            for g in r.groups:
                print(f"  {g.class_name:<24} {len(g.students):>3} students  "
                      f"{g.instructor or 'no instructor'}")
        for p in payrolls:
            print(f"\n{p.path}  {p.period}")
            for i in p.instructors:
                print(f"  {i.name:<18}{i.hours:>7}h  {i.hourly_rate:>7}/h")
        _report(warnings)
        return

    for f in ("academy.db", "academy.db-wal", "academy.db-shm"):
        if os.path.exists(f):
            os.remove(f)

    db.init()
    conn = db.connect()
    try:
        seed_instructors(conn, payrolls, rosters, warn)
        class_of, sessions_of = seed_classes_and_sessions(conn, rosters, warn)
        seed_clients(conn, rosters, class_of, sessions_of, warn)
        seed_cards(conn, make_pngs)

        counts = {t: conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                  for t in ("instructors", "instructor_hours", "classes",
                            "sessions", "clients", "subscriptions",
                            "bookings", "credentials")}
        money = conn.execute(
            "SELECT COALESCE(SUM(price),0) paid,"
            "       SUM(CASE WHEN price IS NULL THEN 1 ELSE 0 END) unpriced"
            "  FROM subscriptions").fetchone()
    finally:
        conn.close()

    print("\nMB Ballet Academy loaded from the sheets:")
    for k, v in counts.items():
        print(f"  {k:<18}{v}")
    print(f"\n  recorded revenue  {money['paid']:.0f} EGP"
          f"   ({money['unpriced']} plans have no price in the sheet)")
    _report(warnings)
    print("\n  ENTRY_SECRET=... python server.py")
    print("  http://127.0.0.1:8000\n")


def _report(warnings):
    if not warnings:
        return
    print(f"\n{len(warnings)} thing(s) the sheets left ambiguous:")
    for w in warnings:
        print(f"  - {w}")


if __name__ == "__main__":
    main()
