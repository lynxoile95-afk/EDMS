"""Revision scheme parsing, ordering and transition classification."""

from __future__ import annotations

import pytest

from app.services.revisions import (
    EQUAL,
    GREATER,
    INVALID,
    LOWER,
    NEW,
    SKIP,
    compare_revs,
    is_acceptable_transition,
    next_expected_rev,
    rev_to_seq,
    seq_to_rev,
    title_similarity,
)


@pytest.mark.parametrize(
    ("rev", "scheme", "expected"),
    [
        ("A", "auto", 1),
        ("B", "auto", 2),
        ("Z", "auto", 26),
        ("AA", "auto", 27),
        ("AB", "auto", 28),
        ("0", "auto", 0),
        ("1", "numeric", 1),
        ("10", "numeric", 10),
        ("P01", "auto", 1),
        ("P10", "auto", 10),
        ("A1", "auto", 1001),
        ("", "auto", 0),
    ],
)
def test_rev_to_seq(rev, scheme, expected):
    assert rev_to_seq(rev, scheme) == expected


def test_rev_order_is_monotonic():
    assert rev_to_seq("A") < rev_to_seq("B") < rev_to_seq("Z") < rev_to_seq("AA") < rev_to_seq("AB")
    assert rev_to_seq("B1") < rev_to_seq("B2") < rev_to_seq("C1")


@pytest.mark.parametrize(
    ("seq", "scheme", "expected"),
    [(1, "alpha", "A"), (26, "alpha", "Z"), (27, "alpha", "AA"), (2, "numeric", "2"), (3, "p_prefix", "P03")],
)
def test_seq_to_rev(seq, scheme, expected):
    assert seq_to_rev(seq, scheme) == expected


def test_next_expected_rev():
    assert next_expected_rev(None) == "A"
    assert next_expected_rev("A") == "B"
    assert next_expected_rev("Z") == "AA"
    assert next_expected_rev("3", "numeric") == "4"


@pytest.mark.parametrize(
    ("current", "new", "expected"),
    [
        (None, "A", NEW),
        ("A", "B", GREATER),
        ("A", "A", EQUAL),
        ("B", "A", LOWER),
        ("A", "C", SKIP),
        ("A", "", INVALID),
        ("A", "02", GREATER),  # numeric token after a letter revision: treated as >
    ],
)
def test_compare_revs_alpha(current, new, expected):
    assert compare_revs(current, new, "alpha") == expected


def test_compare_revs_numeric_and_pprefix():
    assert compare_revs("9", "10", "numeric") == GREATER
    assert compare_revs("P01", "P02") == GREATER
    assert compare_revs("P02", "P01") == LOWER


def test_acceptability():
    assert is_acceptable_transition(NEW)
    assert is_acceptable_transition(GREATER)
    assert is_acceptable_transition(SKIP)
    assert not is_acceptable_transition(EQUAL)
    assert not is_acceptable_transition(LOWER)


def test_title_similarity():
    assert title_similarity("Plot Plan", "plot plan") > 0.95
    assert title_similarity("چیدمان لوله‌کشی", "چیدمان لوله‌كشي") > 0.95
    assert title_similarity("Plot Plan", "Piping Layout") < 0.6
    assert title_similarity(None, "x") == 0.0
