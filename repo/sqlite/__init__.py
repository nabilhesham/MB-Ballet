"""
The SQLite implementation.

Wraps the connection `db.py` already opens, so the pragmas, the WAL mode and
`db.tx()`'s re-entrant boundary are unchanged — this is a different way of
reaching the same database, not a different database.
"""

import sqlite3

import db

from ..base import Repo
from ..errors import DuplicateKey
from ..filters import normalise_sort
from .filters import compile_order, compile_where
from .ports import (SqliteAccess, SqliteBookings, SqliteClasses,
                    SqliteClients, SqliteEvents, SqliteInstructors,
                    SqlitePlans, SqliteSessions)


class SqliteRepo(SqliteAccess, SqliteSessions, SqliteBookings,
                 SqliteClasses, SqliteClients, SqliteInstructors,
                 SqlitePlans, SqliteEvents, Repo):

    def __init__(self, conn):
        self.conn = conn

    # ---------------------------------------------------------- lifecycle

    def close(self):
        self.conn.close()

    def begin(self):
        return db.tx(self.conn)

    @property
    def in_transaction(self):
        return self.conn.in_transaction

    # ---------------------------------------------------------- helpers

    def _select(self, coll, flt, sort=None, limit=None, fields=None, count=False):
        what = "COUNT(*) AS n" if count else (", ".join(fields) if fields else "*")
        where, params = compile_where(flt)
        sql = f"SELECT {what} FROM {coll}"
        if where:
            sql += f" WHERE {where}"
        if not count:
            sql += compile_order(sort)
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql, params)

    @staticmethod
    def _rows(cur):
        return [dict(r) for r in cur.fetchall()]

    # ---------------------------------------------------------- primitives

    def get(self, coll, id_):
        row = self.conn.execute(f"SELECT * FROM {coll} WHERE id=?", (id_,)).fetchone()
        return dict(row) if row else None

    def find_one(self, coll, flt, sort=None):
        rows = self.find(coll, flt, sort=sort, limit=1)
        return rows[0] if rows else None

    def find(self, coll, flt=None, sort=None, limit=None, fields=None):
        return self._rows(
            self._select(coll, flt, normalise_sort(sort), limit, fields))

    def count(self, coll, flt=None):
        return self._select(coll, flt, count=True).fetchone()["n"]

    def exists(self, coll, flt):
        return self.find_one(coll, flt) is not None

    def distinct(self, coll, field, flt=None):
        where, params = compile_where(flt)
        sql = f"SELECT DISTINCT {field} AS v FROM {coll}"
        if where:
            sql += f" WHERE {where}"
        return [r["v"] for r in self.conn.execute(sql, params).fetchall()]

    def insert(self, coll, doc):
        names = ", ".join(doc)
        marks = ", ".join("?" * len(doc))
        try:
            cur = self.conn.execute(
                f"INSERT INTO {coll} ({names}) VALUES ({marks})", tuple(doc.values()))
        except sqlite3.IntegrityError as e:
            raise DuplicateKey(str(e)) from e
        return cur.lastrowid

    def insert_many(self, coll, docs):
        # One at a time on purpose: executemany does not report the ids back,
        # and callers need them (add_plan books what it has just sold). The
        # cost is nothing here — a local file, inside one transaction — and
        # the Mongo implementation is where the batching actually matters.
        return [self.insert(coll, d) for d in docs]

    def insert_ignore(self, coll, doc):
        names = ", ".join(doc)
        marks = ", ".join("?" * len(doc))
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO {coll} ({names}) VALUES ({marks})",
            tuple(doc.values()))
        return cur.lastrowid if cur.rowcount else None

    def update(self, coll, id_, fields):
        sets = ", ".join(f"{k}=?" for k in fields)
        cur = self.conn.execute(
            f"UPDATE {coll} SET {sets} WHERE id=?", (*fields.values(), id_))
        return cur.rowcount > 0

    def update_where(self, coll, flt, fields):
        sets = ", ".join(f"{k}=?" for k in fields)
        where, params = compile_where(flt)
        sql = f"UPDATE {coll} SET {sets}"
        if where:
            sql += f" WHERE {where}"
        return self.conn.execute(sql, (*fields.values(), *params)).rowcount

    def delete(self, coll, id_):
        return self.conn.execute(
            f"DELETE FROM {coll} WHERE id=?", (id_,)).rowcount > 0

    def delete_where(self, coll, flt):
        where, params = compile_where(flt)
        sql = f"DELETE FROM {coll}"
        if where:
            sql += f" WHERE {where}"
        return self.conn.execute(sql, params).rowcount

    # ---------------------------------------------------------- admin

    # `settings` is deliberately not here. migrate() writes its own
    # bookkeeping row into it, so a database that has only ever been
    # initialised would otherwise report itself as non-empty — which is the
    # exact false answer this method exists to stop giving.
    TABLES = ("instructors", "classes", "sessions", "clients", "subscriptions",
              "freezes", "bookings", "credentials", "instructor_hours",
              "instructor_hour_adjustments", "access_events")

    def init_schema(self):
        self.conn.executescript(db.SCHEMA)
        db.migrate(self.conn)

    def is_empty(self):
        """
        Whether there is any academy data yet.

        A table that does not exist counts as empty rather than raising. The
        first-run case -- a brand new file with no schema in it -- is the
        whole reason the launchers ask this, so it must not be the one case
        that fails.
        """
        for table in self.TABLES:
            try:
                if self.count(table):
                    return False
            except sqlite3.OperationalError:
                continue          # no such table: nothing in it, then
        return True

    def drop_all(self):
        # The file, its write-ahead log and its shared-memory index. Closing
        # first so the handles are released before they are unlinked.
        import os
        import config
        self.conn.close()
        base = config.sqlite_path()
        for suffix in ("", "-wal", "-shm"):
            path = base + suffix
            if os.path.exists(path):
                os.remove(path)
        self.conn = db.connect()
        self.init_schema()
