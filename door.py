"""
The door: remote-unlock an EZVIZ DL05 after a check-in.

Ported from the standalone `unlock_dl05_fast.py`, and it keeps that script's
one real insight -- **the cloud protocol needs exactly two calls**:

    PUT .../DoorLockMgr/QueryRemoteUnlockRandomCode  -> randomCode
    PUT .../DoorLockMgr/RemoteUnlockReq              bindCode + randomCode

The two the script learned to avoid are the ones that make the difference
with a client standing at the counter: a login on every run (the library
refreshes only on a real 401, so the cached session is used as-is), and a
`get_terminals()` lookup for the bind code (stable, so it is cached in a
file beside the token and re-fetched only when missing or rejected). Warm
path: two round trips, not four.

--------------------------------------------------------------------------
**The door is a step after the check-in, never a gate on it.** By the time
anything here runs, the slot is spent and the attendance is written. If the
unlock fails, nothing is lost and nothing is inconsistent -- reception opens
the door by hand, exactly as they did before this existed. Every failure
therefore comes back as `ok=False` with a sentence, and `open_door()` does
not raise: an exception on this path would turn a recorded check-in into a
500 on the kiosk.

**Outbound only.** The laptop calls EZVIZ's cloud, which calls the lock. So
`server.py` keeps binding to 127.0.0.1 and the no-authentication model is
untouched -- nothing connects *to* this machine, there is no port to
forward and no LAN exposure. That is a deliberate departure from the Pi
design CLAUDE.md used to describe, which would have needed the server on
the LAN.

**It depends on the internet**, which is the cost and is worth stating
plainly next to the rule that reception stays on SQLite precisely so the
app keeps working without it. The door is the one feature that cannot; when
the line is down the lock's own keypad, fingerprint and key all still work,
and the verdict is still on screen.

Standard library plus `pyezvizapi`, which is imported **inside** the
functions on purpose. It needs Python 3.12, so requirements.txt installs it
under a version marker and a 3.11 machine legitimately has no door: a
module-level import would turn that into an app that will not start at all.
"""

from __future__ import annotations

import json
import os
import stat
import time

import config

# The one the DL05 accepts for a phone-less remote unlock.
REMOTE_UNLOCK_TYPE = "unLinkIPC"

# Long enough for two cloud round trips on a slow line, short enough that a
# receptionist is not left watching a spinner before reaching for the latch.
TIMEOUT_S = 12


# ---------------------------------------------------------------- settings
def configured() -> bool:
    """
    Is there a lock to talk to?

    One question with one answer: the serial. There is no DOOR_BACKEND
    switch, because "is a lock set up" is already answered by whether the
    academy put the settings in `.env`, and two switches for one fact
    eventually disagree. No serial means the kiosk shows no door line and no
    button, which is exactly how it behaved before any of this existed.
    """
    return bool(config.ezviz_serial())


def _bind_file() -> str:
    """The one this app writes: beside the canonical session file."""
    return config.ezviz_token_file() + ".bind"


def bind_paths() -> list[str]:
    """
    Where to look for the cached terminal bind, canonical first.

    `.bind` is named after the session it belongs to, and the standalone
    script writes `<its own token file>.bind` -- so a machine where somebody
    copied both files in beside the launcher has the pair sitting together
    there, not beside the database. Looking only beside the canonical path
    would make the app re-fetch a bind the script had already proven, and
    pick a different terminal while doing it (see `_fetch_bind_code`).

    Read-only but the first, exactly like `token_paths()`: a bind fetched
    here is written to `_bind_file()`, so the two converge.
    """
    return [p + ".bind" for p in token_paths()]


def _save(path: str, text: str) -> None:
    """0600, because both of these files grant door access."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows and some network drives do not carry POSIX modes. The file
        # is still beside academy.db on a machine behind the counter, which
        # is the same protection the database itself has.
        pass


def token_paths() -> list[str]:
    """
    Where to look for the cached session: the real name, then the one
    Windows leaves you with.

    `config.ezviz_token_file()` is the canonical path and the only one
    anything here *writes* -- but the file arrives on the reception machine
    by hand, and on Windows a name beginning with a dot is genuinely hard to
    produce: Explorer refuses a rename to ".ezviz_token.json" outright, and
    with known extensions hidden a copied file can end up as
    ".ezviz_token.json.txt" with nothing on screen to show it. Either way
    the app reports "no session is saved on this computer" while somebody is
    looking straight at the file they just copied in.

    So a dot-less `ezviz_token.json` beside it is read as a fallback. It is
    deliberately read-only: the first unlock writes the canonical name in
    the `finally` below, which from then on wins here, so the two converge
    rather than drifting. The canonical path is always first, and the same
    fallback applies to a path named in `EZVIZ_TOKEN_FILE` -- Windows
    mangles that name exactly as readily, and one rule is better than two.

    **And the folder the app was started from**, for the same practical
    reason one step along: `unlock_dl05_fast.py` defaults its session to
    `./.ezviz_token.json`, so on a machine where somebody has run that
    script -- which is how the first session gets minted at all -- the file
    is sitting wherever they ran it. In a source checkout that is this same
    folder and the extra entry costs nothing; for a packaged build it is
    the difference between "the script opens the door and the app does not"
    and the app finding the session the script just saved.

    Every entry is read-only but the first. `open_door()`'s `finally`
    always writes `config.ezviz_token_file()`, so a session found anywhere
    else is copied to the canonical place on the first unlock that uses it,
    and the list stops mattering from then on.
    """
    out = []
    for folder, base in _candidates():
        for name in (base, base.lstrip(".")):
            path = os.path.join(folder, name)
            if path not in out:
                out.append(path)
    return out


def _candidates():
    """(folder, filename) pairs, canonical first. See token_paths()."""
    named = config.ezviz_token_file()
    folder, base = os.path.split(named)
    pairs = [(folder, base)]
    launched = config.launch_dir()
    if launched != folder:
        pairs.append((launched, base))
    return pairs


def _load_token() -> dict | None:
    return (_read_token() or (None, None))[0]


def _read_token():
    """The cached session and the path it came from, or None."""
    for path in token_paths():
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh), path
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
    return None


def session_file() -> str:
    """
    Which of `token_paths()` the session is actually being read from, or "".

    The one question the kiosk's "no session is saved on this computer"
    cannot answer and that settles every report of "the standalone script
    opens the door and the app does not": they are either reading the same
    file or they are not. `GET /api/access/door` returns it.
    """
    found = _read_token()
    return found[1] if found else ""


def bind_file_in_use() -> str:
    """Which of `bind_paths()` holds a bind, "env" for EZVIZ_BIND_CODE, or
    "". The path, never the code -- a bind grants door access."""
    if (os.environ.get("EZVIZ_BIND_CODE") or "").strip():
        return "env"
    for path in bind_paths():
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read().strip():
                    return path
        except (FileNotFoundError, OSError):
            continue
    return ""


def _load_bind() -> str | None:
    named = (os.environ.get("EZVIZ_BIND_CODE") or "").strip()
    if named:
        return named
    for path in bind_paths():
        try:
            with open(path, encoding="utf-8") as fh:
                found = fh.read().strip()
        except (FileNotFoundError, OSError):
            continue
        if found:
            return found
    return None


# ------------------------------------------------- no session to unlock with
# Two states, not one, and they need different things done about them.
#
# A packaged build is handed the serial and never the session -- the serial
# identifies a device, the session opens a door -- so the file is copied in
# beside the database by hand. Which means the commonest way to arrive here
# is a build somebody has just installed and not copied it into yet, and the
# only sentence this used to have ("run the unlock script once to sign in")
# named the wrong remedy for it: the session usually already exists, on the
# machine it was signed in on.
#
# The other state is a file that is there and cannot be read -- truncated,
# half-copied, wrong permissions. `_load_token()` answers None to both, so
# the existence check is what tells them apart.
_MISSING = (
    "No EZVIZ session is saved on this computer — copy .ezviz_token.json in "
    "beside the database or beside what you launched, or run the unlock "
    "script once to sign in. Open the door by hand for now",
    "no session file at {path}, and no EZVIZ_EMAIL/EZVIZ_PASSWORD to sign "
    "in with")
_UNREADABLE = (
    "The saved EZVIZ session could not be read — copy .ezviz_token.json in "
    "again beside the database. Open the door by hand for now",
    "{path} is there but is not readable JSON")


def _no_session() -> tuple[str, str]:
    """The sentence for the screen and the one for the log, as a pair."""
    there = [p for p in token_paths() if os.path.exists(p)]
    pair = _MISSING if not there else _UNREADABLE
    # Name what was actually found when something was: "unreadable" about a
    # file at a path that holds nothing is the sentence that wastes an hour.
    # And name *every* place that was looked in when nothing was, because the
    # file is usually sitting in one of the others -- which is the hour this
    # already cost once.
    where = there[0] if there else " or ".join(token_paths())
    return pair[0], pair[1].format(path=where)


def session_problem() -> str:
    """
    Why this machine could not unlock anything yet, or "" when it can.

    The startup banner's half of the question above, so the two cannot
    drift. It exists because the launchers cannot answer it for the machine
    that matters: the reception laptop double-clicks a binary, so a check
    living in `start.sh` or `START.bat` is a check that never runs there,
    and the first anyone hears of a missing session is an amber line under a
    client's verdict with the client standing at the counter.

    A source checkout with EZVIZ_EMAIL/EZVIZ_PASSWORD in `.env` can sign in
    for itself, so a missing file is not a problem there and this says
    nothing -- the same condition `_client()` falls through on.
    """
    if _load_token():
        return ""
    if os.environ.get("EZVIZ_EMAIL") and os.environ.get("EZVIZ_PASSWORD"):
        return ""
    return _no_session()[1]


# ---------------------------------------------------------------- the calls
def _client():
    """
    A client from the cached session, without a network refresh.

    A missing token falls back to a password login, which is the only path
    that can prompt for an SMS code -- and cannot, here, because nothing is
    attached to a console. So it is attempted without one and fails with a
    sentence saying to run the standalone script once, which is where an
    interactive first login belongs.
    """
    from pyezvizapi import EzvizClient

    token = _load_token()
    if token:
        return EzvizClient(token=token, timeout=TIMEOUT_S)

    email = os.environ.get("EZVIZ_EMAIL")
    password = os.environ.get("EZVIZ_PASSWORD")
    if not (email and password):
        plain, technical = _no_session()
        raise DoorTrouble(plain, technical)
    client = EzvizClient(email, password, config.ezviz_region(),
                         timeout=TIMEOUT_S)
    client.login()
    _save(config.ezviz_token_file(), json.dumps(client._token))
    return client


def _fetch_bind_code(client) -> str:
    """
    The terminal bind (a phone's `sign` + `userId`), looked up and cached.

    The lock treats a remote unlock as coming from a bound phone, so one has
    to be named. `EZVIZ_TERMINAL` picks it by name; otherwise the most
    recently active real phone wins, skipping this integration's own
    terminal -- a bind pointing at the thing doing the asking is not a
    phone the lock will accept.
    """
    mine = (client._token or {}).get("feature_code")
    valid = []
    for t in client.get_terminals().get("terminals", []):
        sign = str(t.get("sign") or "").strip()
        user_id = str(t.get("userId") or "").strip()
        if not sign or not user_id:
            continue
        valid.append({
            "sign": sign, "user_id": user_id,
            "name": str(t.get("name") or t.get("terminalName") or user_id),
            "last": str(t.get("lastModifytime") or t.get("lastModifyTime") or ""),
            "is_me": sign == mine,
        })
    if not valid:
        raise DoorTrouble(
            "No phone is linked to the EZVIZ account — open the EZVIZ app on "
            "the academy's phone once; open the door by hand for now",
            "get_terminals returned no usable terminal")

    wanted = (os.environ.get("EZVIZ_TERMINAL") or "").strip()
    if wanted:
        hits = [t for t in valid if wanted.casefold() in t["name"].casefold()]
        if not hits:
            raise DoorTrouble(
                "The door is set up for a phone that is not linked any more "
                "— open the door by hand",
                f"EZVIZ_TERMINAL={wanted!r} matched no bound terminal")
        chosen = max(hits, key=lambda t: t["last"])
    else:
        # Two terminals are excluded rather than one, and the second is the
        # standalone script's own rule, restored here because the script is
        # what has been proven to open this lock: `hassio` is a Home
        # Assistant integration's terminal, not a phone, and the lock
        # refuses a bind naming it exactly as it refuses one naming us. An
        # account carrying one is how the app came to pick a different
        # terminal from the script on the same account and be rejected.
        others = [t for t in valid
                  if not t["is_me"] and t["name"].casefold() != "hassio"]
        chosen = max(others or valid, key=lambda t: t["last"])
    return chosen["sign"] + chosen["user_id"]


class DoorTrouble(Exception):
    """
    A failure with two faces: one for the receptionist, one for the log.

    This project's rule is that a refusal is a sentence somebody can read
    aloud and act on, and that technical detail goes somewhere the screen
    does not show. The door needs both -- "the lock did not open" is what
    reception acts on, and "code 10002 device offline" is what tells the
    next person why, weeks later.
    """

    def __init__(self, plain: str, technical: str = ""):
        super().__init__(plain)
        self.plain = plain
        self.technical = technical


def _checked(resp: dict, what: str) -> dict:
    """
    EZVIZ answers a rejection with HTTP 200 and a code in the body, so the
    library does not raise and a failed unlock would otherwise read as a
    success. Every response goes through here.
    """
    meta = resp.get("meta") or {}
    if meta.get("code") != 200:
        more = (meta.get("moreInfo") or {}).get("msgDetail", "")
        raise DoorTrouble(
            "The lock did not open — open the door by hand",
            f"{what} refused: code {meta.get('code')} "
            f"{meta.get('message', '')} {more}".strip())
    return resp


def _unlock(client, serial: str, bind_code: str) -> None:
    base = f"/v3/iot-feature/action/{serial}/DoorLock/0/DoorLockMgr"
    got = _checked(client._request_json(
        "PUT", f"{base}/QueryRemoteUnlockRandomCode", json_body={"value": {}}),
        "The one-time code request")
    random_code = (got.get("data") or {}).get("randomCode")
    if not random_code:
        raise DoorTrouble("The lock did not open — open the door by hand",
                          f"no randomCode in {got}")
    _checked(client._request_json("PUT", f"{base}/RemoteUnlockReq", json_body={
        "value": {"unLockInfo": {
            "bindCode": bind_code, "randomCode": random_code,
            "type": REMOTE_UNLOCK_TYPE,
            "userName": os.environ.get("EZVIZ_EMAIL") or "",
        }}}), "The unlock")


# ---------------------------------------------------------------- the door
def open_door() -> dict:
    """
    Unlock the door. Returns {"ok", "detail", "ms"} and never raises.

    `detail` is the whole sentence a receptionist reads, and it ends in what
    to do — the same rule the deny messages follow. `technical` is the code
    or exception behind it and is deliberately a separate field: the kiosk
    never shows it, the route logs it, and weeks later it is the only thing
    that explains why.
    """
    t0 = time.perf_counter()

    def done(ok, detail, technical=""):
        return {"ok": ok, "detail": detail, "technical": technical,
                "ms": int((time.perf_counter() - t0) * 1000)}

    serial = config.ezviz_serial()
    if not serial:
        return done(False, "No door is set up")

    try:
        client = _client()
    except ImportError as exc:
        return done(False,
                    "The door software is not installed on this laptop — "
                    "open the door by hand", str(exc))
    except DoorTrouble as exc:
        return done(False, exc.plain, exc.technical)
    except Exception as exc:                              # noqa: BLE001
        return done(False,
                    "Could not reach EZVIZ — check the internet, and open "
                    "the door by hand",
                    f"{exc.__class__.__name__}: {exc}")

    try:
        bind_code = _load_bind()
        fresh_bind = False
        if not bind_code:
            bind_code = _fetch_bind_code(client)
            _save(_bind_file(), bind_code)
            fresh_bind = True

        try:
            _unlock(client, serial, bind_code)
        except DoorTrouble:
            # A rejection is usually a bind the lock no longer accepts -- the
            # phone was re-registered, or the app was reinstalled. Look it up
            # once more and retry; not when it was just fetched, or a genuine
            # refusal would be asked twice for nothing.
            if fresh_bind or os.environ.get("EZVIZ_BIND_CODE"):
                raise
            bind_code = _fetch_bind_code(client)
            _save(_bind_file(), bind_code)
            _unlock(client, serial, bind_code)

        return done(True, "Door opened")
    except DoorTrouble as exc:
        return done(False, exc.plain, exc.technical)
    except Exception as exc:                              # noqa: BLE001
        return done(False,
                    "Could not reach EZVIZ — check the internet, and open "
                    "the door by hand",
                    f"{exc.__class__.__name__}: {exc}")
    finally:
        # The library may have refreshed the session mid-request; keeping it
        # is what makes the next unlock two round trips instead of three.
        try:
            _save(config.ezviz_token_file(), json.dumps(client._token))
        except Exception:                                 # noqa: BLE001
            pass
        try:
            client.close_session()
        except Exception:                                 # noqa: BLE001
            pass
