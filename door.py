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
    return config.ezviz_token_file() + ".bind"


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


def _load_token() -> dict | None:
    try:
        with open(config.ezviz_token_file(), encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _load_bind() -> str | None:
    named = (os.environ.get("EZVIZ_BIND_CODE") or "").strip()
    if named:
        return named
    try:
        with open(_bind_file(), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except (FileNotFoundError, OSError):
        return None


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
        raise DoorTrouble(
            "The door needs setting up again — run the unlock script once to "
            "sign in, then open the door by hand for now",
            "no cached token and no EZVIZ_EMAIL/EZVIZ_PASSWORD")
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
        others = [t for t in valid if not t["is_me"]] or valid
        chosen = max(others, key=lambda t: t["last"])
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
