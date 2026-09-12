"""
Copy an existing academy.db into MongoDB, ids and all.

    python migrate_to_mongo.py --dry-run     count what would move
    python migrate_to_mongo.py               move it

Without this, "the backend is a configuration choice" is only true for a
database that starts empty, which is not the situation anyone is actually in.

**Every integer id is preserved.** That is the whole point rather than a nice
touch: `tokens.py` packs `client_id` as a uint32 into every card already in a
client's hands, and the client id is the printed member number. A migration
that renumbered would invalidate every card in circulation.

Both sides go through the repository interface, so a successful run is also
evidence the two implementations agree about document shapes.

Reads from MB_SQLITE_PATH, writes to MB_MONGO_URI / MB_MONGO_DB. It refuses a
target that already holds data unless --force.
"""

import sys

import config

config.load_env()

import db                                    # noqa: E402
import repo as data                          # noqa: E402
from repo.mongo import ids as mongo_ids      # noqa: E402
from repo.mongo import schema as mongo_schema  # noqa: E402
from repo.sqlite import SqliteRepo           # noqa: E402

# Parents before children, so a half-finished run leaves nothing pointing at
# a document that is not there yet. Nothing enforces this in MongoDB — it is
# for whoever has to read the database after an interrupted run.
ORDER = ("settings", "instructors", "classes", "sessions", "clients",
         "subscriptions", "freezes", "bookings", "credentials",
         "instructor_hours", "instructor_hour_adjustments", "access_events")

BATCH = 500


def sqlite_repo() -> SqliteRepo:
    return SqliteRepo(db.connect(config.sqlite_path()))


def mongo_repo():
    from repo.mongo import MongoRepo
    return MongoRepo(config.mongo_uri(), config.mongo_db())


def copy(source, target, coll, dry_run):
    rows = source.find(coll)
    if dry_run or not rows:
        return len(rows)

    # insert_many() would mint fresh ids from the counters. The ids are the
    # thing being preserved, so the documents go in with the ones they have.
    docs = []
    for row in rows:
        body = mongo_schema.fill(coll, {k: v for k, v in row.items() if k != "id"})
        body["_id"] = row["id"]
        docs.append(body)
    for i in range(0, len(docs), BATCH):
        target.db[coll].insert_many(docs[i:i + BATCH])
    return len(rows)


def main() -> int:
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    force = "--force" in argv

    if config.backend() != config.MONGO and not dry_run:
        print("Set MB_DB_BACKEND=mongo (or pass --dry-run) before running this.")
        return 1

    source = sqlite_repo()
    try:
        counts = {c: source.count(c) for c in ORDER}
        total = sum(counts.values())
        print(f"\nSource: {config.sqlite_path()}")
        for coll, n in counts.items():
            print(f"  {coll:<30}{n:>7}")
        print(f"  {'':<30}{'-' * 7}\n  {'total':<30}{total:>7}")

        if dry_run:
            print("\n--dry-run: nothing was written.")
            return 0

        target = mongo_repo()
        try:
            print(f"\nTarget: {config.describe()}")
            if not target.is_empty() and not force:
                print("\nThe target database already holds data. Re-run with "
                      "--force if you mean to add to it.")
                return 1

            target.init_schema()
            moved = {}
            for coll in ORDER:
                moved[coll] = copy(source, target, coll, dry_run)
                print(f"  {coll:<30}{moved[coll]:>7}  copied")

            # Without this the next insert would reuse an id that already
            # exists -- the counters start at zero on a database that was
            # filled from outside.
            mongo_ids.sync_counters(target.db, mongo_schema.FIELDS)
            print("\n  id counters synced to the highest id in each collection")

            bad = {c: (counts[c], moved[c]) for c in ORDER if counts[c] != moved[c]}
            if bad:
                print(f"\n  MISMATCH: {bad}")
                return 1
            print(f"\n  {sum(moved.values())} documents moved, every id preserved.")
            print("  Check a previously printed card still scans before "
                  "trusting this.")
        finally:
            target.close()
    finally:
        source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
