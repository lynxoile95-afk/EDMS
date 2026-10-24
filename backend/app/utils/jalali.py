"""Jalali (Shamsi) <-> Gregorian conversion and date parsing.

Storage is always UTC Gregorian (`datetime`/`date`); the display layer renders
Jalali.  This module has no dependencies and is unit-tested.

Algorithm: the well-known Khayyam/Birashk-style arithmetic conversion
(``jdn`` based), valid for 1178–1633 Jalali.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from app.services.normalization import to_ascii_digits

_JALALI_EPOCH = 1948320  # Julian day number of 1 Farvardin 1 AP

_MONTHS_FA = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
_WEEKDAYS_FA = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]


def _div(a: int, b: int) -> int:
    return a // b


def _gregorian_jdn(year: int, month: int, day: int) -> int:
    """Julian day number from a Gregorian date (proleptic)."""
    a = _div(14 - month, 12)
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + _div(153 * m + 2, 5) + 365 * y + _div(y, 4) - _div(y, 100) + _div(y, 400) - 32045


def _jdn_to_gregorian(jdn: int) -> tuple[int, int, int]:
    a = jdn + 32044
    b = _div(4 * a + 3, 146097)
    c = a - _div(146097 * b, 4)
    d = _div(4 * c + 3, 1461)
    e = c - _div(1461 * d, 4)
    m = _div(5 * e + 2, 153)
    day = e - _div(153 * m + 2, 5) + 1
    month = m + 3 - 12 * _div(m, 10)
    year = 100 * b + d - 4800 + _div(m, 10)
    return year, month, day


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    """Convert a Jalali date to a Gregorian :class:`datetime.date`."""
    jy += 1595
    days = -355668 + (365 * jy) + (_div(jy, 33) * 8) + _div((jy % 33) + 3, 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30 + 186)
    year, month, day = _jdn_to_gregorian(days)
    return date(year, month, day)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Convert a Gregorian date to ``(jyear, jmonth, jday)``."""
    jdn = _gregorian_jdn(gy, gm, gd)
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + _div(gy2 + 3, 4) - _div(gy2 + 99, 100) + _div(gy2 + 399, 400)
    g_day_no += gd2
    for i in range(gm2):
        g_day_no += 31 if i in (0, 2, 4, 6, 7, 9, 11) else (29 if i == 1 else 30)
    j_day_no = g_day_no - 79
    j_np = _div(j_day_no, 12053)
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * _div(j_day_no, 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += _div(j_day_no - 1, 365)
        j_day_no = (j_day_no - 1) % 365
    jm = 1
    if j_day_no < 186:
        jm += _div(j_day_no, 31)
        jd = 1 + (j_day_no % 31)
    else:
        j_day_no -= 186
        jm = 7 + _div(j_day_no, 30)
        jd = 1 + (j_day_no % 30)
    del jdn  # kept for readability of the algorithm above
    return jy, jm, jd


def is_jalali_year(year: int) -> bool:
    return 1178 <= year <= 1633


def to_jalali_date(value: date | datetime | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    return gregorian_to_jalali(value.year, value.month, value.day)


def format_jalali(value: date | datetime | None, with_weekday: bool = False) -> str:
    """``2026-10-24`` -> ``1405/08/02`` (or with Persian month/weekday names)."""
    jalali = to_jalali_date(value)
    if jalali is None:
        return "—"
    jy, jm, jd = jalali
    if with_weekday:
        weekday = _WEEKDAYS_FA[value.weekday()] if isinstance(value, date) else ""
        return f"{weekday} {jd} {_MONTHS_FA[jm - 1]} {jy}"
    return f"{jy}/{jm:02d}/{jd:02d}"


def format_jalali_long(value: date | datetime | None) -> str:
    jalali = to_jalali_date(value)
    if jalali is None:
        return "—"
    jy, jm, jd = jalali
    return f"{jd} {_MONTHS_FA[jm - 1]} {jy}"


_DATE_RE = re.compile(r"(\d{2,4})\s*[/\-.‌]\s*(\d{1,2})\s*[/\-.‌]\s*(\d{1,2})")


def parse_date(raw: str | None, assume_jalali_if_small: bool = True) -> date | None:
    """Parse a date found in a title block.

    Accepts ``1403/05/12``, ``2024-08-02``, ``02.08.2024`` and Persian digits.
    A year in 1178…1633 is treated as Jalali.
    """
    if not raw:
        return None
    text = to_ascii_digits(raw)
    match = _DATE_RE.search(text)
    if not match:
        return None
    first, second, third = (int(g) for g in match.groups())
    candidates: list[tuple[int, int, int]] = []
    if len(match.group(1)) == 4:  # YYYY-MM-DD
        candidates.append((first, second, third))
    else:  # DD-MM-YYYY
        candidates.append((third, second, first))
    for year, month, day in candidates:
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        if is_jalali_year(year) and (assume_jalali_if_small or year < 1700):
            try:
                return jalali_to_gregorian(year, month, day)
            except Exception:
                continue
        if 1900 <= year <= 2200:
            try:
                return date(year, month, day)
            except ValueError:
                continue
    return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_jalali_string(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return format_jalali(dt) + " " + dt.strftime("%H:%M")
