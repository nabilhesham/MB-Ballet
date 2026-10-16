"""
`--explain`: why every name on one class's roster is on it.

The report says who was a visitor. This answers the question that actually
gets asked when somebody expected a name to disappear and it did not, and it
is written to be readable against the screen: it takes the roster from
`repo.class_students()` -- the method the screen itself calls -- and then
walks the same rule over the same bookings in the open.

The case it exists for is the one that caught the academy out: a booking
with **no plan behind it** keeps its client on the roster, deliberately,
and no filter will ever remove it.
"""

import sqlite3

import pytest

import access
import class_visitors
import db
from fixtures import add_session


def _visit(academy, to_class, who=None):
    """The profile's "move session" correction: a slot funded by one class's
    plan, pointed at another class's date."""
    repo = academy.repo
    who = who or academy.solo_ballet
    there = add_session(repo, to_class, academy.bea, db.now() + 30 * 86400, 1.0)
    mine = repo.find("bookings", {"client_id": who})
    moved = mine[-1]
    assert access.move_booking(repo, who, moved["session_id"], there,
                               allow_other_class=True)["ok"]
    return there, moved


def test_it_accounts_for_every_name_the_screen_shows(academy):
    repo = academy.repo
    _visit(academy, academy.flex)

    out = _run(academy, academy.flex)

    roster = repo.class_students(academy.flex, access.lapsed_cutoff())
    for r in roster:
        assert r["name_en"] in out, f"{r['name_en']} is on screen and unexplained"
    assert "that is a bug in the" not in out


def test_the_visitor_is_named_as_excluded_and_not_as_a_bug(academy):
    _visit(academy, academy.flex)
    people = {c["id"]: c for c in academy.repo.find("clients")}

    out = _run(academy, academy.flex)

    who = people[academy.solo_ballet]["name_en"]
    assert "Excluded by the filter" in out
    assert who in out.split("Excluded by the filter")[1]
    assert "STILL on the roster" not in out


def test_a_booking_with_no_plan_behind_it_is_named_as_what_keeps_her(academy):
    """The case the academy actually hit. There is no other class to read,
    so the session's own class is the only answer -- and no filter removes
    it."""
    repo = academy.repo
    there, moved = _visit(academy, academy.flex)
    repo.update("bookings", moved["id"], {"subscription_id": None})

    out = _run(academy, academy.flex)

    assert "no plan behind it" in out
    assert "KEEPS her on the roster" in out
    assert "STILL on the roster" not in out, \
        "she is genuinely a student of it by the fallback, not a filter bug"


def test_the_plain_report_now_names_those_too(academy):
    """They used to be skipped, which made the commonest "the filter did not
    work" case invisible in the report written to explain the filter."""
    repo = academy.repo
    there, moved = _visit(academy, academy.flex)
    repo.update("bookings", moved["id"], {"subscription_id": None})

    found = class_visitors.survey(repo)
    assert [r["client_id"] for r in found["no_plan"]] == [academy.solo_ballet]
    assert found["rows"] == [], "with no plan it is not a cross-class visit"


@pytest.mark.sqlite_only
def test_sqlite_cannot_hold_a_plan_id_pointing_at_nothing(academy):
    """Which is why `explain` has a branch for it anyway: the foreign key
    makes the state impossible here, and MongoDB has no such constraint. The
    branch must not call it the no-plan fallback -- both backends read the
    class off the plan and get nothing, so such a row is *excluded*, which
    is the opposite of what the fallback does."""
    repo = academy.repo
    _, moved = _visit(academy, academy.flex)

    with pytest.raises(sqlite3.IntegrityError):
        repo.update("bookings", moved["id"], {"subscription_id": 99999})


def test_a_class_can_be_named_rather_than_numbered(academy):
    """The question always arrives as a name, and an id is something
    somebody has to go and look up."""
    repo = academy.repo
    cl = repo.get("classes", academy.flex)

    assert class_visitors.find_class(repo, str(academy.flex)) == (academy.flex, None)
    assert class_visitors.find_class(repo, cl["name"][:6].lower()) == (academy.flex, None)

    found, problem = class_visitors.find_class(repo, "no such class")
    assert found is None and "No class name contains" in problem


def test_an_ambiguous_name_lists_the_matches_rather_than_guessing(academy):
    repo = academy.repo
    repo.insert("classes", {"name": "Pre-Ballet Friday", "colour": "#87438E",
                            "duration_hours": 1.0, "active": 1})
    repo.insert("classes", {"name": "Pre-Ballet Thursday", "colour": "#87438E",
                            "duration_hours": 1.0, "active": 1})

    found, problem = class_visitors.find_class(repo, "pre-ballet")

    assert found is None
    assert "more than one class" in problem
    assert "Friday" in problem and "Thursday" in problem


def _run(academy, class_id, capsys=None):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        class_visitors.explain(academy.repo, class_id)
    return buf.getvalue()
