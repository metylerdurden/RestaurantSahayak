"""Regression coverage for the Analytics Agent date-range bug: the model previously
had to compute an absolute date_from/date_to from a relative phrase itself and got it
wrong (reproduced live: for real date 2026-08-21, it called get_daily_sales with
date_from=2026-08-17/date_to=2026-08-18 for "yesterday", which should have resolved to
2026-08-20). resolve_period() is the deterministic fix — pure function, no LLM, no
database — so every one of these periods is pinned exactly, not just "close enough"."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.analytics import GetDailySalesInput, GetItemSalesInput, GetNoShowRateInput
from app.tools.analytics_tools import resolve_period

# A Friday, deliberately not a week boundary, so this_week/last_week must actually
# compute Monday..Sunday rather than accidentally matching `today` by coincidence.
FRIDAY = date(2026, 8, 21)


def test_today():
    assert resolve_period("today", today=FRIDAY) == (FRIDAY, FRIDAY)


def test_yesterday():
    assert resolve_period("yesterday", today=FRIDAY) == (date(2026, 8, 20), date(2026, 8, 20))


def test_yesterday_across_a_month_boundary():
    assert resolve_period("yesterday", today=date(2026, 9, 1)) == (date(2026, 8, 31), date(2026, 8, 31))


def test_this_week_is_monday_through_sunday_containing_today():
    assert resolve_period("this_week", today=FRIDAY) == (date(2026, 8, 17), date(2026, 8, 23))


def test_this_week_when_today_is_monday():
    monday = date(2026, 8, 17)
    assert resolve_period("this_week", today=monday) == (monday, date(2026, 8, 23))


def test_this_week_when_today_is_sunday():
    sunday = date(2026, 8, 23)
    assert resolve_period("this_week", today=sunday) == (date(2026, 8, 17), sunday)


def test_last_week_is_the_full_previous_monday_through_sunday():
    assert resolve_period("last_week", today=FRIDAY) == (date(2026, 8, 10), date(2026, 8, 16))


def test_last_7_days_is_a_rolling_window_ending_today_inclusive():
    date_from, date_to = resolve_period("last_7_days", today=FRIDAY)
    assert date_to == FRIDAY
    assert date_from == date(2026, 8, 15)
    assert (date_to - date_from).days == 6  # 7 calendar days inclusive of both ends


def test_last_30_days_is_a_rolling_window_ending_today_inclusive():
    date_from, date_to = resolve_period("last_30_days", today=FRIDAY)
    assert date_to == FRIDAY
    assert (date_to - date_from).days == 29


def test_this_month_is_the_1st_through_today():
    assert resolve_period("this_month", today=FRIDAY) == (date(2026, 8, 1), FRIDAY)


def test_last_month_is_the_full_previous_calendar_month():
    assert resolve_period("last_month", today=FRIDAY) == (date(2026, 7, 1), date(2026, 7, 31))


def test_last_month_across_a_year_boundary():
    assert resolve_period("last_month", today=date(2026, 1, 15)) == (date(2025, 12, 1), date(2025, 12, 31))


# --- input model validation: exactly one of period or explicit dates ---


@pytest.mark.parametrize("model_cls", [GetDailySalesInput, GetItemSalesInput, GetNoShowRateInput])
def test_period_alone_is_valid(model_cls):
    model_cls.model_validate({"period": "yesterday"})


@pytest.mark.parametrize("model_cls", [GetDailySalesInput, GetItemSalesInput, GetNoShowRateInput])
def test_explicit_dates_alone_are_valid(model_cls):
    model_cls.model_validate({"date_from": "2026-08-01", "date_to": "2026-08-07"})


@pytest.mark.parametrize("model_cls", [GetDailySalesInput, GetItemSalesInput, GetNoShowRateInput])
def test_period_and_explicit_dates_together_is_rejected(model_cls):
    with pytest.raises(ValidationError):
        model_cls.model_validate({"period": "yesterday", "date_from": "2026-08-01", "date_to": "2026-08-07"})


@pytest.mark.parametrize("model_cls", [GetDailySalesInput, GetItemSalesInput, GetNoShowRateInput])
def test_neither_period_nor_explicit_dates_is_rejected(model_cls):
    with pytest.raises(ValidationError):
        model_cls.model_validate({})


@pytest.mark.parametrize("model_cls", [GetDailySalesInput, GetItemSalesInput, GetNoShowRateInput])
def test_only_date_from_without_date_to_is_rejected(model_cls):
    with pytest.raises(ValidationError):
        model_cls.model_validate({"date_from": "2026-08-01"})


@pytest.mark.parametrize("model_cls", [GetDailySalesInput, GetItemSalesInput, GetNoShowRateInput])
def test_reversed_date_range_is_rejected(model_cls):
    with pytest.raises(ValidationError):
        model_cls.model_validate({"date_from": "2026-08-07", "date_to": "2026-08-01"})
