"""
MongoDB answers to the named questions in repo/ports.py.

**These look nothing like their SQLite counterparts, and that is the design.**
The contract is the dict that comes back, which tests/test_parity.py checks
directly by running both backends side by side.

Where SQLite joins, this either fetches by `$in` and joins in Python or, on
the reads a screen waits for, says the same thing as a `$lookup` pipeline.
Both shapes are here on purpose, and which one a method uses is a decision
about round trips rather than taste:

- **`$in` fetches, joined in Python** are the default. They are far easier to
  read than a pipeline and cost nothing extra where the joined rows are
  needed anyway or the method is off the hot path.
- **A `$lookup` pipeline** is what a read *a person is waiting for* gets,
  because the `$in` shape costs one round trip per table joined and those are
  ~100ms each against Atlas. `client_bookings()`, `_sessions_decorated()`,
  `plan_rows()`, `_plan_ends()`, `recent_events()` and `client_cards()` are
  the ones that earned it: each was three to five round trips answering one
  question, several of them charged twice to the same page.

A pipeline here keeps to joins by primary key and projects every field
through `$ifNull`, which is the pipeline's stand-in for `schema.normalise()`:
`$project` drops a field whose expression resolves to missing, and a caller
reading a key that is sometimes absent is exactly the null-vs-missing trap
`schema.py` exists to close. `$group` is used where it is genuinely an
aggregate over many documents and the pipeline stays short.

Aggregates always return numbers, never None. SQL's `SUM` over no rows is
NULL and Mongo's is no group at all; normalising in both places is what stops
that difference reaching a caller.
"""

from datetime import date as _date

import identity

from ..ports import (AccessPort, BookingsPort, ClassesPort, ClientsPort,
                     EventsPort, InstructorsPort, PlansPort, SessionsPort)
from . import schema
from .filters import compile_filter, rename_id


class _Helpers:

    def _by_id(self, coll, ids):
        """`{id: document}` for a set of ids, in one round trip."""
        ids = [i for i in set(ids) if i is not None]
        if not ids:
            return {}
        return {d["_id"]: schema.normalise(coll, d)
                for d in self.db[coll].find({"_id": {"$in": ids}},
                                            session=self.session)}

    def _rows(self, coll, flt=None, sort=None, limit=None):
        cur = self.db[coll].find(rename_id(compile_filter(coll, flt)),
                                 session=self.session)
        if sort:
            cur = cur.sort(sort)
        if limit is not None:
            cur = cur.limit(limit)
        return [schema.normalise(coll, d) for d in cur]

    def _agg(self, coll, pipeline):
        return list(self.db[coll].aggregate(pipeline, session=self.session))

    def _projected(self, coll, fields, flt=None):
        """
        Plain dicts carrying `id` and nothing but `fields`.

        **Deliberately not schema.normalise().** That fills in every declared
        field with its default, so a projected document would hand a caller a
        plausible-looking zero or None for a field that was never read. Here
        a field nobody asked for is simply absent and reading it raises,
        which is the failure mode you want.

        It exists for the whole-collection reads. classes_with_counts()
        carries every session and every booking in the academy to work out
        who still studies what, and at ~220ms a collection against Atlas most
        of that was columns -- notes, prices, timestamps -- that no loop here
        looks at. The round trip is the same; the wire is a fraction of it.
        """
        want = [f for f in fields if f != "id"]
        return [{**{f: d.get(f) for f in want}, "id": d["_id"]}
                for d in self.db[coll].find(
                    rename_id(compile_filter(coll, flt)),
                    {f: 1 for f in want}, session=self.session)]

    def _sessions_decorated(self, match, sort, limit=None, not_booked_by=None):
        """
        Sessions with their class, their instructor and their
        booked/attended counts, in **one** round trip.

        This was `_decorate_sessions()`, which took sessions already fetched
        and then paid an `$in` for the classes, an `$in` for the instructors
        and an aggregate for the counts -- three more round trips on top of
        the fetch, charged to the timetable, the class page and the calendar
        alike. Written as a pipeline the way plan_rows() and
        client_bookings() are: every join is by primary key, and the counts
        fall out of the bookings already joined.

        `not_booked_by` reuses that same bookings join rather than reading
        the client's slots separately -- it is the "somewhere to go" half of
        a kiosk swap, and the rows it filters on are already here.
        """
        # `$sort` takes a document, where cursor.sort() takes a list of pairs.
        # A dict preserves insertion order, so the tiebreak stays a tiebreak.
        pipeline = [{"$match": match}, {"$sort": dict(sort)}]
        # Before the joins: a limit is much cheaper applied to bare sessions,
        # and $sort/$limit ordering is preserved through the stages below.
        if limit is not None:
            pipeline.append({"$limit": limit})
        pipeline += [
            {"$lookup": {"from": "classes", "localField": "class_id",
                         "foreignField": "_id", "as": "_c"}},
            {"$lookup": {"from": "instructors", "localField": "instructor_id",
                         "foreignField": "_id", "as": "_i"}},
            {"$lookup": {"from": "bookings", "localField": "_id",
                         "foreignField": "session_id", "as": "_b"}},
        ]
        if not_booked_by:
            pipeline.append({"$match": {"_b.client_id": {"$ne": not_booked_by}}})
        pipeline += [
            {"$set": {
                # $first over an empty array is null, which is what a session
                # with no instructor should read as.
                "class_name": {"$ifNull": [{"$first": "$_c.name"}, None]},
                "colour": {"$ifNull": [{"$first": "$_c.colour"}, None]},
                "instructor_name": {"$ifNull": [{"$first": "$_i.name"}, None]},
                "booked": {"$size": "$_b"},
                "attended": {"$size": {"$filter": {
                    "input": "$_b", "cond": {"$eq": ["$$this.status", "present"]}}}}}},
            {"$unset": ["_c", "_i", "_b"]},
        ]
        return [schema.normalise("sessions", d)
                for d in self._agg("sessions", pipeline)]


class MongoSessions(SessionsPort, _Helpers):

    def sessions_in_range(self, start=None, end=None, class_id=None,
                          not_booked_by=None):
        # A bound left out is left off the query — see the note on the port.
        # starts_at is declared and always written, so an absent filter here
        # cannot be the null-matching trap compile_filter() guards against.
        window = {}
        if start is not None:
            window["$gte"] = start
        if end is not None:
            window["$lt"] = end
        flt = {"starts_at": window} if window else {}
        if class_id:
            flt["class_id"] = class_id
        return self._sessions_decorated(
            flt, [("starts_at", 1), ("_id", 1)], not_booked_by=not_booked_by)

    def slot_conflict(self, starts_at, ends_at, exclude_id=None):
        # Half-open overlap, the same two comparisons SQLite makes. Both
        # columns are stored, so this is an index scan rather than $expr --
        # which is why ends_at is a column at all (see db.py).
        flt = {"status": {"$ne": "cancelled"},
               "starts_at": {"$lt": ends_at},
               "ends_at": {"$gt": starts_at}}
        if exclude_id is not None:
            flt["_id"] = {"$ne": exclude_id}
        doc = self.db["sessions"].find_one(
            flt, sort=[("starts_at", 1), ("_id", 1)], session=self.session)
        if doc is None:
            return None
        s = schema.normalise("sessions", doc)
        k = self.get("classes", s["class_id"])
        return {"id": s["id"], "starts_at": s["starts_at"],
                "duration_hours": s["duration_hours"],
                "class_name": k["name"] if k else None}

    def complete_finished_sessions(self, now):
        return self.db["sessions"].update_many(
            {"status": "scheduled", "ends_at": {"$lt": now, "$ne": None}},
            {"$set": {"status": "completed"}},
            session=self.session).modified_count

    def next_sweep_deadline(self, now):
        doc = self.db["sessions"].find_one(
            {"status": {"$ne": "cancelled"}, "ends_at": {"$gte": now, "$ne": None}},
            {"ends_at": 1}, sort=[("ends_at", 1)], session=self.session)
        return doc["ends_at"] if doc else None

    def session_detail(self, session_id):
        s = self.get("sessions", session_id)
        if s is None:
            return None
        k = self.get("classes", s["class_id"])
        i = self.get("instructors", s["instructor_id"]) if s["instructor_id"] else None
        return {**s,
                "class_name": k["name"] if k else None,
                "colour": k["colour"] if k else None,
                "instructor_name": i["name"] if i else None}


class MongoClasses(ClassesPort, _Helpers):

    @staticmethod
    def _plan_end(expires_on, last_ts):
        """
        One plan's end: the later of its own `expires_on` and the day of the
        last session it pays for. The rule lives here once, whichever way
        the two callers below arrived at `last_ts`.

        The epoch becomes a local day here and never in the pipeline. `$max`
        picks the same session either way (a day is monotonic in its
        timestamp), and this app has no timezone concept -- converting in
        BSON would anchor it to UTC and move a late-evening session onto the
        next day. See CLAUDE.md, "Dates stay exactly as they are".
        """
        last = _date.fromtimestamp(last_ts).isoformat() if last_ts else None
        return max(x for x in (expires_on, last, "") if x is not None)

    def _plan_ends(self, sessions=None, bookings=None, sub_ids=None):
        """
        {subscription_id: the last day it covers}. The Python counterpart of
        the SQLite _PLAN_END CTE; access.plan_end() is the same rule for a
        single plan.

        **Two ways in, because the two callers are in genuinely different
        positions, and both are one round trip.**

        `sessions` and `bookings` are for a caller that has already read them
        -- classes_with_counts() holds every session and every booking by the
        time it gets here, so the last session per plan is arithmetic it can
        do for free, and all that is left to fetch is the plans themselves.

        `sub_ids` is for a caller that has not: class_students() knows only
        one class's bookings, so the nested `$lookup` walks
        subscription -> its bookings -> their sessions for it. **Bounded by
        those ids and not left open**: unbounded it walks every plan in the
        academy, which measured 295ms against Atlas -- most of three round
        trips, to answer a question about one class.
        """
        if sessions is not None and bookings is not None:
            starts = {s["id"]: s["starts_at"] for s in sessions}
            last = {}
            for b in bookings:
                sub, ts = b.get("subscription_id"), starts.get(b["session_id"])
                if sub is None or ts is None:
                    continue
                if ts > last.get(sub, 0):
                    last[sub] = ts
            return {s["id"]: self._plan_end(s.get("expires_on"), last.get(s["id"]))
                    for s in self._projected("subscriptions", ["expires_on"])}

        ids = sorted({s for s in (sub_ids or ()) if s is not None})
        if not ids:
            return {}
        return {d["_id"]: self._plan_end(d.get("expires_on"), d.get("last_ts"))
                for d in self._agg("subscriptions", [
                    {"$match": {"_id": {"$in": ids}}},
                    {"$lookup": {
                        "from": "bookings", "localField": "_id",
                        "foreignField": "subscription_id", "as": "_b",
                        "pipeline": [
                            {"$lookup": {"from": "sessions",
                                         "localField": "session_id",
                                         "foreignField": "_id", "as": "_s"}},
                            {"$project": {
                                "starts_at": {"$first": "$_s.starts_at"}}}]}},
                    {"$project": {"expires_on": 1,
                                  "last_ts": {"$max": "$_b.starts_at"}}},
                ])}

    def _booking_end(self, booking, plan_ends, starts):
        """
        One booking's end: its plan's, or -- with no plan behind it (older
        rows, subscription_id NULL) -- its own session's day, the same
        fallback _decide() makes.
        """
        end = plan_ends.get(booking.get("subscription_id")) or ""
        if end:
            return end
        ts = starts.get(booking["session_id"])
        return _date.fromtimestamp(ts).isoformat() if ts is not None else ""

    def classes_with_counts(self, active, lapsed_before):
        import db as _db
        classes = self._rows("classes", {"active": active},
                             sort=[("name", 1), ("_id", 1)])
        ids = [c["id"] for c in classes]
        upcoming = {r["_id"]: r["n"] for r in self._agg("sessions", [
            {"$match": {"class_id": {"$in": ids}, "status": "scheduled",
                        "starts_at": {"$gt": _db.now()}}},
            {"$group": {"_id": "$class_id", "n": {"$sum": 1}}}])}
        # Distinct clients still studying the class. Membership is derived
        # from bookings -- there is no enrolment list -- and a client whose
        # every plan here ended before `lapsed_before` has stopped being a
        # student. Same cutoff as class_students(), which it has to be: a
        # list saying 12 beside a page showing 8 is worse than either.
        # Only the columns the loops below read. These are the two
        # whole-collection reads in the app -- see _projected().
        sessions = self._projected("sessions", ["class_id", "starts_at"])
        sess_class = {s["id"]: s["class_id"] for s in sessions}
        starts = {s["id"]: s["starts_at"] for s in sessions}
        bookings = self._projected(
            "bookings", ["client_id", "session_id", "subscription_id"])
        # Both collections are already in hand, so the last session per plan
        # is arithmetic rather than a query -- see _plan_ends().
        plan_ends = self._plan_ends(sessions, bookings)
        latest = {}
        for b in bookings:
            cid = sess_class.get(b["session_id"])
            if cid is None:
                continue
            k = (cid, b["client_id"])
            end = self._booking_end(b, plan_ends, starts)
            if end > latest.get(k, ""):
                latest[k] = end
        students = {}
        for (cid, client), end in latest.items():
            if end >= lapsed_before:
                students.setdefault(cid, set()).add(client)
        staff = self._by_id("instructors",
                            [c["instructor_id"] for c in classes])
        return [{**c, "upcoming": upcoming.get(c["id"], 0),
                 "students": len(students.get(c["id"], ())),
                 "instructor_name": (staff.get(c["instructor_id"]) or {}).get("name")}
                for c in classes]

    def class_sessions(self, class_id, limit):
        decorated = self._sessions_decorated(
            {"class_id": class_id}, [("starts_at", -1), ("_id", -1)], limit=limit)
        # class_sessions is always one class, so the class name is redundant
        # and SQLite does not select it either.
        for s in decorated:
            s.pop("class_name", None)
            s.pop("colour", None)
        return decorated

    def class_students(self, class_id, lapsed_before):
        mine = {s["id"]: s["starts_at"] for s in
                self._rows("sessions", {"class_id": class_id})}
        # The bookings first, so the plan ends can be asked for only the
        # plans that actually fund this class -- see _plan_ends().
        booked = self._rows("bookings", {"session_id": {"in": sorted(mine)}})
        plan_ends = self._plan_ends(
            sub_ids=[b.get("subscription_id") for b in booked])
        tally, latest = {}, {}
        for b in booked:
            t = tally.setdefault(b["client_id"], {"slots": 0, "attended": 0})
            t["slots"] += 1
            if b["status"] == "present":
                t["attended"] += 1
            end = self._booking_end(b, plan_ends, mine)
            if end > latest.get(b["client_id"], ""):
                latest[b["client_id"]] = end
        people = self._by_id("clients", tally)
        # The lapsed cutoff, exactly as the SQLite HAVING applies it: a
        # client stays while ANY plan of theirs in this class ended on or
        # after the cutoff, so one live plan keeps them whatever else of
        # theirs has run out. Nothing is deleted -- see ports.py.
        rows = [{"id": cid, "name_en": people[cid]["name_en"],
                 "phone": people[cid]["phone"],
                 "photo_path": people[cid]["photo_path"], **t}
                for cid, t in tally.items()
                if cid in people and people[cid]["active"]
                and latest.get(cid, "") >= lapsed_before]
        rows.sort(key=lambda r: (r["name_en"] or "", r["id"]))
        return rows


class MongoBookings(BookingsPort, _Helpers):

    def plan_counts(self, sub_id):
        return self.plan_counts_bulk([sub_id])[sub_id]

    def plan_counts_bulk(self, sub_ids):
        out = {s: {"assigned": 0, "present": 0, "absent": 0} for s in sub_ids}
        ids = [s for s in sub_ids if s is not None]
        if not ids:
            return out
        for r in self._agg("bookings", [
                {"$match": {"subscription_id": {"$in": ids}}},
                {"$group": {"_id": "$subscription_id",
                            "assigned": {"$sum": 1},
                            "present": {"$sum": {"$cond": [
                                {"$eq": ["$status", "present"]}, 1, 0]}},
                            "absent": {"$sum": {"$cond": [
                                {"$eq": ["$status", "absent"]}, 1, 0]}}}}]):
            out[r["_id"]] = {"assigned": r["assigned"], "present": r["present"],
                             "absent": r["absent"]}
        return out

    def attendance_counts(self, session_ids):
        out = {s: {"booked": 0, "attended": 0} for s in session_ids}
        ids = [s for s in session_ids if s is not None]
        if not ids:
            return out
        for r in self._agg("bookings", [
                {"$match": {"session_id": {"$in": ids}}},
                {"$group": {"_id": "$session_id",
                            "booked": {"$sum": 1},
                            "attended": {"$sum": {"$cond": [
                                {"$eq": ["$status", "present"]}, 1, 0]}}}}]):
            out[r["_id"]] = {"booked": r["booked"], "attended": r["attended"]}
        return out

    def check_in_booking(self, booking_id, at):
        # A conditional single-document update: atomic by definition, and the
        # filter is evaluated as part of it. matched_count == 0 means someone
        # got there first, so nothing is deducted.
        res = self.db["bookings"].update_one(
            {"_id": booking_id, "status": {"$ne": "present"}},
            {"$set": {"status": "present", "checked_in_at": at}},
            session=self.session)
        return res.matched_count > 0

    def settle_absences(self, now, frozen_sub_ids):
        # Driven from the bookings, not from the sessions. Reading the id of
        # every finished session and sending the lot back as an `$in` was two
        # round trips whose payload grew with the academy's whole history --
        # on the path a client waits through at the desk. Still-`booked`
        # bookings are the bounded set: what is left to settle plus what is
        # yet to happen, never what has already been settled once.
        match = {"status": "booked"}
        if frozen_sub_ids:
            # `$nin` matches a null subscription_id correctly here *because*
            # every document carries the field explicitly (see schema.py) and
            # None is not in the list.
            match["subscription_id"] = {"$nin": list(frozen_sub_ids)}
        due = [d["_id"] for d in self.db["bookings"].aggregate([
            {"$match": match},
            {"$lookup": {
                "from": "sessions", "localField": "session_id",
                "foreignField": "_id", "as": "_s",
                "pipeline": [
                    {"$match": {"status": {"$ne": "cancelled"},
                                "ends_at": {"$lt": now, "$ne": None}}},
                    {"$project": {"_id": 1}}]}},
            {"$match": {"_s": {"$ne": []}}},
            {"$project": {"_id": 1}},
        ], session=self.session)]
        if not due:
            return 0
        return self.db["bookings"].update_many(
            {"_id": {"$in": due}}, {"$set": {"status": "absent"}},
            session=self.session).modified_count


class MongoAccess(AccessPort, _Helpers):

    def credential_by_token(self, token):
        cred = self.find_one("credentials", {"token": token})
        if cred is None:
            return None
        k = self.get("classes", cred["class_id"]) if cred["class_id"] else None
        return {**cred, "class_name": k["name"] if k else None,
                "colour": k["colour"] if k else None}

    def client_bookings(self, client_id):
        # One round trip, not five. This is the hottest read in the app -- a
        # client is standing at the desk while it runs -- and the
        # fetch-then-`$in`-per-table shape the rest of this module uses was
        # costing five, one for the bookings and one for each table they join
        # to. Written as a pipeline the way plan_rows() is, for the same
        # reason: the joins are all by primary key and the input is one
        # client's bookings, so what makes a `$lookup` pipeline unreadable
        # elsewhere -- a conditional join, an unbounded input -- is absent.
        #
        # Every field is projected through `$ifNull`, which is this method's
        # stand-in for schema.normalise(): `$project` drops a field whose
        # expression resolves to missing, and a caller reading a key that is
        # sometimes absent is exactly the null-vs-missing trap the schema
        # module exists to close.
        def first(path):
            return {"$ifNull": [{"$first": path}, None]}

        rows = self._agg("bookings", [
            {"$match": {"client_id": client_id}},
            {"$lookup": {"from": "sessions", "localField": "session_id",
                         "foreignField": "_id", "as": "_s"}},
            {"$set": {"_s": {"$first": "$_s"}}},
            # A booking whose session is gone is dropped, which is what the
            # SQLite side's inner JOIN does.
            {"$match": {"_s": {"$type": "object"}}},
            {"$lookup": {"from": "classes", "localField": "_s.class_id",
                         "foreignField": "_id", "as": "_c"}},
            {"$lookup": {"from": "instructors", "localField": "_s.instructor_id",
                         "foreignField": "_id", "as": "_i"}},
            {"$lookup": {"from": "subscriptions", "localField": "subscription_id",
                         "foreignField": "_id", "as": "_p"}},
            {"$project": {
                "_id": 0,
                "booking_id": "$_id",
                "status": {"$ifNull": ["$status", None]},
                "checked_in_at": {"$ifNull": ["$checked_in_at", None]},
                "subscription_id": {"$ifNull": ["$subscription_id", None]},
                "session_id": "$_s._id",
                "starts_at": {"$ifNull": ["$_s.starts_at", None]},
                "duration_hours": {"$ifNull": ["$_s.duration_hours", None]},
                "session_status": {"$ifNull": ["$_s.status", None]},
                "session_class_id": {"$ifNull": ["$_s.class_id", None]},
                "class_name": first("$_c.name"),
                "colour": first("$_c.colour"),
                "instructor_name": first("$_i.name"),
                "plan_class_id": first("$_p.class_id"),
            }},
        ])
        rows.sort(key=lambda r: (r["starts_at"], r["session_id"]))
        return rows

    def giveable_slots(self, client_id, now):
        mine = self._rows("bookings", {"client_id": client_id})
        sessions = self._by_id("sessions", [b["session_id"] for b in mine])
        plans = self._by_id("subscriptions", [b["subscription_id"] for b in mine])
        classes = self._by_id(
            "classes",
            [s["class_id"] for s in sessions.values()]
            + [p["class_id"] for p in plans.values()])
        rows = []
        for b in mine:
            s = sessions.get(b["session_id"])
            if s is None or s["status"] == "cancelled":
                continue
            if not ((b["status"] == "booked" and s["starts_at"] > now)
                    or b["status"] == "absent"):
                continue
            k = classes.get(s["class_id"])
            plan = plans.get(b["subscription_id"])
            pc = classes.get(plan["class_id"]) if plan else None
            rows.append({"session_id": s["id"], "status": b["status"],
                         "starts_at": s["starts_at"],
                         "class_name": k["name"] if k else None,
                         "colour": k["colour"] if k else None,
                         "plan_name": plan["plan"] if plan else None,
                         "plan_class": pc["name"] if pc else None})
        rows.sort(key=lambda r: (r["starts_at"], r["session_id"]))
        return rows

    def session_roster(self, session_id):
        mine = self._rows("bookings", {"session_id": session_id})
        people = self._by_id("clients", [b["client_id"] for b in mine])
        rows = [{"booking_id": b["id"], "status": b["status"],
                 "checked_in_at": b["checked_in_at"],
                 "id": b["client_id"],
                 "name_en": people[b["client_id"]]["name_en"],
                 "phone": people[b["client_id"]]["phone"],
                 "photo_path": people[b["client_id"]]["photo_path"]}
                for b in mine if b["client_id"] in people]
        rows.sort(key=lambda r: (r["name_en"] or "", r["id"]))
        return rows

    def day_attendance_totals(self, start, end):
        # One round trip: today's live sessions and their bookings counted
        # together, rather than reading the session ids and sending them back
        # as an `$in`. Aggregates are always numbers here, never None --
        # `$group` over no rows is no group at all.
        rows = self._agg("sessions", [
            {"$match": {"starts_at": {"$gte": start, "$lt": end},
                        "status": {"$ne": "cancelled"}}},
            {"$lookup": {"from": "bookings", "localField": "_id",
                         "foreignField": "session_id", "as": "_b"}},
            {"$unwind": "$_b"},
            {"$group": {
                "_id": None, "expected": {"$sum": 1},
                "arrived": {"$sum": {"$cond": [
                    {"$eq": ["$_b.status", "present"]}, 1, 0]}},
                "absent": {"$sum": {"$cond": [
                    {"$eq": ["$_b.status", "absent"]}, 1, 0]}}}},
        ])
        if not rows:
            return {"expected": 0, "arrived": 0, "absent": 0}
        return {k: rows[0][k] for k in ("expected", "arrived", "absent")}


class MongoClients(ClientsPort, _Helpers):

    def search_clients(self, active, q):
        flt = {"active": active}
        if q:
            flt = {"$or": [{"name_en": {"like": f"%{q}%"}},
                           {"phone": {"like": f"%{q}%"}},
                           {"school": {"like": f"%{q}%"}}],
                   "active": active}
        # Sorted by the server, not in Python: this returns the whole client
        # table and the new name_en index carries the order. name_en is NOT
        # NULL on both backends, so there is no null-ordering difference to
        # reconcile -- and the id tiebreak is what keeps the two in step.
        return self._rows("clients", flt, sort=[("name_en", 1), ("_id", 1)])

    def clients_by_phone_key(self, key):
        # Same comparison as the SQLite side, for the same reason: the last
        # ten digits are not something either backend can filter on. See
        # repo/sqlite/ports.py.
        rows = [r for r in self._rows("clients", {})
                if r.get("phone") and identity.phone_key(r["phone"]) == key]
        rows.sort(key=lambda r: r["id"])
        return [{"id": r["id"], "name_en": r["name_en"], "phone": r["phone"],
                 "active": r["active"]} for r in rows]

    def card_counts_bulk(self, client_ids):
        out = {c: 0 for c in client_ids}
        ids = [c for c in client_ids if c is not None]
        if not ids:
            return out
        for r in self._agg("credentials", [
                {"$match": {"client_id": {"$in": ids}, "revoked_at": None}},
                {"$group": {"_id": "$client_id", "n": {"$sum": 1}}}]):
            out[r["_id"]] = r["n"]
        return out

    def client_cards(self, client_id):
        # One round trip. The client profile is the heaviest read in the
        # admin, so the cards and their classes come back together.
        rows = [{"id": d["_id"], "token": d["token"], "class_id": d["class_id"],
                 "issued_at": d["issued_at"], "class_name": d["class_name"],
                 "colour": d["colour"]}
                for d in self._agg("credentials", [
                    {"$match": {"client_id": client_id, "revoked_at": None}},
                    {"$lookup": {"from": "classes", "localField": "class_id",
                                 "foreignField": "_id", "as": "_c"}},
                    {"$project": {
                        "token": 1,
                        "class_id": {"$ifNull": ["$class_id", None]},
                        "issued_at": {"$ifNull": ["$issued_at", None]},
                        "class_name": {"$ifNull": [{"$first": "$_c.name"}, None]},
                        "colour": {"$ifNull": [{"$first": "$_c.colour"}, None]}}},
                ])]
        rows.sort(key=lambda r: (r["class_name"] or "", r["id"]))
        return rows

    def _booking_rows(self, client_id, keep, columns, sub_id=None):
        """
        The shared body of client_upcoming() and plan_sessions(): filter,
        order and project one client's bookings.

        It reads through client_bookings(), which is the same question in one
        `$lookup` pipeline. It used to fetch the bookings and then one `$in`
        per table it joins to -- four round trips each, and the profile paid
        them twice because `upcoming` and `history` were two calls about the
        same rows. (`history` is gone: api/clients.py derives both lists from
        one client_bookings() itself.) `keep` is given the whole row rather
        than a separate booking and session, since the pipeline has already
        joined them.
        """
        rows = self.client_bookings(client_id)
        if sub_id is not None:
            rows = [r for r in rows if r["subscription_id"] == sub_id]
        # `class_id` here is the *session's* class, which is what both
        # callers mean and what the SQLite side selects.
        out = [{c: (r["session_class_id"] if c == "class_id" else r[c])
                for c in columns}
               for r in rows if keep(r)]
        out.sort(key=lambda r: (r["starts_at"], r["session_id"]))
        return out

    def client_upcoming(self, client_id, now):
        return self._booking_rows(
            client_id,
            lambda r: r["starts_at"] >= now and r["session_status"] != "cancelled",
            ("booking_id", "status", "session_id", "starts_at", "duration_hours",
             "class_id", "class_name", "colour", "instructor_name"))

    def plan_sessions(self, client_id, sub_id):
        return self._booking_rows(
            client_id, lambda r: True,
            ("status", "checked_in_at", "session_id", "starts_at",
             "duration_hours", "class_name", "colour", "instructor_name"),
            sub_id=sub_id)

    def merge_client_facts(self, client_id, **facts):
        joined = facts.pop("joined_on", None)
        # An aggregation-pipeline update, so this stays one statement.
        # $ifNull treats missing and null identically, which is exactly
        # COALESCE's rule; $min takes the earlier date.
        sets = {k: {"$ifNull": [f"${k}", v]} for k, v in facts.items()}
        if joined is not None:
            sets["joined_on"] = {"$min": ["$joined_on", joined]}
        if not sets:
            return
        self.db["clients"].update_one({"_id": client_id}, [{"$set": sets}],
                                      session=self.session)

    def takings(self, date_field, month_from, month_to):
        """
        One aggregate. It used to be three unfiltered collection fetches --
        every client twice and every subscription once -- filtered by date in
        Python, and month_intake() calls this twice per dashboard.

        The two date_fields are scoped differently on purpose and that has to
        survive: "starts_on" asks when the money arrived and reads the plan's
        own date, while "joined_on" asks who the client is and reads theirs,
        whenever the plan was bought. See the dashboard note in CLAUDE.md.
        """
        # Not an assert: this is interpolated into the pipeline below, and
        # asserts are stripped under `python -O`.
        if date_field not in ("joined_on", "starts_on"):
            raise ValueError(f"unknown date_field {date_field!r}")
        when = "$starts_on" if date_field == "starts_on" else "$cl.joined_on"
        rows = self._agg("subscriptions", [
            {"$lookup": {"from": "clients", "localField": "client_id",
                         "foreignField": "_id", "as": "cl"}},
            {"$unwind": "$cl"},
            {"$match": {"cl.active": 1}},
            {"$set": {"_when": when}},
            # $ne guards the null: a bare {"$lt": x} matches null on MongoDB
            # and never on SQLite. repo/mongo/filters.py adds this
            # automatically on the tier-one path; a raw pipeline must say it.
            {"$match": {"_when": {"$gte": month_from, "$lt": month_to,
                                  "$ne": None}}},
            {"$group": {"_id": None, "plans": {"$sum": 1},
                        "paid": {"$sum": {"$ifNull": ["$price", 0]}},
                        "unpriced": {"$sum": {"$cond": [
                            {"$eq": ["$price", None]}, 1, 0]}}}},
        ])
        # No matching plans is no group at all, and the contract is zero.
        if not rows:
            return {"paid": 0.0, "unpriced": 0, "plans": 0}
        r = rows[0]
        return {"paid": float(r["paid"]), "unpriced": r["unpriced"],
                "plans": r["plans"]}

    def joined_counts(self, windows):
        if not windows:
            return []
        rows = self._agg("clients", [
            {"$match": {"active": 1}},
            {"$group": {"_id": None, **{
                f"w{n}": {"$sum": {"$cond": [
                    {"$and": [{"$gte": ["$joined_on", a]},
                              {"$lt": ["$joined_on", b]}]}, 1, 0]}}
                for n, (a, b) in enumerate(windows)}}},
        ])
        # `$group` over no rows is no group at all, where SQL's SUM is NULL.
        return [rows[0][f"w{n}"] if rows else 0 for n in range(len(windows))]


class MongoInstructors(InstructorsPort, _Helpers):


    def salary_hours(self, instructor_id, period_from, period_to):
        rows = self._rows("instructor_hours", {
            "instructor_id": instructor_id,
            "work_date": {"gte": period_from, "lte": period_to}})
        if not rows:
            return {"hours": 0.0, "days": 0, "from": None, "to": None}
        days = [r["work_date"] for r in rows]
        return {"hours": round(sum(r["hours"] or 0 for r in rows), 2),
                "days": len(rows), "from": min(days), "to": max(days)}

    def adjustments_sum(self, instructor_id, period_from, period_to):
        rows = self._rows("instructor_hour_adjustments", {
            "instructor_id": instructor_id,
            "adjustment_date": {"gte": period_from, "lte": period_to}})
        return round(sum(r["delta_hours"] or 0 for r in rows), 2)

    def taught_totals_bulk(self, instructor_ids, start=None, end=None,
                           status="completed"):
        out = {i: {"sessions": 0, "hours": 0.0} for i in instructor_ids}
        ids = [i for i in instructor_ids if i is not None]
        if not ids:
            return out
        match = {"instructor_id": {"$in": ids}}
        if status:
            match["status"] = status
        if start is not None:
            match.setdefault("starts_at", {})["$gte"] = start
        if end is not None:
            match.setdefault("starts_at", {})["$lt"] = end
        for r in self._agg("sessions", [
                {"$match": match},
                {"$group": {"_id": "$instructor_id", "n": {"$sum": 1},
                            "h": {"$sum": "$duration_hours"}}}]):
            out[r["_id"]] = {"sessions": r["n"], "hours": round(r["h"] or 0, 2)}
        return out

    def instructor_sessions(self, instructor_id, start, end, limit):
        sessions = [schema.normalise("sessions", d) for d in
                    self.db["sessions"].find(
                        {"instructor_id": instructor_id,
                         "starts_at": {"$gte": start, "$lt": end}},
                        session=self.session)
                    .sort([("starts_at", -1), ("_id", -1)]).limit(limit)]
        classes = self._by_id("classes", [s["class_id"] for s in sessions])
        counts = self.attendance_counts([s["id"] for s in sessions])
        return [{"id": s["id"], "starts_at": s["starts_at"],
                 "duration_hours": s["duration_hours"], "status": s["status"],
                 "class_name": (classes.get(s["class_id"]) or {}).get("name"),
                 "colour": (classes.get(s["class_id"]) or {}).get("colour"),
                 "attended": counts[s["id"]]["attended"]}
                for s in sessions]


class MongoPlans(PlansPort, _Helpers):

    def active_plans_with_clients(self):
        # One round trip rather than the plans and then their owners. This
        # runs on the dashboard, where every trip saved is one off the
        # landing page.
        return [{"id": d["client_id"], "name_en": d["name_en"],
                 "phone": d["phone"], "sub_id": d["_id"], "plan": d["plan"]}
                for d in self._agg("subscriptions", [
                    {"$match": {"active": 1}},
                    {"$lookup": {"from": "clients", "localField": "client_id",
                                 "foreignField": "_id", "as": "_p"}},
                    {"$set": {"_p": {"$first": "$_p"}}},
                    # An archived owner drops the plan, as the join did.
                    {"$match": {"_p.active": 1}},
                    {"$project": {
                        "client_id": 1, "plan": {"$ifNull": ["$plan", None]},
                        "name_en": {"$ifNull": ["$_p.name_en", None]},
                        "phone": {"$ifNull": ["$_p.phone", None]}}},
                    {"$sort": {"_id": 1}},
                ])]

    def last_session_ts(self, sub_id):
        mine = self._rows("bookings", {"subscription_id": sub_id})
        return self.max_starts_at([b["session_id"] for b in mine])

    def last_session_ts_bulk(self, sub_ids):
        ids = [s for s in dict.fromkeys(sub_ids) if s is not None]
        if not ids:
            return {}
        return {r["_id"]: r["t"] for r in self._agg("bookings", [
            {"$match": {"subscription_id": {"$in": ids}}},
            {"$lookup": {"from": "sessions", "localField": "session_id",
                         "foreignField": "_id", "as": "s"}},
            {"$unwind": "$s"},
            {"$group": {"_id": "$subscription_id",
                        "t": {"$max": "$s.starts_at"}}},
        ]) if r["t"] is not None}

    def max_starts_at(self, session_ids):
        ids = [s for s in session_ids if s is not None]
        if not ids:
            return None
        doc = self.db["sessions"].find_one(
            {"_id": {"$in": ids}}, {"starts_at": 1},
            sort=[("starts_at", -1)], session=self.session)
        return doc["starts_at"] if doc else None

    def plan_rows(self, sub_ids):
        ids = [s for s in dict.fromkeys(sub_ids) if s is not None]
        if not ids:
            return {}
        docs = self._agg("subscriptions", [
            {"$match": {"_id": {"$in": ids}}},
            {"$lookup": {"from": "classes", "localField": "class_id",
                         "foreignField": "_id", "as": "_c"}},
            {"$lookup": {"from": "bookings", "localField": "_id",
                         "foreignField": "subscription_id", "as": "_b"}},
            {"$set": {
                # $first over an empty array is null, which is what a plan
                # with no class should read as.
                "class_name": {"$first": "$_c.name"},
                "class_colour": {"$first": "$_c.colour"},
                "assigned": {"$size": "$_b"},
                "present": {"$size": {"$filter": {
                    "input": "$_b", "cond": {"$eq": ["$$this.status", "present"]}}}},
                "absent": {"$size": {"$filter": {
                    "input": "$_b", "cond": {"$eq": ["$$this.status", "absent"]}}}}}},
            {"$unset": ["_c", "_b"]},
        ])
        return {d["_id"]: schema.normalise("subscriptions", d) for d in docs}

    def active_plans_for(self, client_ids):
        ids = [c for c in client_ids if c is not None]
        if not ids:
            return {}
        plans = self._rows("subscriptions",
                           {"active": 1, "client_id": {"in": ids}})
        # Soonest to expire wins -- the one needing attention.
        plans.sort(key=lambda p: ((p["expires_on"] or ""), p["id"]))
        out = {}
        for p in plans:
            out.setdefault(p["client_id"], p)
        return out


class MongoEvents(EventsPort, _Helpers):

    def recent_events(self, since, limit):
        # One round trip: the events, who they were, and the class of the
        # session each was for. It was four -- the events, then an `$in` per
        # table behind them -- on the dashboard, which pays for nine other
        # reads besides. The class is two joins out from the event, which is
        # what the nested lookup is doing.
        return [{"id": d["_id"], "scanned_at": d["scanned_at"],
                 "decision": d["decision"], "reason": d["reason"],
                 "confirmed_at": d["confirmed_at"],
                 "name_en": d["name_en"], "class_name": d["class_name"]}
                for d in self._agg("access_events", [
                    {"$match": {"scanned_at": {"$gte": since}}},
                    {"$sort": {"scanned_at": -1, "_id": -1}},
                    {"$limit": limit},
                    {"$lookup": {"from": "clients", "localField": "client_id",
                                 "foreignField": "_id", "as": "_p"}},
                    {"$lookup": {
                        "from": "sessions", "localField": "session_id",
                        "foreignField": "_id", "as": "_s",
                        "pipeline": [
                            {"$lookup": {"from": "classes",
                                         "localField": "class_id",
                                         "foreignField": "_id", "as": "_c"}},
                            {"$project": {
                                "class_name": {"$first": "$_c.name"}}}]}},
                    {"$project": {
                        "scanned_at": 1, "decision": 1,
                        "reason": {"$ifNull": ["$reason", None]},
                        "confirmed_at": {"$ifNull": ["$confirmed_at", None]},
                        "name_en": {"$ifNull": [{"$first": "$_p.name_en"}, None]},
                        "class_name": {
                            "$ifNull": [{"$first": "$_s.class_name"}, None]}}},
                ])]

    def event_totals(self, since):
        rows = self._agg("access_events", [
            {"$match": {"scanned_at": {"$gte": since}}},
            {"$group": {
                "_id": None,
                "scans": {"$sum": {"$cond": [
                    {"$eq": ["$source", "scan"]}, 1, 0]}},
                "denied": {"$sum": {"$cond": [
                    {"$eq": ["$decision", "deny"]}, 1, 0]}}}},
        ])
        # `$group` over no rows is no group at all, where SQL's SUM is NULL.
        # Both have to arrive here as 0 -- see the module docstring.
        return ({"scans": rows[0]["scans"], "denied": rows[0]["denied"]}
                if rows else {"scans": 0, "denied": 0})
