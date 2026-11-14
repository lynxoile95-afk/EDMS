# Reply Sheet + print fidelity — implementation plan

> **For the agent executing this:** follow `skills/vendor/superpowers/skills/executing-plans/SKILL.md`.
> Work task by task, in one session, and tick the checkboxes as you go. If a task's
> premise turns out wrong, stop and update this file before writing code.

**Goal.** The Transmittal Workspace already has a *Reply Sheet* tab (screenshots 012/013)
but nothing behind it. Make the reply sheet real data — one row per comment, with the
contractor's response and the coordinator's disposition — and make the two printed
artefacts (the transmittal cover sheet and the reply sheet) faithful in the ReportLab
path, which is the only path available on hosts without Pango.

**Why now.** The review cycle is the product's core loop: a comment is registered,
answered, then judged. The referral route (committed `66ed487`) moves the *sheet*; the
reply sheet is what the discipline engineers actually annotate. Printing is how the
sheet leaves the system.

---

## Global Constraints

* Persian, RTL, Jalali dates in every human-facing string; English in code.
* No new dependencies. WeasyPrint is preferred when importable, ReportLab is the
  guaranteed path — both must produce the same information, not the same pixels.
* Generated PDFs are built from the database, never from a stored file.
* `backend/app/services/rendering.py` stays pure: it receives a context dict and returns
  `bytes`; it does not touch the session.
* Every endpoint gets a Persian `summary=` and the project ACL (`acl.require_project`).

## Review Focus

* A row with no response must still print (empty cell, not a dropped row).
* Disposition is `CommentDisposition`; an unknown value must not silently become
  `accepted`.
* The two PDFs must fit A4 portrait with ≥ 8 pt body text and repeat the table header on
  every page.

---

## Task 1 — `reply_sheet_rows()`: the data behind the tab

**Files.** `backend/app/services/reply_sheet.py` (new),
`backend/tests/test_reply_sheet.py` (new).

**Contract.**

```python
@dataclass(frozen=True)
class ReplySheetRow:
    doc_code: str | None
    title: str | None
    rev: str | None
    line_no: int
    comment_code: str
    comment_text: str
    author_name: str | None
    response_text: str | None
    disposition: str | None
    status: str
    created_at: datetime


def reply_sheet_rows(db: Session, transmittal: Transmittal) -> list[ReplySheetRow]:
    """One row per comment, ordered by line then by comment creation.

    Documents without comments contribute no rows; a transmittal with no comments
    at all returns an empty list (the UI shows its EmptyState).
    """
```

**Steps.**

- [x] Write `tests/test_reply_sheet.py` first: a transmittal with two revisions, one
      answered and one unanswered comment → two rows, ordered, with the response
      carried on the first row only.
- [x] Watch it fail (`ModuleNotFoundError: app.services.reply_sheet`).
- [x] Implement with a single query joining `CommentList → Comment → DocumentRevision →
      MDLItem`, filtering `DocumentRevision.id.in_(line revision ids)` of the
      transmittal.
- [x] `pytest tests/test_reply_sheet.py -q` green — **2 passed** (2026-10-24).

## Task 2 — `render_reply_sheet()`: the printable form

**Files.** `backend/app/services/rendering.py` (extend),
`backend/tests/test_reply_sheet.py` (extend).

**Contract.**

```python
REPLY_SHEET_HTML: str          # sibling of COVER_HTML, same page furniture
def build_reply_context(*, number, project_name, from_org, to_org, issue_date,
                        rows: list[dict], generated_at=None) -> dict
def render_reply_sheet(context: dict) -> bytes   # WeasyPrint → ReportLab → RuntimeError
```

**Steps.**

- [ ] Test asserts the produced bytes start with `b"%PDF"` and that
      `build_reply_context` renders a row into `rows` HTML with the response escaped
      (`<` → `&lt;`).
- [ ] Add the template: same header as the cover sheet, then a table with
      ردیف / کد مدرک / عنوان / Rev / کد نظر / نظر / پاسخ / وضعیت نظر / تعیین تکلیف.
- [ ] ReportLab path draws the same table (see Task 3 for the shared helper).

## Task 3 — ReportLab fidelity: draw the table, not just the header

**Files.** `backend/app/services/rendering.py`.

Today `_render_reportlab` prints six header lines and then loops over `line_i` keys that
nothing ever sets, so the fallback PDF carries no document rows at all. Replace it with a
small grid renderer used by both PDFs.

**Contract.**

```python
def _draw_grid(pdf, *, x, y, width, columns: list[tuple[str, float]],
               rows: list[list[str]], shape, font_name: str) -> float:
    """Draw a bordered table with a repeating header; return the new y (may be <0)."""
```

**Steps.**

- [ ] Test: `render_pdf(build_context(...))` output contains each `doc_code` of the
      context when `weasyprint` is missing (monkeypatch the import to raise).
- [ ] Implement the grid: header row on a dark fill, page break at the bottom margin,
      header repeated, cells wrapped by character budget (no textwrap on shaped text).
- [ ] Both `_render_reportlab` and the new reply-sheet fallback call it.

## Task 4 — Endpoints and the tab

**Files.** `backend/app/api/routes/transmittals.py`,
`backend/app/schemas/transmittals.py`,
`frontend/src/pages/TransmittalDetail.tsx`, `frontend/src/lib/types.ts`.

**Contract.**

```python
GET  /api/v1/transmittals/{id}/reply-sheet        -> list[ReplySheetRowOut]
GET  /api/v1/transmittals/{id}/reply-sheet.pdf    -> application/pdf, filename "<number>-reply-sheet.pdf"
```

**Steps.**

- [ ] Backend tests: DCC gets 200 with N rows; a non-member gets 404 (project ACL).
- [ ] UI: the Reply Sheet tab renders the rows from the endpoint (it currently shows a
      hard-coded sample) and a ribbon button «دریافت برگه پاسخ» downloads the PDF.
- [ ] `npx tsc --noEmit && npx vite build`, `pytest` full suite, then a live `curl` of
      both endpoints with the seeded `dcc@example.com` session.

---

## Out of scope (deliberately)

* **Judgment / Owner's Comment columns** — these are `U` items in
  `docs/03-reference-system-review.md`; the customer names them but does not describe who
  may write them. Parked, not guessed.
* **Paraph (لیست پاراف) and document chain (زنجیره مدارک)** — separate subsystems; each
  needs its own plan (`superpowers:writing-plans`, one plan per subsystem).
* **Technical Query module** — separate subsystem, same rule.
