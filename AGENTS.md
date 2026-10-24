# AGENTS.md — how to work in this repository

> This file is the **activation config** for the two skill bundles vendored under
> `skills/vendor/`. Read it before the first tool call of a session; it is short on
> purpose. Everything below was adopted deliberately — including the two places
> where we do **not** follow a skill literally, marked «انحراف آگاهانه».

## 0. What is vendored, and why

| Bundle | Source (uploaded to this repo) | What it is | How we use it |
|---|---|---|---|
| `skills/vendor/task-observer/` | `one-skill-to-rule-them-all-main.zip` | meta-skill: observation log → weekly review → skill/repo improvements | We keep the **log**, the **format** and the **weekly review**. See §4. |
| `skills/vendor/superpowers/skills/` | `superpowers-main.zip` | 15 process skills: brainstorming, writing-plans, executing-plans, TDD, systematic-debugging, verification-before-completion, code review, worktrees … | We follow them for engineering process work. See §3. |

Both bundles are third-party (CC-BY-4.0 / MIT respectively — see each `LICENSE`).
They are vendored read-only: **do not edit them**; when an upstream rule needs
adapting for this repo, write the adaptation here and log an observation (§4).

## 1. Session start (reduced)

1. `bash skills/vendor/task-observer/scripts/session-start-scan.sh "/home/user/EDMS"`
   — prints the frontmatter of every open observation. Read it whole.
2. If `skill-observations/last-review-date.txt` is `never` or older than 7 days and
   there are open observations: mention the backlog **in one line** and carry on
   with the user's task. Never gate work on a review.
3. Announce the skill you are using when it matters to the user, in one short line
   («از systematic-debugging استفاده میکنم: اول علت ریشه، بعد اصلاح»).

> **انحراف آگاهانه ۱ —** the upstream skill demands a disk write after every third
> completed todo item and a multi-probe session-start protocol. In this harness
> todos are not always used and the ceremony competes with the user's request for
> context. Replacement rule: **flush observations at the end of any turn in which
> one was noticed**, one file per observation, and append `no observations` to
> `skill-observations/checkpoints.log` on a turn that ended without one. Rationale
> logged as observation 0006.

## 2. Project facts an agent must not rediscover

**Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 + Alembic / Pydantic v2 · React + TS
+ Vite + Tailwind (RTL) · SQLite for local runs, PostgreSQL 16 in compose.

**Provision the sandbox** (everything outside the repo is wiped between sessions):

```bash
cd /home/user/EDMS
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt "bcrypt==4.0.1" pytest httpx
mkdir -p /tmp/edms/storage
cd backend
export DATABASE_URL="sqlite:////tmp/edms/edms.db" STORAGE_BACKEND=local LOCAL_STORAGE_PATH=/tmp/edms/storage \
       SECRET_KEY=dev-secret-key-for-local-run-longer-value-1234567890 CELERY_TASK_ALWAYS_EAGER=true \
       BOOTSTRAP_ADMIN_EMAIL=admin@example.com BOOTSTRAP_ADMIN_PASSWORD=Admin123!
../.venv/bin/alembic upgrade head && SEED_DEMO=true ../.venv/bin/python -m app.bootstrap
```

**Verification, before any completion claim:**

```bash
cd backend && ../.venv/bin/python -m pytest          # whole suite; never quote a per-file run
cd frontend && npx tsc --noEmit && npx vite build
```

**Demo logins:** `admin@example.com` / `Admin123!` · `dcc@`, `pm@`, `reviewer@`,
`submitter@example.com` / `Demo1234!`.

**Environment gotchas that cost real time** (each is an observation, see §4):
the preview proxy strips `Authorization` → every fetch must use `authHeaders()`;
`/revisions/{id}/transmittals` returns transmittal *lines*, not transmittals;
`auth_headers` is a conftest **function**, not a fixture; WeasyPrint falls back to
ReportLab (no libpango) and SMTP is absent — both are expected, not bugs.

## 3. Which process skill, when

| Situation | Skill | Non-negotiable core |
|---|---|---|
| "Let's add X" / vague feature request | `brainstorming` | clarify scope in one pass, then a spec — no code first |
| Multi-step task with a spec | `writing-plans` | exact files, signatures, values, and the test that proves each task |
| Executing that plan in-session | `executing-plans` | one task at a time, each ends with its own green test + commit |
| Any bug / failing test / weird output | `systematic-debugging` | **root cause before any fix**; symptom patches are failures |
| About to say "done / fixed / passing" | `verification-before-completion` | run the command **in this turn**, paste the real output |
| Before writing a feature | `test-driven-development` | red → green → refactor; if you didn't see it fail you don't know it tests anything |

Two repo-specific additions to those skills:

- **Domain rules win over green tests.** This product decides with rules, AI only
  suggests; a test that encodes a guessed rule is worse than a failing one.
- **Unknown stays unknown.** The reference-system skill (see `docs/03`) marks
  behaviour it could not observe as `U`. Those stay unimplemented and are listed as
  customer questions (observation 0004). Never invent field semantics to "complete"
  a screen.

> **انحراف آگاهانه ۲ —** `superpowers` asks for a git worktree per task
> (`using-git-worktrees`). Arena ties this session to the branch
> `arena/66df99e5-edms`; creating worktrees would fork the work away from it. We
> work on that one branch and commit per task instead.

## 4. The observation log

```
skill-observations/
  observation-log/NNNN-slug.md   # one file per observation, YAML frontmatter + Issue/Improvement/Principle
  observation-log/archive/       # resolved & aged out; .id-floor is the id counter's floor
  checkpoints.log                # append-only trace
  last-review-date.txt           # `never` until a review actually runs
  cross-cutting-principles.md
```

Write a new observation with the bundle's own tool — never by hand, it derives the
id atomically:

```bash
bash skills/vendor/task-observer/scripts/new-observation.sh --body /tmp/obs.md <slug> "/home/user/EDMS"
```

Statuses: `open` · `actioned` · `declined` · `superseded` · `parked` (`parked`
must name the condition that unparks it). Review the backlog weekly per
`skills/vendor/task-observer/references/weekly-review.md`; the review record goes to
`skill-observations/reviews/YYYY-MM-DD/REVIEW.md`.

## 5. Repository map (orientation)

```
backend/app/{api/routes,models,schemas,services}   FastAPI app, SQLAlchemy models, extraction/numbering/DDM services
backend/alembic/versions/                          migrations
backend/tests/                                     pytest suite (conftest fixtures: client, db, project, users, ddm_rules)
frontend/src/{pages,components,lib}                React app, classic-EDMS skin, api client with 3-channel auth
docs/01..03                                        scope, architecture, reference-system review
docs/superpowers/plans/                            implementation plans produced with the writing-plans skill
skills/vendor/                                     third-party skill bundles (read-only)
```
