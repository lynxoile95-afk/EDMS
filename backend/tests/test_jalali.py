"""The Jalali ⇄ Gregorian conversion, pinned against known Nowruz anchors.

The old implementation returned ``year -2689`` for ``1402-09-06`` and shifted
every Jalali date by one day, so these anchors are the guard against a repeat.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.utils.jalali import (
    format_jalali,
    gregorian_to_jalali,
    is_jalali_year,
    jalali_to_gregorian,
    parse_date,
)

#: First day of the Jalali year ⇄ the Gregorian day it starts on.
NOWRUZ = [
    ((1399, 1, 1), date(2020, 3, 20)),
    ((1400, 1, 1), date(2021, 3, 21)),
    ((1401, 1, 1), date(2022, 3, 21)),
    ((1402, 1, 1), date(2023, 3, 21)),
    ((1403, 1, 1), date(2024, 3, 20)),
    ((1404, 1, 1), date(2025, 3, 21)),
    ((1405, 1, 1), date(2026, 3, 21)),
]


@pytest.mark.parametrize("jalali,gregorian", NOWRUZ)
def test_nowruz_is_the_same_day_in_both_calendars(jalali, gregorian):
    assert jalali_to_gregorian(*jalali) == gregorian


@pytest.mark.parametrize("jalali,gregorian", NOWRUZ)
def test_the_round_trip_returns_the_same_jalali_date(jalali, gregorian):
    assert gregorian_to_jalali(gregorian.year, gregorian.month, gregorian.day) == jalali


def test_the_drawing_date_from_the_spec_converts_correctly():
    assert jalali_to_gregorian(1402, 9, 6) == date(2023, 11, 27)


def test_the_last_day_of_a_leap_year_is_the_day_before_nowruz():
    # 1402 was a leap year (366 days): 1402-12-29 is the day before 1403-01-01.
    assert jalali_to_gregorian(1402, 12, 29) == date(2024, 3, 19)


def test_shahrivar_is_31_days_long_so_mehr_starts_the_next_day():
    shahrivar_end = jalali_to_gregorian(1402, 6, 31)
    mehr_start = jalali_to_gregorian(1402, 7, 1)
    assert (mehr_start - shahrivar_end).days == 1
    assert shahrivar_end == date(2023, 9, 22) and mehr_start == date(2023, 9, 23)


def test_a_year_outside_the_supported_range_is_rejected_not_silently_wrong():
    with pytest.raises(ValueError):
        jalali_to_gregorian(900, 1, 1)
    with pytest.raises(ValueError):
        jalali_to_gregorian(1402, 13, 1)


def test_is_jalali_year_knows_the_two_calendars_apart():
    assert is_jalali_year(1402) is True
    assert is_jalali_year(2023) is False


def test_format_jalali_renders_persian_digits_order_with_slashes():
    assert format_jalali(date(2023, 11, 27)) == "1402/09/06"


def test_parse_date_reads_persian_digits_as_jalali():
    assert parse_date("۱۴۰۲/۰۹/۰۶") == date(2023, 11, 27)


def test_parse_date_keeps_a_gregorian_date_gregorian():
    assert parse_date("2023-11-27") == date(2023, 11, 27)
