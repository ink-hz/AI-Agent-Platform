# Agent Lifecycle Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace restart-sensitive Agent card uptime with durable `Live Since` and `Last Updated` lifecycle dates while retaining current runtime on Agent Detail for diagnostics.

**Architecture:** Store lifecycle metadata and evidence provenance in the existing Fleet catalog, project it through `FleetReadService`, and expose an explicit `current_runtime_seconds` field in the Fleet API. The Overview and Agent Detail consume the same Fleet record, so lifecycle semantics remain consistent without duplicating inference logic.

**Tech Stack:** Python 3, FastAPI, Pydantic, YAML, React 19, TypeScript, Vitest.

## Global Constraints

- `live_since` is durable and must not change when a process restarts.
- `last_updated_at` represents deployment or code/configuration updates, never conversation activity.
- `current_runtime_seconds` is diagnostic and must not appear on Overview cards.
- Missing dates display as `Not recorded`; no date may be fabricated.
- Preserve all unrelated user changes already present in the working tree.

---

### Task 1: Fleet lifecycle contract

**Files:**
- Modify: `backend/tests/test_fleet_service.py`
- Modify: `backend/tests/test_fleet_api.py`
- Modify: `backend/app/fleet/catalog.py`
- Modify: `backend/app/fleet/catalog.yaml`
- Modify: `backend/app/fleet/models.py`
- Modify: `backend/app/fleet/service.py`

**Interfaces:**
- Consumes: runtime `instance.uptime_seconds` and lifecycle fields from `AgentProfile`.
- Produces: `FleetAgent.live_since`, `live_since_basis`, `last_updated_at`, `last_updated_basis`, and `current_runtime_seconds`.

- [ ] **Step 1: Write failing Fleet service tests**

Add assertions that the `hr-bot` Fleet record returns its catalog lifecycle dates and maps runtime uptime to `current_runtime_seconds` without exposing Fleet `uptime_seconds`.

- [ ] **Step 2: Run the focused backend test and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_service.py -q`

Expected: FAIL because the new lifecycle properties do not exist.

- [ ] **Step 3: Extend catalog and response models**

Add nullable ISO timestamp strings and lifecycle basis literals to `AgentProfile` and `FleetAgent`. Populate all eleven catalog profiles from reviewed release artifacts, repository history, or earliest Session evidence. Rename only the Fleet response field from `uptime_seconds` to `current_runtime_seconds`; leave cluster and remote-health models unchanged.

- [ ] **Step 4: Project lifecycle metadata in FleetReadService**

Map profile fields directly to the Fleet response and map `instance.uptime_seconds` to `current_runtime_seconds`.

- [ ] **Step 5: Run focused backend tests and verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_service.py backend/tests/test_fleet_api.py -q`

Expected: all selected tests pass.

### Task 2: Lifecycle formatting and Overview card

**Files:**
- Modify: `webui/src/types.ts`
- Modify: `webui/src/copy.ts`
- Modify: `webui/src/copy.test.ts`
- Modify: `webui/src/fleet.ts`
- Modify: `webui/src/fleet.test.ts`
- Modify: `webui/src/FleetAgentCard.tsx`
- Modify: `webui/src/FleetAgentCard.test.tsx`

**Interfaces:**
- Consumes: Fleet lifecycle timestamps and basis fields from Task 1.
- Produces: `formatLifecycleDate` and `formatLastUpdated`, plus the new Overview metadata row.

- [ ] **Step 1: Write failing formatter and card tests**

Assert `Jun 17, 2026`, `3 hours ago`, and `Not recorded` outputs. Assert the card contains `Live Since` and `Last Updated` and does not contain `Uptime` or runtime duration text.

- [ ] **Step 2: Run focused frontend tests and verify RED**

Run: `npm test -- --run src/fleet.test.ts src/FleetAgentCard.test.tsx src/copy.test.ts`

Expected: FAIL because the new fields and formatters are absent.

- [ ] **Step 3: Implement lifecycle formatters and card copy**

Use English UI labels, an English calendar date, a compact English relative time, an exact timestamp title, and `Not recorded` for null or invalid values.

- [ ] **Step 4: Run focused frontend tests and verify GREEN**

Run: `npm test -- --run src/fleet.test.ts src/FleetAgentCard.test.tsx src/copy.test.ts`

Expected: all selected tests pass.

### Task 3: Agent Detail diagnostics

**Files:**
- Modify: `webui/src/pages/AgentDetailPage.tsx`
- Create: `webui/src/pages/AgentDetailPage.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `fetchFleetOverview`, the matching `FleetAgent`, and lifecycle formatters from Task 2.
- Produces: a lifecycle metadata block with `Live Since`, `Last Updated`, provenance, and `Current Runtime`.

- [ ] **Step 1: Write a failing Agent Detail source test**

Assert that Agent Detail fetches Fleet Overview, matches by Agent ID, and includes the three diagnostic labels and lifecycle provenance mapping.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm test -- --run src/pages/AgentDetailPage.test.tsx`

Expected: FAIL because Agent Detail does not yet load Fleet data.

- [ ] **Step 3: Implement Agent Detail lifecycle diagnostics**

Load Fleet Overview in the existing request group, match the current Agent, render exact lifecycle dates plus basis descriptions, and use the existing uptime formatter only for `Current Runtime`.

- [ ] **Step 4: Run focused frontend tests and verify GREEN**

Run: `npm test -- --run src/pages/AgentDetailPage.test.tsx`

Expected: the focused test passes.

### Task 4: Verification and deployment

**Files:**
- Verify: `backend/`
- Verify: `webui/`
- Deploy: existing Platform LaunchAgent/static build workflow

**Interfaces:**
- Consumes: all implementation tasks.
- Produces: a verified local production dashboard.

- [ ] **Step 1: Run the complete backend suite**

Run: `backend/.venv/bin/pytest backend/tests -q`

Expected: all tests pass.

- [ ] **Step 2: Run the complete frontend suite**

Run: `npm test -- --run`

Expected: all tests pass.

- [ ] **Step 3: Build the production frontend**

Run: `npm run build`

Expected: TypeScript and Vite finish with exit code 0.

- [ ] **Step 4: Restart only AI Agent Platform**

Use the existing LaunchAgent for `com.orbbec.ai-agent-platform`; do not restart any MetaBot, AI FAE, or AI ADMIN service.

- [ ] **Step 5: Verify the live API and rendered page**

Confirm `http://127.0.0.1:8000/api/fleet/overview` contains lifecycle fields and that `http://127.0.0.1:8000/` renders `Live Since` and `Last Updated` without `Uptime` on Overview cards.
