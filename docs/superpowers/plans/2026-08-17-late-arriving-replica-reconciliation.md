# Late-Arriving Replica Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make late-arriving FAE/ADMIN sessions eligible for cloud replication and safely reconcile the current 30-Session/56-Turn production gap without clearing cloud data.

**Architecture:** Add a replication-only timestamp to `RawSession`, selected as `greatest(last_active_at, source_synced_at)` for mirrored sources and `last_active_at` for live MetaBot data. Keep the existing signed sequence/digest protocol, but add a guarded local state rewind command so the current chain can rescan an older replication window and Upsert stable sanitized IDs.

**Tech Stack:** Python 3.11, PostgreSQL/psycopg, Ed25519 signed JSONL batches, AES-GCM cloud replica, pytest, launchd, Docker Compose.

## Global Constraints

- Do not clear or reset `platform_replica` production data.
- Do not change stable HMAC-derived Session, Turn, or user identifiers.
- Preserve `source_instance_id`, `next_sequence`, and `previous_digest` during rewind.
- Do not modify or restart FAE, ADMIN, or MetaBot services.
- Use TDD: every production behavior change must first have a failing test.
- Production completion requires 514 AI FAE Sessions and 1025 AI FAE Turns on both local and cloud.

---

### Task 1: Replication-aware Session cursor

**Files:**
- Modify: `backend/app/cloud_replica/models.py`
- Modify: `backend/app/cloud_replica/source.py`
- Modify: `backend/app/cloud_replica/exporter.py`
- Test: `backend/tests/test_cloud_source.py`
- Test: `backend/tests/test_cloud_exporter.py`

**Interfaces:**
- Produces: `RawSession.replica_updated_at: datetime | None` and `RawSession.replication_cursor_at: datetime`.
- `ReplicaSource.fetch_sessions()` pages by `(replica_updated_at, session_key)`.
- `ReplicaExporter.export_batch()` checkpoints using `replication_cursor_at` while serialized records retain business `last_active_at`.

- [ ] **Step 1: Write failing source tests**

Update SQL assertions to require the explicit expression and add a FAE fixture whose old `last_active_at` is paired with a new `source_synced_at`/`replica_updated_at`. Assert it is returned with the new cursor timestamp.

```python
assert "greatest(last_active_at, coalesce(source_synced_at, last_active_at)) as replica_updated_at" in SESSION_SQL.lower()
assert "(replica_updated_at, session_key)" in SESSION_SQL.lower()
assert result[0].replication_cursor_at == now
assert result[0].last_active_at == now - timedelta(days=3)
```

- [ ] **Step 2: Run source tests and observe RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_cloud_source.py`

Expected: failures because `replica_updated_at` and replication cursor SQL do not exist.

- [ ] **Step 3: Implement the source cursor**

Add an optional field and stable fallback:

```python
replica_updated_at: datetime | None = None

@property
def replication_cursor_at(self) -> datetime:
    return self.replica_updated_at or self.last_active_at
```

In `SESSION_SQL`, calculate `replica_updated_at` in a CTE and use it for the lower bound, upper bound and order while retaining `last_active_at >= retention_floor`. Map the selected column into `RawSession`.

- [ ] **Step 4: Write failing exporter pagination test**

Create 101 sessions with an old shared `last_active_at` and a new shared `replica_updated_at`. Assert the first batch contains 100, the second contains 1, and the second checkpoint equals the replication timestamp rather than business time.

- [ ] **Step 5: Run exporter test and observe RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_cloud_exporter.py -k replica_updated`

Expected: exporter checkpoints on `last_active_at`, causing invalid pagination/checkpoint assertions.

- [ ] **Step 6: Implement exporter checkpoint selection**

Use `(session.replication_cursor_at, session.session_key)` when selecting the page checkpoint and set `next_watermark = checkpoint.replication_cursor_at`. Do not place `replica_updated_at` in the sanitized record.

- [ ] **Step 7: Verify Task 1**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_cloud_source.py tests/test_cloud_exporter.py tests/test_cloud_protocol.py tests/test_cloud_importer.py`

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add backend/app/cloud_replica/models.py backend/app/cloud_replica/source.py backend/app/cloud_replica/exporter.py backend/tests/test_cloud_source.py backend/tests/test_cloud_exporter.py
git commit -m "fix: replicate late-arriving mirrored sessions"
```

### Task 2: Guarded export-state rewind

**Files:**
- Modify: `backend/app/cloud_replica/exporter.py`
- Modify: `backend/app/cloud_replica/cli.py`
- Test: `backend/tests/test_cloud_exporter.py`
- Test: `backend/tests/test_cloud_cli.py`

**Interfaces:**
- Produces: `rewind_export_state(*, state_path: Path, queue_dir: Path, target: datetime, expected_next_sequence: int, now: datetime) -> ExportState`.
- Produces CLI: `python -m app.cloud_replica.cli rewind-export --to <UTC-ISO> --expected-next-sequence <N>`.

- [ ] **Step 1: Write failing rewind tests**

Create a valid mode-0600 state at sequence 765. Assert a successful rewind changes only `upper_watermark` and `cursor_session_key`; parameterize failures for a queued batch, incorrect sequence, target not earlier than current, target older than 365 days, unsafe file mode and non-UTC target. Assert failed calls leave bytes unchanged.

- [ ] **Step 2: Run rewind tests and observe RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_cloud_exporter.py -k rewind`

Expected: import/name failure because `rewind_export_state` does not exist.

- [ ] **Step 3: Implement minimal guarded rewind**

Reuse the existing strict state validation and `_atomic_replace()`. Reject any `batch-*.jsonl` regular file in the queue, require the exact next sequence, require an aware UTC target satisfying `now - 365 days <= target < current watermark`, preserve the digest chain fields, clear the composite cursor, and return the new state.

- [ ] **Step 4: Write failing CLI tests**

Assert `rewind-export` requires both arguments, reads only configured state/queue paths, passes the fixed clock, prints aggregate JSON without the digest, and returns `1` with `{"error":"rewind-export_failed"}` when validation fails.

- [ ] **Step 5: Run CLI tests and observe RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_cloud_cli.py -k rewind`

Expected: argparse rejects the unknown command.

- [ ] **Step 6: Implement the CLI wiring**

Add the command and arguments, parse a strict `Z` timestamp, call `rewind_export_state`, and emit only:

```json
{"status":"rewound","next_sequence":765,"upper_watermark":"2026-08-16T19:20:15.000000Z"}
```

- [ ] **Step 7: Verify Task 2**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_cloud_exporter.py tests/test_cloud_cli.py`

Expected: all tests pass.

- [ ] **Step 8: Commit Task 2**

```bash
git add backend/app/cloud_replica/exporter.py backend/app/cloud_replica/cli.py backend/tests/test_cloud_exporter.py backend/tests/test_cloud_cli.py
git commit -m "feat: add guarded replica export rewind"
```

### Task 3: Full verification, publish and deploy

**Files:**
- No additional source files expected.

**Interfaces:**
- Consumes the Task 1 cursor and Task 2 rewind CLI.

- [ ] **Step 1: Run all backend tests**

Run: `cd backend && .venv/bin/python -m pytest -q`

Expected: zero failures.

- [ ] **Step 2: Run frontend and deployment gates**

Run from `webui`: `npm test -- --reporter=dot`, `npm run build`, `npm audit --omit=dev --audit-level=high`.

Run from repository root: `./deploy/cloud/acceptance.sh local`.

Expected: all commands exit 0, audit reports 0 vulnerabilities, acceptance prints `CLOUD_PLATFORM_LOCAL_GATE_OK`.

- [ ] **Step 3: Verify repository and merge current production branch if needed**

Fetch origin, verify the production release commit is an ancestor of HEAD, and ensure `git status --short` and `git diff --check` are clean.

- [ ] **Step 4: Push and deploy**

Push the same commit atomically to `feat/agent-public-entry` and `master`, run the existing `deploy/cloud/deploy.sh` with the protected config, then run `accept-dingtalk-production.sh`.

- [ ] **Step 5: Commit only if verification required a source correction**

No empty verification commit. Any correction returns to a failing test first.

### Task 4: Controlled production reconciliation

**Files:**
- Runtime state only: protected private state/queue paths outside Git.

**Interfaces:**
- Consumes `rewind-export` and `deploy/cloud/push-replica.sh`.

- [ ] **Step 1: Capture immutable baselines**

Record local/cloud FAE Session and Turn counts, cloud generation sequence/digest metadata, export state SHA-256, queue emptiness, Platform service health, and FAE container ID/StartedAt/RestartCount. Do not print DSNs, keys, payloads or raw text.

- [ ] **Step 2: Stop only the cloud-sync LaunchAgent**

Use launchd to unload `com.orbbec.ai-agent-platform-cloud-sync`; verify no sync process remains. Do not stop Platform or any Agent.

- [ ] **Step 3: Back up and rewind export state**

Copy the mode-0600 state file to a timestamped mode-0600 backup. Run `rewind-export` with the exact observed next sequence and a target one microsecond before the latest successful local FAE import that introduced the missing rows. Verify queue remains empty immediately after rewind.

- [ ] **Step 4: Drain reconciliation batches**

Run `push-replica.sh` repeatedly. Continue while `cursor_session_key` is non-empty or `upper_watermark` is behind the captured current bound. Require each run to print `REPLICA_PUSH_OK sequence=<N>` and keep the queue empty after acknowledgements.

- [ ] **Step 5: Restore the five-minute scheduler**

Reload the existing LaunchAgent plist, trigger one ordinary run, require exit code 0 and confirm the source watermark advances to current time.

- [ ] **Step 6: Reconcile counts and invariants**

Require local/cloud `ai-fae-agent` counts to equal 514 Sessions and 1025 Turns and newest activity timestamps to match. Require continuous sequence growth, empty queue, no excluded Agent visibility, 5/5 Platform services healthy, and unchanged FAE container ID/StartedAt/RestartCount.

- [ ] **Step 7: Preserve recovery evidence**

Keep the timestamped pre-rewind state backup until the next successful encrypted cloud backup. Report its path, final sequence, counts and health evidence without sensitive content.
