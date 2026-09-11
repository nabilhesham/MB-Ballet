"""
Compile the filter dialect to a MongoDB query document.

Mostly a rename — the dialect is deliberately Mongo-shaped — with one
addition that is the whole reason this file is not four lines long.

**A range or inequality comparison on a nullable field gets an automatic
`$ne: None`.** BSON sorts null before every string and number, so
`{"frozen_until": {"$lte": "2026-09-11"}}` matches every document whose
`frozen_until` is null. SQL does not: `NULL <= 'x'` is NULL, which is not
true, so the row never comes back.

Left to the call sites, this would be a bug that appears only on one backend,
only on rows where a nullable column happens to be empty, and never in a way
that looks wrong — `lift_expired_freezes()` would unfreeze plans that were
never frozen. Adding the guard here means no caller has to know.
"""

from ..filters import validate
from .schema import NULLABLE

_OPS = {"eq": "$eq", "ne": "$ne", "lt": "$lt", "lte": "$lte", "gt": "$gt",
        "gte": "$gte", "in": "$in", "nin": "$nin"}

# The operators whose SQL meaning excludes NULL but whose BSON meaning does
# not. `eq`/`ne`/`in`/`nin` compare by value and agree across both, so they
# are deliberately absent.
_ORDERED = ("lt", "lte", "gt", "gte")


def compile_filter(coll: str, flt: dict) -> dict:
    validate(flt)
    out = {}
    for field, cond in (flt or {}).items():
        if field == "$or":
            out["$or"] = [compile_filter(coll, sub) for sub in cond]
            continue

        if not isinstance(cond, dict):
            out[field] = cond
            continue

        part = {}
        for op, value in cond.items():
            if op == "like":
                # SQL LIKE with % wildcards -> an anchored regex. Only the
                # leading and trailing wildcards are used anywhere in this
                # app, so the middle is escaped literally.
                import re
                body = re.escape(value.strip("%"))
                prefix = "" if value.startswith("%") else "^"
                suffix = "" if value.endswith("%") else "$"
                part["$regex"] = f"{prefix}{body}{suffix}"
                part["$options"] = "i"      # SQLite's LIKE is case-insensitive
                continue
            part[_OPS[op]] = value

        # See the module docstring. Only for ordered comparisons, and only on
        # a field that can actually be null.
        if (any(op in _ORDERED for op in cond)
                and field in NULLABLE.get(coll, ())
                and "$ne" not in part):
            part["$ne"] = None

        out[field] = part
    return out


def compile_sort(sort):
    """`[(field, 1|-1)]` -> the same, with `id` mapped to Mongo's `_id`."""
    return [("_id" if f == "id" else f, d) for f, d in (sort or [])]


def to_mongo_id(field: str) -> str:
    return "_id" if field == "id" else field


def rename_id(flt: dict) -> dict:
    """
    `id` is `_id` in the stored document.

    Integer ids are a domain invariant here, not a storage detail — the card
    token packs one as a uint32 — so `_id` holds the integer itself rather
    than an ObjectId beside it.
    """
    if not flt:
        return {}
    out = {}
    for field, cond in flt.items():
        if field == "$or":
            out["$or"] = [rename_id(sub) for sub in cond]
        else:
            out[to_mongo_id(field)] = cond
    return out
