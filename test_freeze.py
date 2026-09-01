"""
Freezing: what it releases, what it refuses, and what it puts back.
Run after seeding.

The plans are looked up rather than named, because freezing now needs a plan
of FREEZE_MIN_SESSIONS or more and the seed's plan sizes come from whichever
workbooks are in sheets/.
"""
import db, access
from datetime import date, timedelta

conn = db.connect()
res = []
def check(label, cond, detail=""):
    res.append(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label + (f" — {detail}" if detail else ""))

def freezable(exclude=()):
    """A live plan big enough to freeze, with a card, that is not already frozen."""
    # "x NOT IN (NULL)" is NULL, never true, so an empty list needs a real value.
    marks = ",".join("?" * len(exclude)) or "-1"
    return conn.execute(
        f"SELECT s.* FROM subscriptions s WHERE s.active=1 AND s.frozen_on IS NULL"
        f"   AND s.sessions_total >= ? AND s.id NOT IN ({marks})"
        "  ORDER BY (SELECT COUNT(*) FROM bookings b WHERE b.subscription_id=s.id"
        "              AND b.status='booked') DESC LIMIT 1",
        (access.FREEZE_MIN_SESSIONS, *exclude)).fetchone()


sub = freezable()
assert sub is not None, (
    f"no plan of {access.FREEZE_MIN_SESSIONS}+ sessions in the database — "
    "re-seed before running this")
cid = sub["client_id"]
sid_ = sub["id"]

print("\n1. FREEZING RELEASES FUTURE BOOKINGS")
before = access.plan_state(conn, sid_)
booked_before = conn.execute(
    "SELECT COUNT(*) n FROM bookings WHERE subscription_id=? AND status='booked'",(sid_,)).fetchone()["n"]
r = access.freeze_plan(conn, sid_, reason="travelling")
after = access.plan_state(conn, sid_)
check("freeze succeeded", r["ok"], f"released {r['released']}")
check("released all future bookings", r["released"] == booked_before,
      f"{booked_before} booked -> {r['released']} released")
check("released slots became unassigned",
      after["unassigned"] == before["unassigned"] + r["released"],
      f"unassigned {before['unassigned']} + {r['released']} released = {after['unassigned']}")
check("remaining is unchanged", after["remaining"] == before["remaining"],
      f"{before['remaining']} sessions still owed")
check("marked frozen", after["frozen"] and after["frozen_until"] is None, "open-ended")

print("\n2. A FROZEN PLAN CANNOT BE SCANNED")
tok = conn.execute("SELECT token FROM credentials WHERE client_id=? AND revoked_at IS NULL LIMIT 1",(cid,)).fetchone()["token"]
v = access.verify(conn, tok)
check("scan refused", not v["granted"], v["message"])
check("payload flags frozen", v.get("frozen") is True)

print("\n3. THE SWEEP DOES NOT EAT A FROZEN CLIENT'S SESSIONS")
cls = conn.execute("SELECT id,duration_hours FROM classes LIMIT 1").fetchone()
past = conn.execute("INSERT INTO sessions (class_id,instructor_id,starts_at,duration_hours,status)"
  " VALUES (?,1,?,1.5,'scheduled')",(cls["id"], db.now()-4*3600)).lastrowid
conn.execute("INSERT INTO bookings (client_id,session_id,subscription_id,status,created_at)"
  " VALUES (?,?,?,'booked',?)",(cid,past,sid_,db.now())); conn.commit()
access.settle_past_sessions(conn)
st = conn.execute("SELECT status FROM bookings WHERE session_id=? AND client_id=?",(past,cid)).fetchone()["status"]
check("frozen client's past booking left alone", st == "booked", f"status {st}")

print("\n4. UNFREEZING EXTENDS THE EXPIRY")
old_exp = access.plan_state(conn, sid_)["expires_on"]
conn.execute("UPDATE subscriptions SET frozen_on=? WHERE id=?",
             ((date.today()-timedelta(days=10)).isoformat(), sid_)); conn.commit()
u = access.unfreeze_plan(conn, sid_)
state = access.plan_state(conn, sid_)
check("unfreeze succeeded", u["ok"], f"{u['days']} days added")
check("expiry moved out by the frozen days",
      state["expires_on"] == (date.fromisoformat(old_exp)+timedelta(days=u["days"])).isoformat(),
      f"{old_exp} -> {state['expires_on']}")
check("no longer frozen", not state["frozen"])
check("frozen days recorded", state["frozen_days"] == u["days"], f"{state['frozen_days']}d total")

print("\n5. A DATED FREEZE LIFTS ITSELF")
sub2 = freezable(exclude=(sid_,)) or sub
yesterday = (date.today()-timedelta(days=1)).isoformat()
access.freeze_plan(conn, sub2["id"], until=yesterday,
                   from_date=(date.today()-timedelta(days=8)).isoformat())
check("frozen with an end date", access.plan_state(conn, sub2["id"])["frozen"])
n = access.lift_expired_freezes(conn)
after2 = access.plan_state(conn, sub2["id"])
check("lifted automatically once the date passed", n == 1 and not after2["frozen"],
      f"{after2['frozen_days']} days added")

print("\n6. GUARDS")
access.freeze_plan(conn, sid_)
again = access.freeze_plan(conn, sid_)
check("cannot freeze twice", not again["ok"], again.get("error"))
access.unfreeze_plan(conn, sid_)
none = access.unfreeze_plan(conn, sid_)
check("cannot unfreeze what is not frozen", not none["ok"], none.get("error"))
bad = access.freeze_plan(conn, sid_, until=(date.today()-timedelta(days=3)).isoformat())
check("rejects an end date in the past", not bad["ok"], bad.get("error"))

print("\n7. HISTORY IS KEPT")
h = conn.execute("SELECT * FROM freezes WHERE subscription_id=? ORDER BY id",(sid_,)).fetchall()
check("freeze history recorded", len(h) >= 2, f"{len(h)} entries")
done = [x for x in h if x["ended_on"]]
check("finished freezes carry their length", all(x["days_added"] is not None for x in done),
      f"{len(done)} completed")

print("\n" + "="*52)
print(f"  {sum(res)}/{len(res)} checks passed")
print("="*52 + "\n")
