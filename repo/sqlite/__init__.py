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
from .ports import (SqliteBookings, SqliteClasses, SqliteEvents,
                    SqlitePlans, SqliteSessions)


class SqliteRepo(SqliteSessions, SqliteBookings, SqliteClasses,
                 SqlitePlans, SqliteEvents, Repo):

    def __init__(self, conn):
        self.conn = conn

    # ---------------------------------------------------------- lifecycle

    def close(self):
        self.conn.close()

    def begin(self):
        return db.tx(self.conn)

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

    # ---------------------------------------------------------- escape hatch

    def raw(self, sql, params=()):
        """See Repo.raw. Temporary, and the count only goes down."""
        return self.conn.execute(sql, params)
