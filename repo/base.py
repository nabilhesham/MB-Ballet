"""
The data-access interface.

Two tiers, with a boundary that is structural rather than a matter of
discipline.

**Tier 1, here: primitives.** Single-collection only. The signatures make a
join impossible to express, so "anything harder belongs in tier 2" does not
depend on anyone remembering it. Roughly half of the app's queries are this
shape — `SELECT * FROM classes WHERE id=?` and its relatives — and they need
no named method.

**Tier 2, in ports.py: named business questions.** Everything that joins,
aggregates, has a computed predicate, or is a compare-and-swap. Named for
the question the app actually asks, returning plain dicts.

The governing rule for both: **the interface is written in the weaker
backend's vocabulary, and SQLite implements downward into it.** MongoDB
cannot do `ORDER BY ABS(x - ?)` or a five-table join without becoming
unreadable; SQLite can do everything Mongo can. Nothing here quotes SQL.

Rows come back as plain `dict`s — never `sqlite3.Row`, never a BSON
document. `api/helpers.py`'s `rows()`/`one()` did that conversion at the HTTP
boundary; it happens here now, so `access.py` never holds a backend-shaped
object either.
"""

from abc import ABC, abstractmethod


class Repo(ABC):
    """
    One connection's worth of data access, plus its transaction boundary.

    Obtained from `repo.connect()` and closed by the caller, mirroring the
    `conn = db.connect() / try / finally: conn.close()` shape every route
    already had. It is also a context manager.

    A note that matters for the networked backend: on SQLite `connect()`
    opens a file handle and `close()` closes it. On Mongo it takes a handle
    onto a process-global client — which *is* the connection pool, with its
    own topology monitoring — and `close()` ends only the logical session.
    Constructing a client per request against Atlas means a TLS handshake and
    topology discovery per request, which is the usual way a Mongo app ends
    up forty times slower than it should be.
    """

    # ---------------------------------------------------------- lifecycle

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def begin(self):
        """
        A transaction, as a context manager:

            with repo.begin():
                ...

        Commits on the way out, rolls back if anything raises, and is
        **re-entrant** — the outermost block owns the transaction and inner
        ones are no-ops. Re-entrancy is not optional: swap_and_check_in()
        calls move_booking() and check_in(), and settle_past_sessions()
        calls lift_expired_freezes(), which calls unfreeze_plan() per row.

        No savepoints. An inner block that raises rolls the whole outermost
        transaction back, which is what this app wants — nothing in it
        half-succeeds on purpose.
        """

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------------------------------------------------------- primitives
    #
    # `flt` is the dialect in repo/filters.py. `sort` is [(field, 1|-1)];
    # the primary key is appended as a tiebreak so two backends cannot
    # legitimately disagree about the order of equal rows.

    @abstractmethod
    def get(self, coll: str, id_) -> dict | None:
        """One document by primary key, or None."""

    @abstractmethod
    def find_one(self, coll: str, flt: dict, sort=None) -> dict | None:
        ...

    @abstractmethod
    def find(self, coll: str, flt: dict = None, sort=None,
             limit: int = None, fields=None) -> list:
        ...

    @abstractmethod
    def count(self, coll: str, flt: dict = None) -> int:
        ...

    @abstractmethod
    def exists(self, coll: str, flt: dict) -> bool:
        ...

    @abstractmethod
    def distinct(self, coll: str, field: str, flt: dict = None) -> list:
        ...

    @abstractmethod
    def insert(self, coll: str, doc: dict) -> int:
        """
        Insert one document; returns its new integer id.

        **The return value is the contract, not how it is produced.** SQLite
        satisfies it with `lastrowid`; Mongo mints the id from a counters
        collection first and uses it as `_id`. `lastrowid` is not part of
        this interface.

        Integer ids are not a storage detail here and cannot become
        ObjectIds: tokens.py packs `client_id` as a uint32 into every card
        already in a client's hands, the client id *is* the printed member
        number, and it is in filenames on disk.
        """

    @abstractmethod
    def insert_many(self, coll: str, docs: list) -> list:
        """
        Insert several; returns their ids in order.

        Not a convenience. Against Atlas every insert costs a round-trip for
        the id on top of the write, so add_plan (12 bookings), repeat_sessions
        (up to 96 sessions) and seed.py (thousands) would each pay it per row.
        The Mongo implementation allocates a block of ids with a single
        increment.
        """

    @abstractmethod
    def insert_ignore(self, coll: str, doc: dict):
        """
        Insert unless a unique index refuses it; returns the id or None.

        seed.py's `INSERT OR IGNORE`. It is only idempotent because those
        unique indexes exist — without them the Mongo backend would silently
        duplicate, which is the single most likely quiet divergence in the
        port.
        """

    @abstractmethod
    def update(self, coll: str, id_, fields: dict) -> bool:
        """Set fields on one document; True if it existed."""

    @abstractmethod
    def update_where(self, coll: str, flt: dict, fields: dict) -> int:
        """Set fields on every match; returns how many changed."""

    @abstractmethod
    def delete(self, coll: str, id_) -> bool:
        ...

    @abstractmethod
    def delete_where(self, coll: str, flt: dict) -> int:
        ...

    # ---------------------------------------------------------- admin

    @abstractmethod
    def init_schema(self) -> None:
        """
        Create whatever the backend needs before it can be written to.

        Tables and indexes on SQLite; collections, indexes and the id
        counters on Mongo. Idempotent — it runs on every startup.
        """

    @abstractmethod
    def is_empty(self) -> bool:
        """
        Whether there is anything here yet.

        Replaces `os.path.exists("academy.db")`, which is not a question a
        networked backend can answer — and which was never quite the right
        one anyway: a database that exists but was never seeded would skip
        the seed prompt.
        """

    @abstractmethod
    def drop_all(self) -> None:
        """
        Destroy everything. Only seed.py calls this, only with --force.

        On SQLite it unlinks the file. On a shared remote database it is a
        different class of accident entirely, which is why the Mongo
        implementation must make the caller name what it is dropping.
        """
