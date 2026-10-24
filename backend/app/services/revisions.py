"""Revision scheme parsing and ordering.

Supported schemes (per project, ``settings.rev_scheme``):

``alpha``     A, B, C … Z, AA, AB …   (base-26)
``numeric``   0, 1, 2 … 10
``p_prefix``  P01, P02 … P10 (preliminary) then numeric/A
``auto``      inferred per value (default)

Mixed values such as ``A1``/``B2`` (revision + minor) are supported by
combining the alphabetic part and the numeric part into one comparable integer.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.services.normalization import normalize_rev

_ALPHA_RE = re.compile(r"^[A-Z]+$")
_NUM_RE = re.compile(r"^\d+$")
_P_PREFIX_RE = re.compile(r"^P(\d{1,3})$")
_MIXED_RE = re.compile(r"^([A-Z]+)(\d{1,3})$")

#: comparison outcome codes
NEW = "NEW"
GREATER = "GREATER"
EQUAL = "EQUAL"
LOWER = "LOWER"
SKIP = "SKIP"
INVALID = "INVALID"


def _alpha_to_int(value: str) -> int:
    total = 0
    for ch in value:
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total


def _int_to_alpha(value: int) -> str:
    if value <= 0:
        return "A"
    out = ""
    while value > 0:
        value, rem = divmod(value - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def rev_to_seq(rev: str | None, scheme: str = "auto") -> int:
    """Map a revision token to a strictly increasing integer (0 = invalid)."""
    token = normalize_rev(rev)
    if not token:
        return 0
    if scheme == "p_prefix" or _P_PREFIX_RE.match(token):
        match = _P_PREFIX_RE.match(token)
        if match:
            return int(match.group(1))
    if _NUM_RE.match(token):
        return int(token)
    if _ALPHA_RE.match(token):
        return _alpha_to_int(token)
    mixed = _MIXED_RE.match(token)
    if mixed:
        return _alpha_to_int(mixed.group(1)) * 1000 + int(mixed.group(2))
    # unknown shape: deterministic hash-free fallback -> last resort ordering
    digits = re.sub(r"\D", "", token)
    return int(digits) if digits else 0


def seq_to_rev(seq: int, scheme: str = "auto") -> str:
    """Inverse of :func:`rev_to_seq` for generating the expected next revision."""
    if scheme == "numeric":
        return str(max(seq, 0))
    if scheme == "p_prefix":
        return f"P{max(seq, 1):02d}"
    if seq <= 0:
        return "A"
    if seq <= 2_000_000 and seq < 26 * 1000:  # pure alphabetic band
        return _int_to_alpha(seq)
    return str(seq)


def next_expected_rev(current: str | None, scheme: str = "auto") -> str:
    """The revision a normal supersede would use."""
    if not normalize_rev(current):
        return seq_to_rev(1, scheme)
    return seq_to_rev(rev_to_seq(current, scheme) + 1, scheme)


def compare_revs(current: str | None, new: str | None, scheme: str = "auto") -> str:
    """Classify the transition ``current -> new``.

    Returns one of ``NEW`` (no current revision), ``GREATER``, ``EQUAL``,
    ``LOWER``, ``SKIP`` (greater by more than one step) or ``INVALID``.
    """
    new_token = normalize_rev(new)
    if not new_token:
        return INVALID
    current_token = normalize_rev(current)
    if not current_token:
        return NEW
    cur_seq = rev_to_seq(current_token, scheme)
    new_seq = rev_to_seq(new_token, scheme)
    if new_seq == 0 and new_token not in {"0"}:
        return INVALID
    if new_seq == cur_seq:
        return EQUAL
    if new_seq < cur_seq:
        return LOWER
    if new_seq == cur_seq + 1:
        return GREATER
    return SKIP


def is_acceptable_transition(verdict: str) -> bool:
    return verdict in {NEW, GREATER, SKIP}


def title_similarity(a: str | None, b: str | None) -> float:
    """0..1 similarity between two titles (Persian-aware via normalization)."""
    from app.services.normalization import normalize_title

    left, right = normalize_title(a), normalize_title(b)
    if not left or not right:
        return 0.0
    return fuzz.token_set_ratio(left, right) / 100.0
