"""
Tier two: the named business questions.

Everything that joins, aggregates, has a computed predicate, or is a
compare-and-swap. Each method is named for the question the app actually
asks and returns plain dicts, so the two backends can answer it however
suits them — `repo/sqlite/access.py` keeps its joins while
`repo/mongo/access.py` will do a handful of `$in` fetches and join in
Python. **The contract is the returned shape, not the shape of the query.**

The ports are `ABC`s and the concrete repo composes them, so a backend that
has not implemented one fails at construction — at startup, not at reception
on a Saturday.

A note on the bulk methods. They are not optimisations to add later. On
SQLite the N+1 they replace is free; against Atlas each round trip is tens
of milliseconds, so `/api/dashboard` calling `plan_state()` once per active
client is three round trips per client and about forty-five seconds on a
few hundred of them. Writing them in from the start is what keeps the two
backends comparable at all.
"""

from abc import ABC, abstractmethod


class SessionsPort(ABC):

    @abstractmethod
    def sessions_in_range(self, start: int, end: int, class_id: int = None,
                          not_booked_by: int = None) -> list:
        """
        Sessions starting in [start, end), each with its class and instructor
        named and its booked/attended counts.

        Those two counts appeared as the same pair of correlated subqueries
        in five separate places — the timetable, the class page twice, the
        dashboard and the kiosk's swap picker. One method now.

        `not_booked_by` drops sessions the client already holds a slot in,
        which is what the "somewhere to go" half of a swap needs.
        """

    @abstractmethod
    def slot_conflict(self, starts_at: int, ends_at: int, exclude_id: int = None):
        """
        The session already occupying this slot, or None.

        Half-open: back-to-back is how a timetable is built, so only a real
        overlap counts. A cancelled session occupies nothing.
        """

    @abstractmethod
    def complete_finished_sessions(self, now: int) -> int:
        """Mark every scheduled session whose end has passed completed."""

    @abstractmethod
    def session_detail(self, session_id: int):
        """One session with its class and instructor named, or None."""


class ClassesPort(ABC):

    @abstractmethod
    def classes_with_counts(self, active: int) -> list:
        """
        The class list: each class with its default instructor named, how
        many sessions are still ahead of it, and how many distinct clients
        have ever had a slot in it.

        Membership is derived from bookings — there is no enrolment list,
        which is why this counts "students with a booking" rather than a
        roster.
        """

    @abstractmethod
    def class_sessions(self, class_id: int, limit: int) -> list:
        """A class's sessions, newest first, with instructor and counts."""

    @abstractmethod
    def class_students(self, class_id: int) -> list:
        """
        Everyone with a booking in this class, with how many slots they have
        had and how many they attended.
        """


class BookingsPort(ABC):

    @abstractmethod
    def plan_counts(self, sub_id: int) -> dict:
        """
        `{"assigned", "present", "absent"}` for one plan.

        Used slots are counted from the bookings rather than tracked in a
        column, so the two can never drift apart. Always integers, never
        None: SQL's SUM over no rows is NULL and Mongo's is no group at all,
        and normalising here is what stops that difference reaching callers.
        """

    @abstractmethod
    def plan_counts_bulk(self, sub_ids: list) -> dict:
        """The same, for many plans, in one round trip. Keyed by plan id."""

    @abstractmethod
    def attendance_counts(self, session_ids: list) -> dict:
        """`{session_id: {"booked", "attended"}}`, in one round trip."""

    @abstractmethod
    def check_in_booking(self, booking_id: int, at: int) -> bool:
        """
        Mark a booking present only if it is not already.

        The double-spend guard, and a compare-and-swap rather than a read
        followed by a write: False means somebody got there first and nothing
        was deducted. A single-document conditional update on either backend.
        """

    @abstractmethod
    def settle_absences(self, now: int, frozen_sub_ids: list) -> int:
        """
        Sweep still-booked slots of finished sessions to absent, skipping
        plans that are frozen — a paused client must never lose a session.
        Returns how many were settled.
        """


class PlansPort(ABC):

    @abstractmethod
    def active_plans_with_clients(self) -> list:
        """
        Every live plan of an active client, with the client named. What the
        dashboard's attention list is built from.
        """

    @abstractmethod
    def last_session_ts(self, sub_id: int):
        """The latest `starts_at` among a plan's bookings, or None."""

    @abstractmethod
    def max_starts_at(self, session_ids: list):
        """The latest `starts_at` among the given sessions, or None."""


class EventsPort(ABC):

    @abstractmethod
    def recent_events(self, since: int, limit: int) -> list:
        """
        The kiosk feed: scans since a moment, newest first, with the client
        and the session's class named where they are known.
        """
