"""
Auth, admin data management and destructive operations.

Split out of server.py because these routes have different rules: everything
here is either about who you are, or about changing/removing records that the
day-to-day screens deliberately cannot touch.
"""

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

import auth
import db

router = APIRouter()


# ---------------------------------------------------------------- models
class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    current: Optional[str] = None
    new_password: str


class UserIn(BaseModel):
    username: str
    password: str
    name: Optional[str] = None
    role: str = "staff"


class UserPatch(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None


class BalanceIn(BaseModel):
    sessions_total: Optional[int] = None
    sessions_used: Optional[int] = None
    expires_on: Optional[str] = None
    plan: Optional[str] = None
    reason: str = ""


# ---------------------------------------------------------------- deps
def current_user(mbp_session: Optional[str] = Cookie(default=None)) -> dict:
    conn = db.connect()
    try:
        u = auth.user_for_token(conn, mbp_session)
        if u is None:
            raise HTTPException(401, "not signed in")
        return u
    finally:
        conn.close()


def admin_only(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "admin access required")
    return user


# ---------------------------------------------------------------- auth
@router.post("/auth/login")
def do_login(body: LoginIn, response: Response):
    conn = db.connect()
    try:
        auth.purge_expired(conn)
        r = auth.login(conn, body.username, body.password)
        if r is None:
            raise HTTPException(401, "Wrong username or password")
        response.set_cookie(
            auth.SESSION_COOKIE, r["token"], httponly=True, samesite="lax",
            max_age=auth.SESSION_DAYS * 86400, path="/")
        auth.audit(conn, r["user"], "login", "user", r["user"]["id"])
        return {"user": r["user"]}
    finally:
        conn.close()


@router.post("/auth/logout")
def do_logout(response: Response, mbp_session: Optional[str] = Cookie(default=None)):
    conn = db.connect()
    try:
        if mbp_session:
            auth.logout(conn, mbp_session)
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"ok": True}
    finally:
        conn.close()


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return user


@router.post("/auth/password")
def change_password(body: PasswordIn, user: dict = Depends(current_user)):
    problems = auth.password_problems(body.new_password)
    if problems:
        raise HTTPException(400, "Password " + ", ".join(problems))
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        # A forced first change skips the current-password check, since the
        # generated one was printed in a terminal and may not be to hand.
        if not row["must_change"]:
            if not body.current or not auth.verify_password(body.current, row["password_hash"]):
                raise HTTPException(400, "Current password is wrong")
        auth.set_password(conn, user["id"], body.new_password)
        auth.audit(conn, user, "password_change", "user", user["id"])
        return {"ok": True, "note": "signed out on other devices"}
    finally:
        conn.close()


# ---------------------------------------------------------------- users
@router.get("/admin/users")
def list_users(user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        return [auth.public_user(r) for r in
                conn.execute("SELECT * FROM users ORDER BY username").fetchall()]
    finally:
        conn.close()


@router.post("/admin/users")
def add_user(body: UserIn, user: dict = Depends(admin_only)):
    problems = auth.password_problems(body.password)
    if problems:
        raise HTTPException(400, "Password " + ", ".join(problems))
    conn = db.connect()
    try:
        if conn.execute("SELECT 1 FROM users WHERE username=?",
                        (body.username.strip().lower(),)).fetchone():
            raise HTTPException(400, "That username is taken")
        uid = auth.create_user(conn, body.username, body.password, body.role, body.name)
        auth.audit(conn, user, "create", "user", uid, {"username": body.username,
                                                       "role": body.role})
        return {"id": uid}
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.patch("/admin/users/{uid}")
def patch_user(uid: int, body: UserPatch, user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "no such user")
        # Never let the last admin be demoted or switched off — that locks
        # everyone out of the Admin section with no way back in.
        admins = conn.execute(
            "SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1").fetchone()["n"]
        losing_admin = (body.role and body.role != "admin") or body.active is False
        if row["role"] == "admin" and row["active"] and admins <= 1 and losing_admin:
            raise HTTPException(400, "This is the only admin — promote someone else first")

        if body.name is not None:
            conn.execute("UPDATE users SET name=? WHERE id=?", (body.name, uid))
        if body.role is not None:
            if body.role not in ("admin", "staff"):
                raise HTTPException(400, "role must be admin or staff")
            conn.execute("UPDATE users SET role=? WHERE id=?", (body.role, uid))
        if body.active is not None:
            conn.execute("UPDATE users SET active=? WHERE id=?", (1 if body.active else 0, uid))
            if not body.active:
                conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
        conn.commit()
        auth.audit(conn, user, "update", "user", uid, body.model_dump(exclude_none=True))
        return {"ok": True}
    finally:
        conn.close()


@router.post("/admin/users/{uid}/reset")
def reset_password(uid: int, body: PasswordIn, user: dict = Depends(admin_only)):
    problems = auth.password_problems(body.new_password)
    if problems:
        raise HTTPException(400, "Password " + ", ".join(problems))
    conn = db.connect()
    try:
        auth.set_password(conn, uid, body.new_password)
        conn.execute("UPDATE users SET must_change=1 WHERE id=?", (uid,))
        conn.commit()
        auth.audit(conn, user, "reset_password", "user", uid)
        return {"ok": True}
    finally:
        conn.close()


# ---------------------------------------------------------------- deletions
# Anything with history is deactivated rather than deleted. Losing the record
# of who attended what is worse than a slightly cluttered list.

@router.delete("/admin/clients/{cid}")
def delete_client(cid: int, hard: bool = False, user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        c = conn.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "no such client")
        visits = conn.execute("SELECT COUNT(*) n FROM attendance WHERE client_id=?",
                              (cid,)).fetchone()["n"]
        if hard and visits:
            raise HTTPException(400,
                f"{c['name_en']} has {visits} recorded visits — archive instead of deleting")
        if hard:
            conn.execute("DELETE FROM enrolments WHERE client_id=?", (cid,))
            conn.execute("DELETE FROM credentials WHERE client_id=?", (cid,))
            conn.execute("DELETE FROM subscriptions WHERE client_id=?", (cid,))
            conn.execute("DELETE FROM access_events WHERE client_id=?", (cid,))
            conn.execute("DELETE FROM clients WHERE id=?", (cid,))
            action = "delete"
        else:
            conn.execute("UPDATE clients SET active=0 WHERE id=?", (cid,))
            conn.execute("UPDATE credentials SET revoked_at=? WHERE client_id=?"
                         " AND revoked_at IS NULL", (db.now(), cid))
            action = "archive"
        conn.commit()
        auth.audit(conn, user, action, "client", cid, {"name": c["name_en"]})
        return {"ok": True, "action": action}
    finally:
        conn.close()


@router.post("/admin/clients/{cid}/restore")
def restore_client(cid: int, user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        conn.execute("UPDATE clients SET active=1 WHERE id=?", (cid,))
        conn.commit()
        auth.audit(conn, user, "restore", "client", cid)
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/admin/classes/{clid}")
def delete_class(clid: int, hard: bool = False, user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        c = conn.execute("SELECT * FROM classes WHERE id=?", (clid,)).fetchone()
        if not c:
            raise HTTPException(404, "no such class")
        held = conn.execute(
            "SELECT COUNT(*) n FROM attendance a JOIN sessions s ON s.id=a.session_id"
            " WHERE s.class_id=?", (clid,)).fetchone()["n"]
        if hard and held:
            raise HTTPException(400,
                f"{c['name']} has {held} attendance records — archive instead")
        if hard:
            conn.execute("DELETE FROM sessions WHERE class_id=?", (clid,))
            conn.execute("DELETE FROM enrolments WHERE class_id=?", (clid,))
            conn.execute("DELETE FROM classes WHERE id=?", (clid,))
            action = "delete"
        else:
            conn.execute("UPDATE classes SET active=0 WHERE id=?", (clid,))
            conn.execute("DELETE FROM sessions WHERE class_id=? AND starts_at > ?"
                         " AND status='scheduled'", (clid, db.now()))
            action = "archive"
        conn.commit()
        auth.audit(conn, user, action, "class", clid, {"name": c["name"]})
        return {"ok": True, "action": action}
    finally:
        conn.close()


@router.delete("/admin/instructors/{iid}")
def delete_instructor(iid: int, user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        n = conn.execute("SELECT COUNT(*) n FROM classes WHERE instructor_id=? AND active=1",
                         (iid,)).fetchone()["n"]
        if n:
            raise HTTPException(400, f"Still teaching {n} class(es) — reassign them first")
        conn.execute("UPDATE instructors SET active=0 WHERE id=?", (iid,))
        conn.commit()
        auth.audit(conn, user, "archive", "instructor", iid)
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/admin/subscriptions/{sid}")
def delete_subscription(sid: int, user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        s = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "no such plan")
        conn.execute("DELETE FROM subscriptions WHERE id=?", (sid,))
        conn.commit()
        auth.audit(conn, user, "delete", "subscription", sid,
                   {"client_id": s["client_id"], "plan": s["plan"]})
        return {"ok": True}
    finally:
        conn.close()


@router.patch("/admin/subscriptions/{sid}")
def adjust_subscription(sid: int, body: BalanceIn, user: dict = Depends(admin_only)):
    """
    Manual balance correction. Rare, but the alternative is staff inventing
    workarounds, so it exists and is logged with a reason.
    """
    conn = db.connect()
    try:
        s = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "no such plan")
        before = {"total": s["sessions_total"], "used": s["sessions_used"],
                  "expires_on": s["expires_on"], "plan": s["plan"]}
        if body.sessions_total is not None:
            conn.execute("UPDATE subscriptions SET sessions_total=? WHERE id=?",
                         (max(0, body.sessions_total), sid))
        if body.sessions_used is not None:
            conn.execute("UPDATE subscriptions SET sessions_used=? WHERE id=?",
                         (max(0, body.sessions_used), sid))
        if body.expires_on:
            conn.execute("UPDATE subscriptions SET expires_on=? WHERE id=?",
                         (body.expires_on, sid))
        if body.plan:
            conn.execute("UPDATE subscriptions SET plan=? WHERE id=?", (body.plan, sid))
        conn.commit()
        auth.audit(conn, user, "adjust", "subscription", sid,
                   {"before": before, "change": body.model_dump(exclude_none=True)})
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/admin/credentials/{crid}")
def revoke_credential(crid: int, user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        conn.execute("UPDATE credentials SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                     (db.now(), crid))
        conn.commit()
        auth.audit(conn, user, "revoke", "credential", crid)
        return {"ok": True}
    finally:
        conn.close()


# ---------------------------------------------------------------- overview
@router.get("/admin/overview")
def overview(user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        tables = ("clients", "classes", "sessions", "instructors", "subscriptions",
                  "enrolments", "attendance", "credentials", "access_events", "users")
        counts = {t: conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                  for t in tables}
        archived = {
            "clients": conn.execute(
                "SELECT COUNT(*) n FROM clients WHERE active=0").fetchone()["n"],
            "classes": conn.execute(
                "SELECT COUNT(*) n FROM classes WHERE active=0").fetchone()["n"],
            "instructors": conn.execute(
                "SELECT COUNT(*) n FROM instructors WHERE active=0").fetchone()["n"],
        }
        revenue = conn.execute(
            "SELECT COALESCE(SUM(price),0) total, COUNT(*) n FROM subscriptions"
            " WHERE price IS NOT NULL").fetchone()
        return {"counts": counts, "archived": archived,
                "revenue": {"total": revenue["total"], "plans": revenue["n"]}}
    finally:
        conn.close()


@router.get("/admin/audit")
def audit_log(limit: int = 100, user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log ORDER BY at DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        conn.close()


@router.get("/admin/archived")
def archived(user: dict = Depends(admin_only)):
    conn = db.connect()
    try:
        return {
            "clients": [dict(r) for r in conn.execute(
                "SELECT id, name_en, phone FROM clients WHERE active=0"
                " ORDER BY name_en").fetchall()],
            "classes": [dict(r) for r in conn.execute(
                "SELECT id, name, colour FROM classes WHERE active=0 ORDER BY name").fetchall()],
            "instructors": [dict(r) for r in conn.execute(
                "SELECT id, name, specialty FROM instructors WHERE active=0"
                " ORDER BY name").fetchall()],
        }
    finally:
        conn.close()


@router.delete("/admin/sessions/{sid}")
def force_delete_session(sid: int, user: dict = Depends(admin_only)):
    """
    Erase a session that already has attendance on it.

    Refunds every charge first. Deleting without refunding would leave clients
    paying for a class with no record it ever happened — the balance would look
    wrong and nothing would explain why.

    The plain DELETE /api/sessions/{id} still refuses in this case; reaching
    this route is a deliberate admin choice.
    """
    conn = db.connect()
    try:
        s = conn.execute(
            "SELECT s.*, c.name AS class_name FROM sessions s"
            "  JOIN classes c ON c.id = s.class_id WHERE s.id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "no such session")

        refunded = 0
        for a in conn.execute(
                "SELECT * FROM attendance WHERE session_id=? AND charged=1", (sid,)).fetchall():
            conn.execute("UPDATE subscriptions SET sessions_used = sessions_used - 1"
                         " WHERE id=? AND sessions_used > 0", (a["subscription_id"],))
            refunded += 1

        n = conn.execute("SELECT COUNT(*) n FROM attendance WHERE session_id=?",
                         (sid,)).fetchone()["n"]
        conn.execute("DELETE FROM attendance WHERE session_id=?", (sid,))
        conn.execute("UPDATE access_events SET session_id=NULL WHERE session_id=?", (sid,))
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        conn.commit()

        auth.audit(conn, user, "delete", "session", sid, {
            "class": s["class_name"], "starts_at": s["starts_at"],
            "attendance_erased": n, "sessions_refunded": refunded})
        return {"ok": True, "refunded": refunded, "attendance_erased": n}
    finally:
        conn.close()
