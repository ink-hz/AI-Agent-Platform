# HR Result Pipeline Refactor Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. This plan delivers migration stage 1, not the entire runtime migration.

**Goal:** Existing durable HR results reach conversations despite progress-event additions, attachment failures, or a hanging browser event stream.

**Architecture:** Separate Relay intake from public projection, commit text before optional result enrichment, and persist enrichment retry state atomically with the answer. The UI consumes snapshots independently of streaming and never resubmits work on reconnect.

**Tech Stack:** Python, psycopg/PostgreSQL, React/TypeScript, pytest, Vitest.

## Global Constraints

- Work only in `.worktrees/hr-position-core-availability`; preserve existing commits and ignored environments.
- No production writes, deployment, business-message replay or unrelated application changes in this plan.
- No new planner model call, broker, crawler, antivirus or user-access restriction.
- Keep legacy IDs and failed history. Never auto-resubmit a Turn from a read/reconnect path.
- Preserve strict artifact owner/conversation/task/agent validation and privacy-filtered public events.
- Test with local real PostgreSQL; mock only transport/failure boundaries.

### Task 1: Relay protocol compatibility

**Files:** `backend/app/agent_brain/repository.py`, `backend/tests/test_agent_brain_repository.py`, `backend/tests/test_agent_brain_orchestrator.py`.

- [ ] Add failing real-DB regression: contiguous thinking-summary/work-update events followed by final result must advance the cursor, without persisting private payload fields to public events.
- [ ] Replay a direct HR result with the observed event ordering through `advance_pending`, reconstruct service and replay; expect one terminal result.
- [ ] Use the Relay protocol's accepted event vocabulary as the intake contract; keep projection whitelist independent. Reject malformed run/sequence and do not silently skip gaps.
- [ ] Run repository and orchestrator pytest suites. Commit only task files.

### Task 2: Durable result enrichment separate from text

**Files:** new `backend/control_migrations/088_conversation_result_deliveries.sql`, new `backend/app/agent_brain/result_delivery.py`, `backend/app/agent_brain/conversation_projection.py`, `backend/app/agent_brain/orchestrator.py`; related conversation projection / attachment delivery / migration tests.

**Interface:** `ConversationResultDelivery(repository, result_projection).project_pending(limit=50) -> int`. Each mission gets one delivery record bound to its assistant message; states pending/completed/failed, attempts, next_attempt_at and sanitized last_error_code.

- [ ] Write failing tests: valid text remains completed when artifacts are pending/invalid; optional projection rollback cannot remove the answer; reconstruct projection service after failure and retry without duplicate citations or bindings; one failing item does not block a healthy item.
- [ ] Migration creates delivery table referencing existing mission/message identities, unique per mission and message, bounded attempt count, due index and production/preview application grants matching existing migration patterns. No content duplication in plaintext.
- [ ] `project_terminal` stores the answer, terminal events and pending enrichment row in one transaction. Call the existing strict result projector only from a separate transaction that owns the delivery row.
- [ ] Retry due rows with `FOR UPDATE SKIP LOCKED`; persist attempt count and next attempt delay (5 seconds exponential, capped 300 seconds, 8 attempts). Roll back failed enrichment using a savepoint, then persist failure metadata without sensitive exception strings. Completion commits bindings and delivery state together.
- [ ] Orchestrator no longer gates a valid text answer on artifact readiness. Existing strict binder continues to reject foreign or non-ready artifacts. Projection loop retries enrichment even when no new mission is claimed.
- [ ] Isolate individual terminal projection failures so healthy records in the selected batch still progress. Log identifiers/reason, not answer text.
- [ ] Run migration, projection, orchestrator and attachment-delivery regression tests. Commit task files.

### Task 3: Browser read resilience

**Files:** `webui/src/pages/ConversationPage.tsx`, `webui/src/pages/ConversationPage.test.tsx`; extract a focused hook only if useful.

- [ ] Add failing tests for a stream promise that never resolves but snapshot becomes completed; answer appears, send unlocks, settled callback fires once and no POST is repeated.
- [ ] Add failing test that attachment-list failure still renders existing messages and leaves an actionable attachment warning.
- [ ] Poll snapshots at a bounded 5-second interval while a Turn is active, at most one in flight. Stop/abort on terminal, unmount or conversation switch. A failed poll must not kill future reads or clear existing messages.
- [ ] Streaming and polling share monotonic result handling so a stale active snapshot cannot overwrite a completed Turn. Stop the stream when snapshot proves terminal. Keep stream progress rendering unchanged.
- [ ] Independent material-list failure cannot reject the required conversation/messages load.
- [ ] Run `npm test -- --run src/pages/ConversationPage.test.tsx`, relevant conversation tests and `npm run build`. Commit task files.

### Task 4: Integration review and migration ledger

- [ ] Verify actual changed files with git diff and targeted/full relevant tests; independent review of transaction boundaries, replay and browser race behavior.
- [ ] Fix Important findings and rerun covering tests.
- [ ] Record exact results and remaining stages in this document; do not claim V2 direct migration, Feishu cutover, pure-read SSE or production rollout is completed.

## Progress

2026-09-07: Design and stage-1 plan recorded. No production changes. Tasks not yet complete.
