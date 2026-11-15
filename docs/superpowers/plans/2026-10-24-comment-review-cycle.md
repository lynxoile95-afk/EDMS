# Comment Review Cycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between our comment handling and the reference system's review cycle: a comment carries a *disposition* from the vocabulary the customer already uses, and the Reply Sheet becomes a real document generated from a transmittal's comment lists.

**Architecture:** One nullable column on `comments` plus an enum, written by the existing `POST /comments/{id}/close` route; no table is introduced and nothing is back-filled. The Reply Sheet is a **read model** over `comment_lists` → `comments` for a transmittal, rendered by the existing PDF service, so it cannot drift from the data it summarises.

**Tech Stack:** FastAPI · SQLAlchemy 2.x · Alembic · Pydantic v2 · pytest · React + TS (labels only).

**Spec:** `docs/03-reference-system-review.md` §3 rows 8, 9 and §5 (the `U` paragraph). Behaviour vocabulary is taken from `edms-engineering-docs.skill` → `references/forms-and-glossary.md`, tagged **O** (observed on screen).

## Global Constraints

- Persian, RTL, Jalali display; UTC storage. Human copy Persian with bilingual codes in parentheses, e.g. «پذیرفته (Accepted)».
- Enum values are the **English screen values** the reference system prints — `accepted`, `canceled`, `commented`, `no_comment`, `rejected` — stored as those strings, never as integers.
- Migrations are additive and reversible: `0002` adds one nullable column and drops it on downgrade. No data migration.
- `code` (1/2/3) keeps its meaning; disposition is a **separate** axis, never a replacement.
- Never invent the Reply Sheet's Judgment / Provider-Clarification semantics: they stay `U` and are parked in Task 3.
- Every task ends green: `cd backend && ../.venv/bin/python -m pytest` (whole suite) and `cd frontend && npx tsc --noEmit`.

## Review Focus

1. **Closing without answering** — a comment with no `response_text` is closed; expect `409`, not a silent close.
2. **Disposition on a re-closed comment** — closing twice must not rewrite the first disposition.
3. **Unknown disposition value** — a client sending `"Accepted"` (capital) or `"ok"` gets `422`, never a stored garbage string.
4. **Reply Sheet on a transmittal with no comments** — expect an empty but valid PDF, not a 500.
5. **Mixed-script export** — Persian comment text inside the PDF table must not break RTL shaping or column order.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/models/enums.py` | `CommentDisposition` enum + `DISPOSITION_VALUES` |
| `backend/app/models/transmittal.py` | `Comment.disposition` column |
| `backend/alembic/versions/0002_comment_disposition.py` | additive migration |
| `backend/app/schemas/transmittals.py` | `CommentClose`, `CommentOut.disposition` |
| `backend/app/api/routes/comments.py` | close route takes an optional disposition |
| `backend/app/services/rendering.py` | Reply Sheet renderer (reuses the transmittal PDF path) |
| `backend/app/api/routes/transmittals.py` | `GET /transmittals/{id}/reply-sheet.pdf` |
| `frontend/src/lib/format.ts` | `DISPOSITION_LABELS` |
| `frontend/src/pages/CommentList.tsx` | show + choose disposition |
| `backend/tests/test_comment_disposition.py` | Task 1 tests |
| `backend/tests/test_reply_sheet.py` | Task 2 tests |

---

## Task 1 — Comment disposition vocabulary  ← *executed in this session*

**Files:** `models/enums.py`, `models/transmittal.py`, `alembic/versions/0002_comment_disposition.py`, `schemas/transmittals.py`, `api/routes/comments.py`, `tests/test_comment_disposition.py`

**Interfaces:**

```python
class CommentDisposition(StrEnum):
    ACCEPTED = "accepted"
    CANCELED = "canceled"
    COMMENTED = "commented"
    NO_COMMENT = "no_comment"
    REJECTED = "rejected"

DISPOSITION_VALUES: tuple[str, ...] = tuple(m.value for m in CommentDisposition)

class CommentClose(BaseModel):
    disposition: CommentDisposition = CommentDisposition.ACCEPTED
    note: str | None = None
```

- [x] Write the failing test: create → respond → close with `rejected`; assert `disposition == "rejected"` and that closing an unanswered comment returns 409.
- [x] Run it: `pytest tests/test_comment_disposition.py` → red (`CommentClose` does not exist).
- [x] Add the enum, the column and the migration.
- [x] Wire the route; reuse `CommentClose` for the request body.
- [x] Run the whole suite → green.
- [x] Frontend: `DISPOSITION_LABELS` + a select in `CommentList.tsx`.
- [x] Commit: `feat(comments): disposition vocabulary (accepted/canceled/commented/no_comment/rejected)`.

## Task 2 — Reply Sheet as a generated document

**Files:** `services/rendering.py`, `api/routes/transmittals.py`, `tests/test_reply_sheet.py`

- [ ] Test: `GET /transmittals/{id}/reply-sheet.pdf` returns `application/pdf` starting with `%PDF`, for a transmittal whose revision has two comments; and for a transmittal with none.
- [ ] Implement `render_reply_sheet(transmittal) -> bytes` next to `render_transmittal`, same font/RTL path, columns: ردیف · کد · شرح نظر · پاسخ پیمانکار · وضعیت · تاریخ.
- [ ] Add the route, ACL-scoped exactly like `GET /transmittals/{id}/pdf`.
- [ ] Wire the «Reply Sheet» tab's download button to the new endpoint.
- [ ] Green suite + commit.

## Task 3 — PARKED: Judgment / Owner's Comments / Provider Clarification

Not a code task. `status: parked`, unpark condition:
**«پاسخ کارفرما به پرسش‌های ۳ تا ۵ سند docs/03 (معنای -RW و ستون‌های Judgment/Provider Clarification) دریافت شود»**.
Until then the Reply Sheet ships without those columns and the UI does not show them.

---

## Out of scope (separate plans, one per subsystem)

Technical Query module · پاراف / زنجیره مدارک · Permitted Document List + Contract
model · master-data lookup tables · QlikView-style reporting. Each is an independent
subsystem and gets its own plan when it is scheduled; folding them into this one
would produce a plan whose tasks cannot be reviewed or rejected independently.
