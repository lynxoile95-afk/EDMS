"""Jalali (Shamsi) <-> Gregorian conversion and date parsing.

Storage is always UTC Gregorian (`datetime`/`date`); the display layer renders
Jalali.  This module has no dependencies and is unit-tested.

Algorithm: the well-known Khayyam/Birashk-style arithmetic conversion
(``jdn`` based), valid for 1178–1633 Jalali.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


#: The 33-year leap "breaks" table from the official algorithm (jalaali-js /
#: Borkowski). Each entry is the Jalali year in which the leap pattern restarts.
_BREAKS = (
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
    1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
)


def _mod(a: int, b: int) -> int:
    return a - b * (a // b)


@dataclass(frozen=True, slots=True)
class _YearInfo:
    leap: int
    gy: int
    march: int


def _jal_cal(jy: int) -> _YearInfo:
    """Leap flag and the Gregorian day-of-March on which this Jalali year starts."""
    if not (1178 <= jy < 1633):
        raise ValueError(f"سال جلالی {jy} بیرون از بازهٔ پشتیبانی‌شده (۱۱۷۸–۱۶۳۳) است.")
    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    jump = 0
    for index in range(1, len(_BREAKS)):
        jm = _BREAKS[index]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return _YearInfo(leap=leap, gy=gy, march=march)


def _jalali_jdn(jy: int, jm: int, jd: int) -> int:
    info = _jal_cal(jy)
    return _gregorian_jdn(info.gy, 3, info.march) + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    """Convert a Jalali date to a Gregorian :class:`datetime.date`.

    The previous implementation mixed an epoch-day formula with a Julian-day
    converter and returned dates centuries off (``1402-09-06`` -> year -2689), so
    nothing could convert Jalali to Gregorian at all. This one is the standard
    breaks-based algorithm, pinned by the tests in ``tests/test_jalali.py``.
    """
    if not (1 <= jm <= 12 and 1 <= jd <= 31):
        raise ValueError(f"تاریخ جلالی نامعتبر: {jy}-{jm}-{jd}")
    year, month, day = _jdn_to_gregorian(_jalali_jdn(jy, jm, jd))
    return date(year, month, day)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Convert a Gregorian date to ``(jyear, jmonth, jday)``."""
    jdn = _gregorian_jdn(gy, gm, gd)
    jy = gy - 621
    info = _jal_cal(jy)
    jdn_first = _gregorian_jdn(gy, 3, info.march)
    k = jdn - jdn_first
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if info.leap == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


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
