# Cloud Fleet Agent Count Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production Fleet Overview report and retain usage for the complete Business Agent catalog instead of only the two runtime placeholders visible in cloud-replica mode.

**Architecture:** Add an opt-in catalog-completion policy to `FleetReadService`. Cloud-replica construction enables the policy; local construction keeps the current runtime-discovery behavior. Missing catalog entries become unique `unknown` runtime rows, while observed rows remain authoritative.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest/pytest-asyncio, React/Vitest production verification, Docker Compose cloud deployment.

## Global Constraints

- Agent Catalog determines roster membership and Business/System visibility.
- Runtime snapshots determine runtime state when evidence exists.
- Missing runtime evidence must remain `unknown`; it must never appear healthy, online, or active.
- Cloud completion must not duplicate AI FAE, AI ADMIN, or any other observed ID.
- Local runtime-contract discovery must remain observed-only.
- Do not start cloud pollers or modify FAE/ADMIN services.

---

### Task 1: Complete the cloud Fleet roster without changing local behavior

**Files:**
- Modify: `backend/tests/test_fleet_service.py`
- Modify: `backend/app/fleet/service.py`

**Interfaces:**
- Consumes: `AgentCatalog.all_profiles()` and the existing local/remote runtime snapshots.
- Produces: `FleetReadService(..., include_catalog_agents: bool = False)` and a completed instance list used by `overview()`.

- [ ] **Step 1: Write the failing production-shape regression test**

Extend `make_service` with an `include_catalog_agents=False` keyword and pass it into `FleetReadService`. Add this test:

```python
@pytest.mark.asyncio
async def test_cloud_roster_completion_keeps_catalog_agents_and_usage():
    service = make_service(
        UsageRecord("hr-bot", 14, 4, 2, NOW, "HR question"),
        bot_ids=[],
        include_catalog_agents=True,
    )
    service._remote_monitor = StaticRemoteMonitor(RemoteHealthSnapshot(
        healthy=False,
        checked_at=None,
        error="not_checked",
        agents=[
            RemoteAgentStatus(id="ai-fae-agent", name="AI FAE Agent", status="unknown"),
            RemoteAgentStatus(id="ai-admin-agent", name="AI ADMIN Agent", status="unknown"),
        ],
    ))

    overview = await service.overview(now=NOW)

    assert len({agent.id for agent in overview.agents}) == len(overview.agents) == 12
    assert overview.summary.total_agents == 10
    assert get_agent(overview, "hr-bot").state == "unknown"
    assert get_agent(overview, "hr-bot").total_conversations == 14
    assert overview.summary.total_conversations == 14
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_fleet_service.py::test_cloud_roster_completion_keeps_catalog_agents_and_usage -q
```

Expected: FAIL because `FleetReadService.__init__` does not accept `include_catalog_agents`.

- [ ] **Step 3: Implement the minimal roster completion policy**

In `backend/app/fleet/service.py`, add a small internal runtime row:

```python
@dataclass(frozen=True)
class _UnobservedCatalogAgent:
    id: str
    name: str
    status: str = "unknown"
    uptime_seconds: int | None = None
```

Store `include_catalog_agents` in `FleetReadService.__init__`. In `overview()`, after combining local and remote instances, append one `_UnobservedCatalogAgent` for each missing `AgentProfile` only when the option is enabled:

```python
instances = list(cluster.instances) + (list(remote.agents) if remote else [])
if self._include_catalog_agents:
    observed_ids = {instance.id for instance in instances}
    instances.extend(
        _UnobservedCatalogAgent(profile.id, profile.name)
        for profile in self._catalog.all_profiles()
        if profile.id not in observed_ids
    )
```

Leave observed rows untouched and continue deriving usage, trends, cards, and summary from the completed list.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_fleet_service.py -q
```

Expected: all Fleet service tests PASS, including the new 12-unique/10-Business production-shape assertion and the existing observed-only test.

- [ ] **Step 5: Commit the service behavior**

```bash
git add backend/app/fleet/service.py backend/tests/test_fleet_service.py
git commit -m "fix: complete cloud fleet roster from catalog"
```

### Task 2: Enable catalog completion only in cloud-replica construction

**Files:**
- Modify: `backend/tests/test_cloud_mode.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `FleetReadService(..., include_catalog_agents: bool = False)` from Task 1.
- Produces: cloud `build_cloud_replica_services()` calls `FleetReadService(..., include_catalog_agents=True)`.

- [ ] **Step 1: Write a failing cloud-construction test**

Add a focused unit test around `build_cloud_replica_services`. Replace its repository, secret-reader, encryption-reader, cache, and service collaborators with lightweight fakes; capture the `FleetReadService` keyword arguments and assert:

```python
assert captured["include_catalog_agents"] is True
```

The fake replica repository must expose `check_schema()`, and the fake Fleet constructor should return a sentinel so the test also verifies the returned Fleet service is that sentinel.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_cloud_mode.py::test_cloud_fleet_enables_catalog_roster_completion -q
```

Expected: FAIL because `build_cloud_replica_services()` does not pass `include_catalog_agents=True`.

- [ ] **Step 3: Add the cloud-only constructor flag**

In `backend/app/main.py`, change only the cloud Fleet construction:

```python
fleet_service = FleetReadService(
    cluster_monitor,
    catalog,
    UsageCache(
        ReplicaFlywheelRepository(repository),
        ttl_seconds=config.usage_cache_seconds,
    ),
    active_window_minutes=config.active_window_minutes,
    remote_monitor=remote_monitor,
    include_catalog_agents=True,
)
```

Do not add the flag to the local Fleet constructor.

- [ ] **Step 4: Run cloud and Fleet tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_cloud_mode.py tests/test_fleet_service.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit cloud wiring**

```bash
git add backend/app/main.py backend/tests/test_cloud_mode.py
git commit -m "fix: enable complete fleet roster in cloud"
```

### Task 3: Verify, publish, deploy, and accept production

**Files:**
- Verify only; modify no additional product files unless a verification failure traces to this change.

**Interfaces:**
- Consumes: the two tested commits from Tasks 1 and 2.
- Produces: a pushed release and production evidence showing the Overview roster matches the Agent directory roster.

- [ ] **Step 1: Run complete local quality gates**

Run the repository's existing backend suite, frontend suite, build, dependency audit, and cloud local deployment gate:

```bash
cd backend && .venv/bin/python -m pytest
cd ../webui && npm test -- --run
npm run build
npm audit --audit-level=high
cd .. && deploy/cloud/accept-local.sh
```

Expected: zero failing backend tests, zero failing frontend tests, successful production build, zero high/critical audit findings, and `CLOUD_PLATFORM_LOCAL_GATE_OK`.

- [ ] **Step 2: Inspect the final diff and release ancestry**

Run:

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -5
```

Expected: clean tracked worktree, no whitespace errors, and the design plus two fix commits on top of the previously deployed release.

- [ ] **Step 3: Push the feature branch and fast-forward master atomically**

Use the repository's established non-interactive GitHub push procedure to update `feat/agent-public-entry` and `master` to the same verified commit. Re-fetch and verify both remote refs resolve to that commit.

- [ ] **Step 4: Deploy through the existing cloud deployment script**

Run the established `/opt/orbbec-agent-platform` deployment workflow for the verified commit. Do not restart or modify FAE or ADMIN services.

Expected: deployment reports `CLOUD_PLATFORM_DEPLOY_OK` for the verified commit and Platform services are healthy.

- [ ] **Step 5: Run production acceptance and verify the original symptom**

Run the established DingTalk production acceptance script and a read-only in-container Fleet diagnostic using the production catalog, empty cloud contract, two remote placeholders, and replica usage source.

Expected:

```text
DINGTALK_PRODUCTION_ACCEPTANCE_OK
overview_total_agents=10
overview_unique_agents=12
overview_business_agent_ids=<same ten IDs returned by the Agent directory>
```

Confirm ports 8000 and 8080 remain loopback-only and the FAE container identity/start time/restart count remain unchanged.
