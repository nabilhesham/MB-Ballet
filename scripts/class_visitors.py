"""
Name the clients who are on a class's sessions only as cross-class visitors.

**There is nothing to migrate, and that is the finding rather than a
shortcut.** "A visitor is not a student of the class" is one filter on two
reads -- `repo.class_students()` and `repo.classes_with_counts()` -- and
every booking, every attendance mark and every plan is exactly where it was
before the rule existed. A client whose Ballet slot was pointed at a
Flexibility session still attended that session, still spent that slot, and
still shows it in her own history; the only thing that changed is that the
Flexibility roster no longer calls her one of its students. So there is no
column to backfill, no row to rewrite, and a script that wrote anything here
would be undoing the correction rather than applying it.

What is worth having instead is the list the rule acts on, because until now
it was invisible: who was on which roster for this reason, and therefore
which rosters and which student counts have just gone down. Run it before
and after an upgrade and the two agree -- that *is* the proof nothing moved.

A client who also holds a plan in the class is a student of it and is listed
separately: her visit changes no count, and seeing her here would otherwise
read as a roster that lost somebody it did not.

A booking with **no plan behind it** is a third thing, and it is reported
separately because it is the one that looks like a visitor and is not
treated as one. `subscription_id` NULL has no plan to read a class off, so
the session's own class is the only answer there is -- both port methods
fall back to it, deliberately, and such a client therefore stays on the
roster. It used to be skipped here entirely, which made the commonest
"the filter did not work" case invisible in the very report meant to
explain it.

    python scripts/class_visitors.py                    # every class
    python scripts/class_visitors.py --class 7          # one class, by id
    python scripts/class_visitors.py --class "FRIDAY"   # or by name
    python scripts/class_visitors.py --explain "FRIDAY" # why each name is on
                                                        # that roster at all
"""

import pathlib
import sys
from datetime import date, timedelta

# This script lives in scripts/ -- apart from the app itself, so nobody has to
# work out which files are the app and which are run by hand. The app's own
# modules are therefore one directory up, and the path has to say so before
# the first of them is imported.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config

config.load_env()

import access                                     # noqa: E402
import repo as data                               # noqa: E402


def survey(repo, only_class: int = None) -> dict:
    """
    Every booking whose session belongs to one class and whose plan belongs
    to another, grouped by the class whose roster it used to appear on.

    Six reads whatever the size of the academy -- the same rule the app's own
    pages follow (see "Round trips are the unit of cost").

    A booking with **no plan behind it** is collected too, under
    `no_plan`, rather than skipped. It is not a cross-class visit -- there
    is no plan to read another class off -- but it is the row that most
    often looks like one: it sits on this class's session, it keeps the
    client on this class's roster (both port methods fall back to the
    session's own class for it, deliberately), and the filter will never
    remove it. Leaving it out made the commonest "the filter did not work"
    case invisible in the report written to explain the filter.
    """
    classes = {c["id"]: c for c in repo.find("classes")}
    sessions = {s["id"]: s for s in repo.find("sessions")}
    plans = {p["id"]: p for p in repo.find("subscriptions")}
    clients = {c["id"]: c for c in repo.find("clients")}

    # Who holds a plan in which class, live or lapsed. A client with one is a
    # student of that class on her own account, so her visit is not what put
    # her on its roster.
    plan_holders = {}
    for p in plans.values():
        plan_holders.setdefault(p["class_id"], set()).add(p["client_id"])

    def _blank(here, b):
        return {"class_id": here,
                "client_id": b["client_id"],
                "client": clients.get(b["client_id"], {}).get("name_en", "?"),
                "from_classes": set(),
                "visits": [],
                "also_a_student": b["client_id"] in plan_holders.get(here, set())}

    found, orphans = {}, {}
    for b in repo.find("bookings"):
        sess = sessions.get(b["session_id"])
        if not sess:
            continue
        here = sess["class_id"]
        if only_class is not None and here != only_class:
            continue
        sub = plans.get(b["subscription_id"]) if b.get("subscription_id") else None
        if sub is None:
            row = orphans.setdefault((here, b["client_id"]), _blank(here, b))
            row["visits"].append((sess["starts_at"], b["status"]))
            continue
        if here == sub["class_id"]:
            continue
        row = found.setdefault((here, b["client_id"]), _blank(here, b))
        row["from_classes"].add(sub["class_id"])
        row["visits"].append((sess["starts_at"], b["status"]))

    def _ordered(d):
        return sorted(d.values(),
                      key=lambda r: (classes.get(r["class_id"], {}).get("name", ""),
                                     r["client"]))

    return {"classes": classes, "rows": _ordered(found),
            "no_plan": _ordered(orphans)}


def _said(ts, status):
    """A session as it reads in this report: its day, and the mark on it
    unless that mark is the unremarkable one."""
    day = date.fromtimestamp(ts).isoformat()
    return day if status == "present" else f"{day} ({status})"


def report(survey_result) -> int:
    classes = survey_result["classes"]
    rows, orphans = survey_result["rows"], survey_result["no_plan"]
    print(f"  {config.describe()}")

    visitors = [r for r in rows if not r["also_a_student"]]
    both = [r for r in rows if r["also_a_student"]]

    if not rows and not orphans:
        print("  No cross-class bookings anywhere. Every roster already "
              "listed only its own students.")
        return 0

    current = None
    for r in visitors:
        cl = classes.get(r["class_id"], {})
        if r["class_id"] != current:
            current = r["class_id"]
            print(f"\n  {cl.get('name', '?')}  (class {r['class_id']})")
        came_from = ", ".join(sorted(
            classes.get(c, {}).get("name", f"class {c}") for c in r["from_classes"]))
        when = ", ".join(_said(ts, st) for ts, st in sorted(r["visits"]))
        print(f"      {r['client']}  (client {r['client_id']})")
        print(f"        on a plan for: {came_from}")
        print(f"        session(s): {when}")

    affected = {r["class_id"] for r in visitors}
    print(f"\n  {len(visitors)} client(s) across {len(affected)} class(es) "
          f"were on a roster only as a visitor.")
    print("  Those rosters and student counts are that much shorter now.")

    if both:
        print(f"\n  {len(both)} other cross-class visit(s) are by clients who "
              "also hold a plan\n  in the class they visited. They are students "
              "of it either way, so\n  nothing about them changed:")
        for r in both:
            print(f"      {r['client']} in "
                  f"{classes.get(r['class_id'], {}).get('name', '?')}")

    if orphans:
        print(f"\n  {len(orphans)} client(s) hold a booking on a class's own "
              "session with **no plan\n  behind it** (subscription_id NULL). "
              "Those are not visits and the filter\n  does not touch them: "
              "with no plan there is no other class to read, so the\n  "
              "session's own class is the only answer there is, and they stay "
              "on the\n  roster. If one of these is the name you expected to "
              "disappear, this is\n  why -- see --explain:")
        current = None
        for r in orphans:
            if r["class_id"] != current:
                current = r["class_id"]
                print(f"\n      {classes.get(current, {}).get('name', '?')} "
                      f" (class {current})")
            when = ", ".join(_said(ts, st) for ts, st in sorted(r["visits"]))
            print(f"          {r['client']}  (client {r['client_id']})  {when}")

    print("\n  Nothing was written. There is nothing to write -- see the top "
          "of this file.")
    return 0


def explain(repo, class_id: int) -> int:
    """
    Why every name on one class's roster is on it, booking by booking.

    The report above answers "who was a visitor"; this answers the question
    that actually gets asked, which is **"why is this person still here"**.
    It reads the roster from `repo.class_students()` -- the very method the
    screen calls, on whichever backend is configured -- and then walks the
    same rule over the same bookings in the open, so the two can be read
    against each other. A name on the screen that this cannot account for
    is a bug in the filter; a name both agree on, with a reason printed
    beside it, is the filter working and the reason being something else.

    **It also says when each name goes away by itself**, which is the other
    half of the same question and the half the first version left out. A
    client stays for one calendar month after her last plan in the class
    ends (`access.lapsed_cutoff()`), so a roster can legitimately hold
    somebody with no live plan here at all -- her row reads `no plan` in
    LEFT and names a *different* class under CLASS, because those two
    columns answer different questions (this class's live plan, and all her
    live plans). That looks exactly like a filter that has failed, and it is
    the grace month doing what it is for. Printing the date she drops off is
    what tells the two apart without anybody having to work the calendar.

    Six reads, whatever the size of the class.
    """
    cl = repo.get("classes", class_id)
    if cl is None:
        print(f"  There is no class {class_id}.")
        return 2

    cutoff = access.lapsed_cutoff()
    roster = repo.class_students(class_id, cutoff)
    on_screen = {r["id"]: r for r in roster}

    mine = {x["id"]: x for x in repo.find("sessions", {"class_id": class_id})}
    booked = repo.find("bookings", {"session_id": {"in": sorted(mine)}})
    plans = {q["id"]: q for q in repo.find("subscriptions")}
    classes = {c["id"]: c for c in repo.find("classes")}
    people = {c["id"]: c for c in repo.find("clients")}

    # When each plan behind this roster ends, which is what the cutoff is
    # measured against: the later of its own ENDS ON and the last session it
    # pays for, wherever that session sits. One round trip for all of them
    # rather than access.plan_end() per plan, which is three each -- see
    # last_session_ts_bulk() and "Round trips are the unit of cost".
    behind = sorted({b["subscription_id"] for b in booked
                     if b.get("subscription_id")})
    last_ts = repo.last_session_ts_bulk(behind) if behind else {}
    ends = {}
    for sub_id in behind:
        q = plans.get(sub_id)
        if q is None:
            continue
        ts = last_ts.get(sub_id)
        ends[sub_id] = max(x for x in (q.get("expires_on"),
                                       date.fromtimestamp(ts).isoformat()
                                       if ts else None, "")
                           if x is not None)

    print(f"  {config.describe()}")
    print(f"\n  {cl['name']}  (class {class_id})")
    print(f"  The roster the screen shows: {len(roster)} student(s), "
          f"lapsed cutoff {cutoff}")

    by_client = {}
    for b in booked:
        by_client.setdefault(b["client_id"], []).append(b)

    def name(cid):
        return (people.get(cid) or {}).get("name_en", f"client {cid}")

    counted, excluded = [], []
    for cid, bs in sorted(by_client.items(), key=lambda kv: name(kv[0])):
        lines, keeps, roster_end = [], False, ""
        for b in sorted(bs, key=lambda x: mine[x["session_id"]]["starts_at"]):
            sub_id = b.get("subscription_id")
            sub = plans.get(sub_id) if sub_id else None
            when = _said(mine[b["session_id"]]["starts_at"], b["status"])
            own_day = date.fromtimestamp(
                mine[b["session_id"]]["starts_at"]).isoformat()
            if sub_id and sub is None:
                # A plan id pointing at nothing is **not** the fallback: both
                # backends read the class off the plan and get nothing, so
                # the row is excluded (SQLite's LEFT JOIN makes `sub.class_id
                # = ?` NULL, Mongo's lookup misses). Saying "no plan behind
                # it" here would claim the opposite of what the screen does.
                lines.append(f"{when} — plan {sub_id} no longer exists, so "
                             f"there is no class to read: excluded")
            elif sub is None:
                keeps = True
                # With no plan there is nothing to read an end off either,
                # so the booking's own session day is what the cutoff is
                # measured against -- the same fallback _booking_end() makes.
                roster_end = max(roster_end, own_day)
                lines.append(f"{when} — no plan behind it, so this class is "
                             f"the only answer there is: KEEPS her on the "
                             f"roster")
            elif sub["class_id"] == class_id:
                keeps = True
                roster_end = max(roster_end, ends.get(sub_id, ""))
                lines.append(f"{when} — plan {sub_id} is for this class: "
                             f"KEEPS her on the roster")
            else:
                other = classes.get(sub["class_id"], {}).get(
                    "name", f"class {sub['class_id']}")
                lines.append(f"{when} — plan {sub_id} is for {other}: "
                             f"a visit, not membership (excluded)")
        (counted if keeps else excluded).append((cid, lines, roster_end))

    for cid, lines, roster_end in counted:
        shown = cid in on_screen
        mark = "" if shown else "   <- NOT on the roster (the lapsed cutoff)"
        print(f"\n    {name(cid)}  (client {cid}){mark}")
        for line in lines:
            print(f"        {line}")
        if shown:
            live = any(q["class_id"] == class_id and q.get("active")
                       and q["client_id"] == cid for q in plans.values())
            if live:
                print("        -> a live plan in this class: she is a student "
                      "of it outright")
            else:
                gone = drops_off(roster_end)
                print(f"        -> no live plan in this class. Her last one "
                      f"here ran to {roster_end or 'no date'}, and the "
                      f"one-month")
                print(f"           grace keeps her on the roster until "
                      f"{gone} — this is not the filter failing,")
                print(f"           it is access.lapsed_cutoff() doing what it "
                      f"is for.")

    if excluded:
        print(f"\n  Excluded by the filter — every booking of theirs here is "
              f"funded by\n  another class's plan, so they are visitors and "
              f"not students:")
        for cid, lines, _ in excluded:
            still = "   <- STILL on the roster: that is the bug" \
                if cid in on_screen else ""
            print(f"\n    {name(cid)}  (client {cid}){still}")
            for line in lines:
                print(f"        {line}")

    unaccounted = set(on_screen) - {cid for cid, _, _ in counted}
    if unaccounted:
        print("\n  On the roster with nothing here to explain it — that is a "
              "bug in the\n  filter rather than in the data:")
        for cid in sorted(unaccounted):
            print(f"      {name(cid)}  (client {cid})")

    print("\n  Nothing was written.")
    return 0


def drops_off(roster_end: str, today: date = None) -> str:
    """
    The first day this roster stops listing somebody whose last plan here
    ended on `roster_end`.

    Found by asking `access.lapsed_cutoff()` itself, one day at a time,
    rather than adding a month here: a second copy of "one calendar month
    back, with the day clamped to a shorter month" is exactly the kind of
    arithmetic that agrees until February. It can never scan far -- a month
    is a month -- and this is a script somebody runs by hand.
    """
    if not roster_end:
        return "unknown"
    d = today or date.today()
    for _ in range(70):
        if access.lapsed_cutoff(d) > roster_end:
            return d.isoformat()
        d += timedelta(days=1)
    return "unknown"


def find_class(repo, needle: str):
    """A class by id, or by any part of its name, case-insensitively --
    because the question always arrives as a name ("PRE-BALLET FRIDAY
    5:30"), and an id is something somebody has to go and look up."""
    if needle.isdigit():
        return int(needle), None
    hits = [c for c in repo.find("classes")
            if needle.strip().lower() in (c["name"] or "").lower()]
    if not hits:
        return None, f"No class name contains {needle!r}."
    if len(hits) > 1:
        said = "\n".join(f"      {c['id']}  {c['name']}" for c in hits)
        return None, f"{needle!r} matches more than one class:\n{said}"
    return hits[0]["id"], None


def main(argv) -> int:
    def arg(flag):
        if flag not in argv:
            return None
        i = argv.index(flag)
        if i + 1 >= len(argv):
            print(f"  {flag} needs a class id or part of a class name.")
            return ""
        return argv[i + 1]

    wanted, mode = arg("--explain"), "explain"
    if wanted is None:
        wanted, mode = arg("--class"), "report"
    if wanted == "":
        return 2

    repo = data.connect()
    try:
        only = None
        if wanted is not None:
            only, problem = find_class(repo, wanted)
            if problem:
                print(f"  {problem}")
                return 2
        if mode == "explain":
            return explain(repo, only)
        return report(survey(repo, only))
    finally:
        repo.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
