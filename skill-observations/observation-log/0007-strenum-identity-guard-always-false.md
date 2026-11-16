---
id: 7
title: "`status is CommentStatus.OPEN` is always False for values loaded from the database"
status: open
type: internal
skill: []
proposes_skill: []
target_file: [backend/app/api/routes/comments.py]
siblings_checked: "enum comparisons: comments.close guard (fixed), comments.close disposition branch (fixed), comment_list remaining-count query (SQL, unaffected), every other `is`/`is not` against a StrEnum in app/ grepped — none remain"
area: "persistence / enum comparisons"
date: 2026-10-24
session_context: "Writing the comment-disposition test found that closing an unanswered comment returned 200 instead of 409."
parked_until:
resolved:
resolution:
reference:
commands_verified: "run: pytest tests/test_comment_disposition.py -> red on test_unanswered_comment_cannot_be_closed (200 != 409); run: the same test after switching to == -> passed; run: full suite -> 156 passed"
---

**Issue:** Column types are `String(16)` while the annotation is the `StrEnum`
`CommentStatus`, so a loaded row gives the plain string `"open"`. The guard
`if comment.status is CommentStatus.OPEN:` therefore compared identity and was
**always False**: any reviewer could close a comment the contractor had never
answered, and the comment list would show "closed" with no response. The same
mistake appeared in the new branch that decides whether a disposition has already
been recorded.

**Suggested improvement:** Compare by value (`==` / `!=`), or use SQLAlchemy's
`Enum(..., native_enum=False)` so the attribute comes back as the enum member.
Add a test per guard, because this class of bug is invisible to the happy path.

**Principle:** A guard that cannot fail is indistinguishable from no guard, and it
looks like protection in review. Identity comparison against an enum member is
only safe when the column hands back the member — verify what the type returns
before trusting `is`.
