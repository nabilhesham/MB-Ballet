"""
The MongoDB implementation.

Imported only when `MB_DB_BACKEND=mongo`, so a SQLite-only install needs no
pymongo at all — the reception laptop must not fail to start because a wheel
did not download.

Three things here are not obvious from the SQLite side:

**Integer `_id`.** See ids.py. The card token packs a uint32, so ObjectIds
are not available.

**Every declared field is written, always.** See schema.py. "Missing" and
"null" are different in MongoDB and identical in SQLite, and the differences
are silent.

**Transactions need a replica set.** Atlas is one; a standalone `mongod` is
not, and `start_transaction` against one fails. That is a deployment
requirement, not an implementation detail.
"""

from contextlib import contextmanager

from ..base import Repo
from ..errors import DuplicateKey, Unavailable
from ..filters import normalise_sort
from . import ids, schema
from .client import get_client
from .filters import compile_filter, compile_sort, rename_id
from .ports import (MongoAccess, MongoBookings, MongoClasses, MongoClients,
                    MongoEvents, MongoInstructors, MongoPlans, MongoSessions)


class MongoRepo(MongoAccess, MongoSessions, MongoBookings, MongoClasses,
                MongoClients, MongoInstructors, MongoPlans, MongoEvents, Repo):

    def __init__(self, uri: str, database: str):
        self.client = get_client(uri)
        self.db = self.client[database]
        self.session = None

    # ---------------------------------------------------------- lifecycle

    def close(self):
        # Only the logical session. The client is process-wide and its pool
        # outlives any one request -- see client.py.
        if self.session is not None:
            self.session.end_session()
            self.session = None

    @contextmanager
    def begin(self):
        """
        See Repo.begin. Re-entrant: an inner block is a no-op, because the
        calls genuinely nest.
        """
        if self.session is not None and self.session.in_transaction:
            yield self
            return

        with self.client.start_session() as session:
            self.session = session
            session.start_transaction()
            try:
                yield self
            except BaseException:
                if session.in_transaction:
                    session.abort_transaction()
                raise
            finally:
                pass
            self._commit(session)
            self.session = None

    @staticmethod
    def _commit(session):
        """
        Commit, retrying only when the outcome is unknown.

        `UnknownTransactionCommitResult` means the commit may have succeeded
        and the acknowledgement was lost; retrying it is idempotent and is
        what the driver documentation asks for.

        A `TransientTransactionError` — the body needing to be re-run — is
        deliberately *not* retried. This is a single-user app on one laptop,
        so write contention is nil and the real causes are network blips and
        primary elections. Silently re-running a POST is more dangerous there
        than surfacing the failure and letting reception press the button
        again.
        """
        from pymongo.errors import PyMongoError
        while True:
            try:
                session.commit_transaction()
                return
            except PyMongoError as e:
                if e.has_error_label("UnknownTransactionCommitResult"):
                    continue
                raise

    # ---------------------------------------------------------- helpers

    def _find(self, coll, flt=None, sort=None, limit=None, fields=None):
        projection = None
        if fields:
            projection = {("_id" if f == "id" else f): 1 for f in fields}
        cur = self.db[coll].find(
            rename_id(compile_filter(coll, flt)), projection,
            session=self.session)
        if sort:
            cur = cur.sort(compile_sort(sort))
        if limit is not None:
            cur = cur.limit(limit)
        return cur

    def _docs(self, coll, cur, fields=None):
        rows = [schema.normalise(coll, d) for d in cur]
        if fields:
            keep = set(fields)
            rows = [{k: v for k, v in r.items() if k in keep} for r in rows]
        return rows

    # ---------------------------------------------------------- primitives

    def get(self, coll, id_):
        return schema.normalise(
            coll, self.db[coll].find_one({"_id": id_}, session=self.session))

    def find_one(self, coll, flt, sort=None):
        rows = self.find(coll, flt, sort=sort, limit=1)
        return rows[0] if rows else None

    def find(self, coll, flt=None, sort=None, limit=None, fields=None):
        cur = self._find(coll, flt, normalise_sort(sort), limit, fields)
        return self._docs(coll, cur, fields)

    def count(self, coll, flt=None):
        return self.db[coll].count_documents(
            rename_id(compile_filter(coll, flt)), session=self.session)

    def exists(self, coll, flt):
        return self.find_one(coll, flt) is not None

    def distinct(self, coll, field, flt=None):
        return self.db[coll].distinct(
            "_id" if field == "id" else field,
            rename_id(compile_filter(coll, flt)), session=self.session)

    def insert(self, coll, doc):
        return self.insert_many(coll, [doc])[0]

    def insert_many(self, coll, docs):
        if not docs:
            return []
        # One counter increment for the whole batch. Per-document would cost
        # a round trip each, which is what add_plan (twelve bookings) and
        # seed.py (thousands) would pay.
        first = ids.next_id(self.db, coll, self.session, count=len(docs))
        prepared = []
        for offset, doc in enumerate(docs):
            body = schema.fill(coll, doc)
            body.pop("id", None)
            body["_id"] = first + offset
            prepared.append(body)
        try:
            self.db[coll].insert_many(prepared, session=self.session)
        except Exception as e:
            raise self._translate(e) from e
        return [d["_id"] for d in prepared]

    def insert_ignore(self, coll, doc):
        try:
            return self.insert(coll, doc)
        except DuplicateKey:
            return None

    def update(self, coll, id_, fields):
        body = {k: v for k, v in fields.items() if k != "id"}
        res = self.db[coll].update_one(
            {"_id": id_}, {"$set": body}, session=self.session)
        return res.matched_count > 0

    def update_where(self, coll, flt, fields):
        body = {k: v for k, v in fields.items() if k != "id"}
        res = self.db[coll].update_many(
            rename_id(compile_filter(coll, flt)), {"$set": body},
            session=self.session)
        return res.modified_count

    def delete(self, coll, id_):
        return self.db[coll].delete_one(
            {"_id": id_}, session=self.session).deleted_count > 0

    def delete_where(self, coll, flt):
        return self.db[coll].delete_many(
            rename_id(compile_filter(coll, flt)),
            session=self.session).deleted_count

    # ---------------------------------------------------------- admin

    def init_schema(self):
        schema.ensure_indexes(self.db)
        ids.sync_counters(self.db, schema.FIELDS)

    def is_empty(self):
        # `settings` is excluded for the same reason as on SQLite: it holds
        # bookkeeping rather than academy data.
        return not any(self.db[c].find_one(session=self.session) is not None
                       for c in schema.FIELDS if c != "settings")

    def drop_all(self):
        """
        Destroy everything in this database.

        Deliberately refuses a name that does not look like a test database
        unless MB_MONGO_ALLOW_DROP is set. On SQLite this unlinks a local
        file; here it can be a shared remote database that other people are
        using, and `seed.py --force` is run from the same shell as everything
        else.
        """
        import os
        name = self.db.name
        if not name.startswith("mbtest_") and not os.environ.get("MB_MONGO_ALLOW_DROP"):
            raise RuntimeError(
                f"refusing to drop the remote database {name!r}. This is not a "
                f"local file — set MB_MONGO_ALLOW_DROP=1 if you really mean it.")
        for coll in list(schema.FIELDS) + [ids.COUNTERS]:
            self.db[coll].drop()
        self.init_schema()

    # ---------------------------------------------------------- errors

    @staticmethod
    def _translate(exc):
        """Driver failures become the backend-neutral ones in repo/errors.py."""
        from pymongo.errors import (BulkWriteError, DuplicateKeyError,
                                    ServerSelectionTimeoutError)
        if isinstance(exc, (DuplicateKeyError, BulkWriteError)):
            return DuplicateKey(str(exc))
        if isinstance(exc, ServerSelectionTimeoutError):
            return Unavailable(
                "the MongoDB server could not be reached. If the URI is the "
                "mongodb+srv:// form, this network may be filtering the DNS "
                "SRV lookup it needs — see .env.example for the direct form.")
        return exc
