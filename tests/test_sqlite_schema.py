"""
Schema shape — SQLite only.

These are the checks from the old test_model.py's section 9. They introspect
`PRAGMA table_info` and `sqlite_master`, so they describe the SQL backend
rather than the app's behaviour, and they do not port to a document store.
They stay because they guard decisions that were made deliberately: no
capacity column, bookings having replaced three tables, age being REAL.
"""

import pytest

pytestmark = pytest.mark.sqlite_only


def cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_sessions_carry_duration_not_capacity(academy):
    c = cols(academy.repo.conn, "sessions")
    assert "duration_hours" in c
    assert "capacity" not in c and "duration_min" not in c


def test_classes_carry_a_default_instructor(academy):
    assert "instructor_id" in cols(academy.repo.conn, "classes")


def test_clients_carry_age_school_and_joined_on(academy):
    assert {"age", "school", "joined_on"} <= cols(academy.repo.conn, "clients")


def test_age_is_real_not_integer(academy):
    """The roster sheets hold 4.8 and 12.5; rounding loses the placement."""
    t = {r["name"]: r["type"] for r in academy.repo.conn.execute("PRAGMA table_info(clients)")}
    assert t["age"].upper() == "REAL"


def test_a_fractional_age_survives_a_round_trip(academy):
    conn = academy.repo.conn
    age = conn.execute("SELECT age FROM clients WHERE id=?",
                       (academy.solo_flex,)).fetchone()["age"]
    assert age == 4.8


def test_a_plan_records_what_the_sheet_said_about_payment(academy):
    assert {"price", "payment_note", "months", "days_pattern"} <= cols(
        academy.repo.conn, "subscriptions")


def test_bookings_replaced_the_three_older_tables(academy):
    tables = {r["name"] for r in academy.repo.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "bookings" in tables
    assert not ({"attendance", "enrolments", "session_roster"} & tables)


def test_payroll_hours_have_a_table_of_their_own(academy):
    tables = {r["name"] for r in academy.repo.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "instructor_hours" in tables


def test_ids_are_autoincrement_and_never_reused(academy):
    """
    AUTOINCREMENT is load-bearing, not decoration.

    Without it SQLite hands out max(id)+1, so a hard-deleted client's number
    is reissued to the next person — and the printed card sitting in a drawer
    now belongs to someone else. A MongoDB counters collection never reuses
    either; the two backends agree only because of this keyword.
    """
    sql = {r["name"]: r["sql"] for r in academy.repo.conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table'")}
    for table in ("clients", "sessions", "subscriptions", "bookings",
                  "credentials", "classes", "instructors"):
        assert "AUTOINCREMENT" in sql[table].upper(), table


# ------------------------------------------------------- opening an old file

def test_a_database_older_than_ends_at_still_opens(tmp_path):
    """
    The academy's own database predates `sessions.ends_at`, and `db.init()`
    could not open it: CREATE TABLE IF NOT EXISTS is a no-op on a table that
    exists, so the column stayed missing, and `ix_sess_ends` -- in the same
    executescript, running before migrate() -- failed with "no such column"
    and took the whole script down with it. migrate() never ran at all, so
    the app would not start on the one database that matters.
    """
    import sqlite3

    import db

    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE sessions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id       INTEGER NOT NULL,
            instructor_id  INTEGER,
            starts_at      INTEGER NOT NULL,
            duration_hours REAL NOT NULL DEFAULT 1,
            status         TEXT NOT NULL DEFAULT 'scheduled',
            notes          TEXT);
        INSERT INTO sessions (class_id, starts_at, duration_hours)
             VALUES (1, 1700000000, 1.5);
    """)
    old.commit()
    old.close()

    db.init(path)

    conn = db.connect(path)
    try:
        assert "ends_at" in cols(conn, "sessions")
        # Backfilled, not left NULL: a NULL there is invisible to
        # slot_conflict() and to the absent sweep, which is a wrong answer
        # with nothing on screen to suggest it.
        row = conn.execute("SELECT starts_at, ends_at FROM sessions").fetchone()
        assert row["ends_at"] == row["starts_at"] + int(1.5 * 3600)
        # And the index that used to blow up is there.
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "ix_sess_ends" in names
        assert "images" in {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
