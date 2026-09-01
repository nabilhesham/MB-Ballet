"""SQLite layer for MB Ballet Academy."""

import sqlite3
import time

DB_PATH = "academy.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS instructors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    phone        TEXT,
    email        TEXT,
    specialty    TEXT,
    hourly_rate  REAL NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1
);

-- A class is the offering (Ballet, Flexibility). The instructor lives on each
-- session, not here: who teaches a given date changes often enough that a
-- class-level default was more misleading than useful.
CREATE TABLE IF NOT EXISTS classes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    description    TEXT,
    colour         TEXT NOT NULL DEFAULT '#87438E',
    duration_hours REAL NOT NULL DEFAULT 1.5,
    -- "primary", "level 8", "grade 6" -- the wording the roster sheets use.
    level          TEXT,
    active         INTEGER NOT NULL DEFAULT 1
);

-- A scheduled occurrence of a class.
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id       INTEGER NOT NULL REFERENCES classes(id),
    instructor_id  INTEGER REFERENCES instructors(id),
    starts_at      INTEGER NOT NULL,          -- unix seconds
    duration_hours REAL NOT NULL DEFAULT 1.5,
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

CREATE INDEX IF NOT EXISTS ix_cred_token  ON credentials(token);

CREATE INDEX IF NOT EXISTS ix_ev_time     ON access_events(scanned_at);
CREATE INDEX IF NOT EXISTS ix_sess_start  ON sessions(starts_at);
CREATE INDEX IF NOT EXISTS ix_sub_client  ON subscriptions(client_id, class_id, active);
CREATE INDEX IF NOT EXISTS ix_bk_client   ON bookings(client_id);
CREATE INDEX IF NOT EXISTS ix_bk_session  ON bookings(session_id);
CREATE INDEX IF NOT EXISTS ix_bk_sub      ON bookings(subscription_id);
CREATE INDEX IF NOT EXISTS ix_frz_sub     ON freezes(subscription_id);
CREATE INDEX IF NOT EXISTS ix_ih_date     ON instructor_hours(work_date);
CREATE INDEX IF NOT EXISTS ix_cli_joined  ON clients(joined_on);
CREATE INDEX IF NOT EXISTS ix_sub_starts  ON subscriptions(starts_on);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init(path: str = DB_PATH) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        migrate(conn)


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
    conn.commit()


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
        "instructors": [("hourly_rate", "REAL NOT NULL DEFAULT 0")],
        "classes": [("level", "TEXT")],
        "credentials": [("class_id", "INTEGER")],
        "subscriptions": [("frozen_on", "TEXT"), ("frozen_until", "TEXT"),
                          ("frozen_days", "INTEGER NOT NULL DEFAULT 0"),
                          ("class_id", "INTEGER"),
                          ("payment_note", "TEXT"), ("months", "INTEGER"),
                          ("days_pattern", "TEXT")],
    }
    for table, columns in add.items():
        try:
            have = cols(table)
        except Exception:
            continue
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


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
