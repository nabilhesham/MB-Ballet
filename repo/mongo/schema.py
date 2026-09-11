"""
What a document must contain, and what the database must index.

**Every declared field is written on every insert, even when the value is
None.** That is the single most important rule in this backend, and it is
here rather than left to callers because remembering it at forty write sites
is not a plan.

SQLite makes no distinction between a NULL column and one nobody mentioned —
both read back as `None`. MongoDB does, and the differences are silent and
asymmetric:

    {"f": None}                  matches explicit-null AND missing
    {"f": {"$ne": None}}         matches neither
    {"f": {"$lt": "2026-01-01"}} MATCHES null

That last one has no SQL counterpart at all: `NULL < 'x'` is NULL, which is
not true, so SQLite never returns the row. BSON sorts null before strings, so
Mongo does. It sits one line away from `lift_expired_freezes()`'s
`frozen_until <= today` and the dashboard's `expires <= soon`. The filter
compiler guards the comparison; declaring the fields guards the other half,
by making "missing" a state that cannot occur.

It is also the Mongo counterpart of `db.migrate()`'s `ALTER TABLE ... ADD
COLUMN`: SQLite backfills a new column at write time, and this backfills it
at read time, so a document written before a field existed reads back with
its default rather than absent.
"""

# The 12 collections, their fields, and the value a document gets when the
# caller does not supply one. Derived by hand from db.py's SCHEMA -- the two
# must be kept in step, which tests/test_mongo_schema.py checks.
FIELDS = {
    "instructors": {
        "name": None, "phone": None, "email": None, "specialty": None,
        "hourly_rate": 0.0, "photo_path": None, "active": 1,
    },
    "classes": {
        "name": None, "description": None, "colour": "#87438E",
        "duration_hours": 1.5, "level": None, "instructor_id": None, "active": 1,
    },
    "sessions": {
        "class_id": None, "instructor_id": None, "starts_at": None,
        "duration_hours": 1.5, "ends_at": None, "status": "scheduled",
        "notes": None,
    },
    "clients": {
        "name_en": None, "phone": None, "email": None, "age": None, "dob": None,
        "school": None, "joined_on": None, "photo_path": None, "notes": None,
        "created_at": None, "active": 1,
    },
    "subscriptions": {
        "client_id": None, "class_id": None, "plan": None, "sessions_total": None,
        "sessions_used": 0, "price": None, "payment_note": None, "paid_on": None,
        "notes": None, "months": None, "days_pattern": None, "starts_on": None,
        "expires_on": None, "active": 1, "created_at": None,
        "frozen_on": None, "frozen_until": None, "frozen_days": 0,
    },
    "freezes": {
        "subscription_id": None, "from_date": None, "until_date": None,
        "ended_on": None, "days_added": None, "released": 0, "reason": None,
        "created_at": None,
    },
    "bookings": {
        "client_id": None, "session_id": None, "subscription_id": None,
        "status": "booked", "checked_in_at": None, "created_at": None,
    },
    "credentials": {
        "client_id": None, "class_id": None, "token": None, "kind": "card",
        "issued_at": None, "revoked_at": None,
    },
    "instructor_hours": {
        "instructor_id": None, "work_date": None, "hours": None,
        "source": None, "created_at": None,
    },
    "instructor_hour_adjustments": {
        "instructor_id": None, "adjustment_date": None, "delta_hours": None,
        "note": None, "created_at": None,
    },
    "settings": {"key": None, "value": None},
    "access_events": {
        "client_id": None, "credential_id": None, "session_id": None,
        "scanned_at": None, "decision": None, "reason": None,
        "confirmed_at": None, "session_spent": 0, "source": "scan",
    },
}

# Fields that may legitimately hold null. A range or inequality comparison on
# one of these gets an automatic `$ne: None` -- see repo/mongo/filters.py.
NULLABLE = {coll: {f for f, default in fields.items() if default is None}
            for coll, fields in FIELDS.items()}

# Mirrors db.py's CREATE INDEX lines, plus the three UNIQUE constraints that
# SQLite declares inline. The uniques are not optional: insert_ignore() is
# only idempotent because they exist, and without them seed.py would
# silently duplicate every booking it re-imports.
INDEXES = {
    "credentials": [(["token"], True), (["client_id", "class_id"], False)],
    "access_events": [(["scanned_at"], False), (["client_id"], False),
                      (["session_id"], False)],
    "sessions": [(["starts_at"], False), (["ends_at"], False),
                 (["class_id"], False), (["instructor_id"], False)],
    "subscriptions": [(["client_id", "class_id", "active"], False),
                      (["starts_on"], False)],
    "bookings": [(["client_id", "session_id"], True), (["client_id"], False),
                 (["session_id"], False), (["subscription_id"], False)],
    "freezes": [(["subscription_id"], False)],
    "instructor_hours": [(["instructor_id", "work_date"], True),
                         (["work_date"], False)],
    "instructor_hour_adjustments": [(["instructor_id", "adjustment_date"], False)],
    "clients": [(["joined_on"], False)],
    "settings": [(["key"], True)],
}


def fill(coll: str, doc: dict) -> dict:
    """A document with every declared field present. See the module docstring."""
    out = dict(FIELDS[coll])
    out.update(doc)
    return out


def normalise(coll: str, doc: dict) -> dict:
    """
    A document read back, with `_id` renamed to `id` and any field missing
    from an older write filled in with its default.
    """
    if doc is None:
        return None
    out = dict(FIELDS[coll])
    out.update(doc)
    out["id"] = out.pop("_id", doc.get("_id"))
    return out


def ensure_indexes(database) -> None:
    for coll, specs in INDEXES.items():
        for keys, unique in specs:
            database[coll].create_index([(k, 1) for k in keys], unique=unique)
