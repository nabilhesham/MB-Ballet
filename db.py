"""SQLite layer for MB Ballet Academy."""

import sqlite3
import time
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS instructors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    phone        TEXT,
    email        TEXT,
    specialty    TEXT,
    hourly_rate  REAL NOT NULL DEFAULT 0,
    photo_path   TEXT,
    active       INTEGER NOT NULL DEFAULT 1
);

-- A class is the offering (Ballet, Flexibility). instructor_id is a default,
-- not a replacement for the instructor on each session: it is what a new
-- session falls back to when none is chosen, and what every upcoming
-- session's instructor is overwritten to when this changes. Who actually
-- teaches a given date still lives on the session and stays editable there.
CREATE TABLE IF NOT EXISTS classes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    description    TEXT,
    colour         TEXT NOT NULL DEFAULT '#87438E',
    duration_hours REAL NOT NULL DEFAULT 1.5,
    -- "primary", "level 8", "grade 6" -- the wording the roster sheets use.
    level          TEXT,
    instructor_id  INTEGER REFERENCES instructors(id),
    active         INTEGER NOT NULL DEFAULT 1
);

-- A scheduled occurrence of a class.
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id       INTEGER NOT NULL REFERENCES classes(id),
    instructor_id  INTEGER REFERENCES instructors(id),
    starts_at      INTEGER NOT NULL,          -- unix seconds
    duration_hours REAL NOT NULL DEFAULT 1.5,
    -- starts_at + duration_hours*3600, written rather than derived. It is
    -- the session's own length that decides this and nothing else -- the
    -- class it belongs to has a length too, and that one is only what the
    -- form offers when somebody schedules a session (see api/classes.py).
    --
    -- Stored because two things read it on a path that must not scan:
    -- slot_conflict() guards every create, edit, repeat and un-cancel, and
    -- repo.next_session_end() is the alarm clock the end-of-session event
    -- is set by. Computed in the WHERE clause it is a full table scan,
    -- because a predicate wrapping a column cannot use an index. Stored, it
    -- is a range scan on ix_sess_ends.
    --
    -- access.ends_at_of() is its only writer. Any new path that sets
    -- starts_at or duration_hours must set this too -- see
    -- tests/test_scheduling.py, which asserts no row ever disagrees, and
    -- migrate() below, which puts any row that does back in step.
    ends_at        INTEGER,
    status         TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled|completed|cancelled
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en     TEXT NOT NULL,
    phone       TEXT,
    email       TEXT,
    -- REAL, not INTEGER: the roster sheets carry "4.8" and "12.5" for the
    -- youngest children, and rounding a four-year-old up to five loses the
    -- distinction the class placement is actually made on.
    age         REAL,
    dob         TEXT,
    school      TEXT,
    joined_on   TEXT,
    photo_path  TEXT,
    notes       TEXT,
    created_at  INTEGER NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

-- A plan is bought for one class, and that class is the spine of everything
-- that follows: the sessions it may pay for, the card that proves it, and the
-- balance a scan is read against. A client taking Ballet and Flexibility holds
-- two plans, two sets of bookings and two cards, and nothing about one reaches
-- into the other.
CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    class_id        INTEGER REFERENCES classes(id),
    plan            TEXT NOT NULL,
    sessions_total  INTEGER NOT NULL,
    sessions_used   INTEGER NOT NULL DEFAULT 0,
    price           REAL,
    -- What the roster sheet's PAID column actually said: "package", "free",
    -- "yes", or the amount. Kept verbatim because "package" and a blank price
    -- mean different things -- one is a bundle billed elsewhere, the other is
    -- a number nobody wrote down -- and revenue reporting must not confuse them.
    payment_note    TEXT,
    -- The day the money actually arrived, from the roster sheet's own PAID
    -- DATE column. NULL means unpaid, and is shown as such -- this answers
    -- "when", where payment_note and price answer "what the sheet said".
    -- Nullable on purpose: a plan sold before it is paid for is normal.
    paid_on         TEXT,
    -- Anything reception wants remembered about this particular plan --
    -- "paid half now, half in October", "sister's card". Separate from
    -- clients.notes, which is about the person rather than the purchase.
    notes           TEXT,
    -- The ballet sheet sells months; the flexibility sheet sells session packs.
    months          INTEGER,
    -- "sunday - thursday", "saturday" -- which weekdays this pack is used on.
    days_pattern    TEXT,
    starts_on       TEXT NOT NULL,
    expires_on      TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL,
    -- Freezing pauses the expiry clock. frozen_on is the date it started;
    -- frozen_until is the date it lifts on its own, or NULL for "until I say".
    frozen_on       TEXT,
    frozen_until    TEXT,
    frozen_days     INTEGER NOT NULL DEFAULT 0
);

-- One row per freeze period, kept so "how often does she freeze?" has an
-- answer and so the days added to the expiry date can be explained later.
CREATE TABLE IF NOT EXISTS freezes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    from_date       TEXT NOT NULL,
    until_date      TEXT,
    ended_on        TEXT,
    days_added      INTEGER,
    released        INTEGER NOT NULL DEFAULT 0,
    reason          TEXT,
    created_at      INTEGER NOT NULL
);

-- A booking is one paid slot: this client, in this session, funded by this
-- plan. Buying a 12-session plan creates 12 bookings up front, so the schedule
-- is known in advance rather than discovered when someone turns up.
--   booked   the session has not happened yet
--   present  they attended
--   absent   they did not
-- There is no "excused": a slot is either used or it is not.
CREATE TABLE IF NOT EXISTS bookings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id),
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    subscription_id INTEGER REFERENCES subscriptions(id),
    status          TEXT NOT NULL DEFAULT 'booked',
    checked_in_at   INTEGER,
    -- When the end-of-session event wrote this row off, and nothing else:
    -- NULL on a booking it has never touched, including one entered absent
    -- by hand or born absent with a back-dated plan. "The session ended and
    -- they had not checked in" was the one fact about an absence with no
    -- record of when it was decided, which is exactly what made three
    -- reports of "marked absent too early" impossible to settle from the
    -- data -- the stamp beside the session's own end is the whole answer.
    settled_at      INTEGER,
    -- Which screen wrote `checked_in_at`, because the two mean different
    -- things and one label was covering both. 'kiosk' is the instant the
    -- client was at the door (a scan, or a number/name lookup -- both go
    -- through check_in_booking); 'roster' is the instant somebody pressed
    -- Present on the session page, which for a roster taken at the end of
    -- class is an hour or more after the client actually arrived; 'sheet' is
    -- the seed's derived stamp, which is start+10min and not an observation
    -- at all. NULL on any row written before this column existed.
    checked_in_by   TEXT,
    created_at      INTEGER NOT NULL,
    UNIQUE(client_id, session_id)
);

-- One card per client per class. Scanning the Ballet card checks the client
-- into their Ballet session; the Flexibility card into that one. A client
-- taking both therefore carries two cards.
CREATE TABLE IF NOT EXISTS credentials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id),
    class_id    INTEGER REFERENCES classes(id),
    token       TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT 'card',
    issued_at   INTEGER NOT NULL,
    revoked_at  INTEGER
);

-- One row per instructor per working day, straight from the monthly salary
-- sheet. Hours are what the sheet records; pay is hours x hourly_rate, so a
-- corrected rate re-prices the month rather than leaving a stale total behind.
CREATE TABLE IF NOT EXISTS instructor_hours (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id INTEGER NOT NULL REFERENCES instructors(id),
    work_date     TEXT NOT NULL,
    hours         REAL NOT NULL,
    source        TEXT,
    created_at    INTEGER NOT NULL,
    UNIQUE(instructor_id, work_date)
);

-- A manual correction to hours logged, layered on top of instructor_hours
-- without ever touching it: corrections are counted alongside the salary
-- sheet's own rows, never mixed into or overwriting them, so what the sheet
-- actually said stays visible and auditable. delta_hours can be negative --
-- a correction downward doesn't delete or edit a real imported row either.
CREATE TABLE IF NOT EXISTS instructor_hour_adjustments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id   INTEGER NOT NULL REFERENCES instructors(id),
    adjustment_date TEXT NOT NULL,
    delta_hours     REAL NOT NULL,
    note            TEXT,
    created_at      INTEGER NOT NULL
);

-- Every image the app holds: client and instructor photos, and the printed
-- member cards. Base64 text rather than a BLOB, because the same rows have to
-- live in MongoDB too and base64 is the one encoding both stores and the
-- repository interface's plain dicts carry without a per-backend special case.
--
-- In the database rather than in photos/ and cards/ on purpose. Those folders
-- were the one part of the academy's record that a backup of the database did
-- not contain, and on the hosted backend they were worse than that: the data
-- lived on Atlas and the faces lived on whichever laptop happened to upload
-- them.
--
-- `kind` is what the image is of, `owner_id` who, and `variant` which one of
-- theirs -- the class slug for a card, since a client holds one per class.
-- The three together are unique: an upload or a reissue replaces, never
-- accumulates.
CREATE TABLE IF NOT EXISTS images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    owner_id   INTEGER NOT NULL,
    variant    TEXT NOT NULL DEFAULT '',
    mime       TEXT NOT NULL,
    data       TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- An enquiry: somebody who has asked to come in on a date and is not a client
-- yet. Deliberately NOT a foreign key onto clients -- most of these are
-- strangers who rang up, and the whole point of the list is the ones who have
-- not been written down as clients. They carry their own name, age and mobile
-- because that is all reception has when the phone rings; if the person turns
-- up and enrols, a client is created separately and this row stays as the
-- record of the enquiry.
--
-- No uniqueness rule either, on the number or on anything else: the same
-- family rings twice about two children, and the same person reschedules.
CREATE TABLE IF NOT EXISTS appointments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone       TEXT,
    age         REAL,
    -- ISO YYYY-MM-DD, like every other calendar day in this schema, with the
    -- time of day kept beside it as a separate HH:MM rather than folded into
    -- one timestamp. Both halves are real -- reception does write "Tuesday
    -- at 4" down -- but only the day is ever filtered on, and a day column
    -- is what lets the list's from/to range need no end-of-day arithmetic
    -- (contrast the Sessions list, where the TO bound has to be pushed to
    -- 23:59 because the column is an epoch). on_time is nullable: an
    -- enquiry taken as "sometime Tuesday" is a real state, not a blank to
    -- be filled with midnight.
    on_date     TEXT NOT NULL,
    on_time     TEXT,
    notes       TEXT,
    created_at  INTEGER NOT NULL
);

-- The shop. Leotards, shoes, hairnets: things the academy sells over the
-- counter, which is the second thing money arrives for and until now the
-- only one the till figure did not know about.
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    -- Nullable for the same reason a plan's price is: a thing whose price
    -- nobody has written down must not be reported as zero revenue. A sale
    -- copies this at the moment it happens -- see product_sales.unit_price.
    price       REAL,
    -- What is on the shelf, in total. Deliberately a plain count rather than
    -- a ledger of movements: the academy counts the box, and a stock history
    -- nobody queries is schema churn. A sale takes one off and removing that
    -- sale puts it back, which is the only arithmetic there is.
    --
    -- Since sizes it is the **sum of this product's product_stock rows**,
    -- stored rather than derived, with exactly one writer
    -- (access._sync_stock, through which every change to a size's quantity
    -- goes). Stored because the shop list is one round trip and the number
    -- on every card is the answer to "have we any left"; one writer because
    -- a second one is how `sessions_used` ended up dead weight that could
    -- not be trusted. Never set this from anywhere else.
    stock       INTEGER NOT NULL DEFAULT 0,
    notes       TEXT,
    -- The picture, as a URL this app answers -- the same column every other
    -- picture in the app carries. See images.py.
    photo_path  TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL
);

-- How many of a product are on the shelf, broken down by size.
--
-- **A product with no sizes has exactly one row, with `size` NULL** -- not
-- no rows at all. A hairnet is a real product and "one size" is a real
-- answer, and that uniformity is the whole design: a sale always names a
-- size, the shelf count is always the sum of these rows, and there is no
-- separate sizeless path to drift from the sized one. access.stock_rows()
-- is the only thing that reads them and access.set_product_sizes() the only
-- thing that rewrites the set; an existing product gets its one NULL row
-- from access.ensure_stock_rows(), at startup, idempotently -- see there
-- for why that is not a db.migrate() step.
CREATE TABLE IF NOT EXISTS product_stock (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    -- REAL, not INTEGER, for the same reason clients.age is: shoe and
    -- leotard sizes come in halves, and 34.5 rounded to 34 is the
    -- distinction the fitting was made on. NULL is "this product has no
    -- sizes", and there is at most one such row per product.
    size        REAL,
    qty         INTEGER NOT NULL DEFAULT 0
);

-- One sale: this client bought this many of this product. It is the shop's
-- counterpart of a subscription, and the two deliberately share the rules
-- that matter.
CREATE TABLE IF NOT EXISTS product_sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    client_id   INTEGER NOT NULL REFERENCES clients(id),
    qty         INTEGER NOT NULL DEFAULT 1,
    -- Which size left the shelf, so removing the sale puts it back on the
    -- right one. NULL means the product has no sizes -- the same NULL
    -- product_stock.size carries, and read the same way. Like unit_price it
    -- is a receipt: changing a product's sizes next term must not rewrite
    -- what was handed over last month.
    size        REAL,
    -- **Copied from the product at the moment of sale, never read back
    -- through it.** Re-pricing a leotard must not rewrite what last month's
    -- till took -- the same reason instructor pay is `hours x rate` at read
    -- time and this is not: a rate correction should re-price the month, a
    -- shop price change should not re-price a receipt. Nullable, like the
    -- product's own price, and `qty x unit_price` is the line total.
    unit_price  REAL,
    -- The day the money is dated, exactly as subscriptions.paid_on is, so
    -- one rule governs the whole till: reception writes the sale down when
    -- it happens and the figure lands on the day it is dated. NULL is
    -- unpaid and falls out of every day rather than counting as zero in one.
    paid_on     TEXT,
    notes       TEXT,
    created_at  INTEGER NOT NULL
);

-- Free-form key/value settings. Only a handful, so a table beats a config file
-- that would drift out of sync with what the UI shows.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER,
    credential_id INTEGER,
    session_id    INTEGER,
    scanned_at    INTEGER NOT NULL,
    decision      TEXT NOT NULL,
    reason        TEXT,
    confirmed_at  INTEGER,
    session_spent INTEGER NOT NULL DEFAULT 0,
    -- 'scan'   a card was actually presented at reception
    -- 'manual' staff marked someone present without a card
    source        TEXT NOT NULL DEFAULT 'scan'
);

"""

# Applied *after* migrate(), never with the tables above.
#
# CREATE TABLE IF NOT EXISTS is a no-op on a database that already has the
# table, so a column added to this file later does not appear in an existing
# one until migrate() ALTERs it in -- but an index over that column was in the
# same script and ran first, failing with "no such column" and taking the
# whole executescript with it. migrate() then never ran at all. That is not
# hypothetical: the academy's own database predates sessions.ends_at, and
# `ix_sess_ends` made the app unable to open it.
INDEXES = """
CREATE INDEX IF NOT EXISTS ix_iha_date    ON instructor_hour_adjustments(instructor_id, adjustment_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_img_owner ON images(kind, owner_id, variant);
CREATE INDEX IF NOT EXISTS ix_cred_token  ON credentials(token);
CREATE INDEX IF NOT EXISTS ix_ev_time     ON access_events(scanned_at);
CREATE INDEX IF NOT EXISTS ix_sess_start  ON sessions(starts_at);
CREATE INDEX IF NOT EXISTS ix_sess_ends   ON sessions(ends_at);
CREATE INDEX IF NOT EXISTS ix_sub_client  ON subscriptions(client_id, class_id, active);
CREATE INDEX IF NOT EXISTS ix_bk_client   ON bookings(client_id);
CREATE INDEX IF NOT EXISTS ix_bk_session  ON bookings(session_id);
CREATE INDEX IF NOT EXISTS ix_bk_sub      ON bookings(subscription_id);
CREATE INDEX IF NOT EXISTS ix_frz_sub     ON freezes(subscription_id);
CREATE INDEX IF NOT EXISTS ix_ih_date     ON instructor_hours(work_date);
CREATE INDEX IF NOT EXISTS ix_cli_joined  ON clients(joined_on);
CREATE INDEX IF NOT EXISTS ix_sub_starts  ON subscriptions(starts_on);
CREATE INDEX IF NOT EXISTS ix_appt_date  ON appointments(on_date);
-- The shop: the client profile lists one client's purchases, the product
-- page lists one product's buyers, and the till reads one day's.
CREATE INDEX IF NOT EXISTS ix_psale_client  ON product_sales(client_id);
CREATE INDEX IF NOT EXISTS ix_psale_product ON product_sales(product_id);
CREATE INDEX IF NOT EXISTS ix_psale_paid    ON product_sales(paid_on);
CREATE INDEX IF NOT EXISTS ix_pstock_product ON product_stock(product_id);

-- Added for the joins that already existed but had nothing to stand on. Each
-- one backs a query in repo/sqlite/ports.py that was scanning without it;
-- the Mongo side already carried the first three (repo/mongo/schema.py).
CREATE INDEX IF NOT EXISTS ix_sess_class  ON sessions(class_id);
CREATE INDEX IF NOT EXISTS ix_sess_instr  ON sessions(instructor_id);
CREATE INDEX IF NOT EXISTS ix_cred_client ON credentials(client_id, class_id);
-- settle_session_absences() updates `WHERE status='booked' AND session_id=?`
-- when a session's end-of-session event fires. Without this it was a full
-- scan of bookings. Status first: it is the more selective equality, and the
-- same index still serves the session-id lookups behind every roster.
CREATE INDEX IF NOT EXISTS ix_bk_status   ON bookings(status, session_id);
-- search_clients and class_students both order by name; active_plans_for
-- orders by expires_on.
CREATE INDEX IF NOT EXISTS ix_cli_name    ON clients(name_en);
CREATE INDEX IF NOT EXISTS ix_sub_expires ON subscriptions(expires_on);
"""


def connect(path: str = None) -> sqlite3.Connection:
    """
    Open the database. The path comes from config when not given.

    Read through config rather than from a module constant: the constant was
    bound at import time, which is before server.py had loaded .env, so no
    environment variable could ever have changed it.

    `isolation_level=None` turns off sqlite3's own transaction management.
    It used to open one implicitly at the first DML statement and close it at
    the next commit(), which meant transaction boundaries were wherever a
    commit happened to be rather than where anyone decided they should be.
    Every write now names its own boundary with tx(); anything outside one
    autocommits, which is what a single read wants anyway.
    """
    conn = sqlite3.connect(path or config.sqlite_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


@contextmanager
def tx(conn):
    """
    One transaction. Commits on the way out, rolls back if anything raises.

        with db.tx(conn):
            conn.execute(...)
            conn.execute(...)

    **Re-entrant.** The outermost block owns the transaction and the inner
    ones are no-ops, because the calls genuinely nest: swap_and_check_in()
    calls move_booking() and check_in(), and settle_due_sessions() calls
    lift_expired_freezes(), which calls unfreeze_plan() once per due row.
    Each of those used to commit on its own, so one user action was three
    transactions and a sweep was one per row. Nesting is detected with
    sqlite3's own `in_transaction` rather than a depth counter, since a
    Connection cannot carry attributes.

    There are no savepoints: an inner block that raises rolls the whole
    outermost transaction back. That is the behaviour this app wants —
    nothing here half-succeeds on purpose.

    **BEGIN IMMEDIATE, not BEGIN.** It takes the write lock at the top of the
    block rather than at the first write, so a read-then-write sequence is
    serialised against another writer. book() counts a plan's bookings and
    then inserts one; under a deferred transaction that count is taken
    without the lock, and two concurrent sales could each see room for the
    last slot.

    Rollback used to happen only because closing a connection discards an
    open transaction — it worked, but by accident rather than because
    anything asked for it, and it is the reason a failed multi-statement
    write left no trace of having been attempted.
    """
    if conn.in_transaction:
        yield conn                      # inner block: the outermost one owns it
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init(path: str = None) -> None:
    conn = connect(path)
    try:
        # executescript commits whatever is open before it runs, so this is
        # deliberately not inside a tx() — the schema is its own unit.
        #
        # Three steps, in this order and not two: the tables, then the
        # columns an older database is missing, then the indexes — which may
        # be over a column migrate() has only just added. See INDEXES.
        conn.executescript(SCHEMA)
        migrate(conn)
        conn.executescript(INDEXES)
    finally:
        # sqlite3's connection context manager commits but does not close, so
        # `with connect(...) as conn:` leaked a handle on every call.
        conn.close()


def now() -> int:
    return int(time.time())


DEFAULT_SETTINGS = {
    # Kept as a table rather than a config file so a setting cannot drift out of
    # sync with what the UI shows. Nothing uses it yet — the no-show charging
    # rules it once held were removed when bookings replaced attendance.
}


def get_settings(conn) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for r in conn.execute("SELECT key, value FROM settings").fetchall():
        out[r["key"]] = r["value"]
    return out


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def migrate(conn) -> None:
    """
    Add columns to databases created before those columns existed.

    Kept deliberately simple: only additive ALTERs, no data rewriting. A
    database from the class-enrolment era should be re-seeded rather than
    migrated, since bookings replaced three tables at once.
    """
    def cols(table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    add = {
        "clients": [("age", "REAL"), ("school", "TEXT"), ("joined_on", "TEXT"),
                    ("dob", "TEXT")],
        "instructors": [("hourly_rate", "REAL NOT NULL DEFAULT 0"), ("photo_path", "TEXT")],
        "classes": [("level", "TEXT"), ("instructor_id", "INTEGER")],
        "sessions": [("ends_at", "INTEGER")],
        "credentials": [("class_id", "INTEGER")],
        "appointments": [("on_time", "TEXT")],
        "bookings": [("settled_at", "INTEGER"),
                     ("checked_in_by", "TEXT")],
        "product_sales": [("size", "REAL")],
        "subscriptions": [("frozen_on", "TEXT"), ("frozen_until", "TEXT"),
                          ("frozen_days", "INTEGER NOT NULL DEFAULT 0"),
                          ("class_id", "INTEGER"),
                          ("payment_note", "TEXT"), ("months", "INTEGER"),
                          ("days_pattern", "TEXT"), ("paid_on", "TEXT"),
                          ("notes", "TEXT")],
    }
    for table, columns in add.items():
        try:
            have = cols(table)
        except Exception:
            continue
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # Put every session's end back in step with its own start and length.
    # Deliberately not guarded by a one-shot marker like the expiry backfill
    # below: it is targeted at exactly the rows that are wrong, costs nothing
    # when there are none, and so doubles as a repair for any write path that
    # forgets to set it.
    #
    # **NULL is not the only way to be wrong, and this used to only fix
    # NULL.** A row wrong by an hour is invisible -- slot_conflict() reads
    # the wrong slot and the end-of-session event is set for the wrong
    # moment, with nothing on any screen to say why. The sweep used to
    # re-check this on every pass (access._repair_running_sessions); there is
    # no sweep now, so it is checked here, once, where the one thing that can
    # produce such a row is an older database.
    conn.execute(
        "UPDATE sessions"
        "   SET ends_at = CAST(starts_at + duration_hours * 3600 AS INTEGER)"
        " WHERE ends_at IS NULL"
        "    OR ends_at != CAST(starts_at + duration_hours * 3600 AS INTEGER)")

    # One-shot. expires_on used to be a floor that plan_state() raised at
    # read time to the last session a plan covers; it is now the value
    # itself, kept current by access.refresh_expiry() as bookings change.
    # Lift any date that was relying on that read-time raise up to what was
    # already on screen, once, so nothing appears to regress the moment this
    # ships. Guarded by a marker rather than repeated on every start: after
    # this runs, a stored date sitting below the last session is a
    # deliberate override (typed in edit_plan()) and must not be re-raised.
    if not get_settings(conn).get("expiry_backfilled"):
        conn.execute(
            "UPDATE subscriptions SET expires_on = ("
            "  SELECT MAX(date(s.starts_at, 'unixepoch', 'localtime'))"
            "    FROM bookings b JOIN sessions s ON s.id = b.session_id"
            "   WHERE b.subscription_id = subscriptions.id)"
            " WHERE expires_on < ("
            "  SELECT MAX(date(s.starts_at, 'unixepoch', 'localtime'))"
            "    FROM bookings b JOIN sessions s ON s.id = b.session_id"
            "   WHERE b.subscription_id = subscriptions.id)")
        set_setting(conn, "expiry_backfilled", "1")


def store_credential(conn, client_id, token, kind="card") -> int:
    """Issuing a new card revokes the client's previous one of the same kind."""
    conn.execute(
        "UPDATE credentials SET revoked_at=? WHERE client_id=? AND kind=? AND revoked_at IS NULL",
        (now(), client_id, kind),
    )
    cur = conn.execute(
        "INSERT INTO credentials (client_id, token, kind, issued_at) VALUES (?,?,?,?)",
        (client_id, token, kind, now()),
    )
    return cur.lastrowid
