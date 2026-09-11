"""
SQLite answers to the named questions in repo/ports.py.

These keep their joins. The Mongo implementations will look quite different
— a few `$in` fetches joined in Python rather than a `$lookup` pipeline —
and that is fine: the contract is the dict that comes back, which the parity
tests check directly.
"""

import db

from ..ports import (BookingsPort, ClassesPort, ClientsPort, EventsPort,
                     InstructorsPort, PlansPort, SessionsPort)


def _marks(values):
    return ",".join("?" * len(values))


class SqliteSessions(SessionsPort):

    def sessions_in_range(self, start, end, class_id=None, not_booked_by=None):
        sql = ("SELECT s.*, c.name AS class_name, c.colour,"
               "       i.name AS instructor_name,"
               "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
               "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
               "     AND b.status='present') AS attended"
               "  FROM sessions s JOIN classes c ON c.id=s.class_id"
               "  LEFT JOIN instructors i ON i.id=s.instructor_id"
               " WHERE s.starts_at >= ? AND s.starts_at < ?")
        params = [start, end]
        if class_id:
            sql += " AND s.class_id = ?"
            params.append(class_id)
        if not_booked_by:
            sql += (" AND NOT EXISTS (SELECT 1 FROM bookings b"
                    "   WHERE b.session_id = s.id AND b.client_id = ?)")
            params.append(not_booked_by)
        sql += " ORDER BY s.starts_at, s.id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def slot_conflict(self, starts_at, ends_at, exclude_id=None):
        sql = ("SELECT s.id, s.starts_at, s.duration_hours, c.name AS class_name"
               "  FROM sessions s JOIN classes c ON c.id = s.class_id"
               " WHERE s.status != 'cancelled'"
               "   AND s.starts_at < ? AND s.ends_at > ?")
        params = [ends_at, starts_at]
        if exclude_id is not None:
            sql += " AND s.id != ?"
            params.append(exclude_id)
        row = self.conn.execute(
            sql + " ORDER BY s.starts_at, s.id LIMIT 1", params).fetchone()
        return dict(row) if row else None

    def complete_finished_sessions(self, now):
        return self.conn.execute(
            "UPDATE sessions SET status='completed'"
            " WHERE status='scheduled' AND ends_at < ?", (now,)).rowcount

    def session_detail(self, session_id):
        row = self.conn.execute(
            "SELECT s.*, c.name AS class_name, c.colour, i.name AS instructor_name"
            "  FROM sessions s JOIN classes c ON c.id=s.class_id"
            "  LEFT JOIN instructors i ON i.id=s.instructor_id WHERE s.id=?",
            (session_id,)).fetchone()
        return dict(row) if row else None


class SqliteClasses(ClassesPort):

    def classes_with_counts(self, active):
        return [dict(r) for r in self.conn.execute(
            "SELECT c.*, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM sessions s WHERE s.class_id=c.id"
            "     AND s.starts_at > ? AND s.status='scheduled') AS upcoming,"
            "  (SELECT COUNT(DISTINCT b.client_id) FROM bookings b"
            "     JOIN sessions s ON s.id=b.session_id WHERE s.class_id=c.id) AS students"
            " FROM classes c LEFT JOIN instructors i ON i.id = c.instructor_id"
            " WHERE c.active=? ORDER BY c.name, c.id", (db.now(), active)).fetchall()]

    def class_sessions(self, class_id, limit):
        return [dict(r) for r in self.conn.execute(
            "SELECT s.*, i.name AS instructor_name,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS booked,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s LEFT JOIN instructors i ON i.id = s.instructor_id"
            " WHERE s.class_id=? ORDER BY s.starts_at DESC, s.id DESC LIMIT ?",
            (class_id, limit)).fetchall()]

    def class_students(self, class_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT cl.id, cl.name_en, cl.phone, cl.photo_path,"
            "       COUNT(b.id) AS slots,"
            "       SUM(CASE WHEN b.status='present' THEN 1 ELSE 0 END) AS attended"
            "  FROM bookings b JOIN sessions s ON s.id=b.session_id"
            "  JOIN clients cl ON cl.id=b.client_id"
            " WHERE s.class_id=? AND cl.active=1"
            " GROUP BY cl.id ORDER BY cl.name_en, cl.id", (class_id,)).fetchall()]


class SqliteBookings(BookingsPort):

    def plan_counts(self, sub_id):
        return self.plan_counts_bulk([sub_id])[sub_id]

    def plan_counts_bulk(self, sub_ids):
        out = {s: {"assigned": 0, "present": 0, "absent": 0} for s in sub_ids}
        if not sub_ids:
            return out
        rows = self.conn.execute(
            "SELECT subscription_id AS sub, COUNT(*) assigned,"
            "       SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) present,"
            "       SUM(CASE WHEN status='absent'  THEN 1 ELSE 0 END) absent"
            f"  FROM bookings WHERE subscription_id IN ({_marks(sub_ids)})"
            "  GROUP BY subscription_id", tuple(sub_ids)).fetchall()
        for r in rows:
            out[r["sub"]] = {"assigned": r["assigned"] or 0,
                             "present": r["present"] or 0,
                             "absent": r["absent"] or 0}
        return out

    def attendance_counts(self, session_ids):
        out = {s: {"booked": 0, "attended": 0} for s in session_ids}
        if not session_ids:
            return out
        rows = self.conn.execute(
            "SELECT session_id AS sid, COUNT(*) booked,"
            "       SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) attended"
            f"  FROM bookings WHERE session_id IN ({_marks(session_ids)})"
            "  GROUP BY session_id", tuple(session_ids)).fetchall()
        for r in rows:
            out[r["sid"]] = {"booked": r["booked"] or 0,
                             "attended": r["attended"] or 0}
        return out

    def check_in_booking(self, booking_id, at):
        return self.conn.execute(
            "UPDATE bookings SET status='present', checked_in_at=?"
            " WHERE id=? AND status != 'present'", (at, booking_id)).rowcount > 0

    def settle_absences(self, now, frozen_sub_ids):
        # The frozen plans are fetched separately and passed in rather than
        # joined, because Mongo has no cross-collection update and there are
        # never more than a handful of them.
        sql = ("UPDATE bookings SET status='absent'"
               " WHERE status='booked'"
               "   AND session_id IN (SELECT id FROM sessions"
               "        WHERE status != 'cancelled' AND ends_at < ?)")
        params = [now]
        if frozen_sub_ids:
            sql += (f" AND (subscription_id IS NULL"
                    f"      OR subscription_id NOT IN ({_marks(frozen_sub_ids)}))")
            params.extend(frozen_sub_ids)
        return self.conn.execute(sql, params).rowcount


class SqliteClients(ClientsPort):

    # The three booking lists share a join but not their columns, so they
    # stay three named questions rather than one method with flags. Widening
    # them into a single superset would change what the profile receives.
    _BOOKING_JOIN = (
        "  FROM bookings b JOIN sessions s ON s.id = b.session_id"
        "  JOIN classes cl ON cl.id = s.class_id"
        "  LEFT JOIN instructors i ON i.id = s.instructor_id")

    def search_clients(self, active, q):
        like = f"%{q}%"
        return [dict(r) for r in self.conn.execute(
            "SELECT c.* FROM clients c"
            " WHERE c.active = ? AND (? = '' OR c.name_en LIKE ? OR c.phone LIKE ?"
            "   OR c.school LIKE ?)"
            " ORDER BY c.name_en, c.id", (active, q, like, like, like)).fetchall()]

    def card_counts_bulk(self, client_ids):
        out = {c: 0 for c in client_ids}
        if not client_ids:
            return out
        rows = self.conn.execute(
            "SELECT client_id, COUNT(*) n FROM credentials"
            f" WHERE revoked_at IS NULL AND client_id IN ({_marks(client_ids)})"
            " GROUP BY client_id", tuple(client_ids)).fetchall()
        for r in rows:
            out[r["client_id"]] = r["n"]
        return out

    def client_cards(self, client_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT cr.id, cr.token, cr.class_id, cr.issued_at,"
            "       cl.name AS class_name, cl.colour"
            "  FROM credentials cr LEFT JOIN classes cl ON cl.id = cr.class_id"
            " WHERE cr.client_id=? AND cr.revoked_at IS NULL"
            " ORDER BY cl.name, cr.id", (client_id,)).fetchall()]

    def client_upcoming(self, client_id, now):
        return [dict(r) for r in self.conn.execute(
            "SELECT b.id AS booking_id, b.status, s.id AS session_id, s.starts_at,"
            "       s.duration_hours, s.class_id, cl.name AS class_name, cl.colour,"
            "       i.name AS instructor_name"
            + self._BOOKING_JOIN +
            " WHERE b.client_id=? AND s.starts_at >= ? AND s.status != 'cancelled'"
            " ORDER BY s.starts_at, s.id", (client_id, now)).fetchall()]

    def client_history(self, client_id, now, limit):
        return [dict(r) for r in self.conn.execute(
            "SELECT b.id AS booking_id, b.status, b.checked_in_at, b.subscription_id,"
            "       s.id AS session_id, s.starts_at, cl.name AS class_name, cl.colour,"
            "       i.name AS instructor_name"
            + self._BOOKING_JOIN +
            " WHERE b.client_id=? AND s.starts_at < ?"
            " ORDER BY s.starts_at DESC, s.id DESC LIMIT ?",
            (client_id, now, limit)).fetchall()]

    def plan_sessions(self, client_id, sub_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT b.status, b.checked_in_at, s.id AS session_id, s.starts_at,"
            "       s.duration_hours, cl.name AS class_name, cl.colour,"
            "       i.name AS instructor_name"
            + self._BOOKING_JOIN +
            " WHERE b.client_id=? AND b.subscription_id=?"
            " ORDER BY s.starts_at, s.id", (client_id, sub_id)).fetchall()]


class SqlitePlans(PlansPort):

    def active_plans_with_clients(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT c.id, c.name_en, c.phone, s.id AS sub_id, s.plan"
            "  FROM clients c JOIN subscriptions s ON s.client_id=c.id AND s.active=1"
            " WHERE c.active=1 ORDER BY s.id").fetchall()]

    def last_session_ts(self, sub_id):
        return self.conn.execute(
            "SELECT MAX(s.starts_at) t FROM bookings b"
            "  JOIN sessions s ON s.id=b.session_id"
            " WHERE b.subscription_id=?", (sub_id,)).fetchone()["t"]

    def max_starts_at(self, session_ids):
        if not session_ids:
            return None
        return self.conn.execute(
            f"SELECT MAX(starts_at) t FROM sessions WHERE id IN ({_marks(session_ids)})",
            tuple(session_ids)).fetchone()["t"]

    def active_plans_for(self, client_ids):
        if not client_ids:
            return {}
        rows = self.conn.execute(
            "SELECT * FROM subscriptions"
            f" WHERE active=1 AND client_id IN ({_marks(client_ids)})"
            " ORDER BY expires_on ASC, id ASC", tuple(client_ids)).fetchall()
        out = {}
        for r in rows:
            # ORDER BY expires_on ASC, so the first seen for a client is the
            # soonest to expire -- the one needing attention.
            out.setdefault(r["client_id"], dict(r))
        return out


class SqliteInstructors(InstructorsPort):

    def taught_totals_bulk(self, instructor_ids, start=None, end=None,
                           status="completed"):
        out = {i: {"sessions": 0, "hours": 0.0} for i in instructor_ids}
        if not instructor_ids:
            return out
        sql = ("SELECT instructor_id AS iid, COUNT(*) n,"
               "       COALESCE(SUM(duration_hours),0) h FROM sessions"
               f" WHERE instructor_id IN ({_marks(instructor_ids)})")
        params = list(instructor_ids)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if start is not None:
            sql += " AND starts_at >= ?"
            params.append(start)
        if end is not None:
            sql += " AND starts_at < ?"
            params.append(end)
        sql += " GROUP BY instructor_id"
        for r in self.conn.execute(sql, params).fetchall():
            out[r["iid"]] = {"sessions": r["n"], "hours": round(r["h"] or 0, 2)}
        return out

    def instructor_sessions(self, instructor_id, start, end, limit):
        return [dict(r) for r in self.conn.execute(
            "SELECT s.id, s.starts_at, s.duration_hours, s.status,"
            "       c.name AS class_name, c.colour,"
            "  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id"
            "     AND b.status='present') AS attended"
            "  FROM sessions s JOIN classes c ON c.id = s.class_id"
            " WHERE s.instructor_id = ? AND s.starts_at >= ? AND s.starts_at < ?"
            " ORDER BY s.starts_at DESC, s.id DESC LIMIT ?",
            (instructor_id, start, end, limit)).fetchall()]


class SqliteEvents(EventsPort):

    def recent_events(self, since, limit):
        return [dict(r) for r in self.conn.execute(
            "SELECT e.id, e.scanned_at, e.decision, e.reason, e.confirmed_at,"
            "       c.name_en, cl.name AS class_name"
            "  FROM access_events e LEFT JOIN clients c ON c.id=e.client_id"
            "  LEFT JOIN sessions s ON s.id=e.session_id"
            "  LEFT JOIN classes cl ON cl.id=s.class_id"
            " WHERE e.scanned_at >= ? ORDER BY e.scanned_at DESC, e.id DESC"
            " LIMIT ?", (since, limit)).fetchall()]
