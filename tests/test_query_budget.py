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


def _repo_classes():
    """Every concrete Repo implementation that can be loaded."""
    from repo.sqlite import SqliteRepo
    classes = [SqliteRepo]
    try:
        from repo.mongo import MongoRepo
    except ImportError:          # pragma: no cover - no pymongo installed
        pass
    else:
        classes.append(MongoRepo)
    return classes


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
               "credential_by_token", "client_bookings", "giveable_slots",
               "session_roster", "day_attendance_totals",
               "salary_hours", "adjustments_sum", "taught_totals_bulk",
               "instructor_sessions",
               "plan_counts", "plan_counts_bulk", "attendance_counts", "plan_rows",
               "check_in_booking", "settle_absences",
               "active_plans_with_clients", "last_session_ts", "max_starts_at",
               "last_session_ts_bulk",
               "recent_events")

    def __init__(self):
        # Its own MonkeyPatch, not the test's. undo() reverts everything the
        # instance has done, and the shared fixture is also holding the env
        # vars that point config at the throwaway database — undoing those
        # mid-test sends the next query at the real academy.db.
        self.monkeypatch = pytest.MonkeyPatch()
        self.calls = []

    def __enter__(self):
        # Both backends, not just SQLite. Patching one class meant the
        # counter saw nothing on the other and every assertion passed
        # vacuously -- the same way this helper's first version did by
        # watching the wrong connection.
        for cls in _repo_classes():
            for name in self.COUNTED:
                original = getattr(cls, name)

                def wrapper(inner_self, *a, _o=original, _n=name, **kw):
                    self.calls.append(_n)
                    return _o(inner_self, *a, **kw)

                self.monkeypatch.setattr(cls, name, wrapper)
        return self

    def __exit__(self, *exc):
        self.monkeypatch.undo()
        return False

    def __len__(self):
        return len(self.calls)

    def excluding(self, *names):
        """
        How many calls there were, ignoring some.

        SqliteRepo.insert_many() is documented as N separate INSERTs because
        sqlite3 gives no way to recover N ids from one executemany, while
        MongoRepo.insert_many() really is one round trip. Counting the
        decomposed inserts would make a batched write look unbatched on
        SQLite alone -- so a test about round trips counts the insert_many
        and ignores what SQLite turns it into.
        """
        return len([c for c in self.calls if c not in names])

    def count_of(self, name):
        return self.calls.count(name)

    def report(self):
        from collections import Counter
        tally = Counter(self.calls).most_common()
        return (f"{len(self)} repository calls:\n  "
                + "\n  ".join(f"{n} x {name}" for name, n in tally))


def more_clients(repo, academy, n):
    """
    n extra active clients, each with a live plan and a booking.

    Three writes rather than 3n. The point of this helper is to make the
    *page* do more work, not the setup — and 120 sequential inserts inside
    one transaction is a slow, needlessly heavy way to arrange that against
    a networked backend.
    """
    with repo.begin():
        clients = repo.insert_many("clients", [
            {"name_en": f"Extra {i}", "joined_on": "2026-01-01",
             "created_at": db.now(), "active": 1} for i in range(n)])
        plans = repo.insert_many("subscriptions", [
            {"client_id": cid, "class_id": academy.ballet, "plan": "4 sessions",
             "sessions_total": 4, "price": 100.0, "starts_on": "2026-01-01",
             "expires_on": "2099-01-01", "active": 1, "created_at": db.now()}
            for cid in clients])
        repo.insert_many("bookings", [
            {"client_id": cid, "session_id": academy.ballet_sessions[0],
             "subscription_id": sub, "status": "present", "created_at": db.now()}
            for cid, sub in zip(clients, plans)])


@pytest.fixture
def client(academy):
    import server
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def test_plan_states_costs_one_query_whatever_the_count(academy):
    """
    The plans, their classes and their booking counts together.

    It was three -- one each -- which is three round trips even for the
    single plan the reception scan path asks about, with a client standing
    at the desk. repo.plan_rows() is the one question.
    """
    repo = academy.repo
    more_clients(repo, academy, 40)
    ids = [r["sub_id"] for r in repo.active_plans_with_clients()]
    assert len(ids) > 40, "precondition: plenty of plans"

    with Counted() as counted:
        states = access.plan_states(repo, ids)

    assert len(states) == len(ids)
    assert len(counted) == 1, counted.report()


def test_a_single_plan_state_costs_one_query_too(academy):
    """plan_state() is plan_states() of one, so it cannot drift from it."""
    with Counted() as counted:
        state = access.plan_state(academy.repo, academy.dual_ballet_plan)
    assert state["id"] == academy.dual_ballet_plan
    assert len(counted) == 1, counted.report()


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
    # Was 26: expected_today() used to settle a second time, and plan_states()
    # used to cost three calls rather than one.
    #
    # The ceiling is the higher of the two backends, which is Mongo at 19.
    # That is not slack: sessions_in_range() answers the booked/attended
    # counts with a second aggregate there, where SQLite folds them into a
    # correlated subquery inside the one statement. A genuine extra round
    # trip, counted honestly.
    assert len(counted) <= 19, counted.report()


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


def test_a_scan_costs_a_fixed_number_of_queries(academy, repo):
    """
    The one path with a person standing at the desk waiting for it.

    It used to ask the same four questions of the same client's bookings
    through four port methods, each of which re-read those bookings and
    re-joined the sessions behind them — thirteen round trips on a document
    store to decide something it already had in hand — and it looked the
    client's active plan up twice, once to build the payload and once to
    check it was not frozen.

    The ceiling counts the maintenance sweep too: settle_past_sessions()
    runs at the top of both entry points, so anything added to it is paid for
    here, by the client at the desk.

    The number is a ceiling over both backends and not a per-backend total:
    MongoDB builds some of its named methods out of other counted primitives
    (credential_by_token is a find_one plus a get, minting an id is its own
    increment), so it counts higher than SQLite for the same work. What the
    ceiling holds is the shape — one read of the client's bookings, one of
    their plan — and the test below holds that it does not grow.
    """
    token = academy.dual_ballet_card          # _card() returns the token itself

    with Counted() as c:
        assert access.verify(repo, token)["granted"] is True
    assert len(c) <= 14, c.report()
    assert c.count_of("client_bookings") == 1, c.report()

    with Counted() as c:
        access.verify_by_client(repo, academy.dual)
    assert len(c) <= 14, c.report()
    assert c.count_of("client_bookings") == 1, c.report()


def test_a_scan_does_not_grow_a_query_per_client(academy, repo):
    """
    The ceiling above must not move when the academy does. The sweep inside
    settle_past_sessions() is academy-wide, so a version of it that read a
    row per plan or a row per session would show up here and nowhere else.
    """
    token = academy.dual_ballet_card

    with Counted() as small:
        access.verify(repo, token)

    more_clients(repo, academy, 40)

    with Counted() as large:
        access.verify(repo, token)

    assert len(large) == len(small), \
        f"grew with the academy\nbefore: {small.report()}\nafter: {large.report()}"


def test_a_client_profile_costs_a_fixed_number_of_queries(client):
    """plan_states() answers for every plan the client has ever had at once."""
    with Counted() as counted:
        r = client.get(f"/api/clients/{client.academy.dual}")
    assert r.status_code == 200
    assert len(r.json()["plans"]) == 2
    # Was 16, before plan_states() became one call; 11 rather than 10 since
    # the profile began reading which card images exist -- one find for all
    # of them, which is the right shape and a legitimate extra call.
    assert len(counted) <= 11, counted.report()


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


# ----------------------------------------------------- pages with no budget yet
#
# The four below were uncovered, and two of them held the worst N+1s in the
# app. As above, the property that matters is that the number does not grow
# with the rows on the page -- the ceilings are ratchets.

def test_the_classes_list_does_not_grow_a_query_per_class(client):
    """
    It used to fetch the whole sessions collection, the whole bookings
    collection, and then one instructor per class.
    """
    a = client.academy
    with Counted() as few:
        r = client.get("/api/classes")
    assert r.status_code == 200
    before = len(r.json())

    with a.repo.begin():
        a.repo.insert_many("classes", [
            {"name": f"Extra {i}", "colour": "#87438E", "duration_hours": 1.0,
             "instructor_id": a.ana, "active": 1} for i in range(20)])
    with Counted() as many:
        r = client.get("/api/classes")

    assert len(r.json()) == before + 20
    assert len(many) <= len(few), (
        f"adding 20 classes cost {len(many) - len(few)} extra calls\n"
        f"{many.report()}")


def test_an_instructor_profile_costs_a_fixed_number_of_queries(client):
    with Counted() as counted:
        r = client.get(f"/api/instructors/{client.academy.ana}")
    assert r.status_code == 200, r.text
    # taught_totals_bulk was called three times for the one instructor, and
    # logged_hours re-read a row the route already had.
    # Down from twelve: taught_totals_bulk was called three times for the one
    # instructor and logged_hours re-read a row the route already had. The two
    # that remain ask genuinely different questions -- the period's totals,
    # and what is still upcoming within it.
    #
    # Eleven rather than ten for the same reason the dashboard's ceiling is
    # Mongo's: instructor_sessions() costs a second call for the attendance
    # counts there and one statement on SQLite.
    assert len(counted) <= 11, counted.report()


def test_repeating_a_term_does_not_grow_a_query_per_week(client):
    """
    It was exists() + slot_conflict() + insert() per generated date -- three
    round trips a week, and a term is up to 96 sessions.
    """
    a = client.academy
    from datetime import date, datetime, time, timedelta

    def start(weeks_out):
        d = date.today() + timedelta(days=200 + weeks_out)
        return int(datetime.combine(d, time(7, 0)).timestamp())

    with Counted() as few:
        r = client.post("/api/sessions/repeat", json={
            "class_id": a.ballet, "starts_at": start(0), "duration_hours": 1.0,
            "weeks": 2, "weekdays": [(date.today() + timedelta(days=200)).weekday()]})
    assert r.status_code == 200, r.text

    with Counted() as many:
        r = client.post("/api/sessions/repeat", json={
            "class_id": a.ballet, "starts_at": start(140), "duration_hours": 1.0,
            "weeks": 20,
            "weekdays": [(date.today() + timedelta(days=340)).weekday()]})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 20

    assert many.count_of("insert_many") == 1, "one write for the whole term"
    assert many.excluding("insert") <= few.excluding("insert"), (
        f"10x the weeks cost {many.excluding('insert') - few.excluding('insert')}"
        f" extra calls\n{many.report()}")


def test_a_bulk_delete_does_not_grow_a_query_per_session(client):
    """
    It was seven-plus round trips per session, plus refresh_expiry per plan
    behind them -- several hundred for a term.
    """
    a = client.academy
    from datetime import date, datetime, time, timedelta

    def make(n, day_offset):
        d = date.today() + timedelta(days=day_offset)
        ids = []
        with a.repo.begin():
            for i in range(n):
                ts = int(datetime.combine(d, time(6, 0)).timestamp()) + i * 7200
                ids.append(a.repo.insert("sessions", {
                    "class_id": a.ballet, "starts_at": ts, "duration_hours": 1.0,
                    "ends_at": ts + 3600, "status": "scheduled"}))
        return ids

    small, large = make(2, 500), make(20, 600)

    with Counted() as few:
        r = client.post("/api/sessions/bulk-delete", json={"ids": small})
    assert r.status_code == 200 and r.json()["deleted"] == 2, r.text

    with Counted() as many:
        r = client.post("/api/sessions/bulk-delete", json={"ids": large})
    assert r.status_code == 200 and r.json()["deleted"] == 20, r.text

    assert len(many) <= len(few), (
        f"10x the sessions cost {len(many) - len(few)} extra calls\n{many.report()}")
