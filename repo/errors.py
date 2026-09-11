"""
Backend-neutral failures.

A caller must never have to catch `sqlite3.IntegrityError` in one
deployment and `pymongo.errors.DuplicateKeyError` in another — that is
exactly the kind of leak the interface exists to stop. Each backend
translates its own exceptions into these.
"""


class RepoError(Exception):
    """Anything the data layer refuses."""


class DuplicateKey(RepoError):
    """A unique constraint said no.

    Raised where SQLite gives IntegrityError and Mongo gives
    DuplicateKeyError. The three that matter are bookings(client_id,
    session_id), instructor_hours(instructor_id, work_date) and
    credentials.token.
    """


class Unavailable(RepoError):
    """The database could not be reached.

    Only ever raised by a networked backend. SQLite is a file on the same
    disk as the process; Atlas is not, and reception needs to be told that
    in plain language rather than shown a driver traceback.
    """
