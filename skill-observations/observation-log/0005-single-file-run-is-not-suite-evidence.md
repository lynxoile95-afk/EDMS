---
id: 5
title: "A single test file is not the suite: appended tests hid two fixture errors"
status: open
type: internal
skill: []
proposes_skill: []
target_file: [backend/tests/test_api_flow.py]
siblings_checked: "verification habits: partial run, full run, targeted re-run — partial run excluded as evidence for a completion claim; targeted re-run kept for fast iteration only"
area: "verification"
date: 2026-10-24
session_context: "Two new tests were appended that requested auth_headers as a fixture, but it is a conftest function; a per-file run reported errors that a '147 passed' memory had obscured."
parked_until:
resolved:
resolution:
commands_verified: "run: pytest tests/test_api_flow.py -> 2 errors: \"fixture 'auth_headers' not found\"; run: pytest (whole suite) -> 149 passed after the fix"
---

**Issue:** New tests were judged by the suite's earlier number. Running one file
exposed `fixture 'auth_headers' not found` in the two new tests — an error state that
a green summary from a previous turn had papered over.

**Suggested improvement:** Quote test numbers only from a full-suite run in the same
turn, and treat a per-file run as a loop, never as the deliverable evidence.

**Principle:** Evidence must be of the same scope as the claim. "The suite passes" is
a claim about the suite, so only a suite run can support it.
