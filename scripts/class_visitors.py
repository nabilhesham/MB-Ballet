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

    python scripts/class_visitors.py            # every class
    python scripts/class_visitors.py --class 7  # one class
"""

import pathlib
import sys
from datetime import date

# This script lives in scripts/ -- apart from the app itself, so nobody has to
# work out which files are the app and which are run by hand. The app's own
# modules are therefore one directory up, and the path has to say so before
# the first of them is imported.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config

config.load_env()

import repo as data                               # noqa: E402


def survey(repo, only_class: int = None) -> dict:
    """
    Every booking whose session belongs to one class and whose plan belongs
    to another, grouped by the class whose roster it used to appear on.

    Six reads whatever the size of the academy -- the same rule the app's own
    pages follow (see "Round trips are the unit of cost"). A booking with no
    plan behind it is skipped: the session's class is the only answer there
    is for one, which is the fallback both port methods make.
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

    found = {}
    for b in repo.find("bookings"):
        sess = sessions.get(b["session_id"])
        sub = plans.get(b["subscription_id"]) if b.get("subscription_id") else None
        if not sess or not sub:
            continue
        here, paid_for = sess["class_id"], sub["class_id"]
        if here == paid_for:
            continue
        if only_class is not None and here != only_class:
            continue
        key = (here, b["client_id"])
        row = found.setdefault(key, {
            "class_id": here,
            "client_id": b["client_id"],
            "client": clients.get(b["client_id"], {}).get("name_en", "?"),
            "from_classes": set(),
            "visits": [],
            "also_a_student": b["client_id"] in plan_holders.get(here, set()),
        })
        row["from_classes"].add(paid_for)
        row["visits"].append((sess["starts_at"], b["status"]))

    return {"classes": classes, "rows": sorted(
        found.values(), key=lambda r: (classes.get(r["class_id"], {}).get("name", ""),
                                       r["client"]))}


def report(survey_result) -> int:
    classes, rows = survey_result["classes"], survey_result["rows"]
    print(f"  {config.describe()}")

    visitors = [r for r in rows if not r["also_a_student"]]
    both = [r for r in rows if r["also_a_student"]]

    if not rows:
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
        when = ", ".join(date.fromtimestamp(ts).isoformat() + ("" if st == "present" else f" ({st})")
                         for ts, st in sorted(r["visits"]))
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

    print("\n  Nothing was written. There is nothing to write -- see the top "
          "of this file.")
    return 0


def main(argv) -> int:
    only = None
    if "--class" in argv:
        i = argv.index("--class")
        if i + 1 >= len(argv) or not argv[i + 1].isdigit():
            print("  --class needs a class id, e.g. --class 7")
            return 2
        only = int(argv[i + 1])

    repo = data.connect()
    try:
        return report(survey(repo, only))
    finally:
        repo.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
