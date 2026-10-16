"""
A class's student list is who *bought* the class, not who sat in a session.

Once a slot is sold, the corrections on a client's profile may point it at
any class's session — that is how "she missed Ballet on Tuesday and came to
Flexibility on Wednesday instead" is recorded without selling a second plan
(see CLAUDE.md). The session she attended is a Flexibility session, so
counting bookings by the *session's* class put her on the Flexibility roster
as a student of a class she has never bought.

Everything else about that slot is deliberately untouched: it keeps the plan
that paid for it, the deduction stands, the attendance mark stands, and the
session's own roster still names her — she was there.
"""

import access
import db
from fixtures import add_session


# The ballet-only client: she has never bought flexibility, which is what
# makes her the subject. `dual` would be the wrong one -- she holds a real
# flexibility plan, so she is a student of it however her slots move.
def _visit(academy, to_class):
    """Move one of her ballet slots onto `to_class`'s session, the way the
    profile's "move session" correction does."""
    repo = academy.repo
    there = add_session(repo, to_class, academy.bea, db.now() + 30 * 86400, 1.0)
    mine = repo.find("bookings", {"client_id": academy.solo_ballet})
    moved = mine[-1]
    r = access.move_booking(repo, academy.solo_ballet, moved["session_id"],
                            there, allow_other_class=True)
    assert r["ok"], r
    return there, moved


def test_a_cross_class_visitor_is_not_a_student_of_that_class(academy):
    repo = academy.repo
    _visit(academy, academy.flex)

    roster = repo.class_students(academy.flex, "1900-01-01")

    assert academy.solo_ballet not in [r["id"] for r in roster], \
        "a ballet-funded slot sitting on a flexibility date is attendance, " \
        "not membership"


def test_the_classes_list_count_agrees_with_the_roster(academy):
    """A list saying 12 beside a page showing 11 is worse than either."""
    repo = academy.repo
    _visit(academy, academy.flex)

    listed = [c for c in repo.classes_with_counts(1, "1900-01-01")
              if c["id"] == academy.flex][0]
    roster = repo.class_students(academy.flex, "1900-01-01")

    assert listed["students"] == len(roster)


def test_the_client_still_keeps_the_slot_and_its_plan(academy):
    """Nothing about the booking changes — only who the class page lists."""
    repo = academy.repo
    there, moved = _visit(academy, academy.flex)

    b = repo.find_one("bookings", {"client_id": academy.solo_ballet,
                                   "session_id": there})
    assert b is not None
    assert b["subscription_id"] == moved["subscription_id"]
    # And the session's own roster still names her: she was in the room.
    assert academy.solo_ballet in [r["id"]
            for r in repo.session_roster(there)]


def test_a_real_student_of_the_class_is_untouched(academy):
    """The whole point is that only the visitor goes."""
    repo = academy.repo
    _visit(academy, academy.flex)

    roster = [r["id"] for r in repo.class_students(academy.flex, "1900-01-01")]

    assert academy.solo_flex in roster


def test_a_booking_with_no_plan_behind_it_still_counts(academy):
    """Older rows carry no subscription_id, and the session's class is the
    only answer there is for them."""
    repo = academy.repo
    sid = add_session(repo, academy.flex, academy.bea, db.now() - 2 * 86400, 1.0)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "present",
                             "created_at": db.now()})

    roster = [r["id"] for r in repo.class_students(academy.flex, "1900-01-01")]

    assert academy.planless in roster
