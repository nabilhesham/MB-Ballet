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
    def next_sweep_deadline(self, now: int):
        """
        The earliest `ends_at` at or after `now` among sessions that are not
        cancelled, or None when nothing is left to end.

        This is the next moment `access.settle_past_sessions()` could
        possibly have work to do, which is what lets it skip itself entirely
        until then -- see the deadline note there. NULL `ends_at` is excluded
        for the same reason the sweep itself excludes it: a row the sweep
        cannot act on cannot be the reason to wake it up.
        """

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
        than either number on its own. The same goes for the plan's-class
        rule that one documents — a cross-class visitor is left out of both
        or neither.
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

        **Membership is the booking's *plan's* class, not the session's.**
        A slot may be pointed at any class's session once it is sold (see
        the cross-class corrections in CLAUDE.md) -- that is how "she missed
        Ballet on Tuesday and came to Flexibility on Wednesday instead" is
        recorded without selling a second plan. The session she attended is
        a Flexibility session, so counting bookings by the session's class
        put her on the Flexibility roster as a student of a class she has
        never bought: a visitor covering one missed hour, listed beside
        twelve people who come every week. Nothing else about that slot
        changes -- the deduction, the attendance mark, the plan it belongs
        to and the session's own roster are all exactly as they were, and
        her profile still shows she attended it. One filter, on one read.

        A booking with **no** plan behind it (older rows, `subscription_id`
        NULL) counts, because the session's class is the only answer there
        is for it -- the same fallback the end date above makes.
        """


class ShopPort(ABC):
    """
    The shop: what the academy sells over the counter, and who bought it.

    Five named questions rather than one method with a filter, for the same
    reason the three booking lists are three (see `_BOOKING_JOIN` in
    repo/sqlite/ports.py): they share a join and not their columns, and a
    caller asking "what did one client buy" wants different fields from one
    asking "what did the till take today".

    **A sale's `unit_price` is what it cost on the day**, copied from the
    product when the sale was written down and never read back through it.
    Re-pricing a leotard must not rewrite what last month's till took --
    the opposite of instructor pay, which is `hours x rate` at read time
    precisely so correcting a rate re-prices the month. Both are
    deliberate, and the difference is whether the old number was a
    *receipt* or an *estimate*.

    **A sale's `size` is a receipt in exactly the same way**, and every one
    of the three sale lists below carries it. It is the shelf the thing came
    off -- NULL when the product has no sizes, which is one real row rather
    than the absence of one (see `product_stock` in db.py) -- so removing a
    sale can put it back where it came from, and changing which sizes a
    product comes in next term cannot rewrite what was handed over last
    month. It is REAL, not an integer: shoe sizes come in halves.
    """

    @abstractmethod
    def shop_takings(self, day_from: str, day_to: str) -> dict:
        """
        `{"paid", "sales", "items", "unpriced"}` over sales dated in
        `[day_from, day_to)`, on `paid_on` — the same column and the same
        half-open window `takings()` uses for plans, so one rule governs
        the whole till. `paid` is the sum of `qty x unit_price`, `items` the
        sum of `qty`, `sales` the number of lines, and `unpriced` the lines
        whose product had no price written down: counted and reported, never
        folded in as zero.

        Aggregates are always numbers, never None.
        """

    @abstractmethod
    def purchases_between(self, day_from: str, day_to: str) -> list:
        """
        Every sale dated in that window, with the buyer and the product
        named — what the till's drill-down lists. Ordered by the client's
        name then the sale's id.
        """

    @abstractmethod
    def plans_paid_between(self, day_from: str, day_to: str) -> list:
        """
        Every plan whose `paid_on` falls in that window, with the client and
        the class named. The other half of the same drill-down: the till is
        plans plus the shop, so the list behind it has to be too.
        """

    @abstractmethod
    def client_purchases(self, client_id: int) -> list:
        """One client's sales, newest first, with the product named."""

    @abstractmethod
    def product_buyers(self, product_id: int) -> list:
        """One product's sales, newest first, with the client named."""


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
        by a half-open date range on one of three dates.

        `date_field` is "joined_on" (the client's), "starts_on" (the plan's)
        or "paid_on" (the day the money actually arrived). The first two are
        the whole difference between the dashboard's two intake figures:
        "earned from them" follows the people out of the window, while
        "revenue" stays inside it. Unpriced plans are counted separately and
        never as zero.

        `paid_on` is the one that answers "what came in today", and it is a
        different question from either: a plan written down on Tuesday for a
        payment due Thursday belongs to Thursday. It is also the one that is
        **nullable** -- an unpaid plan has no date and must fall out of every
        window rather than landing in one, which is the null guard both
        implementations already carry for a range comparison.

        The bounds are ISO dates and the range is half-open, so a single day
        is `(day, the day after)`. A bare "YYYY-MM" works as a month bound
        because an ISO date sorts lexicographically against it.
        """

    @abstractmethod
    def joined_counts(self, windows: list) -> list:
        """
        How many active clients joined in each `(from, to)` half-open range,
        in the order given and in **one** round trip.

        The dashboard asks this twice -- the chosen period and the span
        immediately before it, for the like-for-like comparison -- and two
        counts over one collection is a round trip spent on arithmetic.
        Bounds are ISO dates; `joined_on` is compared as a string, which is
        what lets an index serve it (see access.month_intake).
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
    def plan_slot_times(self, sub_ids: list) -> dict:
        """
        `{sub_id: [starts_at, ...]}` ascending -- when each slot a plan holds
        actually falls, cancelled sessions left out.

        This is what makes "how many sessions did they have left *on the
        12th*" answerable at all. `plan_rows()` counts a plan's bookings as
        they stand now, which answers only "today"; a day filter needs to
        know *when* each slot sits, and a plan that ran out last Tuesday is a
        different row from one that ran out this morning.

        One round trip for every plan asked about, because the dashboard asks
        it of every live plan in the academy. Cancelled sessions are dropped
        here rather than by the caller: a cancelled date is not a session
        anybody attended, so counting it as spent would retire a plan that
        still has the slot.
        """

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

    @abstractmethod
    def classes_for(self, client_ids: list) -> dict:
        """
        `{client_id: [{"id", "name", "colour"}]}` — **every** class each of
        them is in right now, from their live plans, named and sorted by
        name. One round trip.

        Deliberately not `active_plans_for()` with the name attached. That
        one answers with a single plan, the soonest to expire, which is the
        right answer to "what needs attention" and the wrong one to "which
        classes is this person in": a child taking Ballet and Flexibility
        would read as one of the two, and which of them changed as the dates
        moved. Every list that names clients shows this column, and a column
        that quietly drops half a client's classes is worse than no column.

        A client with no live plan is absent from the answer rather than
        present with an empty list, the same shape `card_counts_bulk()` and
        the other bulk reads use.
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

    @abstractmethod
    def event_totals(self, since: int) -> dict:
        """
        `{"scans", "denied"}` since a moment -- how many came from the kiosk
        and how many were refused, whatever their source.

        One question rather than two counts over the same window: they sit
        side by side on the dashboard, and reading the window twice is a
        round trip spent on arithmetic. Always integers, never None.
        """

    @abstractmethod
    def event_totals(self, since: int) -> dict:
        """
        `{"scans", "denied"}` since a moment -- how many came from the kiosk
        and how many were refused, whatever their source.

        One question rather than two counts over the same window: they sit
        side by side on the dashboard and reading the window twice is a round
        trip spent on arithmetic. Always integers, never None.
        """
