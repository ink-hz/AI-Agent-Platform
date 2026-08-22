# Brain Task 5 Report: Durable single-Agent orchestrator

## Status

Complete. The Platform now advances encrypted Missions through one durable
professional-Agent state machine, using the outbound Execution Relay and a
single PostgreSQL advisory-lock leader.

Implementation commit: `571ab509ef766804abeeef8d597fcd2cf9bce5b7`.

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

## Post-review durability hardening

The first Task 5 review later identified four Important production gaps. They
were reproduced with RED tests and closed in commits `4c7ef50`, `78c4cb6`, and
`ca77211`:

- Relay state now carries database-trusted timing metadata. Queued deadlines,
  lease/runtime deadlines, queued cancellation, and orchestrator interruption
  atomically converge without requiring a live worker. New progress refreshes
  the trusted runtime deadline.
- Every phase persists its immutable capability-card snapshot. Recovery uses
  that snapshot, while current authorization and capability version are
  checked before new work continues. Revocation/version change reaches an
  explicit terminal state; a result already uploaded is still archived.
- Relay events are bridged idempotently through a persisted per-run cursor into
  closed-schema `agent.accepted` and `agent.progress` events. MissionRun/Task
  state follows execution, and `task.reviewed` is committed exactly once
  before synthesis.
- The scanner claims only Mission ID and owner ID. Content is owner-scoped and
  decrypted one Mission at a time; corrupt ciphertext or a missing first
  message is quarantined with a safe terminal event without blocking healthy
  Missions in the same batch.
- Migration 029 adds only `relay_event_cursor` and `reviewed_at` update grants
  to the app role and relaxes the Relay lease-shape constraint only for
  workerless cancelled/interrupted terminal jobs.

Hardening RED evidence included four failing Mission isolation/lifecycle tests,
four failing capability consistency tests, a failing progress-deadline test,
and two failing Relay/Mission interruption-consistency tests. Each group was
observed GREEN after its corresponding implementation.

Fresh focused verification after the final fix:

```text
162 passed in 6.40s
```

This set includes a real PostgreSQL Relay integration covering planner output,
professional Agent state/progress/result, the durable review checkpoint, and
synthesis creation.

The first post-hardening review found two related Important issues: the trusted
orchestrator lacked an atomic Relay `interrupt()` primitive, so revocation could
leave Relay `cancelled` while MissionRun was `interrupted`. Commit `78c4cb6`
added the primitive and aligned both terminal states. Targeted re-review then
reported no Critical, Important, or Minor findings.

Fresh full backend verification after review and the migration-version assertion
fix:

```text
2086 passed, 1 skipped, 47 warnings in 103.40s (0:01:43)
```

The warnings remain the pre-existing Starlette/httpx deprecations.

## Second-wave reliability closure

A later strict review found one Critical and four Important gaps in the first
hardening pass. Commits `f34ac7b`, `04fda49`, and `9981531` close them without
adding public API or UI scope:

- Worker heartbeats now renew every assigned active lease using the configured
  lease duration, so a progressing 300-second run is not killed by its initial
  45-second lease. The cloud heartbeat protocol returns typed forced-terminal
  requests; the Worker cancels MetaBot, persists the exact terminal state,
  uploads/finishes it, removes the local active run, and can lease the next job.
- A capability-interrupt race now rereads the Relay terminal state when the
  atomic interrupt loses. MissionRun and Mission archive the real
  completed/failed/cancelled/interrupted outcome instead of fabricating an
  interruption.
- Legal zero-Agent authorization remains distinguishable from authorization
  infrastructure failure. A member with no professional grants can still use
  Brain direct answers; unavailable authorization fails closed.
- Run snapshots contain references plus immutable capability data rather than
  duplicate request text. Direct-Agent prompts contain one request copy, and an
  exact 32-KiB request produces a serialized Relay payload below 64 KiB. Restart
  recovery reconstructs the same payload from the encrypted Mission message and
  compact persisted run snapshot.
- Public progress accepts only explicit, bounded `agent.state` fields. Raw
  log/question/file text is never projected, those private events cannot create
  public acceptance/progress milestones, and the fabricated review checkpoint
  was removed. The honest sequence is `agent.result` followed by
  `synthesis.started`.
- Mission quarantine is restricted to deterministic missing/corrupt content and
  guarded by the claimed status/row-version CAS. Database failures and unavailable
  key versions leave the Mission active. Deterministically damaged rows expose a
  readable tombstone in detail/list views, do not hide healthy history, and keep
  the safe terminal event readable.

Additional RED evidence included: a progressing lease expiring after 45 seconds,
forced cloud termination not reaching Worker cleanup, a terminal interrupt race,
legal empty grants being rejected, an exact 32-KiB direct Relay payload measuring
67,106 bytes, private relay logs fabricating `agent.accepted`, transient reads
being quarantined, and one corrupt Mission breaking history reads. The strict
typed-stop client test also rejected the original acceptance of an invented
terminal status.

Fresh focused verification after the final fixes:

```text
307 passed, 1 warning in 8.40s
```

Fresh adjacent Worker/control regression verification:

```text
139 passed, 1 warning in 3.38s
```

The independent re-review reported no Critical or Important findings. It
specifically verified the actual serialized direct Relay payload boundary,
restart reconstruction, and that private Relay event classes cannot advance the
public timeline.

Fresh full backend verification, run once after that review cleared:

```text
2097 passed, 1 skipped, 47 warnings in 105.18s (0:01:45)
```

The 47 warnings are the same pre-existing Starlette/httpx deprecations.

## Third-wave terminal delivery and key-safety closure

A final strict review found one Critical and three Important reliability gaps.
Commits `da37e17` and `627f51c` close them without expanding the public API or
UI:

- Cloud-forced `cancelled` and `interrupted` states are durable pending commands
  with explicit acknowledgement. Only unacknowledged forced terminals are
  returned by heartbeat, the query and local pending queue are capped at 100,
  and an acknowledged command is not replayed.
- A cloud terminal remains authoritative even when the Worker has already
  recorded local `completed` or `failed`. Reconciliation atomically discards
  undelivered outbox rows, adopts the cloud state, uploads that terminal, removes
  the active run, and releases capacity for the next lease. Callback/start
  concurrency is database-arbitrated and no longer deadlocks synchronous MetaBot
  callbacks.
- Migration 030 adds constrained pending/ACK fields plus a partial delivery
  index while preserving least-privilege app-only access.
- Migration 031 adds a write-once-per-version encrypted content-key canary.
  Startup and orchestration validate exact key bytes before content handling.
  A same-version wrong key is now an infrastructure error and cannot mass-
  quarantine Missions; an individual decrypt failure becomes a tombstone only
  after the same key has successfully decrypted its trusted canary.
- Every legal `agent.state`, including the real Core Chat text-only form,
  creates exactly one generic `agent.accepted`. Only whitelisted structured
  fields create `agent.progress`; raw text is never copied, and
  `agent.log`/`agent.question`/`agent.file` remain private.

RED evidence included all four local `completed`/`failed` × cloud
`cancelled`/`interrupted` combinations, stop replay after heartbeat, normal
terminal states incorrectly appearing as stops, an unbounded pending queue,
same-version wrong keys quarantining two healthy Missions, the missing canary
schema, and text-only state failing to create acceptance.

Fresh combined affected verification:

```text
400 passed in 11.09s
```

The independent strict re-review reported no remaining or new Critical or
Important findings and separately passed 16 focused tests.

Fresh full backend verification, run once after that review cleared:

```text
2113 passed, 1 skipped, 46 warnings in 105.84s (0:01:45)
```

The warnings are the existing Starlette per-request cookie deprecations.
