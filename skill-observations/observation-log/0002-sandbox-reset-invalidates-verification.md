---
id: 2
title: "Sandbox resets wipe .venv and /tmp, so 'tests pass' expires between turns"
status: open
type: internal
skill: []
proposes_skill: []
target_file: [AGENTS.md]
siblings_checked: "environment provisioning: docker-compose (not runnable here), .venv, /tmp/edms runtime state — compose excluded (no daemon in sandbox); the .venv and /tmp recipes were added"
area: "local environment / verification"
date: 2026-10-24
session_context: "Mid-turn pytest failed with 'No module named pytest', then '.venv' had vanished; earlier the SQLite DB and storage under /tmp/edms had disappeared the same way."
parked_until:
resolved:
resolution:
reference:
commands_verified: "run: ls -d .venv /tmp/edms -> both missing; run: python3 -m venv .venv && .venv/bin/pip install ... && pytest -> 150 passed"
---

**Issue:** Verification results recorded in an earlier turn are treated as current
by the next turn, but the sandbox discards everything outside the repo (`.venv`,
`/tmp/edms/edms.db`, `/tmp/edms/storage`). Two runs were spent rediscovering this
instead of working.

**Suggested improvement:** A written provisioning recipe (venv + `bcrypt==4.0.1`
pin, `alembic upgrade head`, `SEED_DEMO=true` bootstrap) in `AGENTS.md`, and a rule
that any test-run number quoted to the user must come from a run in the same turn.

**Principle:** A verification claim has a half-life. If the environment can vanish,
re-run before claiming — the cost of the run is always lower than the cost of a
wrong claim.
