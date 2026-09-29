"""
The two day-scoped dashboard figures: what came in on a day, and whose plan
wants doing something about on it.

Both replaced a standing condition with a dated one, and the whole value is
in the boundary. "Needs attention" used to mean two sessions or fewer,
expiring inside a week, or slots with no dates — a fair description of a
client and a poor description of a day's work, so the same twenty names sat
on it for a fortnight and the day a plan actually ran out looked exactly
like the four days either side of it. The takings figure had the mirror
problem: it was keyed on when the plan was typed in, so money dated for
Thursday landed in Tuesday's total.

Every case here is one where a careless rewrite stops being equivalent: the
day before, the day itself, the day after.
"""

from datetime import date, datetime, time, timedelta

import pytest

import access
import db
from fixtures import _ins


def _ts(d: date, hour: int = 18) -> int:
    """Local epoch seconds, like the rest of the fixtures — never UTC."""
    return int(datetime.combine(d, time(hour)).timestamp())


def _day(n: int) -> str:
    """n days from today, as an ISO day."""
    return (date.today() + timedelta(days=n)).isoformat()


@pytest.fixture
def desk(repo):
    """
    One class, and a maker for a client with a plan and dated slots.

    Anchored to today rather than to fixed dates, because these rules are
    all about "this day against the one before it" and the dashboard's day
    is whatever today happens to be.
    """
    cls = _ins(repo, "classes", name="Ballet", colour="#87438E",
               duration_hours=1.5, active=1)

    def session_on(offset_days: int, status: str = "scheduled") -> int:
        starts = _ts(date.today() + timedelta(days=offset_days))
        return _ins(repo, "sessions", class_id=cls, starts_at=starts,
                    duration_hours=1.5, ends_at=starts + 5400, status=status)

    def client(name, *, total, slots, expires, price=None, paid_on=None,
               frozen_on=None):
        """`slots` is a list of day offsets the plan's bookings sit on."""
        cid = _ins(repo, "clients", name_en=name, joined_on=_day(-60),
                   created_at=db.now(), active=1)
        sub = _ins(repo, "subscriptions", client_id=cid, class_id=cls,
                   plan="p", sessions_total=total, price=price,
                   paid_on=paid_on, starts_on=_day(-30), expires_on=expires,
                   frozen_on=frozen_on, active=1, created_at=db.now())
        for off in slots:
            _ins(repo, "bookings", client_id=cid, session_id=session_on(off),
                 subscription_id=sub, status="booked", created_at=db.now())
        return cid, sub

    return type("Desk", (), {"cls": cls, "client": staticmethod(client),
                             "session_on": staticmethod(session_on)})


def _flags(repo, day, subs):
    return access.day_attention(repo, day, access.plan_states(repo, subs))


# ------------------------------------------------------------ what came in
def test_the_day_is_the_payment_date_not_the_day_it_was_typed_in(repo, desk):
    """
    The whole point of the figure. Reception writes a plan down when the
    client asks for it and dates the payment for the day it is due, so a
    plan entered today for tomorrow's money belongs to tomorrow.
    """
    desk.client("Pays Tomorrow", total=4, slots=[], expires=_day(30),
                price=1000.0, paid_on=_day(1))

    assert access.day_income(repo, _day(0))["paid"] == 0
    assert access.day_income(repo, _day(1))["paid"] == 1000.0
    assert access.day_income(repo, _day(1))["plans"] == 1


def test_an_unpaid_plan_is_in_no_day_at_all(repo, desk):
    """
    NULL paid_on is "not paid yet", and it must fall out of every window
    rather than counting as zero in one — the same rule the month's figures
    follow, and the null guard both backends carry on a range comparison.
    """
    desk.client("Owes", total=4, slots=[], expires=_day(30), price=500.0)
    for n in (-1, 0, 1):
        assert access.day_income(repo, _day(n))["paid"] == 0
        assert access.day_income(repo, _day(n))["plans"] == 0


def test_money_with_no_amount_is_counted_but_never_as_zero(repo, desk):
    """The ballet sheet writes "yes" rather than a figure. A day reported as
    a clean total while a plan in it carries no amount is a lie the shape of
    a fact, so the count comes back separately."""
    desk.client("Paid, No Amount", total=4, slots=[], expires=_day(30),
                price=None, paid_on=_day(0))
    got = access.day_income(repo, _day(0))
    assert got["paid"] == 0 and got["plans"] == 1 and got["unpriced"] == 1


def test_neighbouring_days_do_not_bleed(repo, desk):
    """A one-day range is half-open, so yesterday and tomorrow stay out."""
    desk.client("Yesterday", total=4, slots=[], expires=_day(30),
                price=100.0, paid_on=_day(-1))
    desk.client("Today", total=4, slots=[], expires=_day(30),
                price=200.0, paid_on=_day(0))
    desk.client("Tomorrow", total=4, slots=[], expires=_day(30),
                price=400.0, paid_on=_day(1))
    assert access.day_income(repo, _day(0))["paid"] == 200.0


# --------------------------------------------------------- whose plan, when
def test_a_plan_ending_that_day_wants_renewing(repo, desk):
    _, sub = desk.client("Ends Today", total=4, slots=[-3, -2], expires=_day(0))
    assert _flags(repo, _day(0), [sub])[sub]["renew"] is True
    assert sub not in _flags(repo, _day(5), [sub])


def test_running_out_belongs_to_the_day_it_happened_and_no_other(repo, desk):
    """
    The decision this rule turns on. "Ran out" is the day the last slot they
    held falls on — not "is at zero", which would keep every client who
    never renewed on every list for ever, which is how the old standing list
    stopped being read.
    """
    _, sub = desk.client("Finishes Today", total=2, slots=[-4, 0],
                         expires=_day(0))

    yesterday = _flags(repo, _day(-1), [sub])
    today = _flags(repo, _day(0), [sub])
    tomorrow = _flags(repo, _day(1), [sub])

    assert today[sub]["ran_out"] is True
    assert today[sub]["remaining_on"] == 0
    # The day before they still had one, which is its own warning.
    assert yesterday[sub]["ran_out"] is False and yesterday[sub]["one_left"]
    # And the day after they are simply gone from it.
    assert sub not in tomorrow


def test_one_left_is_counted_as_of_that_day(repo, desk):
    """Two slots ahead today is one slot ahead once the first is spent."""
    _, sub = desk.client("Two Ahead", total=3, slots=[-1, 2, 5],
                         expires=_day(20))
    assert sub not in _flags(repo, _day(0), [sub])        # two still to come
    assert _flags(repo, _day(2), [sub])[sub]["one_left"] is True
    assert _flags(repo, _day(5), [sub])[sub]["ran_out"] is True


def test_slots_with_no_dates_yet_are_still_theirs(repo, desk):
    """
    A plan nobody has written the dates against has not run out — it has
    sessions waiting to be booked. Counting unassigned slots as spent would
    retire a client who has paid for four more.
    """
    _, sub = desk.client("Half Booked", total=6, slots=[-2, 0],
                         expires=_day(40))
    assert sub not in _flags(repo, _day(0), [sub])


def test_a_cancelled_session_does_not_spend_the_slot(repo, desk):
    """
    Nobody attended a class that did not run, so the slot is still theirs to
    move to another date — and a client whose last class was cancelled has
    not finished their plan.

    With a control beside it, because "not on the list" is the answer for
    more than one reason: the identical plan whose second session really ran
    *is* run out that day, and that is what pins the difference on the
    cancellation rather than on anything else about the shape.
    """
    def two_sessions(name, second_status):
        cid = _ins(repo, "clients", name_en=name, joined_on=_day(-60),
                   created_at=db.now(), active=1)
        sub = _ins(repo, "subscriptions", client_id=cid, class_id=desk.cls,
                   plan="p", sessions_total=2, starts_on=_day(-30),
                   expires_on=_day(30), active=1, created_at=db.now())
        for off, status in ((-3, "scheduled"), (0, second_status)):
            _ins(repo, "bookings", client_id=cid,
                 session_id=desk.session_on(off, status),
                 subscription_id=sub, status="booked", created_at=db.now())
        return sub

    called_off = two_sessions("Cancelled On", "cancelled")
    went_ahead = two_sessions("Ran As Booked", "scheduled")
    flags = _flags(repo, _day(0), [called_off, went_ahead])

    assert flags[went_ahead]["ran_out"] is True
    assert flags.get(called_off, {}).get("ran_out", False) is False


def test_a_frozen_plan_is_never_on_the_list(repo, desk):
    """Deliberately paused is not a problem to chase — the same exclusion the
    standing rule made."""
    _, sub = desk.client("Paused", total=2, slots=[-4, 0], expires=_day(0),
                         frozen_on=_day(-1))
    assert _flags(repo, _day(0), [sub]) == {}


# ----------------------------------------------------------- over the wire
@pytest.fixture
def api(repo, desk):
    """The routers over the same throwaway database these rules ran against."""
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.app) as c:
        yield c


def test_the_dashboard_carries_the_days_takings(api, desk):
    desk.client("Paid Today", total=4, slots=[], expires=_day(30),
                price=750.0, paid_on=_day(0))
    desk.client("Pays Later", total=4, slots=[], expires=_day(30),
                price=900.0, paid_on=_day(2))
    s = api.get("/api/dashboard").json()["stats"]
    assert s["day_income"] == 750.0 and s["day_plans"] == 1


def test_the_cards_list_narrows_to_the_day_asked_for(api, desk):
    """
    The Cards screen's filter and the dashboard's list are the same rule
    asked about different days — which is the point of it living in
    access.day_attention() rather than in either screen.
    """
    desk.client("Finishes Today", total=2, slots=[-4, 0], expires=_day(60))
    desk.client("Finishes Next Week", total=2, slots=[-4, 7], expires=_day(60))
    # Four sessions still ahead of her: on none of these days is she anywhere
    # near the end, so she is on no list at all.
    desk.client("Plenty Left", total=6, slots=[-4, 1, 3, 7, 9, 11],
                expires=_day(60))

    today = {c["name_en"]: c
             for c in api.get(f"/api/clients?status=attention&on={_day(0)}").json()}
    assert set(today) == {"Finishes Today", "Finishes Next Week"}
    # The same day, two different conversations — which is why the row says
    # which of the three it is rather than only how many are left.
    assert today["Finishes Today"]["ran_out"] is True
    assert today["Finishes Next Week"]["one_left"] is True

    later = {c["name_en"]: c
             for c in api.get(f"/api/clients?status=attention&on={_day(7)}").json()}
    # By then the first has been gone from it for a week.
    assert set(later) == {"Finishes Next Week"}
    assert later["Finishes Next Week"]["ran_out"] is True


def test_a_day_that_is_not_a_day_is_refused_in_words(api):
    """A 422 whose detail is a list of dicts is not something a screen can
    show, which is the same reason ClientIn.phone stays Optional."""
    r = api.get("/api/clients?status=attention&on=not-a-date")
    assert r.status_code == 400
    assert "day" in r.json()["detail"].lower()


def test_one_left_needs_a_session_still_to_come(repo, desk):
    """
    A plan holding one slot nobody has put a date against sits at "1 left"
    for ever, so without this it reappeared on every future day the Cards
    screen could be set to — the standing list creeping back in through the
    day filter. Whether they are down to their last is a fact about a day;
    whether anything is still coming is what makes it *that* day's business.
    """
    _, sub = desk.client("Last One Unbooked", total=4, slots=[-9, -6, -3],
                         expires=_day(90))
    assert sub not in _flags(repo, _day(0), [sub])
    assert sub not in _flags(repo, _day(30), [sub])


def test_one_left_still_warns_before_the_session_itself(repo, desk):
    """The other side of it: a dated session still ahead is exactly the
    client to catch now, not on the day they walk in."""
    _, sub = desk.client("One To Come", total=4, slots=[-9, -6, -3, 4],
                         expires=_day(20))
    assert _flags(repo, _day(0), [sub])[sub]["one_left"] is True
    assert _flags(repo, _day(4), [sub])[sub]["ran_out"] is True
    assert sub not in _flags(repo, _day(5), [sub])
