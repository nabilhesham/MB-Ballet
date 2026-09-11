"""
How many round trips a page costs.

On a local SQLite file an N+1 is invisible — a statement costs microseconds
and the suite never notices. Against a networked backend each one is tens of
milliseconds, so `/api/dashboard` calling `plan_state()` once per active
client (three statements each) is three round trips per client: on two
hundred clients, roughly forty-five seconds on the landing page.

These tests are the only thing that stops that coming back. They assert a
ceiling that does **not** grow with the number of clients, which is the
property that actually matters — not the exact number.
"""

import pytest

import access
import db
from fixtures import _ins


class Counted:
    """
    Counts repository operations — the unit that costs a round trip.

    Deliberately not sqlite3's statement tracer. A route handler opens its
    own connection, so a tracer attached to the fixture's connection sees
    nothing of what the route does and every assertion passes vacuously.
    (It did, until this was noticed.) Counting calls on the Repo object
    catches the route's connection too, and is the same measure on a
    backend that has no SQL at all.
    """

    COUNTED = ("get", "find", "find_one", "count", "exists", "distinct",
               "insert", "insert_many", "insert_ignore", "update",
               "update_where", "delete", "delete_where",
               "sessions_in_range", "slot_conflict", "complete_finished_sessions",
               "session_detail", "classes_with_counts", "class_sessions",
               "class_students", "search_clients", "card_counts_bulk",
               "client_cards", "client_upcoming", "client_history",
               "plan_sessions", "takings", "active_plans_for",
               "credential_by_token", "client_day_bookings", "recent_attendance",
               "next_booked_session", "client_totals", "giveable_slots",
               "session_roster", "day_attendance_totals",
               "salary_hours", "adjustments_sum", "taught_totals_bulk",
               "instructor_sessions",
               "plan_counts", "plan_counts_bulk", "attendance_counts",
               "check_in_booking", "settle_absences",
               "active_plans_with_clients", "last_session_ts", "max_starts_at",
               "recent_events")

    def __init__(self):
        # Its own MonkeyPatch, not the test's. undo() reverts everything the
        # instance has done, and the shared fixture is also holding the env
        # vars that point config at the throwaway database — undoing those
        # mid-test sends the next query at the real academy.db.
        self.monkeypatch = pytest.MonkeyPatch()
        self.calls = []

    def __enter__(self):
        from repo.sqlite import SqliteRepo
        for name in self.COUNTED:
            original = getattr(SqliteRepo, name)

            def wrapper(inner_self, *a, _o=original, _n=name, **kw):
                self.calls.append(_n)
                return _o(inner_self, *a, **kw)

            self.monkeypatch.setattr(SqliteRepo, name, wrapper)
        return self

    def __exit__(self, *exc):
        self.monkeypatch.undo()
        return False

    def __len__(self):
        return len(self.calls)

    def report(self):
        from collections import Counter
        tally = Counter(self.calls).most_common()
        return (f"{len(self)} repository calls:\n  "
                + "\n  ".join(f"{n} x {name}" for name, n in tally))


def more_clients(repo, academy, n):
    """n extra active clients, each with a live plan and a booking."""
    with repo.begin():
        for i in range(n):
            cid = _ins(repo, "clients", name_en=f"Extra {i}",
                       joined_on="2026-01-01", created_at=db.now(), active=1)
            sub = _ins(repo, "subscriptions", client_id=cid, class_id=academy.ballet,
                       plan="4 sessions", sessions_total=4, price=100.0,
                       starts_on="2026-01-01", expires_on="2099-01-01",
                       active=1, created_at=db.now())
            _ins(repo, "bookings", client_id=cid,
                 session_id=academy.ballet_sessions[0], subscription_id=sub,
                 status="present", created_at=db.now())


@pytest.fixture
def client(academy):
    import server
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def test_plan_states_costs_three_queries_whatever_the_count(academy):
    """One for the plans, one for their counts, one for the classes."""
    repo = academy.repo
    more_clients(repo, academy, 40)
    ids = [r["sub_id"] for r in repo.active_plans_with_clients()]
    assert len(ids) > 40, "precondition: plenty of plans"

    with Counted() as counted:
        states = access.plan_states(repo, ids)

    assert len(states) == len(ids)
    assert len(counted) == 3, counted.report()


def test_the_dashboard_does_not_grow_a_query_per_client(client):
    a = client.academy
    with Counted() as few:
        client.get("/api/dashboard")

    more_clients(a.repo, a, 40)
    with Counted() as many:
        client.get("/api/dashboard")

    assert len(many) <= len(few) + 2, (
        f"adding 40 clients cost {len(many) - len(few)} extra statements\n"
        f"{many.report()}")


def test_the_dashboard_stays_under_its_ceiling(client):
    more_clients(client.academy.repo, client.academy, 40)
    with Counted() as counted:
        r = client.get("/api/dashboard")
    assert r.status_code == 200
    # A ratchet, not a target. What must never change is that none of these
    # is per-client. The absolute number moves as access.py is drained —
    # settle_absences reads the frozen plans separately rather than joining
    # them, which is one more call here and the only shape Mongo can express.
    assert len(counted) <= 26, counted.report()


def test_the_clients_list_does_not_grow_a_query_per_client(client):
    """
    It used to be four per client: active_plan, then plan_state's three, then
    a card count. Now five for the whole list.
    """
    a = client.academy
    with Counted() as few:
        client.get("/api/clients")

    more_clients(a.repo, a, 40)
    with Counted() as many:
        r = client.get("/api/clients")

    assert r.status_code == 200
    assert len(r.json()) > 40
    assert len(many) <= len(few) + 2, (
        f"adding 40 clients cost {len(many) - len(few)} extra calls\n{many.report()}")


def test_a_client_profile_costs_a_fixed_number_of_queries(client):
    """plan_states() answers for every plan the client has ever had at once."""
    with Counted() as counted:
        r = client.get(f"/api/clients/{client.academy.dual}")
    assert r.status_code == 200
    assert len(r.json()["plans"]) == 2
    assert len(counted) <= 16, counted.report()


def test_attendance_counts_is_one_query_for_many_sessions(academy):
    repo = academy.repo
    with Counted() as counted:
        got = repo.attendance_counts(academy.ballet_sessions)
    assert len(got) == len(academy.ballet_sessions)
    assert len(counted) == 1, counted.report()


def test_a_session_with_no_bookings_still_gets_zeroes(academy):
    """
    Never None, on either backend. SQL's SUM over no rows is NULL and
    Mongo's is no group at all; normalising in the port is what stops that
    difference reaching the caller.
    """
    repo = academy.repo
    fresh = repo.insert("sessions", {
        "class_id": academy.ballet, "starts_at": db.now() + 90000,
        "duration_hours": 1.0, "ends_at": db.now() + 93600, "status": "scheduled"})
    assert repo.attendance_counts([fresh])[fresh] == {"booked": 0, "attended": 0}
    assert repo.plan_counts_bulk([9999])[9999] == {
        "assigned": 0, "present": 0, "absent": 0}
