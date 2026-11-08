---
id: 0008
date: 2026-10-24
type: pattern
tags: [api, acl, inbox, routing]
project: EDMS
---

## Context
Implementing the review route (contractor → document control → management → four
discipline engineers) and the «کارتابل من» filter it needs.

## Observation
"Everything I can see" and "what is waiting on me" are different questions, and mixing
them makes the inbox useless. The first version of `?mine=true` also returned every sheet
on which the user was merely a *recipient*. The Civil engineer then kept seeing a sheet
that had already been answered, because their DDM recipient row lived on forever.

The definition that matches the organisation's language:

```
mine = an open referral addressed to me
     ∪ a recipient row of mine that I have not acknowledged yet
     ∪ (only for DCC / project manager / admin) sheets whose stage is draft or dcc
```

## Value
The discipline engineer acceptance criterion ("sees only sheets referred to them") is
tested, so this stays true. Getting it wrong is invisible in code review and obvious to
the user, so the definition needs to be written down, not re-derived.

## Action
`app/services/routing.py::pending_for_user` plus the `mine` branch in
`GET /projects/{id}/transmittals`; asserted by
`tests/test_review_routing.py::test_full_referral_chain`. Recorded in README §۸-۱.
