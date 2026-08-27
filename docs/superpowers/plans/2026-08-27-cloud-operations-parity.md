# Cloud Operations Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the cloud read-only Operations Brief match the local brief for answered-Turn usage and safely projected operational events.

**Architecture:** Keep Session decryption and integrity checking inside `ReplicaObservabilityRepository`, expose a narrow usage-leader reader, and inject it into `ReplicaOperationsRepository`. Extend the encrypted operation-event projection with safe event semantics while retaining explicit defaults for old records.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, psycopg, encrypted cloud replica projections, pytest.

## Global Constraints

- Do not modify or restart AI ADMIN, FAE, or Nginx.
- `/office/` and `/office/?view=services` are production invariants and must remain available before and after release.
- Do not project event facts, target paths, fingerprints, user identity, or raw unredacted content.
- The cloud remains read-only and preserves the existing one-year retention boundary.
- Old operation-event projections must remain readable during rolling deployment.
- A replica read failure returns an explicit unavailable response; it must never silently count Sessions as Conversations.

---

### Task 1: Preserve Safe Operation Event Semantics

**Files:**
- Modify: `backend/app/cloud_replica/models.py`
- Modify: `backend/app/cloud_replica/source.py`
- Modify: `backend/app/cloud_replica/sanitize.py`
- Modify: `backend/app/cloud_replica/store.py`
- Modify: `backend/app/cloud_replica/management_repository.py`
- Test: `backend/tests/test_cloud_source.py`
- Test: `backend/tests/test_cloud_sanitizer.py`
- Test: `backend/tests/test_cloud_store.py`
- Test: `backend/tests/test_cloud_management_repository.py`

**Interfaces:**
- Produces: `OperationEventProjection(agent_id: str | None, event_family: str, status: str, title: str, source_kind: str, ...)`.
- Produces: backward-compatible `ReplicaOperationsRepository.list_events()` that reconstructs safe event semantics.

- [ ] **Step 1: Write failing source and sanitizer tests**

Add tests that build an active `remote_sync_unavailable` event and a platform-level `data_access_recovered` event, then assert their sanitized projections contain only:

```python
{
    "kind", "key", "agent_id", "occurred_at", "event_type",
    "event_family", "severity", "status", "title", "summary",
    "source_kind", "sanitizer_policy_version",
}
```

Assert the platform-level record retains `agent_id is None`, while `facts`, `target_path`, and `fingerprint` are absent.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest \
  tests/test_cloud_source.py tests/test_cloud_sanitizer.py tests/test_cloud_store.py -q
```

Expected: FAIL because `OperationEventProjection` lacks the new fields and platform-level events are filtered out.

- [ ] **Step 3: Implement the event projection contract**

Use this model shape:

```python
@dataclass(frozen=True, slots=True)
class OperationEventProjection:
    event_id: str
    agent_id: str | None
    event_type: str
    event_family: str
    severity: str
    status: str
    title: str
    summary: str
    source_kind: str
    occurred_at: datetime
```

In `ReplicaSource.fetch_management_projections()`, project every business event, including `agent_id=None`. In `sanitize_management_projection()`, sanitize `title` and `summary`, validate identifier fields, and emit no other event fields. Extend `_MANAGEMENT_KEYS["operation_event_projection"]` with the exact safe fields above.

- [ ] **Step 4: Write failing reader tests**

Add one new-format record and one old-format record. Assert the new record reconstructs:

```python
event.event_family == "data"
event.status == "active"
event.title == "ai-admin-agent synchronization is unavailable"
event.source_kind == "admin"
```

Assert old records use `event_family="execution"`, `status="historical"`, `title=event_type`, and `source_kind="cloud-replica"` without failing.

- [ ] **Step 5: Run the reader tests and verify RED**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest \
  tests/test_cloud_management_repository.py -q
```

Expected: FAIL because the reader currently hard-codes every event to execution/historical/cloud-replica.

- [ ] **Step 6: Implement backward-compatible reconstruction**

Read new fields when present. For old projections use these defaults:

```python
event_family = value.get("event_family") or _family_for_legacy_event_type(value["event_type"])
status = value.get("status") or "historical"
title = (value.get("title") or {}).get("text") or value["event_type"]
source_kind = value.get("source_kind") or "cloud-replica"
```

The legacy family mapper must be a closed map for known usage, lifecycle, recovery, data, and execution event types; unknown values return `execution`.

- [ ] **Step 7: Run Task 1 tests and commit**

Run the four test files from Steps 2 and 5. Expected: PASS.

```bash
git add backend/app/cloud_replica backend/tests/test_cloud_source.py \
  backend/tests/test_cloud_sanitizer.py backend/tests/test_cloud_store.py \
  backend/tests/test_cloud_management_repository.py
git commit -m "fix(operations): preserve cloud event semantics"
```

### Task 2: Count Answered Turns in the Cloud Brief

**Files:**
- Modify: `backend/app/cloud_replica/repository.py`
- Modify: `backend/app/cloud_replica/management_repository.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_cloud_repository.py`
- Test: `backend/tests/test_cloud_management_repository.py`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Produces: `ReplicaObservabilityRepository.usage_leaders(date_from, date_to, agent_visibility) -> tuple[UsageLeader, ...]`.
- Consumes: injected callable with the same signature in `ReplicaOperationsRepository(..., usage_reader=...)`.

- [ ] **Step 1: Write failing answered-Turn tests**

Build 15 sanitized Session records containing 44 in-window answered Turns, plus an empty-answer Turn, an out-of-window Turn, and a Turn for an excluded Agent. Assert:

```python
leaders = repository.usage_leaders(start, end, "business")
assert [(item.agent_id, item.conversations) for item in leaders] == [
    ("ai-fae-agent", 44),
]
```

- [ ] **Step 2: Run the usage tests and verify RED**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest \
  tests/test_cloud_repository.py tests/test_cloud_management_repository.py -q
```

Expected: FAIL because cloud usage currently counts Session rows by `created_at`.

- [ ] **Step 3: Implement the narrow usage reader**

In `ReplicaObservabilityRepository.usage_leaders()`:

```python
counts: dict[str, int] = {}
allowed = set(self._catalog.ids_for_visibility(agent_visibility))
for record in self._records():
    agent_id = str(record.get("agent_id") or "")
    if agent_id not in allowed or self._catalog.is_excluded(agent_id):
        continue
    for turn in record.get("turns", []):
        occurred_at = _time(turn["created_at"])
        answer = str((turn.get("answer") or {}).get("text") or "").strip()
        if date_from <= occurred_at <= date_to and answer:
            counts[agent_id] = counts.get(agent_id, 0) + 1
```

Return sorted `UsageLeader` values using the canonical catalog display name.

`ReplicaOperationsRepository` must delegate to its injected `usage_reader`. If no reader exists, raise `ReviewRepositoryError("replica usage unavailable")`; do not retain the Session-count SQL fallback.

- [ ] **Step 4: Wire production construction and test it**

In `build_cloud_replica_services()` construct:

```python
repository.operations_repository = ReplicaOperationsRepository(
    database_url,
    cipher=FieldCipher(encryption_key),
    stale_seconds=config.replica_stale_seconds,
    catalog=catalog,
    usage_reader=repository.usage_leaders,
)
```

Add a construction test that asserts the operations repository calls the Session reader rather than querying `platform_replica.sessions` by Session count.

- [ ] **Step 5: Run Task 2 tests and commit**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest \
  tests/test_cloud_repository.py tests/test_cloud_management_repository.py \
  tests/test_main.py -q
```

Expected: PASS.

```bash
git add backend/app/cloud_replica/repository.py \
  backend/app/cloud_replica/management_repository.py backend/app/main.py \
  backend/tests/test_cloud_repository.py \
  backend/tests/test_cloud_management_repository.py backend/tests/test_main.py
git commit -m "fix(operations): count replicated answered turns"
```

### Task 3: Regression, Release, and Administrative Invariance

**Files:**
- Modify only if a regression is found: files already listed in Tasks 1 and 2
- Verify: `deploy/cloud/remote-stage.sh`
- Verify: `deploy/cloud/accept.sh`
- Verify: `deploy/cloud/agent-domain.nginx.conf`

**Interfaces:**
- Consumes: completed event projection and usage-reader behavior.
- Produces: tested release commit and content-free production evidence.

- [ ] **Step 1: Run focused and full regression suites**

```bash
cd backend
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest \
  tests/test_cloud_management_repository.py tests/test_cloud_source.py \
  tests/test_cloud_sanitizer.py tests/test_cloud_store.py \
  tests/test_cloud_repository.py tests/test_operations_service.py \
  tests/test_operations_api.py tests/test_main.py -q
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q
cd ../webui
npm test
npm run build
```

Expected: all tests and build PASS.

- [ ] **Step 2: Verify repository and deployment scripts**

```bash
git diff --check
bash -n deploy/cloud/remote-stage.sh deploy/cloud/accept.sh \
  deploy/cloud/rollback-dingtalk-production.sh
```

Expected: exit 0. Confirm no diff touches AI ADMIN, FAE, Nginx, `/office`, or their deployment files.

- [ ] **Step 3: Record production invariance baseline**

Before Platform deployment, record without response bodies or credentials:

```text
AI ADMIN container ID, ImageID, StartedAt, RestartCount
FAE container ID, ImageID, StartedAt, RestartCount
Nginx managed configuration SHA256
HTTP status for /office/ and /office/?view=services
```

Both administrative URLs must return their expected authenticated page or login flow; no request may expose Session cookies in output.

- [ ] **Step 4: Deploy only Agent Platform and wait for one successful replica import**

Use the existing locked Platform release path. Do not modify Nginx and do not recreate or restart AI ADMIN/FAE. Wait until the replica generation `committed_at` advances after the new local exporter is active.

- [ ] **Step 5: Verify parity and invariance**

Within the same minute window compare local and cloud:

```text
usage.conversations
usage.active_agents
usage.leaders
active attention event IDs/types
first five change event IDs/types/timestamps
```

Expected for the reproduced case: AI FAE usage is 44 on both sides, AI ADMIN synchronization appears active under attention, and FAE usage plus flywheel recovery appear in changes. Re-record the administrative, FAE, and Nginx invariants; every value must match the baseline.

- [ ] **Step 6: Final commit if verification metadata changed**

Do not commit production secrets, cookies, response bodies, or raw event content. If no tracked verification artifact is required, make no empty commit.
