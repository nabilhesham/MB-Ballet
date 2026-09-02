"""
Cursor helpers shared by every router.

Kept here rather than duplicated per-file because every route in every
router uses one or both of these.
"""


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None
