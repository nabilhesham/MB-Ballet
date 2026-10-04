"""
The door: unlocking the academy's EZVIZ DL05 after a check-in.

`pyezvizapi` is stubbed throughout. It needs Python 3.12 and is installed
under a marker, so a suite that imported it for real would be a suite that
cannot run on a 3.11 machine -- and the point being protected here is not
EZVIZ's wire format anyway. It is the shape around it:

  * a failure is an answer, never an exception, because by the time the
    door is touched the check-in is already recorded;
  * that answer has two halves -- a sentence reception acts on, and the
    code behind it, which the kiosk never shows;
  * a rejection arrives as HTTP 200 with a code in the body, so it must not
    read as a success;
  * the cached bind is retried once when the lock stops accepting it;
  * the token and bind files are written 0600 beside academy.db, and the
    token is re-saved even when the unlock failed.
"""

import json
import os
import stat
import sys
import types

import pytest

import config
import door


# --------------------------------------------------------------- the stub
class FakeClient:
    """Records what door.py asks for, and answers what it is told to."""

    def __init__(self, *a, **kw):
        self.init_args, self.init_kw = a, kw
        self.calls = []
        self.closed = False
        self._token = {"session_id": "s", "feature_code": "me"}
        # Set by each test before use.
        self.random_code = "4242"
        self.unlock_code = 200
        self.terminals = [{"sign": "SIGN", "userId": "UID", "name": "iphone",
                           "lastModifytime": "2"}]
        self.unlock_attempts = 0

    def _request_json(self, method, path, json_body=None, **kw):
        self.calls.append((method, path, json_body))
        if path.endswith("QueryRemoteUnlockRandomCode"):
            return {"meta": {"code": 200}, "data": {"randomCode": self.random_code}}
        if path.endswith("RemoteUnlockReq"):
            self.unlock_attempts += 1
            code = (self.unlock_code(self.unlock_attempts)
                    if callable(self.unlock_code) else self.unlock_code)
            return {"meta": {"code": code, "message": "no",
                             "moreInfo": {"msgDetail": "bad bind"}}}
        raise AssertionError(f"unexpected call {path}")

    def get_terminals(self, **kw):
        self.calls.append(("get_terminals", None, None))
        return {"terminals": self.terminals}

    def login(self, sms_code=None):
        self.calls.append(("login", None, None))
        return {}

    def close_session(self):
        self.closed = True


@pytest.fixture
def ez(tmp_path, monkeypatch):
    """A stubbed pyezvizapi, and a token file in a throwaway folder."""
    made = []

    class Auth(Exception):
        pass

    mod = types.ModuleType("pyezvizapi")

    def factory(*a, **kw):
        c = FakeClient(*a, **kw)
        made.append(c)
        return c

    mod.EzvizClient = factory
    mod.EzvizAuthVerificationCode = Auth
    monkeypatch.setitem(sys.modules, "pyezvizapi", mod)

    monkeypatch.setenv("EZVIZ_LOCK_SERIAL", "bk5433560")
    monkeypatch.setenv("EZVIZ_TOKEN_FILE", str(tmp_path / ".ezviz_token.json"))
    monkeypatch.delenv("EZVIZ_BIND_CODE", raising=False)
    monkeypatch.delenv("EZVIZ_TERMINAL", raising=False)
    monkeypatch.delenv("EZVIZ_EMAIL", raising=False)
    monkeypatch.delenv("EZVIZ_PASSWORD", raising=False)
    # A cached session, which is the warm path every unlock but the first
    # takes.
    (tmp_path / ".ezviz_token.json").write_text(
        json.dumps({"session_id": "s", "feature_code": "me"}))
    return types.SimpleNamespace(made=made, dir=tmp_path,
                                 token=tmp_path / ".ezviz_token.json",
                                 bind=tmp_path / ".ezviz_token.json.bind")


# --------------------------------------------------------------- is there one
def test_no_serial_means_no_door(monkeypatch):
    """The state a laptop with no lock settings is in, and the one that has
    to behave exactly as the app did before any of this existed."""
    monkeypatch.delenv("EZVIZ_LOCK_SERIAL", raising=False)
    assert door.configured() is False
    r = door.open_door()
    assert r["ok"] is False and "No door" in r["detail"]


def test_a_serial_is_the_whole_question(monkeypatch):
    monkeypatch.setenv("EZVIZ_LOCK_SERIAL", "BK5433560")
    assert door.configured() is True


def test_the_token_lives_beside_the_database(monkeypatch):
    """Not in the working directory: a packaged build reads its assets from a
    temporary folder that is wiped on exit, so a session cached there is
    gone every time the app closes."""
    monkeypatch.delenv("EZVIZ_TOKEN_FILE", raising=False)
    assert os.path.dirname(config.ezviz_token_file()) == config.app_dir()


# --------------------------------------------------------------- the unlock
def test_the_warm_path_is_two_calls(ez):
    ez.bind.write_text("SIGNUID")
    r = door.open_door()
    assert r["ok"] is True, r
    assert r["detail"] == "Door opened"
    paths = [c[1] for c in ez.made[0].calls]
    assert len(paths) == 2, paths
    assert paths[0].endswith("QueryRemoteUnlockRandomCode")
    assert paths[1].endswith("RemoteUnlockReq")


def test_the_serial_is_upper_cased_into_the_path(ez):
    """`.env` may hold it in either case; the cloud path is the serial."""
    ez.bind.write_text("SIGNUID")
    door.open_door()
    assert "/BK5433560/DoorLock/" in ez.made[0].calls[0][1]


def test_the_one_time_code_is_carried_into_the_unlock(ez):
    ez.bind.write_text("SIGNUID")
    door.open_door()
    sent = ez.made[0].calls[1][2]["value"]["unLockInfo"]
    assert sent["randomCode"] == "4242"
    assert sent["bindCode"] == "SIGNUID"
    assert sent["type"] == door.REMOTE_UNLOCK_TYPE


def test_a_rejection_is_not_a_success(ez):
    """EZVIZ answers a refusal with HTTP 200 and a code in the body, so the
    library does not raise. Without the check this read as an open door."""
    ez.bind.write_text("SIGNUID")
    client = None

    def one_client(*a, **kw):
        nonlocal client
        client = FakeClient(*a, **kw)
        client.unlock_code = 10002
        return client

    sys.modules["pyezvizapi"].EzvizClient = one_client
    r = door.open_door()
    assert r["ok"] is False
    # The receptionist's half says what to do and carries no code...
    assert r["detail"] == "The lock did not open — open the door by hand"
    # ...and the code is kept, in the half the kiosk never shows.
    assert "10002" in r["technical"]


def test_nothing_raises_out_of_it(ez):
    """The property the whole design rests on. A check-in is already
    recorded by the time this runs; an exception here would turn a spent
    slot into a 500 on the kiosk."""
    def explode(*a, **kw):
        raise ValueError("the network fell over")

    sys.modules["pyezvizapi"].EzvizClient = explode
    r = door.open_door()
    assert r["ok"] is False
    assert "open the door by hand" in r["detail"]
    assert "fell over" in r["technical"]
    assert isinstance(r["ms"], int)


def test_a_missing_library_says_so(ez, monkeypatch):
    """A 3.11 laptop installs everything but pyezvizapi -- see the marker in
    requirements.txt -- and must still run, with no door."""
    monkeypatch.delitem(sys.modules, "pyezvizapi")
    monkeypatch.setattr(door, "_load_token", lambda: {"session_id": "s"})

    import builtins
    real = builtins.__import__

    def no_ezviz(name, *a, **kw):
        if name == "pyezvizapi":
            raise ImportError("No module named 'pyezvizapi'")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_ezviz)
    r = door.open_door()
    assert r["ok"] is False
    assert "not installed" in r["detail"]
    assert "open the door by hand" in r["detail"]


# --------------------------------------------------------------- the bind
def test_the_bind_is_looked_up_once_and_cached(ez):
    assert not ez.bind.exists()
    assert door.open_door()["ok"] is True
    assert ez.bind.read_text() == "SIGNUID"
    assert any(c[0] == "get_terminals" for c in ez.made[0].calls)

    # Second unlock: no lookup at all, which is the round trip this saves.
    door.open_door()
    assert not any(c[0] == "get_terminals" for c in ez.made[1].calls)


def test_a_stale_bind_is_refetched_once(ez):
    """The phone was re-registered or the app reinstalled. The first unlock
    is refused, the bind is looked up again, and the second succeeds --
    without the receptionist knowing anything happened."""
    ez.bind.write_text("OLDBIND")
    client = None

    def one(*a, **kw):
        nonlocal client
        client = FakeClient(*a, **kw)
        client.unlock_code = lambda n: 10002 if n == 1 else 200
        return client

    sys.modules["pyezvizapi"].EzvizClient = one
    assert door.open_door()["ok"] is True
    assert client.unlock_attempts == 2
    assert ez.bind.read_text() == "SIGNUID"


def test_a_freshly_fetched_bind_is_not_retried(ez):
    """A genuine refusal must not be asked twice for nothing."""
    client = None

    def one(*a, **kw):
        nonlocal client
        client = FakeClient(*a, **kw)
        client.unlock_code = 10002
        return client

    sys.modules["pyezvizapi"].EzvizClient = one
    assert door.open_door()["ok"] is False
    assert client.unlock_attempts == 1


def test_a_bind_named_in_the_env_is_never_looked_up(ez, monkeypatch):
    monkeypatch.setenv("EZVIZ_BIND_CODE", "FROMENV")
    door.open_door()
    assert not any(c[0] == "get_terminals" for c in ez.made[0].calls)
    assert ez.made[0].calls[1][2]["value"]["unLockInfo"]["bindCode"] == "FROMENV"


def test_this_integrations_own_terminal_is_not_chosen(ez):
    """A bind pointing at the thing doing the asking is not a phone the lock
    accepts. `feature_code` on the cached token is how it is recognised."""
    def one(*a, **kw):
        c = FakeClient(*a, **kw)
        c.terminals = [
            {"sign": "me", "userId": "U1", "name": "Hassio", "lastModifytime": "9"},
            {"sign": "SIGN", "userId": "UID", "name": "iphone", "lastModifytime": "2"},
        ]
        ez.made.append(c)
        return c

    sys.modules["pyezvizapi"].EzvizClient = one
    door.open_door()
    assert ez.bind.read_text() == "SIGNUID"


def test_a_home_assistant_terminal_is_not_chosen_either(ez):
    """
    The standalone script skips a terminal named `hassio` as well as its own,
    and the script is what has been proven to open this lock. It is an
    integration's terminal rather than a phone, and the lock refuses a bind
    naming one -- so an account carrying it is how the app came to choose a
    different terminal from the script and be rejected on the same account.
    """
    def one(*a, **kw):
        c = FakeClient(*a, **kw)
        c.terminals = [
            {"sign": "HA", "userId": "U1", "name": "hassio",
             "lastModifytime": "9"},
            {"sign": "SIGN", "userId": "UID", "name": "iphone",
             "lastModifytime": "2"},
        ]
        ez.made.append(c)
        return c

    sys.modules["pyezvizapi"].EzvizClient = one
    door.open_door()
    assert ez.bind.read_text() == "SIGNUID"


def test_hassio_is_still_used_when_it_is_all_there_is(ez):
    """Excluding every candidate would turn a door that might open into one
    that cannot. The named phone wins when there is one; this is the
    fallback, not the rule."""
    def one(*a, **kw):
        c = FakeClient(*a, **kw)
        c.terminals = [{"sign": "HA", "userId": "U1", "name": "hassio",
                        "lastModifytime": "9"}]
        ez.made.append(c)
        return c

    sys.modules["pyezvizapi"].EzvizClient = one
    assert door.open_door()["ok"] is True
    assert ez.bind.read_text() == "HAU1"


def test_a_bind_beside_the_launcher_is_read_rather_than_refetched(ez, monkeypatch,
                                                                 tmp_path):
    """
    `.bind` is named after the session it belongs to, so the pair the script
    wrote travels together -- and a machine where both were copied in beside
    the launcher has them there, not beside the database. Looking only in the
    canonical place meant re-fetching a bind the script had already proven,
    and choosing a terminal again while doing it.
    """
    launched = tmp_path / "beside-the-exe"
    launched.mkdir()
    (launched / ".ezviz_token.json.bind").write_text("FROMSCRIPT")
    monkeypatch.setattr(config, "launch_dir", lambda: str(launched))

    assert door.open_door()["ok"] is True
    assert not any(c[0] == "get_terminals" for c in ez.made[0].calls)
    sent = ez.made[0].calls[1][2]["value"]["unLockInfo"]["bindCode"]
    assert sent == "FROMSCRIPT"


def test_no_bound_phone_says_what_to_do(ez):
    def one(*a, **kw):
        c = FakeClient(*a, **kw)
        c.terminals = []
        ez.made.append(c)
        return c

    sys.modules["pyezvizapi"].EzvizClient = one
    r = door.open_door()
    assert r["ok"] is False
    assert "EZVIZ app" in r["detail"]
    assert "open the door by hand" in r["detail"]


# --------------------------------------------------------------- the files
def test_both_files_are_private(ez):
    door.open_door()
    for f in (ez.token, ez.bind):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f


def test_the_session_is_kept_even_when_the_unlock_failed(ez):
    """The library may have refreshed it mid-request, and throwing that away
    would make the next unlock pay a login it does not need."""
    ez.bind.write_text("SIGNUID")

    def one(*a, **kw):
        c = FakeClient(*a, **kw)
        c.unlock_code = 10002
        c._token = {"session_id": "refreshed", "feature_code": "me"}
        ez.made.append(c)
        return c

    sys.modules["pyezvizapi"].EzvizClient = one
    assert door.open_door()["ok"] is False
    assert json.loads(ez.token.read_text())["session_id"] == "refreshed"


def test_the_http_session_is_always_closed(ez):
    ez.bind.write_text("SIGNUID")
    door.open_door()
    assert ez.made[0].closed is True


def test_a_session_never_copied_in_says_to_copy_it_in(ez):
    """
    The state a freshly installed build is in, and the one this actually
    reached the academy in: the binary carries the serial (baked from .env)
    and never the session, so until somebody copies `.ezviz_token.json` in
    beside the database there is nothing to unlock with.

    It used to answer "run the unlock script once to sign in", which is the
    wrong remedy for the commonest cause -- the session usually already
    exists, on whichever machine signed in. An interactive login is still
    offered, second, for when it genuinely does not.
    """
    ez.token.unlink()
    r = door.open_door()
    assert r["ok"] is False
    assert "copy .ezviz_token.json in" in r["detail"]
    assert "run the unlock script" in r["detail"]
    # Plain on screen, specific in the log -- including *where* it looked,
    # which is the one thing that settles this from a console.
    assert str(ez.token) in r["technical"]
    assert "EZVIZ_EMAIL" in r["technical"]


def test_a_session_that_cannot_be_read_is_a_different_sentence(ez):
    """
    A file that is there and unusable is a different problem with a
    different fix -- half-copied, truncated, wrong permissions -- and
    `_load_token()` answers None to that exactly as it does to a missing
    file. Collapsing the two would tell somebody to copy in a file they are
    looking straight at.
    """
    ez.token.write_text("{ not json")
    r = door.open_door()
    assert r["ok"] is False
    assert "could not be read" in r["detail"]
    assert "run the unlock script" not in r["detail"]
    assert str(ez.token) in r["technical"]


def test_the_failure_is_still_an_answer_not_an_exception(ez):
    """Whichever of the two it is, the check-in is already recorded."""
    ez.token.unlink()
    assert door.open_door()["ok"] is False
    ez.token.write_text("nonsense")
    assert door.open_door()["ok"] is False


def test_a_baked_email_alone_cannot_sign_in(ez, monkeypatch):
    """
    What makes baking the address into the binary safe.

    `academy.spec` bakes EZVIZ_EMAIL and drops EZVIZ_PASSWORD, so a packaged
    build carries exactly this pair -- and with it, door.py must still refuse
    to attempt a login and must still report the session as missing. The
    address is a login name; the password is the credential.
    """
    ez.token.unlink()
    monkeypatch.setenv("EZVIZ_EMAIL", "reception@example.com")
    monkeypatch.delenv("EZVIZ_PASSWORD", raising=False)

    assert door.session_problem()                   # not silenced by the email
    r = door.open_door()
    assert r["ok"] is False
    assert "copy .ezviz_token.json in" in r["detail"]
    assert ez.made == []                            # no client was constructed


def test_the_unlock_names_the_account_it_came_from(ez, monkeypatch):
    """
    `userName` in the unlock payload, which is the reason the email is baked
    at all.

    The standalone script this was ported from sends the account's address
    there. Left out of the bake, a packaged build sent `""` instead -- the
    one field where the binary reception runs differed from the script the
    protocol was worked out against, and nothing on this side can prove the
    lock ignores it.
    """
    monkeypatch.setenv("EZVIZ_EMAIL", "reception@example.com")
    ez.bind.write_text("SIGNUID")
    assert door.open_door()["ok"] is True

    sent = ez.made[0].calls[1][2]["value"]["unLockInfo"]
    assert sent["userName"] == "reception@example.com"


# ------------------------------------------------- what the banner reports
def test_the_banner_is_quiet_when_there_is_a_session(ez):
    assert door.session_problem() == ""


def test_the_banner_names_a_missing_session_and_where_it_looked(ez):
    """
    The launchers cannot answer this for the machine that matters: the
    reception laptop double-clicks a binary and runs neither of them. So the
    app says it itself, at startup, instead of the first news being an amber
    line under a client's verdict.
    """
    ez.token.unlink()
    problem = door.session_problem()
    assert str(ez.token) in problem


def test_the_banner_is_quiet_when_it_can_sign_in_for_itself(ez, monkeypatch):
    """
    A source checkout with the credentials in `.env` mints its own session
    on the first unlock, so a missing file is not a problem there -- the
    same condition `_client()` falls through on, which is why both ask one
    function.
    """
    ez.token.unlink()
    monkeypatch.setenv("EZVIZ_EMAIL", "reception@example.com")
    monkeypatch.setenv("EZVIZ_PASSWORD", "x")
    assert door.session_problem() == ""


# --------------------------------------------------------------- over HTTP
@pytest.fixture
def client(academy):
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.app) as c:
        yield c


def test_the_kiosk_can_ask_whether_there_is_a_door(client, monkeypatch):
    monkeypatch.delenv("EZVIZ_LOCK_SERIAL", raising=False)
    assert client.get("/api/access/door").json()["configured"] is False
    monkeypatch.setenv("EZVIZ_LOCK_SERIAL", "BK5433560")
    assert client.get("/api/access/door").json()["configured"] is True


def test_the_door_endpoint_names_the_file_it_looks_for(client, monkeypatch):
    """
    The question that costs the most time when the door will not open, and
    the one the kiosk cannot answer: *which* file is it looking for.

    "No EZVIZ session is saved on this computer" gets read standing next to
    the file somebody has just copied in. The path lived only in
    `technical`, which the kiosk never shows, and on the startup banner,
    which a double-clicked binary scrolls past. One URL in a browser settles
    it now.
    """
    monkeypatch.setenv("EZVIZ_LOCK_SERIAL", "BK5433560")
    r = client.get("/api/access/door").json()
    assert r["token_file"].endswith(".ezviz_token.json")
    assert any(p.endswith("/ezviz_token.json") for p in r["also_accepted"])
    # The session's *contents* never leave: a path is not a credential, and
    # this one grants door access.
    assert set(r) == {"configured", "token_file", "also_accepted", "using",
                      "bind_file", "session"}


# ------------------------------------------- the name Windows leaves you with
def test_a_session_saved_without_the_leading_dot_is_still_found(ez):
    """
    Explorer refuses a rename to a name starting with a dot, and hides known
    extensions, so a session copied onto the reception PC by hand can easily
    end up as `ezviz_token.json`. The app said "no session is saved on this
    computer" while somebody was looking straight at the file.
    """
    saved = json.loads(ez.token.read_text())
    ez.token.unlink()
    (ez.dir / "ezviz_token.json").write_text(json.dumps(saved))

    assert door.session_problem() == ""
    ez.bind.write_text("SIGNUID")
    assert door.open_door()["ok"] is True


def test_the_real_name_wins_when_both_are_there(ez):
    """
    The fallback is read-only -- an unlock writes the canonical name, so a
    machine that started with the dot-less one ends up holding both. The
    one door.py maintains is the one it must read.
    """
    (ez.dir / "ezviz_token.json").write_text(json.dumps({"session_id": "stale"}))
    ez.token.write_text(json.dumps({"session_id": "live", "feature_code": "me"}))
    assert door._load_token()["session_id"] == "live"


# ------------------------------------------ the folder the script is run from
def test_the_folder_the_app_was_started_from_is_searched_too(ez, monkeypatch):
    """
    `unlock_dl05_fast.py` defaults to `./.ezviz_token.json`, so the session
    that proves the lock works is sitting next to whatever was run -- and
    reception copies the file beside the thing they double-click, not beside
    the database. Looking only where `config.ezviz_token_file()` points said
    "no session is saved on this computer" about a file in plain sight.
    """
    monkeypatch.setattr(config, "launch_dir", lambda: "/elsewhere")
    paths = door.token_paths()
    assert paths[0] == str(ez.token)
    assert "/elsewhere/.ezviz_token.json" in paths
    assert "/elsewhere/ezviz_token.json" in paths


def test_a_session_beside_the_launcher_opens_the_door(ez, monkeypatch, tmp_path):
    """The script's own default location, which is the one that has been
    proven to work on the academy's machine."""
    saved = json.loads(ez.token.read_text())
    ez.token.unlink()
    launched = tmp_path / "beside-the-exe"
    launched.mkdir()
    (launched / ".ezviz_token.json").write_text(json.dumps(saved))
    monkeypatch.setattr(config, "launch_dir", lambda: str(launched))

    assert door.session_problem() == ""
    assert door.session_file() == str(launched / ".ezviz_token.json")
    ez.bind.write_text("SIGNUID")
    assert door.open_door()["ok"] is True
    # The unlock writes the canonical name, so the next start finds it
    # beside the database whatever folder the launcher was run from.
    assert json.loads(ez.token.read_text())["session_id"]


def test_a_name_with_no_dot_to_strip_is_not_looked_for_twice(ez, monkeypatch):
    """The fallback is the same path when there is no leading dot."""
    monkeypatch.setenv("EZVIZ_TOKEN_FILE", str(ez.dir / "somewhere.json"))
    named = str(ez.dir / "somewhere.json")
    paths = door.token_paths()
    assert paths[0] == named
    assert paths.count(named) == 1


def test_a_failed_unlock_is_still_a_200(client, monkeypatch):
    """Not an error to raise: the check-in that led here is already
    recorded, and a 500 would make a spent slot look like a failed
    request."""
    monkeypatch.setattr(door, "open_door",
                        lambda: {"ok": False, "detail": "nope", "ms": 3})
    r = client.post("/api/access/door/open")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_the_open_call_takes_no_arguments(client, monkeypatch):
    """Which is what lets the manual button and the automatic unlock after a
    check-in be the same request rather than two that can drift."""
    seen = []
    monkeypatch.setattr(door, "open_door",
                        lambda: (seen.append(1), {"ok": True, "detail": "Door opened",
                                                  "ms": 1})[1])
    assert client.post("/api/access/door/open").json()["ok"] is True
    assert len(seen) == 1
