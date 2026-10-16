"""
Fill in `bookings.checked_in_by` on rows written before that column existed.

**Why a script and not a migration.** `db.migrate()` only ever runs against
SQLite, and the question this answers is being asked of a database that may
be on Atlas, so it has to go through `repo` like the app does. It is a
one-shot by nature -- every write from here on records the source itself --
and running it twice is harmless: it only ever touches rows where the column
is still unset.

**What it can prove, and what it infers.** Every check-in at the door goes
through `access.check_in()`, which stamps `access_events.confirmed_at` on the
event `verify()` logged -- so an event proves a kiosk check-in, and the
*absence* of one proves the stamp was not the client arriving. That second
half is the one that matters: it is the difference between "03:14 PM is when
they walked in" and "03:14 PM is when somebody took the roster", which is
what three reports of a one-hour discrepancy came down to.

Telling a roster mark from a seeded one is the only inference here, and it
is a narrow one: `seed.py` writes `starts_at + 600` exactly, to the second,
which nothing a person presses can land on. The script says how many it
decided each way rather than doing it quietly -- the same rule the sheet
reader follows for every repair it makes.

    python backfill_checkin_source.py            # count, write nothing
    python backfill_checkin_source.py --write
"""

import sys

import config

config.load_env()

import repo as data                               # noqa: E402

SEED_OFFSET_S = 600


def plan(repo) -> dict:
    """Which source each unlabelled present-mark has, in four reads."""
    rows = repo.find("bookings", {"status": "present", "checked_in_by": None})
    rows = [r for r in rows if r["checked_in_at"] is not None]
    if not rows:
        return {"kiosk": [], "roster": [], "sheet": [], "total": 0}

    # An event with confirmed_at is a check-in at the door -- check_in() is
    # the only thing that writes it, and every kiosk path goes through it.
    done = {(e["client_id"], e["session_id"]) for e in repo.find(
        "access_events", {"decision": "allow", "confirmed_at": {"ne": None}})}

    starts = {s["id"]: s["starts_at"] for s in repo.find(
        "sessions", {"id": {"in": sorted({r["session_id"] for r in rows})}})}

    out = {"kiosk": [], "roster": [], "sheet": [], "total": len(rows)}
    for r in rows:
        if (r["client_id"], r["session_id"]) in done:
            out["kiosk"].append(r["id"])
        elif r["checked_in_at"] == starts.get(r["session_id"], -1) + SEED_OFFSET_S:
            out["sheet"].append(r["id"])
        else:
            out["roster"].append(r["id"])
    return out


def apply(repo, groups: dict) -> None:
    """One write per source, however many rows each holds."""
    for source in ("kiosk", "roster", "sheet"):
        ids = groups[source]
        if ids:
            repo.update_where("bookings", {"id": {"in": ids}},
                              {"checked_in_by": source})


def main(argv) -> int:
    write = "--write" in argv
    repo = data.connect()
    try:
        groups = plan(repo)
        print(f"  {config.describe()}")
        print(f"  present marks with no source recorded: {groups['total']}")
        if not groups["total"]:
            print("  nothing to do.")
            return 0
        print(f"    {len(groups['kiosk']):>5}  checked in at the desk "
              f"(an access_events row proves it)")
        print(f"    {len(groups['roster']):>5}  marked on the session page "
              f"(no event -- not the client arriving)")
        print(f"    {len(groups['sheet']):>5}  imported from a roster sheet "
              f"(stamped exactly start+10min)")
        if not write:
            print("\n  nothing written. Re-run with --write to apply.")
            return 0
        apply(repo, groups)
        print("\n  written. The session page now says which each one is.")
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
