"""Checks the behaviour the last round of changes asked for. Run after seeding."""

from datetime import date, datetime, time as _t

import access
import db

PASS, FAIL = "  PASS  ", "  FAIL  "
results = []


def check(label, cond, detail=""):
    results.append(cond)
    print(f"{PASS if cond else FAIL}{label}" + (f" — {detail}" if detail else ""))


conn = db.connect()

print("\n1. TWO CARDS, ONE PER CLASS")
names = [r["name"] for r in conn.execute(
    "SELECT cl.name FROM credentials cr JOIN classes cl ON cl.id=cr.class_id"
    " WHERE cr.client_id=2 AND cr.revoked_at IS NULL ORDER BY cl.name")]
check("Mona holds a card per class", names == ["Ballet", "Flexibility"], str(names))

print("\n2. THE CARD DECIDES WHICH CLASS")
mid = int(datetime.combine(date.today(), _t.min).timestamp())
cls = conn.execute("SELECT id, duration_hours FROM classes WHERE name='Ballet'").fetchone()
sid = conn.execute(
    "INSERT INTO sessions (class_id,instructor_id,starts_at,duration_hours,status)"
    " VALUES (?,1,?,?,'scheduled')", (cls["id"], mid + 20 * 3600, cls["duration_hours"])).lastrowid
sub = access.active_plan(conn, 2)
conn.execute("INSERT INTO bookings (client_id,session_id,subscription_id,status,created_at)"
             " VALUES (2,?,?, 'booked', ?)", (sid, sub["id"], db.now()))
conn.commit()

tok = lambda cname: conn.execute(
    "SELECT token FROM credentials cr JOIN classes c ON c.id=cr.class_id"
    " WHERE cr.client_id=2 AND c.name=? AND cr.revoked_at IS NULL", (cname,)).fetchone()["token"]

r = access.verify(conn, tok("Ballet"))
check("Ballet card finds the Ballet session", r["granted"], r["message"])
r2 = access.verify(conn, tok("Flexibility"))
check("Flexibility card refuses", not r2["granted"] and "Flexibility" in r2["message"], r2["message"])

print("\n3. EARLY ARRIVAL STILL CHECKS IN")
mins = r["session"]["minutes_until"]
check("matched a session hours ahead", r["session"]["early"] and mins > 60, f"{mins} min early")
before = access.plan_state(conn, sub["id"])["remaining"]
access.check_in(conn, r["event_id"])
after = access.plan_state(conn, sub["id"])["remaining"]
check("one slot consumed", after == before - 1, f"{before} -> {after}")

print("\n4. A SECOND SCAN THE SAME DAY COSTS NOTHING")
r3 = access.verify(conn, tok("Ballet"))
now = access.plan_state(conn, sub["id"])["remaining"]
check("refused without deducting", not r3["granted"] and now == after, r3["message"])

print("\n5. PRESENT / ABSENT ONLY")
res = access.set_status(conn, sid, 2, "excused")
check("excused is rejected", not res["ok"], res.get("error", ""))
res = access.set_status(conn, sid, 2, "absent")
check("absent is accepted", res["ok"])

print("\n6. UNATTENDED PAST SESSIONS BECOME ABSENT")
past = conn.execute(
    "INSERT INTO sessions (class_id,instructor_id,starts_at,duration_hours,status)"
    " VALUES (?,1,?,1.5,'scheduled')", (cls["id"], db.now() - 4 * 3600)).lastrowid
conn.execute("INSERT INTO bookings (client_id,session_id,subscription_id,status,created_at)"
             " VALUES (3,?,NULL,'booked',?)", (past, db.now()))
conn.commit()
access.settle_past_sessions(conn)
st = conn.execute("SELECT status FROM bookings WHERE session_id=?", (past,)).fetchone()["status"]
check("swept to absent", st == "absent", st)

print("\n7. INSTRUCTOR HOURS AND PAY")
i = conn.execute("SELECT * FROM instructors WHERE id=1").fetchone()
t = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
                 " WHERE instructor_id=1 AND status='completed'").fetchone()
earned = t["h"] * i["hourly_rate"]
check("hours x rate computed", earned > 0,
      f"{i['name']}: {t['n']} sessions, {t['h']}h x {i['hourly_rate']} = {earned:.0f} EGP")

print("\n8. SCHEMA SHAPE")
scols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
check("duration in hours, no capacity",
      "duration_hours" in scols and "capacity" not in scols and "duration_min" not in scols)
ccols = {r["name"] for r in conn.execute("PRAGMA table_info(classes)")}
check("classes carry no instructor", "instructor_id" not in ccols)
clcols = {r["name"] for r in conn.execute("PRAGMA table_info(clients)")}
check("client has age / school / joined_on",
      {"age", "school", "joined_on"} <= clcols)
tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check("bookings replaced the old tables",
      "bookings" in tables and not ({"attendance", "enrolments", "session_roster"} & tables))

print("\n" + "=" * 52)
print(f"  {sum(results)}/{len(results)} checks passed")
print("=" * 52 + "\n")
