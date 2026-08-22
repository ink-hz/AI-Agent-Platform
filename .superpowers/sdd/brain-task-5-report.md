# Brain Task 5 Report: Durable single-Agent orchestrator

## Status

Complete. The Platform now advances encrypted Missions through one durable
professional-Agent state machine, using the outbound Execution Relay and a
single PostgreSQL advisory-lock leader.

## Implemented contract

- Added `MissionOrchestrator.advance_pending(limit)` with a hard limit of 50,
  `FOR UPDATE SKIP LOCKED` Mission scans, owner-scoped run recovery, CAS
  transitions, and per-Mission exception isolation.
- Covered the nine required outcomes: planner direct, direct professional,
  delegated professional plus synthesis, malformed planning protocol,
  professional failure, worker-offline interruption, timeout interruption,
  cancellation before lease, and restart after terminal upload.
- Planning and synthesis are pinned to `agent-brain-bot`; professional/direct
  phases use only a currently authorized immutable capability card. One
  Mission can persist only one professional task through the existing schema.
- Every start and terminal transition uses the Task 4 transactional repository
  methods, so the safe Mission event and state transition commit atomically.
- Added internal-only relay `job_state(run_id)` and ordered `events(run_id)`
  readers. They return stable not-found/unavailable errors and decrypt using
  the existing purpose-bound relay codec; no worker or browser route exposes
  these readers.
- Prompt builders serialize role, output schema, authorized capability cards,
  user request, delegated objective, and synthesis result into one JSON
  envelope. User/model text cannot break structural section boundaries. Every
  prompt is capped at exactly 96 KiB UTF-8.
- A committed Mission run missing its relay job is safely re-enqueued with the
  same server `run_id`, closing the process-exit window between phase commit
  and relay enqueue.
- Oversized visible results fail explicitly rather than leaving a Mission
  active after the Task 4 event schema rejects them.
- Added a session-level PostgreSQL advisory leader. Its connection is
  autocommit (never idle in transaction), and shutdown holds leadership until
  the in-flight advancement pass has drained.
- Added the `PLATFORM_AGENT_BRAIN_ENABLED` gate. It defaults off and is valid
  only with production identity plus the Execution Relay. Enabled startup
  loads and validates the capability catalog, checks all Mission relations and
  exact least-privilege app grants, and fails closed if unavailable.

## TDD evidence

Initial RED command:

```text
cd backend
.venv/bin/pytest tests/test_agent_brain_orchestrator.py \
  tests/test_execution_relay_repository.py tests/test_main.py -q
```

Initial RED result: collection stopped with two expected errors because
`app.agent_brain.orchestrator` and `app.main.agent_brain_loop` did not exist.

Additional hardening regressions were observed RED before fixes:

- restart between phase commit and relay enqueue returned zero and logged a
  missing relay job;
- the real app-role startup probe rejected correct column-level UPDATE grants;
- cancellation released advisory leadership while an in-flight thread was
  still running;
- cancellation was swallowed when that drained pass also raised;
- the advisory leader remained `idle in transaction`;
- a valid but over-8-KiB direct result left the Mission stuck in `planning`.

Fresh focused GREEN:

```text
58 passed, 1 warning in 2.79s
```

The warning is the existing FastAPI/Starlette TestClient deprecation.

Fresh adjacent regression verification:

```text
178 passed in 3.33s
```

This covered control-plane config, Agent-use authorization, and the Task 4
Mission repository after the new internal orchestration readers were added.

Fresh full backend verification, run once after review cleared all findings:

```text
2071 passed, 1 skipped, 47 warnings in 104.63s (0:01:44)
```

The 47 warnings are the existing Starlette/httpx cookie and TestClient
deprecations.

## Independent review

The initial read-only review approved the orchestrator, relay boundary, prompt
envelope, CAS/`SKIP LOCKED` advancement, advisory leader, and three startup
gates. A targeted follow-up found one Important shutdown edge: if an in-flight
pass failed while cancellation was draining it, the loop could swallow the
original cancellation and retry. The failure was reproduced, fixed by always
re-raising the captured `CancelledError` after draining/logging, and verified
with two focused shutdown tests. Final re-review reported no Critical,
Important, or Minor findings.

## Scope and follow-on boundary

Task 5 does not add public Mission APIs, SSE, UI routes, the local
`agent-brain-bot` runtime, or production deployment flags. Those remain Tasks
6 through 9. The current worker allowlist will accept `agent-brain-bot` only
after Task 8 lands, so this feature stays opt-in and fail-closed until then.
