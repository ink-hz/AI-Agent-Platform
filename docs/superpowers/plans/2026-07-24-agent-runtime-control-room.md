# Agent Runtime Control Room Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the thin lifecycle block on Agent Detail with a trustworthy Runtime control-room slice that shows readiness, the model and backend actually in use, the primary channel, production running days, and a read-only evidence detail surface.

**Architecture:** Add a sanitized loopback-only MetaBot runtime observation endpoint, enrich Platform's existing local and remote runtime adapters, and compose those sources with latest Trace evidence and lifecycle metadata in one canonical `AgentRuntimeView`. The React page consumes that read model directly and never infers readiness or active model from unrelated UI fields.

**Tech Stack:** TypeScript 5.9 and Vitest in MetaBot; Python 3.11, FastAPI, Pydantic, httpx, psycopg 3, and pytest in Platform; React 19, TypeScript 5.6, Vitest 2.1, jsdom, and CSS for the WebUI.

## Scope and Decisions

- This plan delivers the Runtime slice only. Capability inventory and scheduler-backed Activity remain the next Control Room slices.
- Runtime states are exactly `Ready`, `Busy`, `Limited`, `Offline`, and `Unknown`.
- A healthy process is not sufficient for `Ready`; a MetaBot must also have a fresh runtime observation and a connected primary channel.
- `Busy` is `Ready` plus one or more active turns.
- `Limited` requires a confirmed degraded runtime or required channel/dependency failure. Missing evidence produces `Unknown`.
- The active model source order is: fresh runtime observation, latest completed Trace, then a visibly labeled configured fallback.
- `Running for N days` is calculated from `live_since`; process uptime appears only on Runtime Detail.
- The MetaBot observation endpoint returns no App ID, token, prompt, chat ID, filesystem path, provider URL, or raw error.
- AI FAE and AI ADMIN reuse the existing remote health/SSH observations. No new public endpoint is opened on Alibaba Cloud.
- Existing Agent descriptions and model names retain their source language.
- All UI remains read-only. No restart, deploy, configuration, or schedule controls are added.
- Existing user-owned dirty files are not edited or staged, especially `backend/app/health/normalizer.py`, `backend/tests/test_health_normalizer.py`, `.claude/`, `registry.local.yaml`, and local logo assets.

## Public Read Model

The Platform endpoint is `GET /api/agents/{agent_id}/runtime` and returns:

```json
{
  "agent_id": "marketing-inbound-bot",
  "readiness": {
    "status": "Ready",
    "reason": "Runtime and primary channel are available",
    "observed_at": "2026-07-24T08:00:00Z",
    "freshness": "live"
  },
  "runtime": {
    "engine": "claude",
    "model": "claude-opus-4-8",
    "model_source": "runtime",
    "backend": "pty",
    "channel": "Feishu",
    "channel_status": "connected",
    "active_turns": 0,
    "process_uptime_seconds": 43120
  },
  "lifecycle": {
    "live_since": "2026-07-16T16:09:46.384700+08:00",
    "last_updated_at": "2026-07-17T11:09:41+08:00",
    "production_runtime_seconds": 672614
  },
  "evidence": []
}
```

`model_source` is one of `runtime`, `trace`, `configured`, or `unavailable`. Configured fallback is rendered as `Configured model`, never as an observed active model.

---

### Task 1: Publish a sanitized MetaBot runtime observation

**Repository:** `/Users/neo/Developer/work/metabot-dev`

**Files:**
- Modify: `src/reliability/runtime-status.ts`
- Modify: `src/api/bot-registry.ts`
- Modify: `src/api/http-server.ts`
- Modify: `tests/runtime-status.test.ts`
- Modify: `tests/http-server-cross-verify.test.ts`

**Interfaces:**
- Consumes: the existing in-memory Bot registry, Feishu WebSocket snapshot, persistent executor registry, and runtime configuration.
- Produces: loopback-only `GET /api/observability/runtime` with sanitized per-Bot runtime facts.

- [ ] **Step 1: Write failing runtime-status tests**

Extend fixtures so `BotRuntimeSource` can expose `activeTurns`, and assert `buildRuntimeStatus()` returns it without leaking a session or chat identifier:

```ts
const result = buildRuntimeStatus({
  releaseSha: "abc123",
  bots: [{
    name: "marketing-inbound-bot",
    platform: "feishu",
    engine: "claude",
    model: "claude-opus-4-8",
    backend: "pty",
    activeTurns: () => 1,
    connectionStatus: () => ({ state: "connected", reconnectAttempts: 0 }),
  }],
});

expect(result.bots[0]).toMatchObject({ activeTurns: 1, ws: { state: "connected" } });
expect(JSON.stringify(result)).not.toMatch(/chat|session|token|secret|workdir/iu);
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `npm test -- tests/runtime-status.test.ts tests/http-server-cross-verify.test.ts`

Expected: FAIL because `activeTurns` and the observability-route classification do not exist.

- [ ] **Step 3: Add a side-effect-free per-Bot active-turn reader**

Add `activeTurns?: () => number` to `BotRuntimeSource`. In `BotRegistry.listRuntimeSources()`, count `hasActiveTurn` only inside that registered Bot's persistent executor registry. Normalize exceptions to `null`; never return executor identities.

- [ ] **Step 4: Add an explicit loopback observation route**

In `http-server.ts`, add pure helpers:

```ts
export function isRuntimeObservationRoute(method: string, url: string): boolean {
  return method === "GET" && url === "/api/observability/runtime";
}

export function isLoopbackAddress(address: string | undefined): boolean {
  return address === "127.0.0.1" || address === "::1" || address === "::ffff:127.0.0.1";
}
```

Bypass bearer authentication only when both helpers return true. Reject non-loopback callers with `403`. Return `buildRuntimeStatus(...)`; do not weaken the existing `/api/status` authentication and do not add fields to public `/api/health`.

- [ ] **Step 5: Verify MetaBot tests and type checking**

Run:

```bash
npm test -- tests/runtime-status.test.ts tests/http-server-cross-verify.test.ts
npm run typecheck
```

Expected: focused tests and type checking PASS.

- [ ] **Step 6: Commit the MetaBot observation contract**

```bash
git add src/reliability/runtime-status.ts src/api/bot-registry.ts src/api/http-server.ts tests/runtime-status.test.ts tests/http-server-cross-verify.test.ts
git commit -m "feat: expose local runtime observations"
```

---

### Task 2: Enrich Platform's local runtime adapter

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/cluster/models.py`
- Modify: `backend/app/cluster/contract.py`
- Modify: `backend/app/cluster/monitor.py`
- Modify: `backend/tests/test_cluster_contract.py`
- Modify: `backend/tests/test_cluster_monitor.py`

**Interfaces:**
- Consumes: `metabot.runtime-contract.json`, `/api/health`, and loopback `/api/observability/runtime`.
- Produces: safe `InstanceStatus` runtime fields used by the canonical Runtime service.

- [ ] **Step 1: Add failing contract tests for safe runtime declarations**

Assert each `MonitorTarget` retains only:

```py
assert target.engine == "claude"
assert target.declared_model == "claude-opus-4-8"
assert target.backend == "pty"
assert target.channel == "Feishu"
assert target.runtime_url == "http://127.0.0.1:9103/api/observability/runtime"
assert "appId" not in target.model_dump_json()
```

Presence of a valid contract `appId` maps to the display-safe channel `Feishu`; its value is discarded.

- [ ] **Step 2: Add failing monitor tests for observed runtime facts and degradation**

Cover:

- matching the returned Bot by exact runtime ID;
- connected WebSocket plus `activeTurns: 0`;
- `activeTurns: 1`;
- missing/malformed observation while `/api/health` remains healthy;
- non-200 observation response;
- no secrets or workdir fingerprints in `InstanceStatus.model_dump_json()`.

- [ ] **Step 3: Run the focused backend tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/test_cluster_contract.py tests/test_cluster_monitor.py -q`

Expected: FAIL because safe runtime fields and the second probe do not exist.

- [ ] **Step 4: Extend the target and instance models**

Add presentation-safe optional fields:

```py
engine: str | None = None
declared_model: str | None = None
observed_model: str | None = None
backend: str | None = None
channel: str | None = None
channel_status: Literal["connected", "connecting", "reconnecting", "failed", "unknown"] = "unknown"
active_turns: int | None = None
runtime_observed_at: str | None = None
```

Keep `workdir` internal to `MonitorTarget`; never copy it into `InstanceStatus`.

- [ ] **Step 5: Probe liveness and observation independently**

`probe_target()` must always preserve the existing `/api/health` result. A missing observation endpoint leaves observation fields unknown but does not mark the process offline. Parse only the whitelisted fields for the exact Bot and stamp `runtime_observed_at` locally.

- [ ] **Step 6: Run focused tests and commit**

```bash
cd backend
.venv/bin/pytest tests/test_cluster_contract.py tests/test_cluster_monitor.py -q
git add app/cluster/models.py app/cluster/contract.py app/cluster/monitor.py tests/test_cluster_contract.py tests/test_cluster_monitor.py
git commit -m "feat: observe MetaBot runtime facts"
```

---

### Task 3: Add latest-Trace and remote runtime evidence

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/observability/models.py`
- Modify: `backend/app/observability/repository.py`
- Modify: `backend/app/observability/service.py`
- Modify: `backend/app/remote_health/models.py`
- Modify: `backend/app/remote_health/monitor.py`
- Modify: `backend/tests/test_observability_repository.py`
- Modify: `backend/tests/test_remote_health.py`

**Interfaces:**
- Consumes: `platform_read.traces`, FAE `/health`, and the existing ADMIN SSH health snapshot.
- Produces: a normalized latest completed runtime observation per Agent.

- [ ] **Step 1: Write failing repository tests for source precedence input**

Add `RuntimeObservation` with `engine`, `model`, `backend`, `observed_at`, and `source_kind`. Test that `get_latest_runtime_observation(agent_id)` executes a bounded query ordered by `coalesce(completed_at, started_at) desc` and returns `None` when no usable trace exists.

- [ ] **Step 2: Write failing remote normalization tests**

Assert FAE uses top-level `llm_model` when present and ADMIN uses `details.health.llm_model`. Also assert the remote snapshot preserves channel status only from confirmed service evidence: ADMIN's DingTalk unit active means `connected`; absent evidence means `unknown`.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_observability_repository.py tests/test_remote_health.py -q
```

- [ ] **Step 4: Implement the normalized evidence methods**

Use a parameterized, single-row PostgreSQL query. Do not read raw Trace details or answers. Extend `RemoteAgentStatus` with only safe normalized runtime fields; leave the existing `details` payload internal to the backend.

- [ ] **Step 5: Verify and commit**

```bash
cd backend
.venv/bin/pytest tests/test_observability_repository.py tests/test_remote_health.py -q
git add app/observability/models.py app/observability/repository.py app/observability/service.py app/remote_health/models.py app/remote_health/monitor.py tests/test_observability_repository.py tests/test_remote_health.py
git commit -m "feat: normalize Agent runtime evidence"
```

---

### Task 4: Compose the canonical Agent Runtime API

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/app/control_room/__init__.py`
- Create: `backend/app/control_room/models.py`
- Create: `backend/app/control_room/service.py`
- Create: `backend/app/control_room/routes.py`
- Create: `backend/tests/test_control_room_service.py`
- Create: `backend/tests/test_control_room_api.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `AgentCatalog`, `ClusterMonitor`, `RemoteHealthMonitor`, `FleetReadService`, and `ObservabilityService`.
- Produces: `GET /api/agents/{agent_id}/runtime`.

- [ ] **Step 1: Write the readiness truth-table tests**

Cover at minimum:

```py
@pytest.mark.parametrize(("process", "channel", "active_turns", "expected"), [
    ("healthy", "connected", 0, "Ready"),
    ("healthy", "connected", 1, "Busy"),
    ("healthy", "failed", 0, "Limited"),
    ("offline", "connected", 0, "Offline"),
    ("healthy", "unknown", 0, "Unknown"),
])
```

Also prove stale runtime observations cannot establish `Ready`.

- [ ] **Step 2: Write model precedence and lifecycle tests**

Assert:

- fresh runtime model wins over a newer-looking configured default;
- latest completed Trace wins when runtime model is absent;
- configured fallback carries `model_source="configured"`;
- absence produces `Model not observed` and `model_source="unavailable"`;
- process uptime reset does not change production runtime anchored to `live_since`;
- partial source failures still return available lifecycle and Agent identity fields.

- [ ] **Step 3: Write API privacy and status tests**

Verify 200, 404, and partial-evidence responses. Serialize the response and assert it contains none of: `appId`, `appSecret`, `token`, `workdir`, `stateDir`, `configPath`, `providerUrl`, or raw health payloads.

- [ ] **Step 4: Run focused tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/test_control_room_service.py tests/test_control_room_api.py tests/test_main.py -q`

- [ ] **Step 5: Implement models and pure composition**

Keep readiness resolution in a pure function. Use a 120-second freshness boundary consistent with Fleet runtime freshness. Return evidence entries with only `kind`, `source`, `status`, `observed_at`, and a concise safe summary.

- [ ] **Step 6: Wire the service once in `create_app()`**

Set `app.state.control_room_service` and include the router. Test injection must allow fake services without live PostgreSQL, SSH, or MetaBot calls.

- [ ] **Step 7: Verify and commit**

```bash
cd backend
.venv/bin/pytest tests/test_control_room_service.py tests/test_control_room_api.py tests/test_main.py -q
git add app/control_room app/main.py tests/test_control_room_service.py tests/test_control_room_api.py tests/test_main.py
git commit -m "feat: add canonical Agent runtime API"
```

---

### Task 5: Build Runtime summary and detail surfaces

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `webui/src/types.ts`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/pages/AgentDetailPage.tsx`
- Modify: `webui/src/pages/AgentDetailPage.test.tsx`
- Create: `webui/src/pages/AgentRuntimePage.tsx`
- Create: `webui/src/pages/AgentRuntimePage.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/agents/{agent_id}/runtime`.
- Produces: compact Runtime card on Agent Detail and read-only `/agents/{id}/runtime` evidence page.

- [ ] **Step 1: Add failing TypeScript/API and route tests**

Define `AgentRuntimeView` matching the backend exactly, add `fetchAgentRuntime()`, and extend the router with `agent-runtime`. Require percent-encoded Agent IDs and a contextual title such as `Runtime · Marketing Inbound · Orbbec Agent Platform`.

- [ ] **Step 2: Replace old lifecycle-card expectations with the approved Runtime hierarchy**

Agent Detail tests must assert this order:

1. About identity;
2. Runtime;
3. existing Recent Activity;
4. existing Recent Sessions.

Runtime must show one prominent readiness badge, model plus backend on one line, channel connection, and `Running for N days`. It must not show process uptime, port, latency, token, cost, feedback, or a separate model panel.

- [ ] **Step 3: Add failing detail-surface tests**

Runtime Detail shows:

- readiness reason and observation freshness;
- current model and its evidence label;
- engine/backend and channel evidence;
- process uptime clearly labeled `Current process`;
- `Live Since`, `Last Updated`, and production duration;
- compact empty/unknown copy without treating missing evidence as failure.

- [ ] **Step 4: Run focused frontend tests and verify RED**

Run:

```bash
cd webui
npm test -- src/router.test.ts src/documentTitle.test.tsx src/pages/AgentDetailPage.test.tsx src/pages/AgentRuntimePage.test.tsx
```

- [ ] **Step 5: Implement the Runtime card and page**

Use the existing typography and card tokens, but give the Runtime card stronger visual weight than the removed three-column lifecycle block. On narrow layouts stack facts without reducing body copy below the platform minimum. Keep the rest of Agent Detail available if the Runtime request fails.

- [ ] **Step 6: Verify, build, and commit**

```bash
cd webui
npm test -- src/router.test.ts src/documentTitle.test.tsx src/pages/AgentDetailPage.test.tsx src/pages/AgentRuntimePage.test.tsx
npm run build
git add src/types.ts src/api.ts src/router.ts src/router.test.ts src/documentTitle.ts src/documentTitle.test.tsx src/App.tsx src/pages/AgentDetailPage.tsx src/pages/AgentDetailPage.test.tsx src/pages/AgentRuntimePage.tsx src/pages/AgentRuntimePage.test.tsx src/styles.css
git commit -m "feat: add Agent runtime control room"
```

---

### Task 6: Full verification and rolling deployment

**Repositories:**
- `/Users/neo/Developer/work/metabot-dev`
- `/Users/neo/Developer/work/Orbbec-Agent-Team`
- `/Users/neo/Developer/work/AI-Agent-Platform`

- [ ] **Step 1: Review diffs and preserve unrelated work**

Run `git status --short` and scoped `git diff --check` in all three repositories. Do not stage `.tools/`, `reports/`, Beisen index files, Platform local registry/logo files, or the existing health-normalizer changes.

- [ ] **Step 2: Run complete automated verification**

```bash
cd /Users/neo/Developer/work/metabot-dev
npm test
npm run typecheck

cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/pytest -q

cd /Users/neo/Developer/work/AI-Agent-Platform/webui
npm test
npm run build
```

Expected: every suite passes and the production frontend build succeeds.

- [ ] **Step 3: Deploy MetaBot observation support with existing safety gates**

Use the maintained MetaBot source deployment workflow documented in `/Users/neo/Developer/work/Orbbec-Agent-Team/docs/deployment.md`. Capture existing PIDs first; update one instance at a time; require `/api/health` plus loopback `/api/observability/runtime` after each restart; stop and roll back on the first failed gate. Never batch-kill or broadly restart PM2 processes.

- [ ] **Step 4: Deploy only the Platform service**

Build `webui/dist`, then restart only `com.orbbec.ai-agent-platform`:

```bash
launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform
```

- [ ] **Step 5: Verify four representative source combinations**

Check MetaBot (`marketing-inbound-bot`), Iris Codex (`codex-assistant`), AI FAE, and AI ADMIN:

```bash
for agent_id in marketing-inbound-bot codex-assistant ai-fae-agent ai-admin-agent; do
  curl --noproxy '*' -fsS "http://127.0.0.1:8000/api/agents/${agent_id}/runtime" |
    jq '{agent_id, readiness, runtime, lifecycle}'
done
```

Expected:

- no response contains secret or raw path fields;
- MetaBot channel evidence reflects the actual WebSocket state;
- Codex does not invent a model when neither runtime nor Trace observed one;
- FAE and ADMIN use their live remote health model when available;
- all four use lifecycle-based production duration rather than process uptime.

- [ ] **Step 6: Verify the deployed UI and service isolation**

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/health
curl --noproxy '*' -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/agents/marketing-inbound-bot
launchctl print gui/$(id -u)/com.orbbec.ai-agent-platform | rg 'state =|pid =|last exit code'
```

Open Agent Detail and Runtime Detail at desktop and narrow widths. Confirm visual hierarchy, fallback copy, model evidence labels, and that no controls were introduced.

- [ ] **Step 7: Record the integration commit**

After verification, commit any final Platform-only documentation adjustments with exact paths. Push only after all repository states and production checks are reported.

## Plan Review Checklist

- [ ] Every Runtime field has a named evidence source and missing-data behavior.
- [ ] Readiness never equates process liveness with Agent readiness.
- [ ] Model precedence and configured-fallback labeling are covered by tests.
- [ ] Production duration and process uptime remain separate.
- [ ] Loopback observation exposes no credentials, IDs, paths, prompts, or chat state.
- [ ] MetaBot, Iris Codex, AI FAE, and AI ADMIN are all covered.
- [ ] No unrelated dirty file is modified or staged.
- [ ] Capability and Activity work is intentionally deferred, not represented by placeholders in the UI.
