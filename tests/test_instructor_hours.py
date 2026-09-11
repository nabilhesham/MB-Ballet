"""
The two hour figures, and which one carries the corrections.

None of the three scripts this suite replaces covered this, and the rule is
load-bearing: an adjustment counted into both `logged_hours` (the salary
sheet) and `taught_hours` (the timetable) would be paid twice. These tests
pin down that only one of them carries it.
"""

from datetime import date, timedelta

import access


def a_week_around_today():
    return ((date.today() - timedelta(days=3)).isoformat(),
            (date.today() + timedelta(days=3)).isoformat())


def test_taught_hours_reports_scheduled_and_adjustment_apart_from_their_sum(academy):
    """The screen must be able to show what was corrected, not just a total."""
    a, b = a_week_around_today()
    t = access.taught_hours(academy.repo, academy.ana, a, b)
    assert set(t) >= {"sessions", "scheduled", "adjustment", "hours"}
    assert t["hours"] == round(t["scheduled"] + t["adjustment"], 2)
    assert t["adjustment"] == 0, "nothing corrected yet"


def test_an_adjustment_moves_the_taught_total_to_what_was_asked_for(academy):
    repo = academy.repo
    day = (date.today() - timedelta(days=1)).isoformat()
    before = access.taught_hours(repo, academy.ana, day, day)["hours"]

    result = access.adjust_taught_hours(repo, academy.ana, day, before + 1.5,
                                        note="stayed for a rehearsal")

    assert result["hours"] == round(before + 1.5, 2)
    assert result["adjustment"] == 1.5


def test_an_adjustment_is_a_dated_delta_not_a_rewrite(academy):
    """
    The correction is its own auditable row. The timetable and the salary
    sheet still say what they always said.
    """
    repo = academy.repo
    day = (date.today() - timedelta(days=1)).isoformat()
    sessions_before = repo.raw(
        "SELECT COALESCE(SUM(duration_hours),0) h FROM sessions WHERE instructor_id=?",
        (academy.ana,)).fetchone()["h"]

    access.adjust_taught_hours(repo, academy.ana, day, 99.0)

    rows = repo.raw(
        "SELECT * FROM instructor_hour_adjustments WHERE instructor_id=?",
        (academy.ana,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["adjustment_date"] == day
    assert repo.raw(
        "SELECT COALESCE(SUM(duration_hours),0) h FROM sessions WHERE instructor_id=?",
        (academy.ana,)).fetchone()["h"] == sessions_before


def test_a_correction_never_reaches_the_salary_sheet_figure(academy):
    """Only one of the two figures may carry it, or it is counted twice."""
    repo = academy.repo
    day = (date.today() - timedelta(days=1)).isoformat()
    a, b = (date.today() - timedelta(days=30)).isoformat(), date.today().isoformat()
    logged_before = access.logged_hours(repo, academy.ana, a, b)

    access.adjust_taught_hours(repo, academy.ana, day, 99.0)

    logged_after = access.logged_hours(repo, academy.ana, a, b)
    assert logged_after["hours"] == logged_before["hours"]
    assert logged_after["pay"] == logged_before["pay"]


def test_days_worked_counts_only_real_salary_sheet_rows(academy):
    """A correction is not a claim of an extra day worked."""
    repo = academy.repo
    a, b = (date.today() - timedelta(days=30)).isoformat(), date.today().isoformat()
    days_before = access.logged_hours(repo, academy.ana, a, b)["days"]

    access.adjust_taught_hours(
        repo, academy.ana, (date.today() - timedelta(days=90)).isoformat(), 5.0)

    assert access.logged_hours(repo, academy.ana, a, b)["days"] == days_before


def test_a_wider_range_picks_up_a_days_correction_by_summing(academy):
    repo = academy.repo
    day = (date.today() - timedelta(days=1)).isoformat()
    access.adjust_taught_hours(
        repo, academy.ana, day,
        access.taught_hours(repo, academy.ana, day, day)["hours"] + 2.0)

    a, b = a_week_around_today()
    assert access.taught_hours(repo, academy.ana, a, b)["adjustment"] == 2.0
