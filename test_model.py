"""
Checks the behaviour the last round of changes asked for. Run after seeding.

Everything it needs is looked up rather than hardcoded — the client who holds
two cards, the classes those cards are for, an instructor with completed
sessions. The seed comes from the academy's own spreadsheets now, so a test
naming a particular client or a class called "Ballet" only tests which
workbook happened to be in sheets/.
"""

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
# Someone taking two classes: they are what the one-card-per-class rule is for.
subject = conn.execute(
    "SELECT cr.client_id, COUNT(DISTINCT cr.class_id) n FROM credentials cr"
    " WHERE cr.revoked_at IS NULL AND cr.class_id IS NOT NULL"
    " GROUP BY cr.client_id HAVING n >= 2 LIMIT 1").fetchone()
check("someone holds a card in more than one class",
      subject is not None, "" if subject else "seed has no dual-class client")
if subject is None:
    raise SystemExit("cannot run: no client holds two cards")

cid = subject["client_id"]
who = conn.execute("SELECT name_en FROM clients WHERE id=?", (cid,)).fetchone()["name_en"]
held = conn.execute(
    "SELECT cl.id, cl.name, cl.duration_hours FROM credentials cr"
    "  JOIN classes cl ON cl.id = cr.class_id"
    " WHERE cr.client_id=? AND cr.revoked_at IS NULL ORDER BY cl.name", (cid,)).fetchall()
mine, other = held[0], held[1]
check(f"{who} holds one card per class", len(held) == len({c["id"] for c in held}),
      ", ".join(c["name"] for c in held))

print("\n2. THE CARD DECIDES WHICH CLASS")
mid = int(datetime.combine(date.today(), _t.min).timestamp())
cls = mine
sid = conn.execute(
    "INSERT INTO sessions (class_id,instructor_id,starts_at,duration_hours,status)"
    " VALUES (?,1,?,?,'scheduled')", (cls["id"], mid + 20 * 3600, cls["duration_hours"])).lastrowid
sub = access.active_plan(conn, cid, cls["id"])
conn.execute("INSERT INTO bookings (client_id,session_id,subscription_id,status,created_at)"
             " VALUES (?,?,?, 'booked', ?)", (cid, sid, sub["id"], db.now()))
conn.commit()

tok = lambda class_id: conn.execute(
    "SELECT token FROM credentials WHERE client_id=? AND class_id=?"
    "   AND revoked_at IS NULL", (cid, class_id)).fetchone()["token"]

r = access.verify(conn, tok(mine["id"]))
check(f"the {mine['name']} card finds that class's session", r["granted"], r["message"])
r2 = access.verify(conn, tok(other["id"]))
check(f"the {other['name']} card refuses",
      not r2["granted"] and other["name"] in r2["message"], r2["message"])
check("the card reports its own class's plan",
      r.get("plan") == sub["plan"], f"{r.get('plan')!r} vs {sub['plan']!r}")

print("\n3. EARLY ARRIVAL STILL CHECKS IN")
mins = r["session"]["minutes_until"]
check("matched a session hours ahead", r["session"]["early"] and mins > 60, f"{mins} min early")
before = access.plan_state(conn, sub["id"])["remaining"]
access.check_in(conn, r["event_id"])
after = access.plan_state(conn, sub["id"])["remaining"]
check("one slot consumed", after == before - 1, f"{before} -> {after}")

print("\n4. A SECOND SCAN THE SAME DAY COSTS NOTHING")
r3 = access.verify(conn, tok(mine["id"]))
now = access.plan_state(conn, sub["id"])["remaining"]
check("refused without deducting", not r3["granted"] and now == after, r3["message"])

print("\n5. PRESENT / ABSENT ONLY")
res = access.set_status(conn, sid, cid, "excused")
check("excused is rejected", not res["ok"], res.get("error", ""))
res = access.set_status(conn, sid, cid, "absent")
check("absent is accepted", res["ok"])

print("\n6. UNATTENDED PAST SESSIONS BECOME ABSENT")
past = conn.execute(
    "INSERT INTO sessions (class_id,instructor_id,starts_at,duration_hours,status)"
    " VALUES (?,1,?,1.5,'scheduled')", (cls["id"], db.now() - 4 * 3600)).lastrowid
someone = conn.execute("SELECT id FROM clients WHERE id != ? LIMIT 1", (cid,)).fetchone()["id"]
conn.execute("INSERT INTO bookings (client_id,session_id,subscription_id,status,created_at)"
             " VALUES (?,?,NULL,'booked',?)", (someone, past, db.now()))
conn.commit()
access.settle_past_sessions(conn)
st = conn.execute("SELECT status FROM bookings WHERE session_id=?", (past,)).fetchone()["status"]
check("swept to absent", st == "absent", st)

print("\n7. INSTRUCTOR HOURS AND PAY")
i = conn.execute(
    "SELECT i.* FROM instructors i JOIN sessions s ON s.instructor_id = i.id"
    " WHERE s.status='completed' AND i.hourly_rate > 0 LIMIT 1").fetchone()
check("an instructor has completed sessions and a rate", i is not None)
if i:
    t = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(duration_hours),0) h FROM sessions"
                     " WHERE instructor_id=? AND status='completed'", (i["id"],)).fetchone()
    earned = t["h"] * i["hourly_rate"]
    check("hours x rate computed", earned > 0,
          f"{i['name']}: {t['n']} sessions, {t['h']}h x {i['hourly_rate']} = {earned:.0f} EGP")

    # The salary sheet is the other half of the picture: hours are stored per
    # day and the pay is derived, never written down, so a corrected rate
    # re-prices the month instead of leaving a stale total behind.
    logged = conn.execute(
        "SELECT COALESCE(SUM(hours),0) h, COUNT(*) d FROM instructor_hours"
        " WHERE instructor_id=?", (i["id"],)).fetchone()
    check("payroll hours imported from the salary sheet", logged["d"] > 0,
          f"{logged['d']} days, {logged['h']}h = {logged['h'] * i['hourly_rate']:.0f} EGP")

print("\n8. THE MONTH'S INTAKE")
m = access.month_intake(conn, max(r["m"] for r in conn.execute(
    "SELECT DISTINCT substr(joined_on,1,7) m FROM clients WHERE joined_on IS NOT NULL")))
check("new clients counted for the month", m["new_clients"] > 0,
      f"{m['month']}: {m['new_clients']} new, {m['new_clients_prev']} the month before")
check("their revenue is a subset of the month's",
      m["new_revenue"] <= m["revenue"], f"{m['new_revenue']} of {m['revenue']} EGP")
check("unpriced plans counted, not treated as zero",
      m["unpriced"] + m["plans"] > 0 and m["unpriced"] <= m["plans"],
      f"{m['unpriced']} of {m['plans']} plans carry no price")

print("\n9. SCHEMA SHAPE")
scols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
check("duration in hours, no capacity",
      "duration_hours" in scols and "capacity" not in scols and "duration_min" not in scols)
ccols = {r["name"] for r in conn.execute("PRAGMA table_info(classes)")}
check("classes carry no instructor", "instructor_id" not in ccols)
clcols = {r["name"] for r in conn.execute("PRAGMA table_info(clients)")}
check("client has age / school / joined_on",
      {"age", "school", "joined_on"} <= clcols)
subcols = {r["name"] for r in conn.execute("PRAGMA table_info(subscriptions)")}
check("plan records what the sheet said about payment",
      {"price", "payment_note", "months", "days_pattern"} <= subcols)
tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check("bookings replaced the old tables",
      "bookings" in tables and not ({"attendance", "enrolments", "session_roster"} & tables))
check("payroll hours have a table of their own", "instructor_hours" in tables)

print("\n" + "=" * 52)
print(f"  {sum(results)}/{len(results)} checks passed")
print("=" * 52 + "\n")
