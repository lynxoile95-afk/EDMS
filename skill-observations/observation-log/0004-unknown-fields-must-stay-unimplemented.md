---
id: 4
title: "Reference-system skill marks fields unknown; implementing them would be invention"
status: open
type: internal
skill: []
proposes_skill: []
target_file: [docs/03-reference-system-review.md]
siblings_checked: "reference vocabulary: P.O.I values (observed, adopted), temp numbering (observed, adopted), Reply Sheet columns (unknown, excluded), -RW status suffix (unknown, excluded); each exclusion cites the observation tag U in the source skill"
area: "domain scope"
date: 2026-10-24
session_context: "Reviewing edms-engineering-docs.skill against our Phase-1 build."
parked_until:
resolved:
resolution:
reference:
commands_verified: "NOT RUN — no command; the evidence is the confidence tags in the skill references"
---

**Issue:** The reference skill tags several behaviours `U` (unknown): the meaning of
the `-RW` status suffix, the difference between its two save options, and the
Reply Sheet's Judgment / Provider Clarification semantics. It is tempting to fill
these in with plausible EDMS behaviour.

**Suggested improvement:** Keep them out of the build and keep them listed as open
questions for the customer in the review document; implement only `O` items and
the `I` items whose inference is spelled out.

**Principle:** A domain model built on invented field semantics is more expensive to
correct than a missing feature is to add. "Unknown" is a design state, not a gap to
be patched.
