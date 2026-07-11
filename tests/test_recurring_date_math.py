import pytest

from app.services.recurring_date_math import due_occurrences, occurrence_at


# ---------- occurrence_at ----------

def test_weekly_occurrence():
    assert occurrence_at(start_date="2026-01-01", frequency="weekly", n=0) == "2026-01-01"
    assert occurrence_at(start_date="2026-01-01", frequency="weekly", n=1) == "2026-01-08"
    assert occurrence_at(start_date="2026-01-01", frequency="weekly", n=4) == "2026-01-29"


def test_biweekly_occurrence():
    assert occurrence_at(start_date="2026-01-01", frequency="biweekly", n=1) == "2026-01-15"


def test_monthly_occurrence_stays_anchored():
    assert occurrence_at(start_date="2026-01-15", frequency="monthly", n=0) == "2026-01-15"
    assert occurrence_at(start_date="2026-01-15", frequency="monthly", n=1) == "2026-02-15"
    assert occurrence_at(start_date="2026-01-15", frequency="monthly", n=11) == "2026-12-15"


def test_monthly_occurrence_crossing_a_year_boundary():
    assert occurrence_at(start_date="2026-11-15", frequency="monthly", n=3) == "2027-02-15"


def test_monthly_on_the_31st_clamps_in_short_months_but_stays_anchored():
    # Rent due "on the 31st" — January has 31 days, February doesn't.
    # The anchor stays the 31st; only the month WITHOUT a 31st clamps.
    assert occurrence_at(start_date="2026-01-31", frequency="monthly", n=0) == "2026-01-31"
    assert occurrence_at(start_date="2026-01-31", frequency="monthly", n=1) == "2026-02-28"  # clamped, 2026 not a leap year
    assert occurrence_at(start_date="2026-01-31", frequency="monthly", n=2) == "2026-03-31"  # back to the real anchor, not permanently drifted to the 28th


def test_monthly_on_the_31st_in_a_leap_year_february():
    assert occurrence_at(start_date="2028-01-31", frequency="monthly", n=1) == "2028-02-29"  # 2028 is a leap year


def test_yearly_occurrence():
    assert occurrence_at(start_date="2026-03-10", frequency="yearly", n=3) == "2029-03-10"


def test_unknown_frequency_raises():
    with pytest.raises(ValueError):
        occurrence_at(start_date="2026-01-01", frequency="daily", n=0)


# ---------- due_occurrences ----------

def test_no_occurrences_due_before_the_start_date():
    result = due_occurrences(start_date="2026-08-01", frequency="monthly", end_date=None, last_materialized=None, today="2026-07-10")
    assert result == []


def test_first_occurrence_due_on_its_own_start_date():
    result = due_occurrences(start_date="2026-07-10", frequency="monthly", end_date=None, last_materialized=None, today="2026-07-10")
    assert result == ["2026-07-10"]


def test_catch_up_generates_every_missed_occurrence_in_order():
    # Hasn't been checked since March; today is July -- should catch up April, May, June, July.
    result = due_occurrences(start_date="2026-01-10", frequency="monthly", end_date=None, last_materialized="2026-03-10", today="2026-07-10")
    assert result == ["2026-04-10", "2026-05-10", "2026-06-10", "2026-07-10"]


def test_already_materialized_occurrences_are_not_repeated():
    result = due_occurrences(start_date="2026-01-10", frequency="monthly", end_date=None, last_materialized="2026-07-10", today="2026-07-10")
    assert result == []


def test_end_date_stops_generation():
    result = due_occurrences(start_date="2026-01-10", frequency="monthly", end_date="2026-05-10", last_materialized=None, today="2026-07-10")
    assert result == ["2026-01-10", "2026-02-10", "2026-03-10", "2026-04-10", "2026-05-10"]


def test_end_date_exactly_on_a_boundary_is_inclusive():
    result = due_occurrences(start_date="2026-01-10", frequency="weekly", end_date="2026-01-17", last_materialized=None, today="2026-01-31")
    assert result == ["2026-01-10", "2026-01-17"]


def test_weekly_catch_up_over_several_missed_weeks():
    result = due_occurrences(start_date="2026-06-01", frequency="weekly", end_date=None, last_materialized="2026-06-08", today="2026-06-29")
    assert result == ["2026-06-15", "2026-06-22", "2026-06-29"]
