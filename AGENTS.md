# AGENTS.md — working method for this repository

This file is the entry point for any agent (human or AI) touching EDMS. It records
**which skills drive the work**, **when each one fires**, and **the project rules that
overrule everything else**. The skills themselves are vendored read-only under
`skills/vendor/`; the observations we collect while using them live in
`skill-observations/`.

---

## 1. Installed skills

| Skill bundle | Path | What it is for |
|---|---|---|
| `task-observer` v2.0.0 | `skills/vendor/task-observer/` | Turn every session into evidence: log observations, review them weekly, promote the recurring ones into principles. |
| `superpowers` (brainstorming, writing-plans, executing-plans, TDD, systematic-debugging, verification-before-completion, code-review, receiving-feedback) | `skills/vendor/superpowers/` | The engineering loop: think → plan → test-first → verify → review. |

`skills/vendor/` is **read-only**. Never edit a vendored `SKILL.md`; record what we
learned in `skill-observations/` instead.

### Loading the skills (start of every session)

```bash
bash skills/vendor/superpowers/skills/using-superpowers/scripts/skill-load.sh
python3 skills/vendor/task-observer/scripts/session-start-scan.sh
cat skill-observations/last-review-date.txt
```

The first script prints the skills that apply to the current task; the second prints the
observation backlog and tells you whether a weekly review is due.

---

## 2. Which skill fires when

| Situation | Skill | Non-negotiable output |
|---|---|---|
| A new feature or a changed behaviour is requested | `superpowers:brainstorming` | One-page design note; ask about intent, not implementation |
| Design agreed, more than ~30 minutes of work | `superpowers:writing-plans` | File under `docs/superpowers/plans/YYYY-MM-DD-<name>.md` with exact files, signatures and tests |
| Executing a plan | `superpowers:executing-plans` | Tick the checkboxes in the plan file as you go |
| Any behaviour change in `backend/app` | `superpowers:test-driven-development` | Test first, watch it fail, then the smallest code that passes |
| A bug you cannot explain | `superpowers:systematic-debugging` | Hypothesis list before any edit; no shotgun fixes |
| Before saying "done" | `superpowers:verification-before-completion` | The command output that proves it (see §5) |
| Reviewing a diff or receiving review comments | `superpowers:code-review` / `receiving-feedback` | Findings with file:line, not opinions |
| Something surprised you, twice | `task-observer` | A new file in `skill-observations/` with the frontmatter in §6 |

---

## 3. Project rules that overrule any skill

1. **Persian first.** Every human-facing string is Persian, with a bilingual
   (fa/en) label when the reference system prints one. Code, comments and commits are
   English.
2. **No placeholders.** No `TODO`, no `pass  # implement later`, no stub endpoints.
   If a phase is genuinely out of scope, say so in the plan with the reason.
3. **Implement for real, verify for real.** "It should work" is not evidence; the
   command output is (§5).
4. **Ask nothing, assume loudly.** When the request leaves a gap, state the assumption
   at the top of the answer and proceed.
5. **One source of truth per UI element.** Windows, field grids, ribbons, tabs, dialogs
   come from `frontend/src/components/{window,ui,Tabs}.tsx`. A page that hand-rolls its
   own chrome is a bug (see README §۱۰-۱).
6. **Every screen that shows a document renders it.** A blank preview pane counts as
   broken.
7. **UTC in the database, Jalali/RTL in the UI.**
8. **AI suggests, rules decide.** No model output becomes a decision without a rule and
   a confidence beside it.
9. **Parked questions stay parked.** Items marked `U` in
   `docs/03-reference-system-review.md` need a customer answer; do not guess them into
   the code.

---

## 4. Fixed stack

Python 3.12 · FastAPI · SQLAlchemy 2.x + Alembic · Pydantic v2 · PostgreSQL 16 (JSONB) ·
MinIO · Redis + Celery · PyMuPDF / pdfplumber · Tesseract `fas+eng` (pluggable
PaddleOCR) · rapidfuzz · React + TypeScript + Vite + Tailwind (RTL) · PDF.js ·
JWT behind an LDAP/AD/Keycloak abstraction · Docker Compose (`api`, `worker`, `postgres`,
`redis`, `minio`, `frontend`, `nginx`) · ReportLab (WeasyPrint when available) for
Persian PDFs.

No external cloud AI. A local OpenAI-compatible VLM is optional and only for the
ambiguous last layer.

---

## 5. Verification commands (copy-paste)

```bash
# backend
cd backend && ../.venv/bin/python -m pytest                      # 161 tests
cd backend && ../.venv/bin/python -m pytest tests/test_review_routing.py -x -q

# frontend
cd frontend && npx tsc --noEmit && npx vite build

# live smoke (servers must be running, see README §۵)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5173/
```

A task is not finished until the relevant lines above have been run **after** the last
edit and their output pasted into the answer.

---

## 6. Observation log

Location: `skill-observations/`, one Markdown file per observation:

```markdown
---
id: 0008
date: 2026-10-24
type: pattern | correction | project-preference
tags: [tag-a, tag-b]
project: EDMS
---

## Context
What task was running when this came up.

## Observation
What happened, factually.

## Value
Why the next session needs this.

## Action
What changed (code, docs, AGENTS.md) or what should.
```

Weekly review: re-read the backlog, promote anything that recurred three times into
§3 of this file, then update `skill-observations/last-review-date.txt`.

### Honest limits of the adoption

The bundles are extensive. We run them at the depth where they earn their cost:
checklists are applied to tasks that matter (anything touching money, data loss,
published API shapes, or the review route), and skipped for one-line typo fixes. We do
**not** re-run the full ceremony on every tool call; the aim is a working product.
That trade-off is itself logged as observation `0006`.
