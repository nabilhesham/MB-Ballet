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
    def sessions_in_range(self, start: int = None, end: int = None,
                          class_id: int = None, not_booked_by: int = None) -> list:
        """
        Sessions starting in [start, end), each with its class and instructor
        named and its booked/attended counts.

        **Either bound may be None, meaning no bound on that side.** Both
        None is the whole timetable, which is what the Sessions screen asks
        for: it is the only place a session can be deleted from, so anything
        it cannot show is a session nobody can remove — and one outside its
        range still showed in the calendar and still held its slot against
        slot_conflict(), so scheduling over it was refused by something
        invisible. See CLAUDE.md.

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
    def classes_with_counts(self, active: int, lapsed_before: str) -> list:
        """
        The class list: each class with its default instructor named, how
        many sessions are still ahead of it, and how many distinct clients
        have ever had a slot in it.

        Membership is derived from bookings — there is no enrolment list,
        which is why this counts "students with a booking" rather than a
        roster.

        `lapsed_before` applies the same cutoff class_students() does, and
        has to: a list saying 12 students beside a page showing 8 is worse
        than either number on its own.
        """

    @abstractmethod
    def class_sessions(self, class_id: int, limit: int) -> list:
        """A class's sessions, newest first, with instructor and counts."""

    @abstractmethod
    def class_students(self, class_id: int, lapsed_before: str) -> list:
        """
        Who is currently a student of this class, with how many slots they
        have had and how many they attended.

        `lapsed_before` is an ISO date from access.lapsed_cutoff(): a client
        whose every plan in this class ended before it has stopped being a
        student and is left out. A plan's end is the later of its
        `expires_on` and the last session it pays for (access.plan_end), and
        a booking with no plan behind it falls back to its own session's
        date — the same fallback _decide() makes.

        It filters a read and deletes nothing: the bookings and the
        attendance stay exactly where they are, which is what keeps this
        reversible and keeps the history intact. A client with a live or
        recently-ended plan in the class is unaffected.
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


class ClientsPort(ABC):

    @abstractmethod
    def search_clients(self, active: int, q: str) -> list:
        """
        The clients list. `q` matches name, phone or school; blank matches
        everyone.

        Searching server-side is deliberate — the kiosk's name lookup goes
        through the same endpoint, so what reception finds at the desk is
        what they would find on the Clients page.
        """

    @abstractmethod
    def clients_by_phone_key(self, key: str) -> list:
        """
        Everyone whose mobile number is this one, archived clients included.

        `key` is identity.phone_key() — the last ten digits — because the
        same person is written down three ways and only that part is common
        to all of them. Deliberately phone only: the caller compares the
        name (see access.duplicate_client), because a shared mobile is
        ordinary here and several rows legitimately come back. Neither backend can express "last ten digits of a column" as a
        filter, so both compare in Python; that is the point of it being a
        named question rather than something a caller builds out of the
        filter dialect.

        Archived rows come back too, and on purpose: a number belonging to an
        archived client means restore them, not add them a second time, and a
        lookup that hid them would make the second one the easy path.

        Returns id, name_en, phone and active, ordered by id.
        """

    @abstractmethod
    def card_counts_bulk(self, client_ids: list) -> dict:
        """How many live cards each client holds. One round trip."""

    @abstractmethod
    def client_cards(self, client_id: int) -> list:
        """A client's live cards, each with its class named."""

    @abstractmethod
    def client_upcoming(self, client_id: int, now: int) -> list:
        """Bookings whose session is still ahead and not cancelled."""

    @abstractmethod
    def client_history(self, client_id: int, now: int, limit: int) -> list:
        """Bookings whose session has been, newest first."""

    @abstractmethod
    def plan_sessions(self, client_id: int, sub_id: int) -> list:
        """Every session one plan has paid for, in order."""

    @abstractmethod
    def merge_client_facts(self, client_id: int, **facts) -> None:
        """
        Fill in blanks on a client without overwriting what is already there,
        and take the *earlier* of the two `joined_on` dates.

        The same student appears in several roster blocks and later ones fill
        in what the first left empty. "Fills blanks, never overwrites" is the
        rule; `joined_on` is the exception because the earliest date is the
        one they actually joined on.
        """

    @abstractmethod
    def takings(self, date_field: str, month_from: str, month_to: str) -> dict:
        """
        `{"paid", "unpriced", "plans"}` over active clients' plans, filtered
        by a month range on one of two dates.

        `date_field` is "joined_on" (the client's) or "starts_on" (the
        plan's), and the choice is the whole difference between the
        dashboard's two intake figures: "earned from them" follows the
        people out of the window, while "revenue" stays inside it. Unpriced
        plans are counted separately and never as zero.
        """


class AccessPort(ABC):

    @abstractmethod
    def credential_by_token(self, token: str):
        """The credential with its class named, or None."""

    @abstractmethod
    def client_bookings(self, client_id: int) -> list:
        """
        Every booking this client has ever had, oldest first, with the
        session, its class, its instructor and **the class of the plan that
        paid for it** all resolved.

        Deliberately one flat fetch rather than a query per question. It is
        the whole of what a scan needs to know about a person, and
        `access.py` decides the four separate questions from it in Python:
        which of their sessions is today, which is nearest to now, whether
        one is already present, what their last few settled sessions were,
        when they last visited, and what is next.

        **It was four port methods** -- `client_day_bookings`,
        `recent_attendance`, `next_booked_session` and `client_totals` --
        each re-reading the same client's bookings and the same sessions
        behind them. On SQLite that is four joins over one small set of rows
        and costs nothing; on a document store each one is a fetch of the
        client's bookings followed by an `$in` per table it has to join, so
        the scan path spent thirteen round trips answering four questions
        about rows it had already had in hand. One client's lifetime
        bookings is a few hundred rows at the very most, which is cheaper to
        carry once than to ask for four times.

        Written as a query the day-scoped half is a four-table join with a
        conditional OR and an `ORDER BY ABS(starts_at - ?)`, which has no
        readable equivalent on a document store either -- so deciding in
        Python is what the interface wanted regardless.

        `plan_class_id` is the point of the join to subscriptions: a card
        proves the *plan's* class, not the session's, which is what lets a
        slot moved to another class still be found by the card that paid
        for it.
        """

    @abstractmethod
    def giveable_slots(self, client_id: int, now: int) -> list:
        """
        Slots of the client's own that could be given up for a swap: one
        still ahead of them, or one they were already marked absent for.
        Each carries the plan that paid for it.
        """

    @abstractmethod
    def session_roster(self, session_id: int) -> list:
        """Who is booked into a session, with their attendance status."""

    @abstractmethod
    def day_attendance_totals(self, start: int, end: int) -> dict:
        """`{"expected", "arrived", "absent"}` across a day's sessions."""


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
    def last_session_ts_bulk(self, sub_ids: list) -> dict:
        """
        The same for many plans at once: `{sub_id: latest_starts_at}`, in one
        round trip. A plan with no bookings is absent from the result rather
        than mapped to None -- "no dates yet" is what refresh_expiry() leaves
        the stored date alone for, and a missing key says that without
        needing a sentinel.

        Deleting a term used to call refresh_expiry() once per affected plan,
        which on MongoDB is three round trips each.
        """

    @abstractmethod
    def max_starts_at(self, session_ids: list):
        """The latest `starts_at` among the given sessions, or None."""

    @abstractmethod
    def plan_rows(self, sub_ids: list) -> dict:
        """
        Everything plan_state() needs about a set of plans, keyed by id, in
        **one** round trip: the subscription's own fields, its class's name
        and colour, and its assigned/present/absent booking counts.

        It used to be three separate questions, which is three round trips
        for a single plan -- and a single plan is what the reception scan
        path asks for, with a client standing at the desk.

        The counts are always integers, never None, for the same reason
        plan_counts_bulk's are: SQL's SUM over no rows is NULL and MongoDB's
        is no group at all. A plan id that does not exist is absent from the
        result rather than mapped to an empty dict.
        """

    @abstractmethod
    def active_plans_for(self, client_ids: list) -> dict:
        """
        Each client's live plan, keyed by client id — the soonest to expire
        where they hold more than one, which is the one needing attention.
        One round trip, for the clients list.
        """


class InstructorsPort(ABC):

    @abstractmethod
    def taught_totals_bulk(self, instructor_ids: list, start: int = None,
                           end: int = None, status: str = "completed") -> dict:
        """
        `{instructor_id: {"sessions", "hours"}}` for sessions of that status,
        optionally inside a half-open time range. One round trip.
        """

    @abstractmethod
    def salary_hours(self, instructor_id: int, period_from: str,
                     period_to: str) -> dict:
        """
        What the salary sheet recorded: `{"hours", "days", "from", "to"}`.

        `days` counts real sheet rows only — a correction is not a claim of
        an extra day worked, and corrections belong to the *taught* figure.
        Only one of the two may carry them or an instructor is paid twice for
        the same hour.
        """

    @abstractmethod
    def adjustments_sum(self, instructor_id: int, period_from: str,
                        period_to: str) -> float:
        """The net manual correction to hours taught across a date range."""

    @abstractmethod
    def instructor_sessions(self, instructor_id: int, start: int, end: int,
                            limit: int) -> list:
        """
        The instructor's sessions in a range, newest first, each with its
        class named and how many people attended.
        """


class EventsPort(ABC):

    @abstractmethod
    def recent_events(self, since: int, limit: int) -> list:
        """
        The kiosk feed: scans since a moment, newest first, with the client
        and the session's class named where they are known.
        """
