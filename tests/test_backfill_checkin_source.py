"""
Filling in `checked_in_by` on rows written before the column existed.

Every row already in the academy's database reads "source not recorded",
which is honest and useless: the question being asked of that column is
whether a stamp is when the client arrived or when somebody took the roster,
and the database can already answer it. `access.check_in()` is the only
thing that writes `access_events.confirmed_at`, and every kiosk path goes
through it -- so an event proves a desk check-in and the absence of one
proves the stamp is not the client arriving.
"""

import backfill_checkin_source as backfill
import access
import db
from fixtures import add_session


def _present_by_hand(repo, academy, session_id, client_id):
    repo.insert("bookings", {"client_id": client_id, "session_id": session_id,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})
    access.set_status(repo, session_id, client_id, "present")
    # Back to how an older row looked.
    b = repo.find_one("bookings", {"session_id": session_id,
                                   "client_id": client_id})
    repo.update("bookings", b["id"], {"checked_in_by": None})
    return b["id"]


def test_an_event_proves_a_desk_check_in(academy):
    repo = academy.repo
    first = access.verify(repo, academy.dual_ballet_card)
    access.check_in(repo, first["event_id"])
    b = repo.find_one("bookings", {"session_id": academy.today_ballet,
                                   "client_id": academy.dual})
    repo.update("bookings", b["id"], {"checked_in_by": None})

    groups = backfill.plan(repo)

    assert b["id"] in groups["kiosk"]
    assert b["id"] not in groups["roster"]


def test_no_event_means_it_was_not_the_client_arriving(academy):
    """The half that answers the question: a mark with no event behind it is
    the roster being taken, whenever that was."""
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    bid = _present_by_hand(repo, academy, sid, academy.planless)

    groups = backfill.plan(repo)

    assert bid in groups["roster"]


def test_the_seeds_own_stamp_is_recognised(academy):
    """`seed.py` writes starts_at + 600 exactly, which nothing a person
    presses lands on -- it is a derived figure, not an observation."""
    repo = academy.repo
    starts = db.now() - 9 * 86400
    sid = add_session(repo, academy.ballet, academy.ana, starts, 1.5)
    bid = repo.insert("bookings", {
        "client_id": academy.planless, "session_id": sid,
        "subscription_id": None, "status": "present",
        "checked_in_at": starts + 600, "created_at": db.now()})

    groups = backfill.plan(repo)

    assert bid in groups["sheet"]


def test_it_writes_nothing_until_asked_and_then_converges(academy):
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    bid = _present_by_hand(repo, academy, sid, academy.planless)

    # A plan alone changes nothing.
    backfill.plan(repo)
    assert repo.get("bookings", bid)["checked_in_by"] is None

    backfill.apply(repo, backfill.plan(repo))
    assert repo.get("bookings", bid)["checked_in_by"] == "roster"

    # Idempotent: a second run has nothing left to look at. (The fixture's
    # own attendance is written with plain SQL and so arrives unlabelled
    # too, exactly as the academy's real rows do -- one pass takes the lot.)
    assert backfill.plan(repo)["total"] == 0


def test_a_row_the_app_already_labelled_is_left_alone(academy):
    """It only ever touches rows where the column is still unset -- a mark
    made since the column existed already says what it is."""
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    repo.insert("bookings", {"client_id": academy.planless, "session_id": sid,
                             "subscription_id": None, "status": "booked",
                             "created_at": db.now()})
    access.set_status(repo, sid, academy.planless, "present")
    b = repo.find_one("bookings", {"session_id": sid,
                                   "client_id": academy.planless})

    groups = backfill.plan(repo)

    assert b["id"] not in groups["kiosk"] + groups["roster"] + groups["sheet"]
    assert repo.get("bookings", b["id"])["checked_in_by"] == "roster"


def test_an_absence_is_not_its_business(academy):
    """No stamp, nothing to attribute."""
    repo = academy.repo
    sid = add_session(repo, academy.ballet, academy.ana, db.now() - 5 * 86400, 1.5)
    bid = repo.insert("bookings", {
        "client_id": academy.planless, "session_id": sid,
        "subscription_id": None, "status": "absent", "created_at": db.now()})

    groups = backfill.plan(repo)

    assert bid not in groups["kiosk"] + groups["roster"] + groups["sheet"]
