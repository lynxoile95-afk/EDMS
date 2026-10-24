---
id: 3
title: "DDM rule pinned to one P.O.I silently drops reviewers when another P.O.I is issued"
status: open
type: internal
skill: []
proposes_skill: []
target_file: [backend/app/bootstrap.py, backend/tests/test_transmittal_grouping.py]
siblings_checked: "distribution rules: seed data, ddm.resolve() matcher, specificity ordering — the matcher is correct (a purpose-specific rule must not match another purpose); the seed data was the defect; no other member"
area: "distribution matrix (DDM)"
date: 2026-10-24
session_context: "Demo seed produced a transmittal with 1 recipient instead of 3 after the P.O.I vocabulary gained IFE/IFP/IPC and the demo was issued as IFC."
parked_until:
resolved:
resolution:
commands_verified: "run: pytest backend -> 150 passed; run: GET /ddm/rules -> four rules with purpose: None; run: reseeded demo -> recipients 3"
---

**Issue:** The demo discipline rules were seeded with `purpose=IFR`. Issuing with
`IFC` matched no discipline rule, so resolution fell back to the catch-all rule and
the transmittal went out with one recipient. Nothing failed, logged, or warned: the
system did exactly what it was told and the operator would only notice the missing
reviewers after the deadline passed.

**Suggested improvement:** Seed discipline rules with an open purpose; add a
regression test that resolves a discipline rule for IFR, IFC, IFE and IPC; and
surface the chosen rule in the UI (the transmittal meta already names it, which is
how the mismatch was spotted).

**Principle:** A silent fallback in a routing system is a silent data loss. When a
rule set can fall through to a catch-all, the catch-all's use must be reported, not
just applied.
