"""
`scripts/class_visitors.py` names who was on a roster only as a visitor.

It is a report rather than a migration, and that is the thing worth holding:
the rule it describes is a filter on two reads, so the script must leave
every booking, plan and attendance mark exactly where it found them. A test
that only checked the wording would pass just as happily on a script that
rewrote a subscription's class on the way past.
"""

import access
import db
import class_visitors
from fixtures import add_session


def _visit(academy, to_class):
    """The profile's "move session" correction: a ballet-funded slot pointed
    at another class's date. `solo_ballet` has never bought flexibility,
    which is what makes her a visitor rather than a student."""
    repo = academy.repo
    there = add_session(repo, to_class, academy.bea, db.now() + 30 * 86400, 1.0)
    mine = repo.find("bookings", {"client_id": academy.solo_ballet})
    moved = mine[-1]
    assert access.move_booking(repo, academy.solo_ballet, moved["session_id"],
                               there, allow_other_class=True)["ok"]
    return there


def test_it_names_the_visitor_and_the_class_that_paid(academy):
    repo = academy.repo
    _visit(academy, academy.flex)

    rows = class_visitors.survey(repo)["rows"]
    mine = [r for r in rows if r["client_id"] == academy.solo_ballet
            and r["class_id"] == academy.flex]

    assert len(mine) == 1, "the visit should be reported exactly once"
    assert mine[0]["from_classes"] == {academy.ballet}
    assert not mine[0]["also_a_student"]
    assert len(mine[0]["visits"]) == 1


def test_a_client_who_also_holds_a_plan_there_is_listed_as_a_student(academy):
    """Her visit changes no count, so reporting her as a lost student would
    describe a roster that did not shrink."""
    repo = academy.repo
    there = add_session(repo, academy.flex, academy.bea,
                        db.now() + 31 * 86400, 1.0)
    ballet_slots = [b for b in repo.find("bookings", {"client_id": academy.dual})
                    if repo.get("subscriptions", b["subscription_id"])["class_id"]
                    == academy.ballet]
    moved = ballet_slots[-1]
    assert access.move_booking(repo, academy.dual, moved["session_id"], there,
                               allow_other_class=True)["ok"]

    rows = class_visitors.survey(repo)["rows"]
    mine = [r for r in rows if r["client_id"] == academy.dual
            and r["class_id"] == academy.flex]

    assert len(mine) == 1
    assert mine[0]["also_a_student"], \
        "she holds a flexibility plan, so she is a student of it either way"


def test_one_class_can_be_asked_about_on_its_own(academy):
    repo = academy.repo
    _visit(academy, academy.flex)

    assert class_visitors.survey(repo, only_class=academy.ballet)["rows"] == []
    assert class_visitors.survey(repo, only_class=academy.flex)["rows"]


def test_it_writes_nothing(academy):
    """The whole point of the script: there is nothing to migrate."""
    repo = academy.repo
    _visit(academy, academy.flex)

    def snapshot():
        return [repo.find(c) for c in
                ("bookings", "subscriptions", "sessions", "clients", "classes")]

    before = snapshot()
    class_visitors.report(class_visitors.survey(repo))

    assert snapshot() == before


def test_an_academy_with_no_visitors_says_so(academy, capsys):
    class_visitors.report(class_visitors.survey(academy.repo))

    assert "No cross-class bookings" in capsys.readouterr().out
