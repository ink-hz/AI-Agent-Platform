# Cloud Agent Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish FAE/DAQ/ADMIN/VOC directly in the cloud with independent source freshness and recover missing conversation data.
**Architecture:** Consistent source snapshots, per-session diff, DB-enforced publisher ownership, isolated10s worker and existing encrypted read models. Native HR/SDK retain their real objects and permissions.
**Tech Stack:** Python/psycopg, PostgreSQL, existing FastAPI/React, systemd/Docker deployment.

## Global Constraints

- Preserve stable canonical keys, source families, HMAC inputs and verified identity rules.
- Preserve the sanitizer's current business-text/credential policy; keep all credentials out of ordinary logs and arguments.
- No Agent model/prompt/loop change, production eval, fabricated answers or cross-user HR observation.
- Source business reads are read-only repeatable-read; incomplete snapshots never replace data or advance success watermarks.
- Ownership prevents legacy office imports and other agents from overwriting cloud records.
- Target10–30s from source commit to API visibility; measure source snapshot processing against a10s poll budget.
- Retain backups, controlled per-agent cutover and ownership rollback; do not disturb concurrent security releases.

### Task 1: canonical source snapshot adapters

Files: new `backend/app/cloud_sync/{__init__,models,sources,projection}.py`; tests `backend/tests/test_cloud_sync_sources.py` and PostgreSQL integration fixtures.

Interfaces: `AgentSnapshot(agent_id, source_kind, source_observed_at, sessions, complete, elapsed_ms)`; `SourceProfile(agent_id, kind, dsn_file, expected_database, schema)`; `DatabaseAgentSource(profile).read_snapshot() -> AgentSnapshot`. `sessions` is a tuple of existing `RawSession`.

- [x] Write red tests proving source Session/Turn/user identities equal existing exporter contracts, including DAQ `user:fae`, FAE verified owner and VOC UUID5/sent semantics.
- [x] Add read-only repeatable-read SQL for complete source tables; verify expected DB/schema, table shapes, bounded snapshot and exact source identity. Use existing normalization/ownership helpers.
- [x] Test source errors, a late commit visible on the next snapshot, child-only changes and malformed/incomplete data. No raw trace-file scan.
- [x] Run focused source/identity tests and commit this task; independent spec/code review.

Test contract:
```python
assert snapshot.agent_id == 'ai-daq-fae-agent'
assert snapshot.source_kind == 'fae'
assert snapshot.sessions[0].session_key == 'daq:' + native_id
assert build_record(snapshot.sessions[0])['key'] == legacy_record['key']
```

### Task 2: owned publication and per-agent synchronization state

Files: new `backend/app/cloud_sync/store.py`, `backend/migrations/cloud_agent_sync/001_agent_sources.sql`; adapt `backend/app/cloud_replica/store.py`; tests `backend/tests/test_cloud_sync_store.py` and real PostgreSQL tests.

Interfaces: `CloudSyncStore.observation_cycle(agent_id)` spans acquisition through publication;
`CloudSyncStore.publish_snapshot(snapshot, records, lease=lease) -> PublishResult`; `record_failure(agent_id, code, attempted_at)`; `source_statuses()`. Store source-read/published/error/count/timing fields. Reuse existing `ReplicaStore.prepare_session` and encryption.

- [x] Red tests: old office payload cannot replace cloud-owned session; another agent cannot write it; a failed source does not advance success; identical business records do not rewrite ciphertext.
- [x] Implement per-agent ownership and DB trigger/privilege gate, atomic changed-session publication, complete-snapshot deletion scoped to the owned agent, and monotonic source observation.
- [x] Update signed office import to skip cloud-owned session payloads while preserving valid batch acknowledgment and separate management projections.
- [x] Verify transaction rollback, replay, outage, source-count totals and independent source state using isolated PostgreSQL. Commit and independent review.

Test contract:
```python
with store.observation_cycle(agent_id) as lease:
    store.publish_snapshot(new_snapshot, new_records, lease=lease)
legacy.import_batch(old_signed_batch)
assert read_session(key).turn_count == 7
store.record_failure(agent_id, 'source_unavailable', now)
assert status(agent_id).last_source_success_at == prior_success
```

### Task 3: worker, configuration and rollout tools

Files: new `backend/app/cloud_sync/{worker,cli,config}.py`; isolated service deployment under `deploy/cloud/`; update office source exclusion option and runbook. Root integrates Task1/2 interfaces.

- [x] Red tests for independent polling, no overlapping publisher, restart/duplicate safety, credential-file-only config and per-agent failure isolation.
- [x] Implement source read→sanitize/build stable records→publish loop with10s normal polling, bounded backoff and stable payload-free diagnostics.
- [x] Add native HR/SDK and office source-mode descriptors with correct semantic capabilities/workspace links. Preserve native permission boundaries; no invented transcript.
- [x] Add credential/role provisioning and a disabled-by-default worker service. Specify controlled takeover/backfill/rollback commands, backups and read-only production acceptance. No source business data mutation.
- [x] Run worker/config/deployment tests; commit and independent review.

### Task 4: per-agent freshness and visible-page refresh

Files: `backend/app/cloud_replica/repository.py`, `backend/app/observability/models.py`, API source-status wiring; frontend session list/detail/source freshness components and focused tests.

- [x] Red test: a recent cloud batch from another agent cannot make a failing FAE source fresh.
- [x] Expose mode/source-read/publish/error/lag and compute Agent/Session freshness from independent successful source observations. Prove missing source evidence is stale.
- [x] Add bounded visible-page refresh retaining prior content, stale-request cancellation and cleanup; test timer/visibility/error behavior.
- [x] Run focused API/repository/frontend tests and build; commit and independent review.

### Task 5: integrated acceptance and staged production cutover

- [x] Synthetic source DB→adapter→publisher→real HTTP tests for all four families, including real seven-turn fixture shape and identity equality.
- [ ] Whole-branch independent review; resolve Important/Critical findings with focused retests.
- [ ] Reconcile current master/security release, merge exact tested changes, preserve release/config backups and build exact candidate.
- [ ] Deploy disabled worker, validate least privileges and baseline source/cloud counts, then controlled per-agent ownership cutover and historical backfill.
- [ ] Read-only verify the actual seven-turn incident title/count/identity/outcomes; verify source-status progress and lag for every deployed source, including an intentionally isolated non-production outage test.
- [ ] Record exact source/release SHA, migrations, test counts, production comparisons, native/office coverage and remaining limits. No completion claim before the affected checks pass.
