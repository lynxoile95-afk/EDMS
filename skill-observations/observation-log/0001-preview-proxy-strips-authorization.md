---
id: 1
title: "Preview proxy strips Authorization, so binary downloads 401 while JSON calls pass"
status: open
type: internal
skill: []
proposes_skill: []
target_file: [frontend/src/lib/api.ts]
siblings_checked: "transport/auth: token-store, request(), blobUrl(), download() — request() already carried three channels; blobUrl() excluded it and was added; no other family member existed"
area: "api client — binary downloads"
date: 2026-10-24
session_context: "User reported an empty drawing preview on the review page (screenshot). Task: review new skills and run the app."
parked_until:
resolved:
resolution:
reference:
commands_verified: "run: curl -sL -o /dev/null -w '%{http_code} %{size_download}B' .../api/v1/files/source-files/<id> -H 'X-Access-Token: <t>' -> 200 2050B; run: same URL with only Authorization through the vite proxy -> 302 (proxy forwards locally, so the strip happens at the outer preview proxy)"
---

**Issue:** The review page showed «فایل قابل نمایش نیست» instead of the drawing. The
API log proved the cause: `401 ... no credentials were received` on
`GET /api/v1/files/source-files/<id>`. Every JSON call worked because `request()`
attaches the token three ways (`Authorization`, `X-Access-Token`, cookie), but
`blobUrl()` — the only path for binary downloads — attached `Authorization` alone,
and the preview proxy strips it. So the page could not tell a stripped header from
a missing file.

**Suggested improvement:** One `authHeaders()` helper used by both `request()` and
`blobUrl()`, `credentials: "include"` on the download path, and a 401-triggered
refresh retry. Report the real status in the UI instead of the generic message.

**Principle:** An authentication mechanism that exists in two code paths is a
mechanism that works in one of them. Downloads fail silently by design (the user
sees an empty box), so the transport rule must be shared code, not a convention.
