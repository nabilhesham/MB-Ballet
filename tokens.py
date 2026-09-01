"""
Signed access tokens for the client entry system.

Design constraints that drove this:
  - Base32 alphabet only (A-Z, 2-7). No punctuation, so a scanner cannot
    mangle the payload when Windows is on an Arabic keyboard layout.
  - Fixed 40 characters, no padding. Easy to eyeball, easy to validate.
  - The token says WHO, not WHETHER. Permission always comes from the DB.

Layout (25 bytes -> 40 base32 chars):
    1  byte   version
    4  bytes  client_id      (uint32)
    4  bytes  issued_at      (uint32 unix seconds)
    4  bytes  nonce          (random, makes each issue unique)
   12  bytes  hmac-sha256 truncated
"""

import base64
import hmac
import hashlib
import os
import struct
import time

VERSION = 1
TOKEN_LEN = 40
_HEADER = ">BIII"          # 13 bytes
_SIG_LEN = 12


def _secret() -> bytes:
    key = os.environ.get("ENTRY_SECRET")
    if not key:
        raise RuntimeError("ENTRY_SECRET is not set. Put it in .env, never in git.")
    return key.encode()


def _sign(header: bytes) -> bytes:
    return hmac.new(_secret(), header, hashlib.sha256).digest()[:_SIG_LEN]


def issue(client_id: int, issued_at: int | None = None) -> str:
    """Create a token for a client. Returns 40 uppercase base32 characters."""
    issued_at = int(issued_at or time.time())
    nonce = struct.unpack(">I", os.urandom(4))[0]
    header = struct.pack(_HEADER, VERSION, client_id, issued_at, nonce)
    raw = header + _sign(header)
    return base64.b32encode(raw).decode().rstrip("=")


class TokenError(Exception):
    """Token is malformed, forged, or stale."""


def parse(token: str, max_age: int | None = None) -> dict:
    """
    Validate signature and shape. Raises TokenError on anything suspicious.

    max_age: seconds. Pass None for printed cards (they never expire on their
    own -- the subscription in the DB controls that). Pass 60 for rotating
    tokens shown in the client's phone app.
    """
    token = token.strip().upper()

    if len(token) != TOKEN_LEN:
        raise TokenError("wrong length")
    try:
        pad = "=" * (-len(token) % 8)      # 40 chars is already a multiple of 8
        raw = base64.b32decode(token + pad)
    except Exception:
        raise TokenError("not valid base32")
    if len(raw) != 25:
        raise TokenError("wrong payload size")

    header, sig = raw[:13], raw[13:]

    # Constant-time compare -- never use == on a signature.
    if not hmac.compare_digest(sig, _sign(header)):
        raise TokenError("bad signature")

    version, client_id, issued_at, nonce = struct.unpack(_HEADER, header)
    if version != VERSION:
        raise TokenError(f"unknown version {version}")

    age = int(time.time()) - issued_at
    if max_age is not None and age > max_age:
        raise TokenError(f"expired {age - max_age}s ago")
    if age < -300:
        raise TokenError("issued in the future -- check the clock")

    return {
        "client_id": client_id,
        "issued_at": issued_at,
        "nonce": nonce,
        "age_seconds": age,
    }
