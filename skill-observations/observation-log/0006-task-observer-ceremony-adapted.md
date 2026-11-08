---
id: 6
title: "task-observer's per-turn ceremony costs more than it returns in a product repo"
status: open
type: open-source
skill: [task-observer]
proposes_skill: []
target_file: [AGENTS.md]
siblings_checked: "task-observer episodes: session start (adopted in reduced form), 3rd-todo write checkpoint (excluded — see below), weekly review (adopted), observation format (adopted verbatim); exclusions cite SKILL.md \"How to Log\""
area: "activation and cadence"
date: 2026-10-24
session_context: "Adopting one-skill-to-rule-them-all-main.zip into the EDMS repository."
parked_until:
resolved:
resolution:
reference:
commands_verified: "run: python3 scripts/validate-skill-bundle.py . -> OK: all gate checks passed"
---

**Issue:** The skill requires a disk write after every third completed todo item and
a full session-start protocol with several probes. In this harness todos are not
always used, and the ceremony competes with the user's actual request for context
budget.

**Suggested improvement:** Adopt the observation format, the log layout and the
weekly review; replace the per-third-todo checkpoint with one flush at the end of
each turn in which an observation was noticed, and say so in `AGENTS.md` rather than
silently deviating.

**Principle:** A methodology earns its ceremony. Where a rule's enforcement cost
exceeds the loss it prevents, keep the rule's purpose and change its trigger —
visibly, in the activation file, so the next session inherits the decision and not
the ambiguity.
