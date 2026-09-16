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
import images                                # noqa: E402
import repo as data                          # noqa: E402
from repo.mongo import ids as mongo_ids      # noqa: E402
from repo.mongo import schema as mongo_schema  # noqa: E402
from repo.sqlite import SqliteRepo           # noqa: E402

# Parents before children, so a half-finished run leaves nothing pointing at
# a document that is not there yet. Nothing enforces this in MongoDB — it is
# for whoever has to read the database after an interrupted run.
#
# `settings` is deliberately not here. It is SQLite bookkeeping -- one row,
# `expiry_backfilled`, written by db.migrate() to record that a one-shot
# repair has been done -- and db.migrate() never runs against MongoDB, so the
# marker would mean nothing there. Both backends' is_empty() already exclude
# it for that reason; having it in this list contradicted them. It is also
# the one table keyed by `key` rather than `id`, so find() appending its `id`
# tiebreak was "no such column: id" on the very first collection copied.
ORDER = ("instructors", "classes", "sessions", "clients",
         "subscriptions", "freezes", "bookings", "credentials",
         "instructor_hours", "instructor_hour_adjustments", "access_events",
         "appointments",
         # Last: the photos and cards are the bulkiest rows and the only ones
         # nothing else points at, so an interrupted run leaves a database
         # that is whole apart from its pictures.
         "images")

BATCH = 500
IMAGE_BATCH = 25


def sqlite_repo() -> SqliteRepo:
    """
    The source, with its schema brought up to date first.

    db.init() is additive and idempotent -- it is exactly what the app itself
    runs on every start -- and without it this tool cannot read a database
    older than its own ORDER list. The academy's own file predates the
    `images` table, so counting the rows to move died on `no such table:
    images` before it had moved anything. Leaving that to "start the app once
    first" made a manual step load-bearing, which is the kind of instruction
    that gets skipped exactly when it matters.
    """
    path = config.sqlite_path()
    db.init(path)
    return SqliteRepo(db.connect(path))


def mongo_repo():
    from repo.mongo import MongoRepo
    return MongoRepo(config.mongo_uri(), config.mongo_db())


def pictures_with_no_row(source):
    """
    `(faces, cards)` this database points at but does not contain.

    The migration copies rows, so a `photo_path` of `/photos/client_00001.jpg`
    would arrive on the hosted backend as a path to a file on a laptop nobody
    will ever query it from, and a live credential with no card row has no
    picture to copy at all. Both are silent -- the profile renders, the client
    is just faceless and the card has no image -- so they are counted and
    said out loud.

    This is a **warning, not a refusal**, and the difference matters. The
    fixable half is fixed automatically now (see main), and what can be left
    over is a picture that exists nowhere: a photo never taken, or a card
    whose PNG was deleted or never drawn. No amount of importing produces
    those -- only reissuing the card or uploading the photo does -- so
    blocking the migration on them would be a refusal with no way to clear
    it, which is worse than a faceless profile.
    """
    faces = 0
    for coll in ("clients", "instructors"):
        faces += sum(1 for r in source.find(coll, fields=["photo_path"])
                     if (r.get("photo_path") or "").startswith("/photos/"))
    live = source.count("credentials", {"revoked_at": None})
    return faces, max(0, live - source.count("images", {"kind": "card"}))


def copy(source, target, coll, dry_run):
    """
    One collection, by id, replacing whatever is already under each id.

    Not insert_many(): that mints fresh ids from the counters, and the ids are
    the whole point -- tokens.py packs client_id as a uint32 into every card
    already in a client's hands. Passing the ids in explicitly fixes that, but
    a plain insert then fails on the second run with a duplicate key, half way
    through, leaving a database that is neither the old one nor the new one.

    A migration is something people run more than once while they get it
    right, so it converges instead: ReplaceOne(upsert=True) on each id makes a
    re-run end with MongoDB holding exactly what SQLite holds. What it never
    does is delete -- a document here and not in SQLite is left alone and
    counted, because this tool is not the authority on what else might be in
    that database.
    """
    rows = source.find(coll)
    if dry_run or not rows:
        return len(rows)

    from pymongo import ReplaceOne

    ops = []
    for row in rows:
        body = mongo_schema.fill(coll, {k: v for k, v in row.items() if k != "id"})
        body["_id"] = row["id"]
        ops.append(ReplaceOne({"_id": row["id"]}, body, upsert=True))
    # Photos and cards are two orders of magnitude bigger per row than
    # anything else here, so they go in smaller batches: five hundred of them
    # in one command is tens of megabytes against a limit measured in them.
    size = IMAGE_BATCH if coll == "images" else BATCH
    for i in range(0, len(ops), size):
        target.db[coll].bulk_write(ops[i:i + size], ordered=True)
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
        # Pictures that are still files become rows first, because rows are
        # the only thing that travels. It is the same idempotent read-in the
        # app does on startup, and it has to run before the rows are counted
        # or the `images` total is the one from before it. A dry run reports
        # what it would take in and leaves the disk alone. What this says is
        # held back until after the table, so the picture news reads together.
        pending = images.pending_legacy_files(config.legacy_media_dir())
        note = ""
        if pending and not dry_run:
            moved = images.import_legacy_files(source, config.legacy_media_dir())
            if moved:
                note = (f"  Read {moved} photo(s) and card(s) in off the disk "
                        f"first -- rows are the only thing that travels.")
        elif pending:
            note = (f"  {pending} photo(s) and card(s) are still files. A real "
                    f"run reads them in first.")

        counts = {c: source.count(c) for c in ORDER}
        total = sum(counts.values())
        print(f"\nSource: {config.sqlite_path()}")
        for coll, n in counts.items():
            print(f"  {coll:<30}{n:>7}")
        print(f"  {'':<30}{'-' * 7}\n  {'total':<30}{total:>7}")

        faces, cards = pictures_with_no_row(source)
        if note:
            print(f"\n{note}")
        if faces or cards:
            print("")
            if faces:
                print(f"  {faces} client/instructor photo(s) point at a file that "
                      f"is not here.\n"
                      f"    They will show initials instead. Upload the photo "
                      f"again to fix one.")
            if cards:
                print(f"  {cards} live card(s) have no picture stored.\n"
                      f"    The cards still scan -- the token is in the database. "
                      f"Only the\n    printed image is missing, and Reissue on the "
                      f"client's profile draws it.")

        if dry_run:
            print("\n--dry-run: nothing was written to MongoDB.")
            return 0

        target = mongo_repo()
        try:
            print(f"\nTarget: {config.describe()}")
            if not target.is_empty() and not force:
                print("\nThe target database already holds data. Re-run with "
                      "--force to overwrite what shares an id with this "
                      "source;\nnothing else there is touched, and nothing is "
                      "deleted.")
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

            # Documents that were already there under an id this source does
            # not have. Nothing is deleted -- said out loud rather than left
            # for someone to find, since on a re-run it usually means rows
            # deleted from SQLite since the last one.
            extra = {c: n for c in ORDER
                     if (n := target.db[c].count_documents({}) - moved[c]) > 0}
            if extra:
                print(f"\n  Left in place, not in this source: {extra}")

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
