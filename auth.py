"""
Authentication and authorisation.

Two roles:
    staff  — reception screen and read-only dashboard. What the front desk gets.
    admin  — everything, including the admin console, deletions and user management.

Passwords are hashed with scrypt (memory-hard, in the standard library, no extra
dependency). Sessions are opaque random tokens stored server-side, so revoking
one is a DELETE rather than waiting for a JWT to expire.
"""

import hashlib
import hmac
import json
import os
import secrets
import time

SESSION_DAYS = 14
SESSION_COOKIE = "mbp_session"

# scrypt parameters. n=2**14 keeps a login around 50ms on a laptop, which is
# slow enough to make offline cracking expensive and fast enough not to annoy.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(dk_hex) // 2)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def password_problems(password: str) -> list[str]:
    """Kept deliberately mild. Length does the real work."""
    out = []
    if len(password) < 10:
        out.append("must be at least 10 characters")
    if password.lower() in ("password12", "adminadmin", "1234567890", "mbpallete"):
        out.append("too easy to guess")
    return out


# ---------------------------------------------------------------- users
def create_user(conn, username: str, password: str, role: str = "staff",
                name: str | None = None) -> int:
    if role not in ("admin", "staff"):
        raise ValueError("role must be admin or staff")
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, name, created_at)"
        " VALUES (?,?,?,?,?)",
        (username.strip().lower(), hash_password(password), role,
         name or username, int(time.time())),
    )
    conn.commit()
    return cur.lastrowid


def set_password(conn, user_id: int, password: str) -> None:
    conn.execute("UPDATE users SET password_hash=?, must_change=0 WHERE id=?",
                 (hash_password(password), user_id))
    # Force a fresh login everywhere else after a password change.
    conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
    conn.commit()


def ensure_bootstrap_admin(conn) -> str | None:
    """
    First run has no users, so create one and print the password once.

    Returns the generated password if an account was created, else None. The
    account is flagged must_change so the password cannot survive setup.
    """
    n = conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    if n:
        return None
    password = secrets.token_urlsafe(12)
    uid = create_user(conn, "admin", password, "admin", "Administrator")
    conn.execute("UPDATE users SET must_change=1 WHERE id=?", (uid,))
    conn.commit()
    return password


# ---------------------------------------------------------------- sessions
def login(conn, username: str, password: str) -> dict | None:
    u = conn.execute("SELECT * FROM users WHERE username=? AND active=1",
                     (username.strip().lower(),)).fetchone()
    if u is None:
        # Spend the same time as a real check so timing doesn't leak whether
        # the username exists.
        hash_password(password)
        return None
    if not verify_password(password, u["password_hash"]):
        return None

    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn.execute(
        "INSERT INTO auth_sessions (token, user_id, created_at, expires_at)"
        " VALUES (?,?,?,?)",
        (token, u["id"], now, now + SESSION_DAYS * 86400),
    )
    conn.execute("UPDATE users SET last_login=? WHERE id=?", (now, u["id"]))
    conn.commit()
    return {"token": token, "user": public_user(u)}


def logout(conn, token: str) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
    conn.commit()


def user_for_token(conn, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* , s.expires_at FROM auth_sessions s"
        "  JOIN users u ON u.id = s.user_id"
        " WHERE s.token=? AND u.active=1", (token,)).fetchone()
    if row is None:
        return None
    if row["expires_at"] < int(time.time()):
        conn.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
        conn.commit()
        return None
    return public_user(row)


def public_user(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"],
        "role": row["role"],
        "must_change": bool(row["must_change"]),
        "last_login": row["last_login"],
    }


def purge_expired(conn) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (int(time.time()),))
    conn.commit()


# ---------------------------------------------------------------- audit
def audit(conn, user: dict | None, action: str, entity: str,
          entity_id: int | None = None, detail: dict | None = None) -> None:
    """
    Record an admin action. Deletions and edits are invisible otherwise, and
    'who changed this client's balance' is the question that eventually gets
    asked.
    """
    conn.execute(
        "INSERT INTO audit_log (user_id, username, action, entity, entity_id, detail, at)"
        " VALUES (?,?,?,?,?,?,?)",
        (user["id"] if user else None,
         user["username"] if user else "system",
         action, entity, entity_id,
         json.dumps(detail, ensure_ascii=False) if detail else None,
         int(time.time())),
    )
    conn.commit()
