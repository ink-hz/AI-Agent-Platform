# Platform Agent Exclusion List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `fae-bot` and `codex-assistant` from every Agent Platform management surface without deleting or rewriting their stored source data.

**Architecture:** `AgentCatalog` owns an alias-safe `excluded_ids` policy. Fleet, observability, Operations, Review, and runtime projections consume that policy before exposing or aggregating records. Cloud and local repositories retain source rows but return no excluded resource through collection, filter, or direct lookup paths.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, PostgreSQL/psycopg, encrypted cloud replica projections, pytest/pytest-asyncio, React/Vitest verification, Docker Compose deployment.

## Global Constraints

- Initial excluded IDs are exactly `fae-bot` and `codex-assistant`.
- `ai-fae-agent` remains included and must not be confused with `fae-bot`.
- Exclusion is stronger than System visibility and applies to direct URLs.
- Original Session, attachment, feedback, trace, Review, and Operations data is preserved.
- Collection endpoints omit excluded records; explicit excluded filters return empty results; direct excluded resources return 404 through existing not-found behavior.
- DingTalk identity, authorization, Agent runtime contracts, and FAE/ADMIN services are unchanged.

---

### Task 1: Make Agent Catalog the exclusion policy owner

**Files:**
- Modify: `backend/app/fleet/catalog.yaml`
- Modify: `backend/app/fleet/catalog.py`
- Modify: `backend/tests/test_fleet_catalog.py`

**Interfaces:**
- Consumes: YAML `profiles`, `aliases`, and `unresolved_aliases`.
- Produces: `AgentCatalog.is_excluded(agent_id: str) -> bool`, `AgentCatalog.excluded_ids() -> tuple[str, ...]`, and exclusion-aware `canonical_id`, `all_profiles`, and `ids_for_visibility`.

- [ ] **Step 1: Write failing Catalog tests**

Add tests asserting:

```python
def test_catalog_excludes_felix_and_iris_without_excluding_ai_fae():
    catalog = AgentCatalog.default()

    assert catalog.is_excluded("fae-bot") is True
    assert catalog.is_excluded("codex-assistant") is True
    assert catalog.is_excluded("ai-fae-agent") is False
    assert catalog.canonical_id("fae-bot") is None
    assert catalog.canonical_id("codex-assistant") is None
    assert "fae-bot" not in {profile.id for profile in catalog.all_profiles()}
    assert "codex-assistant" not in {profile.id for profile in catalog.all_profiles()}
    assert "ai-fae-agent" in {profile.id for profile in catalog.all_profiles()}


def test_catalog_rejects_unknown_exclusion():
    with pytest.raises(ValueError, match="excluded agent profile"):
        AgentCatalog({}, {}, set(), {"missing-agent"})
```

Update existing direct constructor calls to pass an empty excluded set or make the new constructor parameter optional.

- [ ] **Step 2: Run Catalog tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_fleet_catalog.py -q
```

Expected: FAIL because `is_excluded` and exclusion configuration do not exist.

- [ ] **Step 3: Implement the Catalog contract**

Add to `catalog.yaml`:

```yaml
excluded_ids:
  - fae-bot
  - codex-assistant
```

Extend `AgentCatalog`:

```python
def __init__(
    self,
    profiles: dict[str, AgentProfile],
    aliases: dict[str, str],
    unresolved_aliases: set[str],
    excluded_ids: set[str] | None = None,
) -> None:
    self._excluded_ids = set(excluded_ids or ())
    missing = self._excluded_ids - profiles.keys()
    if missing:
        raise ValueError(f"excluded agent profile not declared: {sorted(missing)}")

def is_excluded(self, agent_id: str) -> bool:
    return self._aliases.get(agent_id, agent_id) in self._excluded_ids

def canonical_id(self, bot_id: str) -> str | None:
    if bot_id in self._unresolved_aliases:
        return None
    canonical = self._aliases.get(bot_id, bot_id)
    if canonical in self._excluded_ids:
        return None
    return canonical

def excluded_ids(self) -> tuple[str, ...]:
    return tuple(sorted(self._excluded_ids))
```

Load `excluded_ids` in `default()`. Filter `all_profiles()` and implement `ids_for_visibility()` from `all_profiles()`. Keep `profile()` metadata lookup unchanged.

- [ ] **Step 4: Run Catalog tests and verify GREEN**

```bash
cd backend
.venv/bin/python -m pytest tests/test_fleet_catalog.py -q
```

Expected: all Catalog tests PASS.

- [ ] **Step 5: Commit Catalog policy**

```bash
git add backend/app/fleet/catalog.py backend/app/fleet/catalog.yaml backend/tests/test_fleet_catalog.py
git commit -m "feat: add platform agent exclusion policy"
```

### Task 2: Apply exclusion to Fleet totals, cards, usage, and runtime

**Files:**
- Modify: `backend/app/fleet/service.py`
- Modify: `backend/tests/test_fleet_service.py`
- Modify: `backend/tests/test_control_room_service.py`

**Interfaces:**
- Consumes: `AgentCatalog.is_excluded()` and exclusion-aware `all_profiles()`.
- Produces: Fleet Overview with 8 Business Agents and 10 unique included Agents; runtime detail returns no excluded Agent.

- [ ] **Step 1: Change the production-shape test to require exclusion**

Update `test_cloud_roster_completion_keeps_catalog_agents_and_usage` to include usage records for `fae-bot`, `codex-assistant`, and `hr-bot`, then assert:

```python
assert len({agent.id for agent in overview.agents}) == len(overview.agents) == 10
assert overview.summary.total_agents == 8
assert {"fae-bot", "codex-assistant"}.isdisjoint(
    agent.id for agent in overview.agents
)
assert get_agent(overview, "hr-bot").total_conversations == 14
assert overview.summary.total_conversations == 14
```

Add a `ControlRoomService` test asserting `get_runtime("fae-bot")` and `get_runtime("codex-assistant")` return `None`. Assert `get_runtime("ai-fae-agent")` still returns a runtime view. The existing runtime API not-found test proves that a `None` service result becomes HTTP 404.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
cd backend
.venv/bin/python -m pytest tests/test_fleet_service.py tests/test_control_room_service.py tests/test_control_room_api.py -q
```

Expected: Fleet assertions FAIL because observed excluded runtime instances are still accepted.

- [ ] **Step 3: Filter runtime instances before Fleet processing**

In `FleetReadService.overview()` replace the raw concatenation with:

```python
instances = [
    instance
    for instance in (
        list(cluster.instances) + (list(remote.agents) if remote else [])
    )
    if not self._catalog.is_excluded(instance.id)
]
```

Catalog completion and `expected_agent_ids` already consume filtered `all_profiles()`. `ControlRoomService.get_runtime()` already builds its allowed map from `all_profiles()`, so the Catalog change must make excluded runtime URLs return `None` without an Agent-specific branch.

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
cd backend
.venv/bin/python -m pytest tests/test_fleet_service.py tests/test_control_room_service.py tests/test_control_room_api.py -q
```

Expected: all selected tests PASS; `ai-fae-agent` remains present.

- [ ] **Step 5: Commit Fleet/runtime enforcement**

```bash
git add backend/app/fleet/service.py backend/tests/test_fleet_service.py backend/tests/test_control_room_service.py
git commit -m "fix: exclude hidden agents from fleet runtime"
```

### Task 3: Enforce exclusion across Agent, Session, Trace, and Flywheel reads

**Files:**
- Modify: `backend/app/cloud_replica/repository.py`
- Modify: `backend/app/observability/repository.py`
- Modify: `backend/tests/test_cloud_repository.py`
- Modify: `backend/tests/test_observability_repository.py`

**Interfaces:**
- Consumes: `AgentCatalog.is_excluded()` and exclusion-aware roster methods.
- Produces: excluded records are absent from local/cloud collection queries and unavailable through direct Session, Trace, Agent, runtime-observation, usage, and improvement lookups.

- [ ] **Step 1: Write cloud-replica read-boundary tests**

Extend the cloud repository fixture so records can specify `agent_id`. Provide one HR record, one `fae-bot` record, and one `codex-assistant` record. Assert:

```python
page = repository.list_sessions(SessionFilters(), 50, 0)
assert [item.agent_id for item in page.items] == ["hr-bot"]
assert repository.list_sessions(SessionFilters(agent_id="fae-bot"), 50, 0).total == 0
assert repository.get_session(fae_session_key) is None
assert repository.get_trace(fae_turn_key) is None
assert repository.get_agent("fae-bot") is None
assert repository.get_agent("ai-fae-agent") is not None
assert "fae-bot" not in {item.bot_id for item in repository.usage_snapshot().records}
```

- [ ] **Step 2: Write local repository policy tests**

Use `visibility_catalog()` with an excluded profile to assert:

```python
assert repository.get_session(excluded_session_key) is None
assert repository.get_trace(excluded_turn_key) is None
assert repository.get_latest_runtime_observation("fae-bot") is None
```

Also inspect recorded SQL/parameters and assert explicit excluded Session and improvement filters use a false condition rather than returning source rows.

- [ ] **Step 3: Run repository tests and verify RED**

```bash
cd backend
.venv/bin/python -m pytest tests/test_cloud_repository.py tests/test_observability_repository.py -q
```

Expected: excluded direct Session/Trace and explicit filter assertions FAIL.

- [ ] **Step 4: Implement cloud filtering at the decrypted record boundary**

Change `ReplicaObservabilityRepository._records()` to decrypt first, then retain only records whose `agent_id` is not excluded:

```python
records = [self._decrypt(row) for row in self._rows(session_key=session_key)]
return [
    record for record in records
    if not self._catalog.is_excluded(str(record.get("agent_id") or ""))
]
```

Before an explicit Agent filter, return an empty `Page` when `is_excluded(filters.agent_id)` is true. Before `get_latest_runtime_observation`, return `None` for an excluded ID. Because `get_session`, `get_trace`, usage, and Agent lists consume `_records()` or `all_profiles()`, they inherit the same denial.

- [ ] **Step 5: Implement equivalent local repository checks**

In `PsycopgObservabilityRepository`:

- skip excluded rows in `list_agents()`;
- append `false` in `_session_conditions()` for an explicitly excluded `filters.agent_id`;
- after loading a Session or Trace row, return `None` immediately if its `agent_id` is excluded, before loading child rows;
- return `None` before querying latest runtime for an excluded ID;
- return an empty improvement page for an excluded explicit filter;
- add `agent_id <> all(%s)` with `sorted(self._catalog.excluded_ids())` to unfiltered Flywheel and improvement aggregation queries.

Expose `AgentCatalog.excluded_ids() -> tuple[str, ...]` as a sorted read-only tuple for parameterized SQL; never interpolate IDs into SQL text.

- [ ] **Step 6: Run repository tests and verify GREEN**

```bash
cd backend
.venv/bin/python -m pytest tests/test_cloud_repository.py tests/test_observability_repository.py -q
```

Expected: all selected tests PASS, including direct lookup denial.

- [ ] **Step 7: Commit observability enforcement**

```bash
git add backend/app/fleet/catalog.py backend/app/cloud_replica/repository.py backend/app/observability/repository.py backend/tests/test_cloud_repository.py backend/tests/test_observability_repository.py
git commit -m "fix: exclude hidden agents from observability reads"
```

### Task 4: Filter Review and Operations management projections

**Files:**
- Modify: `backend/app/cloud_replica/management_repository.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_cloud_management_repository.py`
- Modify: `backend/tests/test_cloud_mode.py`
- Modify: `backend/tests/test_operations_source.py`

**Interfaces:**
- Consumes: `AgentCatalog.is_excluded()` and `canonical_id()`.
- Produces: production Review and Operations projections omit excluded Agents before pagination/aggregation; cloud construction injects the same Catalog instance.

- [ ] **Step 1: Write management projection tests**

Create Review and Operations projection rows for `hr-bot`, `fae-bot`, and `codex-assistant`. Construct repositories with `catalog=AgentCatalog.default()` and assert:

```python
assert [item["agent_id"] for item in review.list_issues(limit=100, offset=0)] == ["hr-bot"]
assert review.list_issues(agent_id="fae-bot", limit=100, offset=0) == []
assert review.get_issue_detail(excluded_issue_id) is None
assert [item.agent_id for item in operations.list_events(EventFilters(), 100, 0).items] == ["hr-bot"]
assert operations.list_events(EventFilters(agent_id="codex-assistant"), 100, 0).total == 0
```

Add a construction test proving both `ReplicaReviewRepository` and `ReplicaOperationsRepository` receive `catalog=catalog`.

- [ ] **Step 2: Write Operations source exclusion test**

Create a custom catalog containing an excluded profile and usage/execution rows for that profile. Assert `PsycopgOperationsSource` emits no occurrence, cumulative total, or execution observation for it, while a non-excluded row remains. This should become green through exclusion-aware `canonical_id()` and `all_profiles()` without Operations-specific branches.

- [ ] **Step 3: Run management tests and verify RED**

```bash
cd backend
.venv/bin/python -m pytest tests/test_cloud_management_repository.py tests/test_cloud_mode.py tests/test_operations_source.py -q
```

Expected: management repository constructors reject `catalog`, or excluded projections remain visible.

- [ ] **Step 4: Filter at `_ProjectionReader._records()`**

Add `catalog: AgentCatalog | None = None` to `_ProjectionReader.__init__`, store `catalog or AgentCatalog.default()`, return `[]` immediately for an explicitly excluded `agent_id`, and filter decrypted results:

```python
return [
    record for record in decrypted
    if not self._catalog.is_excluded(str(record.get("agent_id") or ""))
]
```

This makes unfiltered overview counts, pagination, direct Review issue details, and Operations event lists use the same policy.

- [ ] **Step 5: Inject Catalog in cloud construction**

In `build_cloud_replica_services()` pass `catalog=catalog` to both management repositories:

```python
repository.review_repository = ReplicaReviewRepository(
    database_url,
    cipher=FieldCipher(encryption_key),
    stale_seconds=config.replica_stale_seconds,
    catalog=catalog,
)
repository.operations_repository = ReplicaOperationsRepository(
    database_url,
    cipher=FieldCipher(encryption_key),
    stale_seconds=config.replica_stale_seconds,
    catalog=catalog,
)
```

- [ ] **Step 6: Run management tests and verify GREEN**

```bash
cd backend
.venv/bin/python -m pytest tests/test_cloud_management_repository.py tests/test_cloud_mode.py tests/test_operations_source.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit management enforcement**

```bash
git add backend/app/cloud_replica/management_repository.py backend/app/main.py backend/tests/test_cloud_management_repository.py backend/tests/test_cloud_mode.py backend/tests/test_operations_source.py
git commit -m "fix: exclude hidden agents from management projections"
```

### Task 5: Verify, publish, deploy, and accept production

**Files:**
- Verify only; no additional product changes unless a failing test traces directly to the exclusion policy.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: verified GitHub refs and a production release where Overview, Agent, Session, Review, and Operations surfaces omit both excluded IDs.

- [ ] **Step 1: Run complete quality gates**

```bash
cd backend && .venv/bin/python -m pytest
cd ../webui && npm test -- --run
npm run build
npm audit --omit=dev --audit-level=high
cd .. && ./deploy/cloud/acceptance.sh local
```

Expected: zero failures, zero production dependency vulnerabilities, and `CLOUD_PLATFORM_LOCAL_GATE_OK`.

- [ ] **Step 2: Review diff and repository state**

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -8
```

Expected: clean tracked worktree and only the approved design, plan, tests, Catalog, and read-path changes.

- [ ] **Step 3: Push verified refs and deploy**

Push `HEAD` to both `feat/agent-public-entry` and `master` with a normal fast-forward push, verify both remote refs equal `HEAD`, and run:

```bash
./deploy/cloud/deploy.sh "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"
```

Expected: `CLOUD_PLATFORM_DEPLOY_OK` for the verified commit.

- [ ] **Step 4: Run production acceptance**

Run the existing remote DingTalk acceptance script, then execute read-only in-container checks against the actual Fleet, Observability, Review, and Operations services.

Expected evidence:

```text
DINGTALK_PRODUCTION_ACCEPTANCE_OK
overview_total_agents=8
overview_unique_agents=10
excluded_in_overview=false
excluded_in_agent_directory=false
excluded_in_default_sessions=false
excluded_explicit_session_total=0
excluded_review_rows=0
excluded_operation_rows=0
ai_fae_present=true
```

Confirm all five Platform services are healthy, 8000/8080 remain loopback-only, and the FAE container ID, image, start time, and restart count are unchanged.
