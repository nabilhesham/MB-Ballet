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
import db


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

    row = client.academy.repo.get("sessions", sid)
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

    row = client.academy.repo.get("sessions", sid)
    assert row["starts_at"] == at(7, 9)
    assert row["ends_at"] == access.ends_at_of(at(7, 9), row["duration_hours"])


def test_stretching_a_session_moves_its_end(client):
    r = client.post("/api/sessions", json={
        "class_id": client.academy.ballet, "starts_at": at(8, 11),
        "duration_hours": 1.0})
    sid = r.json()["id"]

    client.put(f"/api/sessions/{sid}", json={"duration_hours": 2.5})

    row = client.academy.repo.get("sessions", sid)
    assert row["duration_hours"] == 2.5
    assert row["ends_at"] == access.ends_at_of(row["starts_at"], 2.5)


def test_repeating_weekly_sets_every_end(client):
    r = client.post("/api/sessions/repeat", json={
        "class_id": client.academy.ballet, "starts_at": at(10, 9),
        "duration_hours": 1.0, "weeks": 4, "weekdays": [date.today().weekday()]})
    assert r.status_code == 200, r.text
    assert r.json()["created"] >= 1

    wrong = [s for s in client.academy.repo.find("sessions")
             if s["ends_at"] != access.ends_at_of(s["starts_at"], s["duration_hours"])]
    assert wrong == []


# ---------------------------------------------------------------- access

def test_a_scan_over_http_grants_and_checks_in(client):
    a = client.academy
    before = access.plan_state(a.repo, a.dual_ballet_plan)["remaining"]

    r = client.post("/api/access/verify", json={"token": a.dual_ballet_card})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["granted"] is True

    client.post("/api/access/checkin", json={"event_id": body["event_id"]})
    after = access.plan_state(a.repo, a.dual_ballet_plan)["remaining"]
    assert after == before - 1


def test_the_wrong_class_card_is_refused_over_http(client):
    r = client.post("/api/access/verify",
                    json={"token": client.academy.dual_flex_card})
    assert r.json()["granted"] is False


# ---------------------------------------------------------------- selling

def sell(client, **over):
    a = client.academy
    cid = over.pop("cid", a.planless)          # popped before the body is built
    body = {"class_id": a.ballet, "plan": "4 sessions", "sessions_total": 4,
            "price": 900.0, "session_ids": a.ballet_sessions[-4:]}
    body.update(over)
    return client.post(f"/api/clients/{cid}/plan", json=body)


def test_selling_a_plan_books_every_slot(client):
    r = sell(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["booked"] == 4
    assert body["class_name"] == "Ballet Level 8"

    state = access.plan_state(client.academy.repo, body["id"])
    assert state["assigned"] == 4
    assert state["unassigned"] == 0, "a plan with unassigned slots is a promise nobody wrote down"


def test_a_plan_runs_through_the_last_session_it_pays_for(client):
    a = client.academy
    chosen = a.ballet_sessions[-4:]
    body = sell(client).json()
    last = a.repo.max_starts_at(chosen)
    from datetime import date as _d
    assert access.plan_state(a.repo, body["id"])["expires_on"] == \
        _d.fromtimestamp(last).isoformat()


def test_a_typed_end_date_overrides_the_last_session(client):
    body = sell(client, expires_on="2099-01-01").json()
    assert access.plan_state(client.academy.repo, body["id"])["expires_on"] == "2099-01-01"


def test_fewer_sessions_than_slots_is_refused(client):
    r = sell(client, sessions_total=4, session_ids=client.academy.ballet_sessions[-2:])
    assert r.status_code == 400
    assert "assign all 4" in r.json()["detail"]


def test_the_same_session_twice_is_refused(client):
    dup = client.academy.ballet_sessions[-1]
    r = sell(client, session_ids=[dup, dup, dup, dup])
    assert r.status_code == 400
    assert "twice" in r.json()["detail"]


def test_a_session_from_another_class_is_refused(client):
    """A Ballet plan must not quietly pay for a Flexibility session."""
    a = client.academy
    r = sell(client, session_ids=a.ballet_sessions[-3:] + a.flex_sessions[-1:])
    assert r.status_code == 400
    assert "not Ballet Level 8 sessions" in r.json()["detail"]


def test_a_session_the_client_already_holds_is_refused(client):
    a = client.academy
    # Dana already has bookings on her own plan's sessions.
    r = sell(client, cid=a.dual, class_id=a.ballet,
             session_ids=a.ballet_sessions[:4])
    assert r.status_code == 400
    assert "already booked" in r.json()["detail"]


def test_an_unknown_class_is_a_404(client):
    r = sell(client, class_id=9999)
    assert r.status_code == 404


def test_selling_replaces_only_that_classs_previous_plan(client):
    """A client taking two classes keeps the other one running."""
    a = client.academy
    r = sell(client, cid=a.dual, class_id=a.ballet,
             session_ids=a.ballet_sessions[-4:])
    assert r.status_code == 200, r.text

    old = a.repo.get("subscriptions", a.dual_ballet_plan)["active"]
    flex = a.repo.get("subscriptions", a.dual_flex_plan)["active"]
    assert old == 0, "the previous ballet plan is retired"
    assert flex == 1, "the flexibility plan is untouched"


def test_a_past_session_is_booked_straight_to_absent(client):
    """
    Reception writes a plan down after the client started coming, so those
    dates really did pass without them being marked present.
    """
    a = client.academy
    past = [s for s in a.ballet_sessions
            if a.repo.get("sessions", s)["ends_at"] < db.now()]
    r = sell(client, sessions_total=2, session_ids=past[:2])
    assert r.status_code == 200, r.text
    state = access.plan_state(a.repo, r.json()["id"])
    assert state["absent"] == 2
    assert state["remaining"] == 0


# ---------------------------------------------------------------- cards

def test_issuing_a_card_revokes_only_that_classs_previous_one(client):
    """A client taking two classes keeps the other card working."""
    a = client.academy
    r = client.post(f"/api/clients/{a.dual}/card", json={"class_id": a.ballet})
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == a.dual_ballet_card

    live = {row["class_id"]: row["token"] for row in a.repo.find(
        "credentials", {"client_id": a.dual, "revoked_at": None})}
    assert live[a.flex] == a.dual_flex_card, "the flex card is untouched"
    assert live[a.ballet] != a.dual_ballet_card, "the ballet card was replaced"


def test_a_revoked_card_stops_scanning_and_the_new_one_works(client):
    a = client.academy
    new = client.post(f"/api/clients/{a.dual}/card",
                      json={"class_id": a.ballet}).json()["token"]
    assert access.verify(a.repo, a.dual_ballet_card)["granted"] is False
    assert access.verify(a.repo, new)["granted"] is True


def test_a_revoked_credential_is_kept_not_deleted(client):
    """The access log must keep pointing at the credential actually used."""
    a = client.academy
    client.post(f"/api/clients/{a.dual}/card", json={"class_id": a.ballet})
    row = a.repo.find_one("credentials", {"token": a.dual_ballet_card})
    assert row is not None, "the old credential row still exists"
    assert row["revoked_at"] is not None


def test_a_card_for_a_class_with_no_plan_is_refused(client):
    """It would mint a credential that can never check anyone in."""
    a = client.academy
    r = client.post(f"/api/clients/{a.solo_ballet}/card", json={"class_id": a.flex})
    assert r.status_code == 400
    assert "no active Evening Flexibility plan" in r.json()["detail"]


def test_a_card_must_name_a_class(client):
    r = client.post(f"/api/clients/{client.academy.dual}/card", json={})
    assert r.status_code == 400
    assert "belongs to a class" in r.json()["detail"]


def test_the_card_url_is_stamped_so_a_reissue_is_not_cached(client):
    a = client.academy
    url = client.post(f"/api/clients/{a.dual}/card",
                      json={"class_id": a.ballet}).json()["card_url"]
    assert "?v=" in url, "one stable filename per client per class, so it needs a stamp"


# ---------------------------------------------------------------- deleting

def test_deleting_a_plan_takes_its_bookings_with_it(client):
    a = client.academy
    before = access.plan_state(a.repo, a.dual_ballet_plan)

    r = client.delete(f"/api/plans/{a.dual_ballet_plan}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bookings"] == before["assigned"]
    assert body["attended"] == before["present"] + before["absent"]

    assert a.repo.count("bookings", {"subscription_id": a.dual_ballet_plan}) == 0


def test_deleting_a_plan_revokes_that_classs_card(client):
    a = client.academy
    assert client.delete(f"/api/plans/{a.dual_ballet_plan}").json()["cards_revoked"] == 1
    assert access.verify(a.repo, a.dual_ballet_card)["granted"] is False
    assert access.verify(a.repo, a.dual_flex_card) is not None, "the other class is unaffected"


def test_deleting_an_unknown_plan_is_a_404(client):
    assert client.delete("/api/plans/9999").status_code == 404


def test_archiving_a_client_with_upcoming_sessions_is_refused(client):
    """
    Refused, not released. The older behaviour deleted those bookings without
    saying so.
    """
    a = client.academy
    r = client.delete(f"/api/clients/{a.dual}")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "upcoming session" in detail

    upcoming = len(a.repo.client_upcoming(a.dual, db.now()))
    assert str(upcoming) in detail, "the number named is the number on the profile"


def test_archiving_a_client_with_nothing_ahead_revokes_their_card(client):
    a = client.academy
    r = client.delete(f"/api/clients/{a.lapsed}")
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "archive"
    assert a.repo.get("clients", a.lapsed)["active"] == 0


def test_an_unused_slot_with_no_date_does_not_block_archiving(client):
    """
    A lapsed plan holding slots nobody will book must not make a client
    permanently un-archivable.
    """
    a = client.academy
    # Widen the lapsed plan so it owes two sessions nobody has picked dates
    # for. Those are slots, not appointments, and must not block the archive.
    a.repo.update("subscriptions", a.lapsed_plan, {
        "sessions_total": a.repo.get("subscriptions", a.lapsed_plan)["sessions_total"] + 2})
    
    assert access.plan_state(a.repo, a.lapsed_plan)["unassigned"] == 2

    assert client.delete(f"/api/clients/{a.lapsed}").status_code == 200


def test_hard_deleting_a_client_with_attendance_is_refused(client):
    """Losing the record of who attended what is worse than a cluttered list."""
    a = client.academy
    r = client.delete(f"/api/clients/{a.dual}", params={"hard": "true"})
    assert r.status_code == 400
    assert "recorded sessions" in r.json()["detail"]


def test_hard_deleting_a_client_with_no_history_removes_everything(client):
    a = client.academy
    r = client.delete(f"/api/clients/{a.planless}", params={"hard": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "delete"
    assert a.repo.get("clients", a.planless) is None


# ------------------------------------------------------- deleting sessions

def test_deleting_a_session_releases_its_bookings(client):
    a = client.academy
    r = client.delete(f"/api/sessions/{a.today_ballet}")
    assert r.status_code == 200, r.text
    assert r.json()["released"] >= 1
    assert a.repo.count("bookings", {"session_id": a.today_ballet}) == 0


def test_deleting_a_session_pulls_the_plans_expiry_back(client):
    """
    Deleting bookings this way bypasses unbook(), so the plans they funded
    need their expiry refreshed by hand — otherwise a plan goes on claiming
    it runs through a date that no longer exists.
    """
    a = client.academy
    plan = a.dual_ballet_plan
    booked = a.repo.find("bookings", {"subscription_id": plan})
    when = {s["id"]: s["starts_at"] for s in a.repo.find(
        "sessions", {"id": {"in": [b["session_id"] for b in booked]}})}
    latest = max(booked, key=lambda b: when[b["session_id"]])
    before = access.plan_state(a.repo, plan)["expires_on"]

    client.delete(f"/api/sessions/{latest['session_id']}", params={"force": "true"})

    after = access.plan_state(a.repo, plan)["expires_on"]
    assert after < before, f"{before} -> {after}"


def test_a_session_carrying_attendance_is_kept_back(client):
    a = client.academy
    attended = a.repo.find_one("bookings", {"status": "present"})
    r = client.delete(f"/api/sessions/{attended['session_id']}")
    assert r.status_code == 400
    assert "attendance record" in r.json()["detail"]
    assert a.repo.get("sessions", attended["session_id"]) is not None


def test_force_deletes_a_session_carrying_attendance(client):
    a = client.academy
    attended = a.repo.find_one("bookings", {"status": "present"})
    r = client.delete(f"/api/sessions/{attended['session_id']}", params={"force": "true"})
    assert r.status_code == 200, r.text
    assert a.repo.get("sessions", attended["session_id"]) is None


def test_a_bulk_delete_names_what_it_kept_back_rather_than_failing(client):
    """
    Clearing a term with one taught week in the middle should remove the
    other eleven and say why the twelfth stayed.
    """
    a = client.academy
    attended = a.repo.find_one("bookings", {"status": "present"})["session_id"]
    # Sessions nobody was marked present or absent at — the ones that should
    # go. Selected rather than assumed: most of the fixture's past sessions
    # carry attendance, which is the whole point of the one that is kept.
    settled = {b["session_id"] for b in a.repo.find("bookings")
               if b["status"] != "booked"}
    untouched = [s["id"] for s in a.repo.find("sessions", {"class_id": a.ballet})
                 if s["id"] not in settled][:3]
    assert len(untouched) == 3 and attended not in untouched

    r = client.post("/api/sessions/bulk-delete",
                    json={"ids": untouched + [attended]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 3
    assert [b["id"] for b in body["blocked"]] == [attended]
    assert body["blocked"][0]["attendance"] >= 1
    assert body["blocked"][0]["class_name"] == "Ballet Level 8"


def test_a_scan_survives_its_session_being_deleted(client):
    """
    access_events.session_id is nulled rather than cascaded: the row records
    that someone scanned, which stays true after the session is gone.
    """
    a = client.academy
    verified = client.post("/api/access/verify",
                           json={"token": a.dual_ballet_card}).json()
    client.post("/api/access/checkin", json={"event_id": verified["event_id"]})

    client.delete(f"/api/sessions/{a.today_ballet}", params={"force": "true"})

    row = a.repo.get("access_events", verified["event_id"])
    assert row is not None, "the event survives"
    assert row["session_id"] is None
    assert row["client_id"] == a.dual


def test_deleting_an_unknown_session_is_a_no_op(client):
    r = client.post("/api/sessions/bulk-delete", json={"ids": [9999]})
    assert r.status_code == 200
    assert r.json()["deleted"] == 0


# -------------------------------------------------------- archiving a class

def test_archiving_a_class_releases_its_upcoming_sessions(client):
    """
    A class that stops being offered has nothing left to happen for. This is
    the one archive path that cascades a delete into another table.
    """
    a = client.academy
    upcoming = [s["id"] for s in a.repo.find("sessions", {
        "class_id": a.ballet, "status": "scheduled",
        "starts_at": {"gt": db.now()}})]
    assert upcoming, "precondition"

    r = client.delete(f"/api/classes/{a.ballet}")
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "archive"
    assert r.json()["released_sessions"] == len(upcoming)

    assert a.repo.count("sessions", {"id": {"in": upcoming}}) == 0


def test_archiving_a_class_keeps_its_past_sessions_and_attendance(client):
    a = client.academy
    def settled_in_ballet():
        mine = {s["id"] for s in a.repo.find("sessions", {"class_id": a.ballet})}
        return len([b for b in a.repo.find("bookings")
                    if b["session_id"] in mine and b["status"] != "booked"])

    before = settled_in_ballet()
    assert before > 0, "precondition"

    client.delete(f"/api/classes/{a.ballet}")

    after = settled_in_ballet()
    assert after == before, "past attendance is never touched"


def test_archiving_a_class_gives_the_slots_back_as_unassigned(client):
    """The client gets the slot back on their plan rather than it dangling."""
    a = client.academy
    before = access.plan_state(a.repo, a.dual_ballet_plan)

    client.delete(f"/api/classes/{a.ballet}")

    after = access.plan_state(a.repo, a.dual_ballet_plan)
    assert after["unassigned"] > before["unassigned"]
    assert after["remaining"] == before["remaining"], "they are still owed the sessions"


def test_archiving_a_class_hides_it_from_the_list(client):
    a = client.academy
    client.delete(f"/api/classes/{a.ballet}")
    ids = [c["id"] for c in client.get("/api/classes").json()]
    assert a.ballet not in ids
    archived = [c["id"] for c in
                client.get("/api/classes", params={"status": "archived"}).json()]
    assert a.ballet in archived


def test_hard_deleting_a_class_with_attendance_is_refused(client):
    r = client.delete(f"/api/classes/{client.academy.ballet}", params={"hard": "true"})
    assert r.status_code == 400
    assert "attendance records" in r.json()["detail"]


def test_an_unknown_class_is_a_404(client):
    assert client.delete("/api/classes/9999").status_code == 404
