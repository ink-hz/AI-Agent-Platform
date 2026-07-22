# System Agent Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Test and Feishu Default from default business reporting and browsing while retaining their monitoring, detail pages, and explicit Session access.

**Architecture:** Define `visibility` once in the Agent catalog and project it through Fleet and Observability API models. Fleet keeps all runtime records for diagnostics but computes summary and trend from Business Agent IDs; Observability applies a Business Agent allowlist only to unfiltered Session queries. Shared frontend helpers consistently filter or partition Agents across Overview, Agents, Sessions, and Agent Data.

**Tech Stack:** Python 3, FastAPI, Pydantic, PostgreSQL/psycopg, YAML, React 19, TypeScript, Vitest, CSS, Vite.

## Global Constraints

- `test-bot` and `feishu-default` are `system`; the other nine cataloged Agents are `business`.
- Unknown runtime identities default to `system`.
- Overview summary, trend, ranking, hero, and cards are Business-only.
- Fleet API retains System Agent records for Agent Detail diagnostics.
- Default Sessions and Agent Data exclude System Agents; explicit System `agent_id` access remains available.
- Agents page retains a visually quieter `System Agents` section.
- Do not delete data or stop health polling.
- Preserve unrelated user changes and restart only AI Agent Platform.

---

### Task 1: Catalog classification and Business-scoped Fleet aggregation

**Files:**
- Modify: `backend/tests/test_fleet_service.py`
- Modify: `backend/app/fleet/catalog.py`
- Modify: `backend/app/fleet/catalog.yaml`
- Modify: `backend/app/fleet/models.py`
- Modify: `backend/app/fleet/service.py`
- Modify: `backend/tests/test_fleet_api.py`

**Interfaces:**
- Consumes: `AgentProfile.visibility` from catalog metadata.
- Produces: `AgentCatalog.ids_for_visibility(visibility)`, `FleetAgent.visibility`, an all-Agent `agents` payload, and Business-only Fleet summary/trend.

- [ ] **Step 1: Write failing Fleet tests**

Add tests that assert:

```python
overview = await make_service(
    UsageRecord("hr-bot", 14, 4, 2, NOW, "business"),
    UsageRecord("test-bot", 1, 0, 0, NOW, "TEST_BOT_FEISHU_OK"),
    trend=[
        DailyUsage("hr-bot", date(2026, 7, 21), 4),
        DailyUsage("test-bot", date(2026, 7, 21), 1),
    ],
).overview(now=NOW)

assert len(overview.agents) == 9
assert get_agent(overview, "test-bot").visibility == "system"
assert get_agent(overview, "feishu-default").visibility == "system"
assert overview.summary.total_agents == 7
assert overview.summary.total_conversations == 14
assert overview.trend[-1].conversations == 4
```

Update the remote test to expect nine Business Agents from eleven diagnostic Agent records. Update the unknown-runtime test to require `visibility == "system"`, `summary.total_agents == 0`, and `summary.total_conversations == 0` while retaining its Agent usage record.

- [ ] **Step 2: Run focused Fleet tests and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_service.py backend/tests/test_fleet_api.py -q`

Expected: FAIL because catalog and API models do not expose `visibility`, and summaries still include System Agents.

- [ ] **Step 3: Add catalog classification**

Extend `AgentProfile`:

```python
visibility: Literal["business", "system"] = "system"
```

Add:

```python
def ids_for_visibility(self, visibility: str) -> tuple[str, ...]:
    return tuple(profile.id for profile in self._profiles.values() if profile.visibility == visibility)
```

Set `visibility: system` on `test-bot` and `feishu-default`, and `visibility: business` on every other profile in `catalog.yaml`. The fallback `profile()` remains `system`.

- [ ] **Step 4: Scope Fleet summary and trend**

Add `visibility` to `FleetAgent`, project it in `_build_agent`, then calculate:

```python
business_agents = [agent for agent in agents if agent.visibility == "business"]
business_ids = {agent.id for agent in business_agents}
```

Use `business_agents` for state counts and Agent totals. Sum usage only for `business_ids` and pass `business_ids` to `_build_trend`. Keep the unfiltered `agents` list in `FleetOverview`.

- [ ] **Step 5: Run focused Fleet tests and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_service.py backend/tests/test_fleet_api.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit Fleet classification**

```bash
git add backend/app/fleet/catalog.py backend/app/fleet/catalog.yaml backend/app/fleet/models.py backend/app/fleet/service.py backend/tests/test_fleet_service.py backend/tests/test_fleet_api.py
git commit -m "feat: scope Fleet reporting to Business Agents"
```

### Task 2: Observability classification and default Session filtering

**Files:**
- Modify: `backend/tests/test_observability_repository.py`
- Modify: `backend/tests/test_observability_api.py`
- Modify: `backend/app/observability/models.py`
- Modify: `backend/app/observability/repository.py`

**Interfaces:**
- Consumes: `AgentCatalog.ids_for_visibility("business")` from Task 1.
- Produces: `AgentSummary.visibility`; Business-only unfiltered Sessions; explicit System Session access.

- [ ] **Step 1: Write failing Observability tests**

Use a catalog containing one Business and one System Agent. Assert `list_agents()` projects each profile's visibility. For `_session_conditions` behavior, execute `list_sessions` through `FakeConnect` and assert:

```python
default_sql = " ".join(statement for statement, _ in fake.executed).lower()
assert "s.agent_id = any(%s)" in default_sql
assert fake.executed[0][1] == (["business-agent"],)
```

For `SessionFilters(agent_id="test-bot")`, assert the SQL contains `s.agent_id = %s`, does not contain `any(%s)`, and passes `("test-bot",)` before pagination parameters.

Update the API fixture and test to assert:

```python
assert response.json()[0]["visibility"] == "business"
```

- [ ] **Step 2: Run focused Observability tests and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_observability_repository.py backend/tests/test_observability_api.py -q`

Expected: FAIL because `AgentSummary.visibility` and the default Business allowlist do not exist.

- [ ] **Step 3: Project visibility into Agent summaries**

Add:

```python
AgentVisibility = Literal["business", "system"]
```

and a required `visibility: AgentVisibility` field to `AgentSummary`. Populate it from `profile.visibility` in both repository construction branches and in API test fixtures.

- [ ] **Step 4: Apply the default Session allowlist**

At the beginning of `_session_conditions`, keep explicit `filters.agent_id` behavior unchanged. When it is absent:

```python
business_ids = list(self._catalog.ids_for_visibility("business"))
if business_ids:
    conditions.append("s.agent_id = any(%s)")
    params.append(business_ids)
else:
    conditions.append("false")
```

This excludes both known System Agents and unknown IDs from default totals and pagination while allowing explicit raw-ID queries.

- [ ] **Step 5: Run focused Observability tests and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_observability_repository.py backend/tests/test_observability_api.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit Observability visibility**

```bash
git add backend/app/observability/models.py backend/app/observability/repository.py backend/tests/test_observability_repository.py backend/tests/test_observability_api.py
git commit -m "feat: hide System Sessions by default"
```

### Task 3: Shared frontend visibility and System directory section

**Files:**
- Create: `webui/src/agentVisibility.ts`
- Create: `webui/src/agentVisibility.test.ts`
- Create: `webui/src/components/AgentDirectorySections.tsx`
- Modify: `webui/src/types.ts`
- Modify: `webui/src/pages/OverviewPage.tsx`
- Modify: `webui/src/pages/AgentsPage.tsx`
- Modify: `webui/src/pages/SessionsPage.tsx`
- Modify: `webui/src/pages/FlywheelPage.tsx`
- Modify: `webui/src/observability.test.tsx`
- Modify: `webui/src/agentDataBrowser.test.tsx`
- Modify: `webui/src/FleetAgentCard.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `visibility` on `FleetAgent` and `AgentSummary`.
- Produces: `businessAgents(agents)`, `partitionAgents(agents)`, `agentsForSelector(agents, selectedId)`, and grouped directory rendering.

- [ ] **Step 1: Write failing shared-helper tests**

Create tests using one Business and one System fixture:

```ts
expect(businessAgents(agents).map((agent) => agent.id)).toEqual(["hr-bot"]);
expect(partitionAgents(agents).system.map((agent) => agent.id)).toEqual(["test-bot"]);
expect(agentsForSelector(agents, "").map((agent) => agent.id)).toEqual(["hr-bot"]);
expect(agentsForSelector(agents, "test-bot").map((agent) => agent.id)).toEqual(["hr-bot", "test-bot"]);
```

- [ ] **Step 2: Write failing page and directory tests**

Add `visibility` to all typed fixtures. Render `AgentDirectorySections` and assert Business Agent markup precedes `System Agents`, the System identity remains linked, and `TEST_BOT_FEISHU_OK` is absent from Overview source/render fixtures. Assert Overview, Sessions, and Flywheel import and call the shared visibility helpers.

- [ ] **Step 3: Run focused frontend tests and verify RED**

Run: `npm test -- --run src/agentVisibility.test.ts src/observability.test.tsx src/agentDataBrowser.test.tsx src/dashboard.test.ts src/FleetAgentCard.test.tsx`

Expected: FAIL because the helpers, grouping component, and visibility model are absent.

- [ ] **Step 4: Implement shared visibility helpers**

Create:

```ts
export function businessAgents<T extends { visibility: AgentVisibility }>(agents: T[]): T[] {
  return agents.filter((agent) => agent.visibility === "business");
}

export function partitionAgents<T extends { visibility: AgentVisibility }>(agents: T[]) {
  return {
    business: businessAgents(agents),
    system: agents.filter((agent) => agent.visibility === "system"),
  };
}

export function agentsForSelector<T extends { id: string; visibility: AgentVisibility }>(agents: T[], selectedId: string): T[] {
  return agents.filter((agent) => agent.visibility === "business" || agent.id === selectedId);
}
```

Add `AgentVisibility = "business" | "system"` and required `visibility` fields to both frontend Agent types.

- [ ] **Step 5: Apply helpers to product pages**

- Overview filters the all-Agent Fleet payload before card and ranking rendering.
- Agents uses `partitionAgents`; the headline reports `business.length`; `AgentDirectorySections` renders the primary grid and a `System Agents` section.
- Sessions uses `agentsForSelector`, preserving an explicitly selected System Agent.
- Flywheel stores only `businessAgents(nextAgents)` and selects from that list.

Add a quieter `.system-agent-section` / `.system-agent-grid` treatment without reducing any text below `11.5px`.

- [ ] **Step 6: Run focused frontend tests and verify GREEN**

Run: `npm test -- --run src/agentVisibility.test.ts src/observability.test.tsx src/agentDataBrowser.test.tsx src/dashboard.test.ts src/FleetAgentCard.test.tsx`

Expected: all selected tests pass.

- [ ] **Step 7: Commit frontend visibility**

```bash
git add webui/src/agentVisibility.ts webui/src/agentVisibility.test.ts webui/src/components/AgentDirectorySections.tsx webui/src/types.ts webui/src/pages/OverviewPage.tsx webui/src/pages/AgentsPage.tsx webui/src/pages/SessionsPage.tsx webui/src/pages/FlywheelPage.tsx webui/src/observability.test.tsx webui/src/agentDataBrowser.test.tsx webui/src/FleetAgentCard.test.tsx webui/src/styles.css
git commit -m "feat: separate Business and System Agents"
```

### Task 4: Full verification and Platform deployment

**Files:**
- Verify: `backend/`
- Verify: `webui/`
- Deploy: existing `com.orbbec.ai-agent-platform` LaunchAgent

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: a deployed nine-Agent business Overview with retained System diagnostics.

- [ ] **Step 1: Run complete backend tests**

Run: `backend/.venv/bin/pytest backend/tests -q`

Expected: all tests pass.

- [ ] **Step 2: Run complete frontend tests and build**

Run tests: `npm test -- --run`

Run build: `npm run build`

Expected: all tests pass and Vite exits successfully.

- [ ] **Step 3: Restart only Platform**

Run: `launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform`

Expected: Platform receives a new PID; MetaBot, AI FAE, and AI ADMIN remain untouched.

- [ ] **Step 4: Verify live scope**

Confirm `/api/health` returns `{"status":"ok"}`. Confirm `/api/fleet/overview` returns eleven diagnostic Agent records, `summary.total_agents == 9`, System visibility on Test and Feishu Default, and Business-only usage/trend. Confirm `/api/sessions` excludes System Sessions while `/api/sessions?agent_id=test-bot` still returns its stored Session.
