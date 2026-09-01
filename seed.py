"""
Seed MB Ballet Academy with demo data.

    ENTRY_SECRET=... python seed.py --force

Wipes academy.db. Never run this once you have real clients.
"""

import os
import random
import sys
from datetime import date, datetime, time, timedelta

import cards
import db
import tokens

INSTRUCTORS = [
    ("Mariam Bassiouny", "01001110001", "Classical ballet", 250),
    ("Nadia Kamal", "01001110003", "Flexibility & conditioning", 200),
    ("Omar Sherif", "01001110002", "Contemporary", 220),
]

# name, colour, duration hours, level, weekday (0=Mon), hour
CLASSES = [
    ("Ballet", "#87438E", 1.5, 5, 16),
    ("Flexibility", "#C9679C", 1.0, 1, 17),
]

CLIENTS = [
    ("Ahmed Hassan",  "01001234501", 11, "Manara Language School", "12 sessions", 12, [0]),
    ("Mona Farid",    "01001234502",  9, "Schutz American School", "8 sessions",   8, [0, 1]),
    ("Karim Adel",    "01001234503", 13, "Victoria College",       "8 sessions",   8, [1]),
    ("Salma Nabil",   "01001234504", 10, "Manara Language School", "12 sessions", 12, [0]),
    ("Youssef Tarek", "01001234505", 12, "El Nasr Boys School",    "16 sessions", 16, [0, 1]),
    ("Layla Mahmoud", "01001234506",  8, "Schutz American School", "12 sessions", 12, [0]),
    ("Hana Ibrahim",  "01001234507",  7, "Sacred Heart",           "8 sessions",   8, [1]),
    ("Tarek Salah",   "01001234508", 14, "Victoria College",       "8 sessions",   8, [0]),
    ("Dina Wagdy",    "01001234509", 10, "Manara Language School", "16 sessions", 16, [0, 1]),
    ("Amir Fouad",    "01001234510", 11, "El Nasr Boys School",    "12 sessions", 12, [0]),
]


def main():
    if os.path.exists("academy.db") and "--force" not in sys.argv:
        print("academy.db exists. Re-run with --force to wipe it.")
        return
    for f in ("academy.db", "academy.db-wal", "academy.db-shm"):
        if os.path.exists(f):
            os.remove(f)

    db.init()
    conn = db.connect()
    today = date.today()

    ins_ids = []
    for name, phone, spec, rate in INSTRUCTORS:
        cur = conn.execute(
            "INSERT INTO instructors (name, phone, specialty, hourly_rate)"
            " VALUES (?,?,?,?)", (name, phone, spec, rate))
        ins_ids.append(cur.lastrowid)

    class_ids = []
    for name, colour, dur, wd, hour in CLASSES:
        cur = conn.execute(
            "INSERT INTO classes (name, colour, duration_hours, description)"
            " VALUES (?,?,?,?,?)",
            (name, colour, dur, f"{name.lower()} at the academy."))
        class_ids.append(cur.lastrowid)

    # Six weeks back, six forward. The instructor lives on each session, so a
    # substitute occasionally takes one.
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=6)
    sessions = {cid: [] for cid in class_ids}
    for w in range(13):
        for cid, (_, _, dur, _, wd, hour) in zip(class_ids, CLASSES):
            d = monday + timedelta(weeks=w, days=wd)
            ts = int(datetime.combine(d, time(hour, 0)).timestamp())
            instructor = ins_ids[0] if cid == class_ids[0] else ins_ids[1]
            if random.random() < 0.12:
                instructor = ins_ids[2]
            status = "completed" if ts + dur * 3600 < db.now() else "scheduled"
            cur = conn.execute(
                "INSERT INTO sessions (class_id, instructor_id, starts_at, duration_hours,"
                " status) VALUES (?,?,?,?,?)", (cid, instructor, ts, dur, status))
            sessions[cid].append((cur.lastrowid, ts))
    conn.commit()

    for name, phone, age, school, plan, total, cls in CLIENTS:
        cur = conn.execute(
            "INSERT INTO clients (name_en, phone, age, school, joined_on, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (name, phone, age, school,
             (today - timedelta(days=random.randint(30, 400))).isoformat(), db.now()))
        cid = cur.lastrowid

        starts = today - timedelta(days=21)
        expires = today + timedelta(days=60)
        cur = conn.execute(
            "INSERT INTO subscriptions (client_id, plan, sessions_total, price, starts_on,"
            " expires_on, created_at) VALUES (?,?,?,?,?,?,?)",
            (cid, plan, total, total * 150, starts.isoformat(), expires.isoformat(), db.now()))
        sub_id = cur.lastrowid

        cutoff = int(datetime.combine(starts, time.min).timestamp())
        pool = []
        for k in cls:
            pool += [s for s in sessions[class_ids[k]] if s[1] >= cutoff]
        pool.sort(key=lambda x: x[1])
        chosen = pool[:total]

        for sid, ts in chosen:
            if ts < db.now():
                status = "present" if random.random() < 0.82 else "absent"
                checked = ts + 600 if status == "present" else None
            else:
                status, checked = "booked", None
            conn.execute(
                "INSERT INTO bookings (client_id, session_id, subscription_id, status,"
                " checked_in_at, created_at) VALUES (?,?,?,?,?,?)",
                (cid, sid, sub_id, status, checked, db.now()))

        for k in cls:
            kid = class_ids[k]
            token = tokens.issue(cid)
            conn.execute(
                "INSERT INTO credentials (client_id, class_id, token, kind, issued_at)"
                " VALUES (?,?,?,?,?)", (cid, kid, token, "card", db.now()))
            klass = conn.execute("SELECT * FROM classes WHERE id=?", (kid,)).fetchone()
            cards.build_card(cid, name, token, plan, expires.isoformat(),
                             class_name=klass["name"], colour=klass["colour"])
        conn.commit()

    counts = {t: conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
              for t in ("instructors", "classes", "sessions", "clients",
                        "subscriptions", "bookings", "credentials")}
    print("\nMB Ballet Academy seeded:")
    for k, v in counts.items():
        print(f"  {k:<15}{v}")
    print("\n  ENTRY_SECRET=... python server.py")
    print("  http://127.0.0.1:8000\n")
    conn.close()


if __name__ == "__main__":
    main()
