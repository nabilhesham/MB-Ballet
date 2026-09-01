"""
Checks that a plan, its sessions and its card all belong to the same class,
and that the freeze rule respects the plan size. Run after seeding.

Nothing here names a class: the seed is built from whichever workbooks are in
sheets/, so a test looking for a class called "Ballet" would only be testing
which term happened to be imported. The subjects are looked up instead.
"""

import access
import db

results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label + (f" — {detail}" if detail else ""))


conn = db.connect()

print("\n1. A PLAN BELONGS TO ONE CLASS")
no_class = conn.execute(
    "SELECT COUNT(*) n FROM subscriptions WHERE class_id IS NULL").fetchone()["n"]
check("every plan names a class", no_class == 0, f"{no_class} without one")

# Someone taking exactly one class: their other classes must come back empty.
solo = conn.execute(
    "SELECT client_id, MIN(class_id) class_id FROM subscriptions WHERE active=1"
    " GROUP BY client_id HAVING COUNT(DISTINCT class_id) = 1 LIMIT 1").fetchone()
check("found a single-class client", solo is not None)
if solo:
    cid, mine = solo["client_id"], solo["class_id"]
    name = conn.execute("SELECT name_en FROM clients WHERE id=?", (cid,)).fetchone()["name_en"]
    other = conn.execute("SELECT id, name FROM classes WHERE id != ? AND active=1 LIMIT 1",
                         (mine,)).fetchone()
    check(f"{name} has a plan in their own class",
          access.active_plan(conn, cid, mine) is not None)
    if other:
        check(f"and none in {other['name']}",
              access.active_plan(conn, cid, other["id"]) is None,
              "a plan lookup must never fall back to another class")

print("\n2. A PLAN ONLY PAYS FOR ITS OWN CLASS'S SESSIONS")
wrong = conn.execute(
    "SELECT COUNT(*) n FROM bookings b JOIN sessions s ON s.id=b.session_id"
    "  JOIN subscriptions sub ON sub.id=b.subscription_id"
    " WHERE sub.class_id IS NOT NULL AND s.class_id != sub.class_id").fetchone()["n"]
check("no booking crosses classes", wrong == 0, f"{wrong} mismatched")

print("\n3. A CARD ONLY EXISTS WHERE THERE IS A PLAN")
orphan = conn.execute(
    "SELECT COUNT(*) n FROM credentials cr"
    " WHERE cr.revoked_at IS NULL AND cr.class_id IS NOT NULL"
    "   AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.client_id=cr.client_id"
    "                     AND s.class_id=cr.class_id AND s.active=1)").fetchone()["n"]
check("no card without a matching plan", orphan == 0, f"{orphan} orphaned")

print("\n4. THE CARD'S CLASS SELECTS THE PLAN")
two = conn.execute(
    "SELECT client_id FROM subscriptions WHERE active=1 GROUP BY client_id"
    " HAVING COUNT(DISTINCT class_id) > 1 LIMIT 1").fetchone()
check("a client with two classes exists", two is not None)
if two:
    c2 = two["client_id"]
    held = conn.execute(
        "SELECT DISTINCT class_id FROM subscriptions WHERE client_id=? AND active=1",
        (c2,)).fetchall()
    a, b = held[0]["class_id"], held[1]["class_id"]
    pa, pb = access.active_plan(conn, c2, a), access.active_plan(conn, c2, b)
    check("two classes give two different plans", pa["id"] != pb["id"],
          f"plan {pa['id']} vs {pb['id']}")
    check("each plan names its own class",
          pa["class_id"] == a and pb["class_id"] == b)

print("\n5. FREEZING NEEDS A PLAN OF 12 OR MORE")
big = conn.execute("SELECT * FROM subscriptions WHERE sessions_total >= ? AND active=1"
                   "   AND frozen_on IS NULL LIMIT 1",
                   (access.FREEZE_MIN_SESSIONS,)).fetchone()
small = conn.execute("SELECT * FROM subscriptions WHERE sessions_total < ? AND active=1"
                     " LIMIT 1", (access.FREEZE_MIN_SESSIONS,)).fetchone()
check("a short plan exists to test with", small is not None)
if small:
    allowed, why = access.can_freeze(small)
    check(f"a {small['sessions_total']}-session plan cannot be frozen", not allowed, why)
    r = access.freeze_plan(conn, small["id"])
    check("the API refuses it too", not r["ok"], r.get("error", ""))

check("a 12+ plan exists to test with", big is not None)
if big:
    allowed, _ = access.can_freeze(big)
    check(f"a {big['sessions_total']}-session plan can be frozen", allowed)
    r = access.freeze_plan(conn, big["id"], reason="test")
    check("freezing works", r["ok"], f"released {r.get('released')}")

    print("\n6. FREEZING ONE CLASS LEAVES THE OTHERS ALONE")
    others = conn.execute(
        "SELECT * FROM subscriptions WHERE client_id=? AND id!=? AND active=1",
        (big["client_id"], big["id"])).fetchall()
    if others:
        still = all(not access.plan_state(conn, o["id"])["frozen"] for o in others)
        check("the client's other plan is untouched", still,
              f"{len(others)} other plan(s)")
    else:
        print("        (this client only takes one class — nothing to compare)")
    access.unfreeze_plan(conn, big["id"])

print("\n7. THE UI AND THE API AGREE ON FREEZING")
mismatch = 0
for r in conn.execute("SELECT id FROM subscriptions WHERE active=1").fetchall():
    st = access.plan_state(conn, r["id"])
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (r["id"],)).fetchone()
    if st["can_freeze"] != access.can_freeze(sub)[0]:
        mismatch += 1
check("every plan reports the same verdict the API would give", mismatch == 0,
      f"{mismatch} disagreed")

print("\n" + "=" * 54)
print(f"  {sum(results)}/{len(results)} checks passed")
print("=" * 54 + "\n")
