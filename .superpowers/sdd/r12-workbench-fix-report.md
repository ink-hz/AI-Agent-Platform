# HR R1.2 workbench review-fix report

Date: 2026-09-04

## Scope

This change closes the implementation audit and both subsequent R1.2 workbench review
passes without mounting routers in `App`, adding migrations, or implementing a
second/in-memory task engine. The frozen position
and candidate contracts were checked directly against the sibling
`feat/hr-r12-position` and `feat/hr-r12-candidate` implementations.

## Review findings closed

1. Resource projection now reads the real `AttachmentRecord.original_name`,
   `declared_mime`/`detected_mime`, state, size, and timestamp fields. The projection
   test uses a real `AttachmentRecord`, so a permissive fake can no longer hide schema
   drift.
2. The web client parses the frozen snake-case position/candidate payloads instead of
   casting them. Context confirmation uses
   `/context/drafts/{draft_context_version_id}/confirm` and the two-baseline payload;
   candidate retry/confirm use the top-level colon-action routes.
3. Promoted position materials and per-turn materials are separate states. Every new
   task starts with an empty explicit selection and submits only checked, ready items.
4. The candidate placeholder is replaced by a minimum business workflow: single/batch
   upload, isolated draft status and retry, human confirmation, candidate list/detail,
   documents, immutable analysis versions, match/interview tasks, same-context
   comparison, and separately persisted `HumanFeedback`.
5. Position artifacts query every `artifact_versions` row, including previous and
   pending/failed/quarantined versions, and uses the version creation timestamp rather
   than the position-link timestamp. Only actually ready attachment/version pairs are
   downloadable.
6. Resource collection reads first verify the exact owner/position exists; missing and
   foreign positions produce the same concealed 404.
7. The context panel renders current, all drafts, and immutable history. A 409 reloads
   the new baseline, compares modules, preserves the user's module selection, and
   offers an explicit retry against the updated baseline.
8. The workbench no longer accepts a caller-provided `runningTask` flag. It restores
   active task records through the centralized durable task API and keeps context and
   materials usable when recovery is temporarily unavailable.
9. Resources show filename, kind/version, media type, size, creation time, source
   conversation/turn, and Chinese availability text. Preview, single download, and
   explicit batch download all request short-lived tickets, validate the ticket path,
   and apply the deployment prefix with `platformPath`.
10. The workspace permits vertical scrolling, preserves a usable chat/composer height,
    gives non-chat panels their own scroll boundary, exposes `tablist`/`tab`/`tabpanel`
    semantics, and collapses candidate/material controls into a one-column mobile
    layout with a bottom material drawer.
11. Every R1.2 mutation accepts and retains an `AbortSignal`; panels abort superseded
    work and unmount cleanup aborts in-flight requests.
12. `section` is synchronized from the route prop, including reuse during browser
    back/forward navigation.
13. Historical binding application requires the persistence callback to report
    `applied` (`true`) or idempotent `no-op` (`false`). Summary counts now report real
    inserted material/artifact rows and no-ops separately.

## Second-review closure

1. Candidate drafts use 1/2/4/8-second backoff, stop on terminal state or after six
   attempts, abort on teardown, and retain a visible manual refresh action.
2. Ready drafts expose editable name/facts, source and unknowns, plus a mandatory
   new-versus-merge identity decision; 409 recovery retains all human edits.
3. Feedback binds to the maximum analysis version, never the API's oldest item.
4. Candidate tasks refresh immutable analyses after completion. Comparison/history
   retain result, evidence, conflicts, unknowns, questions and provenance, while
   cross-context comparison is disabled and pruned.
5. Confirmed position context is returned to parent state immediately.
6. Per-turn materials clear only on accepted task start, survive failure, reset across
   conversations, and refresh/prune after promotion or removal.
7. Resource availability includes retention, erasure, locator, version/result and
   preview-derivative boundaries. An unreadable historical row safely degrades to
   unavailable without hiding the list.
8. `startTask` uses a discriminated generic contract with compile-time negative cases
   and runtime guards; candidate comparison remains on its dedicated endpoint.
9. Durable-task UI distinguishes loading, unavailable, empty and active states; it
   polls accepted/running tasks and shows kind/status/error. Position and candidate
   uncertain retries preserve one idempotency key until reconciliation.
10. Batch download pre-opens every window synchronously, then navigates to validated,
    prefix-aware ticket URLs and closes failed pending windows.
11. Hard-stale mode disables and handler-guards all context/candidate/quick-task writes
    and the candidate uploader.
12. One persistent `DirectAgentWorkspace` retains real conversation navigation,
    composer and execution stream in every section. Material/context overlays are
    focused, Escape-closeable semantic dialogs with responsive backdrops/drawers.
13. Frozen context module labels, real before/after conflict values, and roving tabs
    with Arrow/Home/End complete the minor review items.

## Durable task adapter contract

The workbench owns only this HTTP client contract. The integration branch adapts it to
the existing Conversation/Turn/Mission/execution records and immutable HR context
envelope; it must not create an in-memory task store.

### Start or continue a task

`POST /api/hr/positions/{position_id}/tasks`

Headers: `X-CSRF-Token` and UUID `Idempotency-Key`.

Request:

```json
{
  "task_kind": "candidate_match",
  "context_version_id": "uuid-or-null",
  "candidate_id": "uuid-or-null",
  "position_candidate_id": "uuid-or-null",
  "material_ids": ["explicit-ready-attachment-uuid"],
  "conversation_id": "validated-position-conversation-uuid-or-null"
}
```

For `candidate_match` and `candidate_interview_plan`, `candidate_id` and
`position_candidate_id` are always sent as a pair. The adapter must prove that the
relation belongs to the requested position and that its confirmed context equals
`context_version_id`. Candidate comparison does not use this endpoint; it calls the
frozen `/candidate-comparisons` API. For position quick tasks, a valid current
conversation is continued; a null conversation asks the adapter to create a new
position-bound HR conversation and append the first Turn.

Response (`202` or replay-equivalent success):

```json
{
  "task_id": "durable-task-id",
  "status": "accepted|running|completed|failed",
  "task_kind": "jd|jr|talent_profile|sourcing_strategy|position_interview_plan|candidate_match|candidate_interview_plan",
  "error": "nullable failure detail"
}
```

### Recover active tasks

`GET /api/hr/positions/{position_id}/tasks?status=active`

Response:

```json
{
  "items": [
    {
      "task_id": "durable-task-id",
      "status": "accepted|running",
      "task_kind": "talent_profile",
      "error": null
    }
  ]
}
```

### Reconcile one candidate task to a truthful terminal state

`GET /api/hr/positions/{position_id}/tasks/{task_id}`

The response uses the task shape above and, for candidate tasks, additionally returns
the exact `candidate_id` and `position_candidate_id`. The browser rejects a mismatched
binding, renders `failed` with its error rather than inferring success from absence in
the active list, and refreshes analysis versions only for the candidate that launched
the completed task.

The server remains responsible for exact owner/position authorization, idempotent
replay, original envelope recovery after browser/worker restart, and deriving status
from durable execution records.

## TDD and verification evidence

RED evidence recorded before implementation:

- Real `AttachmentRecord`, full-version resource, missing-position, and no-op backfill
  tests initially failed (five failures).
- Frozen context/candidate route and parser tests failed against the original casts and
  paths.
- Context conflict, candidate workflow, explicit material selection, durable recovery,
  safe download, layout, and route synchronization tests failed before their product
  code was added.
- The tightened task-envelope tests failed in four places before the candidate pair and
  conversation continuation fields were implemented (`14 passed, 4 failed`).

Fresh GREEN evidence after the second review:

- Backend focused suite: `13 passed in 0.18s`.
- Frontend focused workbench/API/style suite: `74 passed` tests.
- The broad frontend run exercised `104` files / `889` tests: every HR test passed;
  one unrelated timing-sensitive FAE URL-history case failed and then passed in an
  immediate isolated rerun (`1 passed`, `39 skipped`).
- `npm run build`: TypeScript and Vite build succeeded; Vite emitted only the existing
  chunk-size advisory.
- `python -m compileall -q app/hr`: passed.
- Ruff import check on changed backend modules/tests: passed after formatting.
- `git diff --check`: passed.

Fresh GREEN evidence after final independent-review remediation:

- Full frontend suite: `106` files / `903` tests passed.
- Backend resource/API suite: `12 passed in 0.21s`.
- `npm run build`: TypeScript and Vite production build passed; only the existing
  chunk-size advisory was emitted.
- `python -m compileall -q backend/app/hr`: passed.
- Added RED→GREEN coverage for section deep-link mounting, authoritative candidate
  task terminal status and binding, interleaved/persistent mutation IDs, failed-task
  material retention, hard-stale resource tickets, resource-panel refresh, complete
  analysis provenance, derivative expiry, typed row degradation, polling reset, and
  drawer focus trapping/restoration.

## Remaining integration risks and boundaries

- The durable `GET/POST /tasks` adapter, including the new single-task terminal lookup,
  remains owned by the parent integration branch. Until it is mounted against the
  existing durable engine, browser/worker restart recovery cannot be proven end-to-end
  from this isolated workbench branch.
- App/router mounting is now implemented and covered by a member deep-link refresh test;
  historical-import CLI composition remains parent integration scope.
- The resource repository query is unit-verified against the real schema contract but
  was not exercised against a live PostgreSQL instance in this isolated worktree.
- The broad frontend suite retains the pre-existing jsdom `Window.scrollTo` warning in
  `HrPositionIndex.test.tsx`; all related tests pass.
