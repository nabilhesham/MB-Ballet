"""
MongoDB answers to the named questions in repo/ports.py.

**These look nothing like their SQLite counterparts, and that is the design.**
The contract is the dict that comes back, which tests/test_parity.py checks
directly by running both backends side by side.

Where SQLite joins, this fetches by `$in` and joins in Python. A four-table
`$lookup` pipeline is technically possible and practically unreadable, and
every join here is bounded by something small — one client's day, one plan's
slots, one class's sessions. `$group` is used where it is genuinely an
aggregate over many documents and the pipeline stays short.

Aggregates always return numbers, never None. SQL's `SUM` over no rows is
NULL and Mongo's is no group at all; normalising in both places is what stops
that difference reaching a caller.
"""

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

    def _decorate_sessions(self, sessions):
        """Attach class, instructor and the booked/attended counts."""
        classes = self._by_id("classes", [s["class_id"] for s in sessions])
        staff = self._by_id("instructors", [s["instructor_id"] for s in sessions])
        counts = self.attendance_counts([s["id"] for s in sessions])
        out = []
        for s in sessions:
            k = classes.get(s["class_id"])
            i = staff.get(s["instructor_id"])
            out.append({**s,
                        "class_name": k["name"] if k else None,
                        "colour": k["colour"] if k else None,
                        "instructor_name": i["name"] if i else None,
                        "booked": counts[s["id"]]["booked"],
                        "attended": counts[s["id"]]["attended"]})
        return out


class MongoSessions(SessionsPort, _Helpers):

    def sessions_in_range(self, start, end, class_id=None, not_booked_by=None):
        flt = {"starts_at": {"$gte": start, "$lt": end}}
        if class_id:
            flt["class_id"] = class_id
        sessions = [schema.normalise("sessions", d) for d in
                    self.db["sessions"].find(flt, session=self.session)
                    .sort([("starts_at", 1), ("_id", 1)])]
        if not_booked_by:
            held = {b["session_id"] for b in self._rows(
                "bookings", {"client_id": not_booked_by})}
            sessions = [s for s in sessions if s["id"] not in held]
        return self._decorate_sessions(sessions)

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

    def classes_with_counts(self, active):
        import db as _db
        classes = self._rows("classes", {"active": active},
                             sort=[("name", 1), ("_id", 1)])
        ids = [c["id"] for c in classes]
        upcoming = {r["_id"]: r["n"] for r in self._agg("sessions", [
            {"$match": {"class_id": {"$in": ids}, "status": "scheduled",
                        "starts_at": {"$gt": _db.now()}}},
            {"$group": {"_id": "$class_id", "n": {"$sum": 1}}}])}
        # Distinct clients with a booking in the class. Membership is derived
        # from bookings -- there is no enrolment list.
        sess_class = {s["id"]: s["class_id"] for s in self._rows("sessions")}
        students = {}
        for b in self._rows("bookings"):
            cid = sess_class.get(b["session_id"])
            if cid is not None:
                students.setdefault(cid, set()).add(b["client_id"])
        return [{**c, "upcoming": upcoming.get(c["id"], 0),
                 "students": len(students.get(c["id"], ())),
                 "instructor_name": (self.get("instructors", c["instructor_id"])
                                     or {}).get("name") if c["instructor_id"] else None}
                for c in classes]

    def class_sessions(self, class_id, limit):
        sessions = [schema.normalise("sessions", d) for d in
                    self.db["sessions"].find({"class_id": class_id},
                                             session=self.session)
                    .sort([("starts_at", -1), ("_id", -1)]).limit(limit)]
        decorated = self._decorate_sessions(sessions)
        # class_sessions is always one class, so the class name is redundant
        # and SQLite does not select it either.
        for s in decorated:
            s.pop("class_name", None)
            s.pop("colour", None)
        return decorated

    def class_students(self, class_id):
        mine = {s["id"] for s in self._rows("sessions", {"class_id": class_id})}
        tally = {}
        for b in self._rows("bookings", {"session_id": {"in": sorted(mine)}}):
            t = tally.setdefault(b["client_id"], {"slots": 0, "attended": 0})
            t["slots"] += 1
            if b["status"] == "present":
                t["attended"] += 1
        people = self._by_id("clients", tally)
        rows = [{"id": cid, "name_en": people[cid]["name_en"],
                 "phone": people[cid]["phone"],
                 "photo_path": people[cid]["photo_path"], **t}
                for cid, t in tally.items()
                if cid in people and people[cid]["active"]]
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
        finished = [s["_id"] for s in self.db["sessions"].find(
            {"status": {"$ne": "cancelled"}, "ends_at": {"$lt": now, "$ne": None}},
            {"_id": 1}, session=self.session)]
        if not finished:
            return 0
        flt = {"status": "booked", "session_id": {"$in": finished}}
        if frozen_sub_ids:
            # `$nin` matches a null subscription_id correctly here *because*
            # every document carries the field explicitly (see schema.py) and
            # None is not in the list.
            flt["subscription_id"] = {"$nin": list(frozen_sub_ids)}
        return self.db["bookings"].update_many(
            flt, {"$set": {"status": "absent"}},
            session=self.session).modified_count


class MongoAccess(AccessPort, _Helpers):

    def credential_by_token(self, token):
        cred = self.find_one("credentials", {"token": token})
        if cred is None:
            return None
        k = self.get("classes", cred["class_id"]) if cred["class_id"] else None
        return {**cred, "class_name": k["name"] if k else None,
                "colour": k["colour"] if k else None}

    def client_day_bookings(self, client_id, start, end):
        mine = self._rows("bookings", {"client_id": client_id})
        sessions = self._by_id("sessions", [b["session_id"] for b in mine])
        today = [b for b in mine
                 if b["session_id"] in sessions
                 and start <= sessions[b["session_id"]]["starts_at"] < end]
        classes = self._by_id("classes",
                              [sessions[b["session_id"]]["class_id"] for b in today])
        staff = self._by_id("instructors",
                            [sessions[b["session_id"]]["instructor_id"] for b in today])
        plans = self._by_id("subscriptions", [b["subscription_id"] for b in today])
        rows = []
        for b in today:
            s = sessions[b["session_id"]]
            k = classes.get(s["class_id"])
            i = staff.get(s["instructor_id"])
            plan = plans.get(b["subscription_id"])
            rows.append({
                "booking_id": b["id"], "status": b["status"],
                "checked_in_at": b["checked_in_at"],
                "subscription_id": b["subscription_id"],
                "session_id": s["id"], "starts_at": s["starts_at"],
                "duration_hours": s["duration_hours"],
                "session_status": s["status"], "session_class_id": s["class_id"],
                "class_name": k["name"] if k else None,
                "colour": k["colour"] if k else None,
                "instructor_name": i["name"] if i else None,
                "plan_class_id": plan["class_id"] if plan else None,
            })
        rows.sort(key=lambda r: (r["starts_at"], r["session_id"]))
        return rows

    def recent_attendance(self, client_id, limit):
        mine = [b for b in self._rows("bookings", {"client_id": client_id})
                if b["status"] != "booked"]
        sessions = self._by_id("sessions", [b["session_id"] for b in mine])
        classes = self._by_id("classes",
                              [s["class_id"] for s in sessions.values()])
        rows = []
        for b in mine:
            s = sessions.get(b["session_id"])
            if s is None:
                continue
            k = classes.get(s["class_id"])
            rows.append({"status": b["status"], "checked_in_at": b["checked_in_at"],
                         "session_id": s["id"], "starts_at": s["starts_at"],
                         "class_name": k["name"] if k else None,
                         "colour": k["colour"] if k else None})
        rows.sort(key=lambda r: (-r["starts_at"], -r["session_id"]))
        return rows[:limit]

    def next_booked_session(self, client_id, after, class_id=None):
        mine = self._rows("bookings", {"client_id": client_id, "status": "booked"})
        sessions = self._by_id("sessions", [b["session_id"] for b in mine])
        ahead = [s for b in mine
                 if (s := sessions.get(b["session_id"])) is not None
                 and s["starts_at"] > after
                 and (class_id is None or s["class_id"] == class_id)]
        if not ahead:
            return None
        s = min(ahead, key=lambda x: (x["starts_at"], x["id"]))
        k = self.get("classes", s["class_id"])
        return {"starts_at": s["starts_at"],
                "class_name": k["name"] if k else None}

    def client_totals(self, client_id):
        mine = self._rows("bookings", {"client_id": client_id})
        sessions = self._by_id("sessions", [b["session_id"] for b in mine])
        present = [b for b in mine if b["status"] == "present"]
        starts = [sessions[b["session_id"]]["starts_at"] for b in present
                  if b["session_id"] in sessions]
        return {"present": len(present),
                "absent": sum(1 for b in mine if b["status"] == "absent"),
                "last_visit": max(starts) if starts else None}

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
        live = [s["_id"] for s in self.db["sessions"].find(
            {"starts_at": {"$gte": start, "$lt": end},
             "status": {"$ne": "cancelled"}}, {"_id": 1}, session=self.session)]
        if not live:
            return {"expected": 0, "arrived": 0, "absent": 0}
        rows = self._rows("bookings", {"session_id": {"in": live}})
        return {"expected": len(rows),
                "arrived": sum(1 for b in rows if b["status"] == "present"),
                "absent": sum(1 for b in rows if b["status"] == "absent")}


class MongoClients(ClientsPort, _Helpers):

    def search_clients(self, active, q):
        flt = {"active": active}
        if q:
            flt = {"$or": [{"name_en": {"like": f"%{q}%"}},
                           {"phone": {"like": f"%{q}%"}},
                           {"school": {"like": f"%{q}%"}}],
                   "active": active}
        rows = self._rows("clients", flt)
        rows.sort(key=lambda r: (r["name_en"] or "", r["id"]))
        return rows

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
        cards = self._rows("credentials", {"client_id": client_id,
                                           "revoked_at": None})
        classes = self._by_id("classes", [c["class_id"] for c in cards])
        rows = [{"id": c["id"], "token": c["token"], "class_id": c["class_id"],
                 "issued_at": c["issued_at"],
                 "class_name": (classes.get(c["class_id"]) or {}).get("name"),
                 "colour": (classes.get(c["class_id"]) or {}).get("colour")}
                for c in cards]
        rows.sort(key=lambda r: (r["class_name"] or "", r["id"]))
        return rows

    def _booking_rows(self, client_id, keep, columns, newest_first=False,
                      limit=None, sub_id=None):
        flt = {"client_id": client_id}
        if sub_id is not None:
            flt["subscription_id"] = sub_id
        mine = self._rows("bookings", flt)
        sessions = self._by_id("sessions", [b["session_id"] for b in mine])
        classes = self._by_id("classes", [s["class_id"] for s in sessions.values()])
        staff = self._by_id("instructors",
                            [s["instructor_id"] for s in sessions.values()])
        rows = []
        for b in mine:
            s = sessions.get(b["session_id"])
            if s is None or not keep(b, s):
                continue
            k, i = classes.get(s["class_id"]), staff.get(s["instructor_id"])
            full = {"booking_id": b["id"], "status": b["status"],
                    "checked_in_at": b["checked_in_at"],
                    "subscription_id": b["subscription_id"],
                    "session_id": s["id"], "starts_at": s["starts_at"],
                    "duration_hours": s["duration_hours"],
                    "class_id": s["class_id"],
                    "class_name": k["name"] if k else None,
                    "colour": k["colour"] if k else None,
                    "instructor_name": i["name"] if i else None}
            rows.append({c: full[c] for c in columns})
        rows.sort(key=lambda r: (r["starts_at"], r["session_id"]),
                  reverse=newest_first)
        return rows[:limit] if limit else rows

    def client_upcoming(self, client_id, now):
        return self._booking_rows(
            client_id,
            lambda b, s: s["starts_at"] >= now and s["status"] != "cancelled",
            ("booking_id", "status", "session_id", "starts_at", "duration_hours",
             "class_id", "class_name", "colour", "instructor_name"))

    def client_history(self, client_id, now, limit):
        return self._booking_rows(
            client_id, lambda b, s: s["starts_at"] < now,
            ("booking_id", "status", "checked_in_at", "subscription_id",
             "session_id", "starts_at", "class_name", "colour", "instructor_name"),
            newest_first=True, limit=limit)

    def plan_sessions(self, client_id, sub_id):
        return self._booking_rows(
            client_id, lambda b, s: True,
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
        assert date_field in ("joined_on", "starts_on"), date_field
        live = {c["id"] for c in self._rows("clients", {"active": 1})}
        if date_field == "joined_on":
            people = {c["id"]: c["joined_on"] for c in self._rows("clients")}
            def when(plan):
                return people.get(plan["client_id"])
        else:
            def when(plan):
                return plan["starts_on"]
        paid, unpriced, plans = 0.0, 0, 0
        for plan in self._rows("subscriptions"):
            if plan["client_id"] not in live:
                continue
            d = when(plan)
            if d is None or not (month_from <= d < month_to):
                continue
            plans += 1
            if plan["price"] is None:
                unpriced += 1
            else:
                paid += plan["price"]
        return {"paid": paid, "unpriced": unpriced, "plans": plans}


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
        plans = self._rows("subscriptions", {"active": 1})
        people = self._by_id("clients", [p["client_id"] for p in plans])
        rows = [{"id": p["client_id"],
                 "name_en": people[p["client_id"]]["name_en"],
                 "phone": people[p["client_id"]]["phone"],
                 "sub_id": p["id"], "plan": p["plan"]}
                for p in plans
                if p["client_id"] in people and people[p["client_id"]]["active"]]
        rows.sort(key=lambda r: r["sub_id"])
        return rows

    def last_session_ts(self, sub_id):
        mine = self._rows("bookings", {"subscription_id": sub_id})
        return self.max_starts_at([b["session_id"] for b in mine])

    def max_starts_at(self, session_ids):
        ids = [s for s in session_ids if s is not None]
        if not ids:
            return None
        doc = self.db["sessions"].find_one(
            {"_id": {"$in": ids}}, {"starts_at": 1},
            sort=[("starts_at", -1)], session=self.session)
        return doc["starts_at"] if doc else None

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
        events = [schema.normalise("access_events", d) for d in
                  self.db["access_events"].find(
                      {"scanned_at": {"$gte": since}}, session=self.session)
                  .sort([("scanned_at", -1), ("_id", -1)]).limit(limit)]
        people = self._by_id("clients", [e["client_id"] for e in events])
        sessions = self._by_id("sessions", [e["session_id"] for e in events])
        classes = self._by_id("classes",
                              [s["class_id"] for s in sessions.values()])
        out = []
        for e in events:
            s = sessions.get(e["session_id"])
            k = classes.get(s["class_id"]) if s else None
            out.append({"id": e["id"], "scanned_at": e["scanned_at"],
                        "decision": e["decision"], "reason": e["reason"],
                        "confirmed_at": e["confirmed_at"],
                        "name_en": (people.get(e["client_id"]) or {}).get("name_en"),
                        "class_name": k["name"] if k else None})
        return out
