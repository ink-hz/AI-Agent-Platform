# HR R1.2 Candidate Intelligence Execution Report

Date: 2026-09-04
Branch: `feat/hr-r12-candidate`
Worktree: `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-r12-candidate`
Base: `578c1caaa704569715497d2911257c3b5e25a24a`

## Final status

`DONE_WITH_CONCERNS`

The candidate subsystem and its durable two-phase parser queue are implemented. Focused tests pass, and candidate migration 070 was applied successfully after master-owned 067/068 and position-owned 069 in disposable production and preview databases. Parent integration must still connect the existing MissionOrchestrator/MetaBot dispatch and result decoder to `CandidateParserQueue` before upload-to-ready is a complete product path.

## Commits

- `28dc70f18cf40a730238a09c72edd9d9ddcc4a24` — `feat(hr): add candidate intelligence schema`
- `464f79abdd6198e0171ac057b9d5ab3de02ae68f` — `feat(hr): add recoverable candidate intelligence service`
- `22fb6f98e20fd641281fec3954140e4e16fb67c0` — `feat(hr): expose candidate intelligence APIs`
- `66190319c69bfeda7b3b0f1889d18248ef255a29` — `feat(hr): bind candidate context and analysis versions`
- `21a11e30c42fded85b92ad41748e0c2bbc5e65ee` — `fix(hr): harden and renumber candidate migration`
- `0f7e808a8e7c3cbf606fe808ab9d6cbae7cc0bb4` — `fix(hr): harden candidate processing boundaries`
- `e6337bd78966169b6a227dd21c51eba729e88cf0` — `fix(hr): bind parser claims to exact owner scope`
- `db4c76ea7ab0b6b8003671831b423ebf2770cb5e` — `fix(hr): make candidate parsing durably recoverable`
- `3debdd6c36b66105a2561e5b86c1cbb432fa7924` — `fix(hr): close candidate processing review gaps`
- `e8c64240b69844ae1685215cba79ecca3b981646` — `fix(hr): make candidate queue restart discoverable`

## TDD evidence

All commands used the preserved root interpreter at `/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python`; no virtual environment was created, changed, staged, or removed.

### Baseline

```text
python -m pytest -q tests/test_hr_position_migration.py tests/test_hr_position_service.py tests/test_hr_position_api.py tests/test_conversation_attachment_binding.py
46 passed, 10 warnings
```

### Task 1 — migration and bounded models

RED:

```text
python -m pytest -q tests/test_hr_candidate_migration.py tests/test_hr_candidate_models.py
collection error: ModuleNotFoundError: app.hr.candidate_models
```

GREEN:

```text
python -m pytest -q tests/test_hr_candidate_migration.py tests/test_hr_candidate_models.py
10 passed
```

### Task 2 — repository, service, and per-file recovery

RED:

```text
python -m pytest -q tests/test_hr_candidate_repository.py tests/test_hr_candidate_service.py tests/test_hr_resume_batch.py
3 collection errors: candidate_repository, candidate_service, and resume_batch absent
```

GREEN:

```text
python -m pytest -q tests/test_hr_candidate_repository.py tests/test_hr_candidate_service.py tests/test_hr_resume_batch.py
16 passed
```

Task 1–2 aggregate before commit: `26 passed`.

### Task 3 — candidate APIs

RED:

```text
python -m pytest -q tests/test_hr_candidate_api.py
collection error: ModuleNotFoundError: app.hr.candidate_routes
```

GREEN:

```text
python -m pytest -q tests/test_hr_candidate_api.py
5 passed
```

Task 1–3 aggregate before commit: `31 passed`.

### Task 4 — candidate context and immutable analysis versions

RED:

```text
python -m pytest -q tests/test_hr_candidate_context.py
collection error: ModuleNotFoundError: app.hr.candidate_context
```

GREEN:

```text
python -m pytest -q tests/test_hr_candidate_context.py tests/test_hr_candidate_service.py
16 passed
```

Task 1–4 aggregate before commit: `41 passed`.

### Task 5 — security, recovery, and review hardening

The first exact regression run exposed an isolated migration ordering mismatch. The final allocation is master 067/068, position 069, and candidate 070; the integrated test assembles that exact chain without merging master into this worktree.

Additional review RED/GREEN evidence:

```text
test_domain_validation_failures_are_projected_as_422_not_server_errors
RED: uncaught ValueError from protected candidate facts
GREEN: 1 passed

test_idempotent_replays_are_bound_to_the_complete_mutation_payload
test_batch_replay_rejects_a_changed_attachment_set
RED: 2 failed
GREEN: 2 passed
```

Independent review RED evidence:

```text
python -m pytest -q tests/test_hr_candidate_migration.py tests/test_hr_candidate_models.py tests/test_hr_candidate_api.py
collection error: CompleteCandidateDraft absent (plus expected migration/detail-route failures)
```

Review GREEN evidence covers the real `state='confirmed'` position contract; persistent canonical request digests and historical replay snapshots; owner-namespaced UUIDs; replay-before-mutable-feedback; nested ATS/locator/protected-field rejection; typed parser completion/failure commands; pending/completed/racing erasure rejection; precise brain grants; relation rebase with immutable old analysis; owner-concealed document/relation detail routes; 409 row conflicts; and feedback-shape validation.

The first business-readiness RED cycle added a durable parser-attempt model and failed at import because it was absent. A later pre-integration review proved that its combined claim/bind API could not discover work globally after restart and required a job before the caller knew the draft. The replacement RED cycle failed because `ClaimNextCandidateDraft`, `AttachCandidateDraftExecution`, and `CandidateParserQueue` were absent. GREEN provides a database-only two-phase contract: global atomic `claim_next` with `FOR UPDATE ... SKIP LOCKED`, an initially unbound attempt containing owner/draft/position/attachment/request identity, exact-worker recovery, and a separately idempotent execution attach.

The attach function accepts only the repository-supported `hr-bot`, validates a terminal job/turn plus run/mission/conversation owner and deterministic request chain, and rejects position-bound conversations. A processing attempt is the authoritative special-purpose parser input mapping: its retained, ready attachment may be granted to the matching turn without changing the attachment's original conversation ownership. The attach rejects any explicit `turn_input` belonging to another attachment, while an unbound parser turn is valid. Real PostgreSQL coverage includes crash recovery, second-owner and same-owner/wrong-request job substitution, wrong-agent and unfinished-execution rejection, wrong explicit input rejection, lease expiry/requeue, successful and failed execution terminals, terminal owner/draft/worker isolation, and full-payload terminal replay conflicts. Brain workers receive only function execution grants, never table-wide SELECT; app-role direct start/complete/fail grants and Python bypass methods were removed.

The position cross-contract RED cycle showed that 101 feedback rows or large corrections could exceed the downstream fragment limits. GREEN keeps all feedback persisted/readable while deterministically selecting newest-first feedback, at most 100 rows, under a 65,536-byte UTF-8 prompt budget. Only injected feedback IDs are returned for later analysis provenance.

### Final review remediation

Observed RED failures covered all review gaps before implementation:

```text
parser queue terminal API: 2 failed (worker identity absent)
terminal/attach migration contract: failed static assertions
analysis snapshot/API/provider/repository: 5 failed
resume coordinator bypass removal: 5 failed
repository newest-100 boundary: 1 failed
revised attempt-authoritative attachment contract: 2 failed
restart discovery interfaces: 3 failed (queue, repository, and SQL absent)
real PostgreSQL discovery: UUID aggregate undefined, then GREEN after explicit cast
terminal failure semantics: failed execution could not be attached, then GREEN
```

GREEN behavior now includes:

- claimed terminal commands carry and transactionally validate exact attempt, owner, draft, worker, request, row version, job, turn, agent, and completed status;
- terminal idempotency replays validate the complete payload and reject changed facts/error codes;
- analysis requests persist exactly the caller's immutable feedback snapshot (maximum 100), SQL validates every feedback row against the selected context, and replay compares feedback identity;
- task context and comparison feedback selection use the relation's exact context; repository queries enforce newest-first `LIMIT 100`, and prompt budgeting returns IDs matching the injected prompt;
- only claimed queue functions are exposed to the brain role; direct app processing mutations and coordinator/service/repository bypasses are absent;
- processing-attempt expiry has a partial recovery index.
- `recover_next(worker_id)` finds the oldest unexpired attempt with `FOR UPDATE SKIP LOCKED`, so restart recovery needs no in-memory attempt ID;
- `discover_execution(attempt_id, worker_id)` returns exactly one owner/request-scoped, unbound `hr-bot` execution identity, reports zero as not-yet-scheduled, and rejects ambiguity;
- discovery/attach accept only terminal job/turn states; completion requires successful `completed` status, while failure accepts failed/cancelled/interrupted execution or a completed execution whose output cannot be parsed.

Final focused regression:

```text
python -m pytest -q backend/tests/test_hr_candidate_*.py backend/tests/test_hr_resume_batch.py backend/tests/test_conversation_attachment_binding.py
98 passed, 10 warnings
```

Final static gate before the hardening commit:

```text
python -m compileall -q backend/app/hr
ruff check --select I backend/app/hr backend/tests/test_hr_candidate_*.py backend/tests/test_hr_resume_batch.py
git diff --check
All checks passed
```

Integrated migration-order verification uses an automatically cleaned temporary migration directory containing the candidate-branch migrations through 066, current master migrations 067/068, position migration 069, and candidate migration 070. The committed PostgreSQL test invokes the existing disposable control-database fixture for both environments and actually calls candidate confirmation and parser functions:

```text
tests/test_hr_candidate_database.py
1 passed
```

## Scope and behavior covered

- CandidateDraft, durable CandidateDraftProcessingAttempt, Candidate, CandidateDocument, PositionCandidate, immutable CandidateAnalysisVersion, and append-only HumanFeedback.
- Protected/unrelated personal-fact rejection and no recruiting workflow fields.
- Batch request payload binding, deterministic per-file identities, isolated ready/failed siblings, retry in place, and persisted batch reconstruction.
- Explicit identity ambiguity handling; no candidate creation or merging from a name alone.
- Owner/position/candidate concealment, exact context/document versions, ready/retained attachment checks, and no storage locator serialization.
- Match, candidate interview-plan, comparison analysis kinds, same-context comparison, evidence/unknown/conflict/question separation, and no score-only ranking.
- Human feedback remains separate and is referenced by later analysis versions without modifying old AI output.
- Private/no-store API responses and 404/409/422/503 projections.
- Exact candidate task-input validation replaces the position 069 fail-closed seam and checks relation, context, ready/unexpired/non-erasing document attachments, and feedback provenance.

## Integration notes

1. Final migration allocation is master 067/068, position 069, candidate 070. Parent integration still owns router mounting, dependency composition, the migration ceiling, and end-to-end acceptance; none were changed here.
2. Candidate 070's context owner foreign keys and position task validation seam were exercised with the actual position 069 migration in disposable production and preview databases.
3. The 10 warnings are pre-existing Starlette `TestClient` cookie deprecation warnings in `test_conversation_attachment_binding.py`.
4. No production migration or data apply was run.
5. Blocking parent-integration dependency: wire MissionOrchestrator/MetaBot dispatch and result decoding to `CandidateParserQueue.claim_next`, `recover_next`, `discover_execution`, `attach_execution`, `recover_attempt`, `complete`, and `fail`. Until that wiring is merged and acceptance-tested, an uploaded draft has a durable queue/worker contract but no automatically scheduled parser in this branch alone.
6. The parent integration must teach `ConversationContextBuilder`/`MissionOrchestrator` to resolve the single processing attempt by the same owner and `turn.client_request_id`, then grant that attempt's sole `attachment_id` as parser input. It must not reassign the attachment's conversation or copy its bytes.
