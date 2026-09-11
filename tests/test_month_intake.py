"""
The dashboard's two intake figures, pinned at the month boundaries.

These exist to hold `month_intake()` still while its range logic is rewritten
from `substr(col,1,7) BETWEEN ?` to a half-open comparison on the whole date.
The two are equivalent for well-formed ISO dates, and every case below is one
where a careless rewrite would stop being equivalent: the last day of a
month, the first day of the next, a client whose plan was bought outside the
window they joined in.
"""

import pytest

import access
import db
from fixtures import _ins


@pytest.fixture
def intake(conn):
    """
    A tiny academy with dates chosen by hand, not derived from today.

    Deliberately not the `academy` fixture: these assertions are about exact
    counts at specific boundaries, and a fixture anchored to the current date
    cannot express "the last day of July".
    """
    cls = _ins(conn, "classes", name="Ballet", colour="#87438E",
               duration_hours=1.5, active=1)

    def client_with_plan(name, joined_on, starts_on, price):
        cid = _ins(conn, "clients", name_en=name, joined_on=joined_on,
                   created_at=db.now(), active=1)
        _ins(conn, "subscriptions", client_id=cid, class_id=cls, plan="p",
             sessions_total=4, price=price, starts_on=starts_on,
             expires_on=starts_on, active=1, created_at=db.now())
        return cid

    # Straddling the July/August boundary by one day on each side.
    client_with_plan("July Last", "2026-07-31", "2026-07-31", 100.0)
    client_with_plan("Aug First", "2026-08-01", "2026-08-01", 200.0)
    client_with_plan("Aug Last", "2026-08-31", "2026-08-31", 400.0)
    client_with_plan("Sep First", "2026-09-01", "2026-09-01", 800.0)

    # Joined in August, but did not buy until September. The reason the two
    # figures are scoped differently: she is an August client whose money
    # arrives in September.
    client_with_plan("Late Payer", "2026-08-14", "2026-09-02", 1600.0)

    # Joined in August, price never written down. Counted, never zero.
    client_with_plan("Unpriced", "2026-08-20", "2026-08-20", None)

    # Archived clients are out of both figures entirely.
    gone = client_with_plan("Archived", "2026-08-05", "2026-08-05", 9999.0)
    conn.execute("UPDATE clients SET active=0 WHERE id=?", (gone,))

    conn.commit()
    return conn


def test_a_single_month_takes_both_its_edges(intake):
    """31 August is in August; 1 September is not."""
    m = access.month_intake(intake, "2026-08")
    # Aug First, Aug Last, Late Payer, Unpriced. Not July Last, Sep First,
    # or the archived one.
    assert m["new_clients"] == 4, m


def test_revenue_is_every_plan_sold_inside_the_window(intake):
    m = access.month_intake(intake, "2026-08")
    # 200 + 400 + unpriced(0). Late Payer's plan starts in September.
    assert m["revenue"] == 600.0, m


def test_earned_from_them_follows_the_clients_out_of_the_window(intake):
    """
    A client who joined in August and paid in September belongs to August.

    Filtering this by the plan's date as well is what once made August read
    "4 new clients, 0 EGP" while three of them had paid.
    """
    m = access.month_intake(intake, "2026-08")
    # 200 + 400 + 1600 (bought in September by an August joiner) + unpriced.
    assert m["new_revenue"] == 2200.0, m


def test_an_unpriced_plan_is_counted_separately_not_as_zero(intake):
    m = access.month_intake(intake, "2026-08")
    assert m["unpriced"] == 1, m
    assert m["plans"] == 3, m          # Aug First, Aug Last, Unpriced


def test_an_archived_client_is_in_neither_figure(intake):
    m = access.month_intake(intake, "2026-08")
    assert m["new_clients"] == 4
    assert m["revenue"] == 600.0, "the archived client's 9999 must not appear"


def test_a_multi_month_span_covers_every_month_in_it(intake):
    m = access.month_intake(intake, "2026-07", "2026-09")
    assert m["new_clients"] == 6, m     # everyone but the archived one
    assert m["months"] == 3
    assert m["revenue"] == 100.0 + 200.0 + 400.0 + 800.0 + 1600.0


def test_the_span_is_swapped_when_given_backwards(intake):
    forwards = access.month_intake(intake, "2026-07", "2026-09")
    backwards = access.month_intake(intake, "2026-09", "2026-07")
    assert forwards == backwards


def test_the_previous_span_is_the_same_width(intake):
    """One month back for a month, three for a quarter — like for like."""
    single = access.month_intake(intake, "2026-08")
    assert single["new_clients_prev"] == 1        # July Last

    quarter = access.month_intake(intake, "2026-07", "2026-09")
    # The three months before July hold nobody.
    assert quarter["new_clients_prev"] == 0
    assert quarter["months"] == 3


def test_months_add_up_across_sub_periods_for_revenue(intake):
    """
    `revenue` is the period-bound figure, so it must sum. (`new_revenue`
    deliberately does not — see the docstring on month_intake.)
    """
    jul = access.month_intake(intake, "2026-07")["revenue"]
    aug = access.month_intake(intake, "2026-08")["revenue"]
    sep = access.month_intake(intake, "2026-09")["revenue"]
    whole = access.month_intake(intake, "2026-07", "2026-09")["revenue"]
    assert jul + aug + sep == whole


def test_a_month_with_nothing_in_it_is_zero_not_none(intake):
    m = access.month_intake(intake, "2026-01")
    assert m["new_clients"] == 0
    assert m["revenue"] == 0
    assert m["plans"] == 0
    assert m["unpriced"] == 0


def test_a_year_boundary_does_not_leak(intake):
    """'2026-01' must not match '2026-10' the way a prefix compare could."""
    conn = intake
    cid = _ins(conn, "clients", name_en="Oct", joined_on="2026-10-15",
               created_at=db.now(), active=1)
    _ins(conn, "subscriptions", client_id=cid, class_id=1, plan="p",
         sessions_total=4, price=50.0, starts_on="2026-10-15",
         expires_on="2026-10-15", active=1, created_at=db.now())
    conn.commit()
    assert access.month_intake(conn, "2026-01")["new_clients"] == 0
    assert access.month_intake(conn, "2026-10")["new_clients"] == 1


# ---------------------------------------------------------------- next_month

def test_next_month_rolls_over_december():
    assert access.next_month("2026-12") == "2027-01"


def test_next_month_within_a_year():
    assert access.next_month("2026-01") == "2026-02"
    assert access.next_month("2026-09") == "2026-10"


def test_next_month_is_the_inverse_of_prev_month():
    for m in ("2026-01", "2026-09", "2026-12", "2027-01"):
        assert access.prev_month(access.next_month(m)) == m


def test_an_iso_date_sorts_correctly_against_a_bare_month():
    """
    The property the range form rests on. If this stops holding, the whole
    of month_intake's range logic is wrong.
    """
    assert "2026-08-01" >= "2026-08"
    assert "2026-08-31" >= "2026-08"
    assert "2026-08-31" < "2026-09"
    assert not ("2026-07-31" >= "2026-08")
    assert "2026-12-31" < access.next_month("2026-12")
