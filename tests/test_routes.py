"""
The api/ layer, exercised over HTTP.

None of this was testable before config.py: route handlers call a bare
`db.connect()`, so any test touching one reached for the real academy.db.
Now the fixture points config at a throwaway file and the routers follow it
there.

This matters well beyond Phase 0. Roughly 42% of the app's database access
lives in these handlers rather than in access.py, and all of it is about to
be moved behind a repository interface. This is the net under that.
"""

from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

import access


@pytest.fixture
def client(academy):
    """
    A TestClient over the real app, against the fixture's database.

    The app is imported here rather than at module scope so the `conn`
    fixture has already set MB_SQLITE_PATH.
    """
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def at(day_offset: int, hour: int, minute: int = 0) -> int:
    d = date.today() + timedelta(days=day_offset)
    return int(datetime.combine(d, time(hour, minute)).timestamp())


# ---------------------------------------------------------------- reads

def test_the_dashboard_answers(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"stats", "today_sessions", "recent", "attention", "today"}


def test_the_dashboard_lists_todays_session(client):
    ids = [s["id"] for s in client.get("/api/dashboard").json()["today_sessions"]]
    assert client.academy.today_ballet in ids


def test_the_clients_list_carries_plan_state(client):
    rows = client.get("/api/clients").json()
    dana = next(r for r in rows if r["id"] == client.academy.dual)
    assert dana["plan"] is not None
    assert dana["remaining"] is not None


def test_the_clients_search_matches_on_name(client):
    rows = client.get("/api/clients", params={"q": "Dana"}).json()
    assert [r["id"] for r in rows] == [client.academy.dual]


def test_an_archived_client_is_not_in_the_active_list(client):
    ids = [r["id"] for r in client.get("/api/clients").json()]
    assert client.academy.archived not in ids
    archived = [r["id"] for r in
                client.get("/api/clients", params={"status": "archived"}).json()]
    assert client.academy.archived in archived


def test_a_client_profile_answers(client):
    r = client.get(f"/api/clients/{client.academy.dual}")
    assert r.status_code == 200
    body = r.json()
    assert body["name_en"] == "Dana Halim"
    assert len(body["plans"]) == 2, "one per class"


# ---------------------------------------------------------------- sessions

def test_creating_a_session_sets_its_end(client):
    """
    ends_at is stored, not derived. If create_session forgets it, the new
    session is invisible to the conflict check and to the absent sweep.
    """
    r = client.post("/api/sessions", json={
        "class_id": client.academy.ballet, "starts_at": at(3, 11),
        "duration_hours": 1.5})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    row = client.academy.conn.execute(
        "SELECT starts_at, duration_hours, ends_at FROM sessions WHERE id=?",
        (sid,)).fetchone()
    assert row["ends_at"] == access.ends_at_of(row["starts_at"], row["duration_hours"])


def test_creating_a_session_in_a_taken_slot_is_refused(client):
    """One session at a time, academy-wide."""
    client.post("/api/sessions", json={
        "class_id": client.academy.ballet, "starts_at": at(4, 11),
        "duration_hours": 1.5})
    clash = client.post("/api/sessions", json={
        "class_id": client.academy.flex, "starts_at": at(4, 11, 30),
        "duration_hours": 1.0})
    assert clash.status_code == 400
    assert "already taken" in clash.json()["detail"]


def test_a_back_to_back_session_is_allowed(client):
    client.post("/api/sessions", json={
        "class_id": client.academy.ballet, "starts_at": at(5, 11),
        "duration_hours": 1.5})                                   # 11:00-12:30
    ok = client.post("/api/sessions", json={
        "class_id": client.academy.flex, "starts_at": at(5, 12, 30),
        "duration_hours": 1.0})
    assert ok.status_code == 200, ok.text


def test_moving_a_session_moves_its_end(client):
    r = client.post("/api/sessions", json={
        "class_id": client.academy.ballet, "starts_at": at(6, 11),
        "duration_hours": 1.5})
    sid = r.json()["id"]

    moved = client.put(f"/api/sessions/{sid}", json={"starts_at": at(7, 9)})
    assert moved.status_code == 200, moved.text

    row = client.academy.conn.execute(
        "SELECT starts_at, duration_hours, ends_at FROM sessions WHERE id=?",
        (sid,)).fetchone()
    assert row["starts_at"] == at(7, 9)
    assert row["ends_at"] == access.ends_at_of(at(7, 9), row["duration_hours"])


def test_stretching_a_session_moves_its_end(client):
    r = client.post("/api/sessions", json={
        "class_id": client.academy.ballet, "starts_at": at(8, 11),
        "duration_hours": 1.0})
    sid = r.json()["id"]

    client.put(f"/api/sessions/{sid}", json={"duration_hours": 2.5})

    row = client.academy.conn.execute(
        "SELECT starts_at, duration_hours, ends_at FROM sessions WHERE id=?",
        (sid,)).fetchone()
    assert row["duration_hours"] == 2.5
    assert row["ends_at"] == access.ends_at_of(row["starts_at"], 2.5)


def test_repeating_weekly_sets_every_end(client):
    r = client.post("/api/sessions/repeat", json={
        "class_id": client.academy.ballet, "starts_at": at(10, 9),
        "duration_hours": 1.0, "weeks": 4, "weekdays": [date.today().weekday()]})
    assert r.status_code == 200, r.text
    assert r.json()["created"] >= 1

    wrong = client.academy.conn.execute(
        "SELECT COUNT(*) n FROM sessions WHERE ends_at IS NULL"
        "    OR ends_at != CAST(starts_at + duration_hours * 3600 AS INTEGER)"
    ).fetchone()["n"]
    assert wrong == 0


# ---------------------------------------------------------------- access

def test_a_scan_over_http_grants_and_checks_in(client):
    a = client.academy
    before = access.plan_state(a.conn, a.dual_ballet_plan)["remaining"]

    r = client.post("/api/access/verify", json={"token": a.dual_ballet_card})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["granted"] is True

    client.post("/api/access/checkin", json={"event_id": body["event_id"]})
    after = access.plan_state(a.conn, a.dual_ballet_plan)["remaining"]
    assert after == before - 1


def test_the_wrong_class_card_is_refused_over_http(client):
    r = client.post("/api/access/verify",
                    json={"token": client.academy.dual_flex_card})
    assert r.json()["granted"] is False
