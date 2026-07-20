# MetaBot Cluster Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Agent entry portal with a read-only dashboard that auto-discovers and monitors the nine local MetaBot / Agent Bot instances.

**Architecture:** A FastAPI cluster monitor reloads the MetaBot runtime contract, probes every public `/api/health`, and exposes one aggregate snapshot at `/api/cluster/status`. The React UI renders that snapshot without links or control actions, and a macOS LaunchAgent serves the production build on `127.0.0.1:8000`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, HTTPX, pytest/respx, React 19, TypeScript, Vite, Vitest, macOS launchd.

## Global Constraints

- Runtime contract: `/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json`.
- Poll interval: 10 seconds; per-instance timeout: 3 seconds.
- Monitor only `GET /api/health`; do not read secrets or call authenticated endpoints.
- Never stop, restart, mutate, or send work to any MetaBot process.
- Dashboard has no Agent entry links and no control actions.
- Contract load failure keeps the last valid target list.
- Frontend API failure keeps the last valid snapshot and marks it stale.
- Production URL: `http://127.0.0.1:8000/`.
- Preserve unrelated dirty-worktree changes and stage only task files.

---

## File Structure

- Create `backend/app/cluster/models.py`: monitor target, status, summary, source, and snapshot models.
- Create `backend/app/cluster/contract.py`: runtime contract parsing and target discovery.
- Create `backend/app/cluster/monitor.py`: dynamic reload, probing, cache, sorting, and polling loop.
- Create `backend/app/cluster/routes.py`: `GET /api/cluster/status`.
- Create `backend/app/cluster/__init__.py`: package marker.
- Create `backend/tests/test_cluster_contract.py`: contract discovery and validation tests.
- Create `backend/tests/test_cluster_monitor.py`: probe, cache, summary, and fallback tests.
- Modify `backend/app/config.py`: contract path and 10-second cluster interval.
- Modify `backend/app/main.py`: initialize monitor, start loop, include route.
- Modify `backend/tests/test_api.py`: cluster API contract test.
- Replace `webui/src/types.ts`: cluster snapshot types.
- Replace `webui/src/api.ts`: cluster status fetcher.
- Replace `webui/src/status.ts`: status metadata, uptime, timestamp, and stale helpers.
- Replace `webui/src/status.test.ts`: helper behavior tests.
- Create `webui/src/dashboard.ts`: state transitions that retain the last snapshot on failure.
- Create `webui/src/dashboard.test.ts`: state transition tests.
- Replace `webui/src/App.tsx`: read-only dashboard UI.
- Modify `webui/src/styles.css`: dashboard summary, grid, instance cards, stale/error states.
- Modify `webui/index.html`: dashboard title.
- Track `webui/public/platform-logo.svg`: Platform identity asset already present in the migration.
- Create `deploy/com.orbbec.ai-agent-platform.plist`: local production LaunchAgent.
- Modify `README.md`: monitor and local production run instructions.

---

### Task 1: Runtime Contract Discovery

**Files:**
- Create: `backend/app/cluster/__init__.py`
- Create: `backend/app/cluster/models.py`
- Create: `backend/app/cluster/contract.py`
- Test: `backend/tests/test_cluster_contract.py`

**Interfaces:**
- Consumes: MetaBot runtime contract JSON with `bots` and optional `testBot`.
- Produces: `load_targets(path: str) -> list[MonitorTarget]` and `ContractLoadError`.

- [ ] **Step 1: Write failing discovery tests**

```python
def test_load_targets_includes_bots_and_enabled_test_bot(tmp_path):
    path = write_contract(tmp_path, bots=[bot("hr-bot", "metabot-hr", 9101)], test_bot=bot("test-bot", "metabot-test", 9106, enabled=True))
    targets = load_targets(str(path))
    assert [(item.id, item.pm2_name, item.port) for item in targets] == [
        ("hr-bot", "metabot-hr", 9101),
        ("test-bot", "metabot-test", 9106),
    ]

def test_disabled_test_bot_is_ignored(tmp_path):
    path = write_contract(tmp_path, bots=[], test_bot=bot("test-bot", "metabot-test", 9106, enabled=False))
    assert load_targets(str(path)) == []

def test_duplicate_name_or_port_is_rejected(tmp_path):
    path = write_contract(tmp_path, bots=[bot("a", "metabot-a", 9100), bot("a", "metabot-b", 9101)])
    with pytest.raises(ContractLoadError, match="duplicate bot id"):
        load_targets(str(path))
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cluster_contract.py -q`

Expected: FAIL because `app.cluster.contract` does not exist.

- [ ] **Step 3: Implement models and loader**

```python
class MonitorTarget(BaseModel):
    id: str
    name: str
    pm2_name: str
    port: int = Field(ge=1, le=65535)
    health_url: str
    workdir: str = ""

def load_targets(path: str) -> list[MonitorTarget]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = list(payload.get("bots", []))
    test_bot = payload.get("testBot")
    if isinstance(test_bot, dict) and test_bot.get("enabled", True):
        entries.append(test_bot)
    targets = [_target_from_entry(entry) for entry in entries]
    _reject_duplicate_ids_and_ports(targets)
    return targets
```

Map every target URL to `http://127.0.0.1:{port}/api/health`. Convert file, JSON, schema, duplicate-ID, and duplicate-port errors to safe `ContractLoadError` messages.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cluster_contract.py -q`

Expected: all contract tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/cluster backend/tests/test_cluster_contract.py
git commit -m "feat: discover MetaBot instances from runtime contract"
```

### Task 2: Cluster Monitor and API

**Files:**
- Modify: `backend/app/cluster/models.py`
- Create: `backend/app/cluster/monitor.py`
- Create: `backend/app/cluster/routes.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_cluster_monitor.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `load_targets(path)` and `MonitorTarget` from Task 1.
- Produces: `probe_target(...)`, `build_snapshot(...)`, `ClusterMonitor.poll_once(client)`, `ClusterMonitor.snapshot()`, `cluster_poll_loop(...)`, and `GET /api/cluster/status`.

- [ ] **Step 1: Write failing monitor tests**

```python
@pytest.mark.asyncio
@respx.mock
async def test_probe_classifies_health_states():
    respx.get("http://127.0.0.1:9100/api/health").mock(return_value=httpx.Response(200, json={"status": "ok", "uptime": 42}))
    status = await probe_target(httpx.AsyncClient(), target(9100), 3.0)
    assert status.status == "healthy"
    assert status.uptime_seconds == 42

@pytest.mark.asyncio
@respx.mock
async def test_non_200_is_degraded_and_connect_error_is_offline():
    respx.get("http://127.0.0.1:9101/api/health").mock(return_value=httpx.Response(503))
    respx.get("http://127.0.0.1:9102/api/health").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as client:
        degraded = await probe_target(client, target(9101), 3.0)
        offline = await probe_target(client, target(9102), 3.0)
    assert (degraded.status, degraded.error) == ("degraded", "http_503")
    assert (offline.status, offline.error) == ("offline", "connection_failed")

def test_snapshot_sorts_failures_first_and_summarizes():
    statuses = [instance("healthy", 9100), instance("checking", 9103), instance("offline", 9102), instance("degraded", 9101)]
    snapshot = build_snapshot(statuses, SourceStatus(healthy=True, checked_at="2026-07-20T10:00:00Z", error=None))
    assert [item.status for item in snapshot.instances] == ["offline", "degraded", "checking", "healthy"]
    assert snapshot.summary.model_dump() == {"total": 4, "healthy": 1, "degraded": 1, "offline": 1, "checking": 1}

@pytest.mark.asyncio
async def test_invalid_contract_keeps_last_good_targets(tmp_path):
    path = write_contract(tmp_path, bots=[bot("hr-bot", "metabot-hr", 9101)])
    monitor = ClusterMonitor(str(path), timeout=3.0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok", "uptime": 5}))) as client:
        await monitor.poll_once(client)
        path.write_text("{", encoding="utf-8")
        await monitor.poll_once(client)
    snapshot = monitor.snapshot()
    assert snapshot.source.healthy is False
    assert [item.id for item in snapshot.instances] == ["hr-bot"]
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cluster_monitor.py -q`

Expected: FAIL because cluster monitor symbols do not exist.

- [ ] **Step 3: Implement monitor models and behavior**

```python
class InstanceStatus(BaseModel):
    id: str
    name: str
    pm2_name: str
    port: int
    status: Literal["healthy", "degraded", "offline", "checking"] = "checking"
    uptime_seconds: int | None = None
    latency_ms: int | None = None
    checked_at: str | None = None
    error: str | None = None

class ClusterSummary(BaseModel):
    total: int
    healthy: int
    degraded: int
    offline: int
    checking: int

class SourceStatus(BaseModel):
    healthy: bool
    checked_at: str | None
    error: str | None

class ClusterSnapshot(BaseModel):
    summary: ClusterSummary
    source: SourceStatus
    instances: list[InstanceStatus]
```

`probe_target` must treat a reachable invalid response as `degraded`, exceptions as `offline`, and never copy exception text containing URLs or environment data. `ClusterMonitor.poll_once` reloads the contract first, synchronizes cache IDs, and preserves the last valid targets on `ContractLoadError`.

- [ ] **Step 4: Add route and app wiring**

```python
router = APIRouter(prefix="/api/cluster", tags=["cluster"])

@router.get("/status")
def cluster_status(request: Request) -> dict:
    return request.app.state.cluster_monitor.snapshot().model_dump()
```

Add to `Config`:

```python
metabot_contract_path=os.getenv(
    "PLATFORM_METABOT_CONTRACT_PATH",
    "/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json",
),
cluster_poll_interval_seconds=float(os.getenv("PLATFORM_CLUSTER_POLL_INTERVAL", "10")),
```

Initialize `ClusterMonitor` in `create_app`, start its loop in lifespan when `start_poller=True`, store it as `app.state.cluster_monitor`, and include the cluster router before static mounting.

- [ ] **Step 5: Add API test and verify GREEN**

```python
def test_cluster_status_returns_snapshot(client):
    response = client.get("/api/cluster/status")
    assert response.status_code == 200
    assert set(response.json()) == {"summary", "source", "instances"}
```

Run: `cd backend && .venv/bin/python -m pytest -q`

Expected: all backend tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/cluster backend/app/config.py backend/app/main.py backend/tests/test_cluster_monitor.py backend/tests/test_api.py
git commit -m "feat: expose MetaBot cluster health snapshot"
```

### Task 3: Dashboard State and Presentation

**Files:**
- Replace: `webui/src/types.ts`
- Replace: `webui/src/api.ts`
- Replace: `webui/src/status.ts`
- Replace: `webui/src/status.test.ts`
- Create: `webui/src/dashboard.ts`
- Create: `webui/src/dashboard.test.ts`
- Replace: `webui/src/App.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/index.html`
- Add: `webui/public/platform-logo.svg`

**Interfaces:**
- Consumes: `GET /api/cluster/status` from Task 2.
- Produces: `fetchClusterStatus`, `formatUptime`, `formatCheckedAt`, `statusMeta`, `isStale`, `applySuccess`, and `applyFailure`.

- [ ] **Step 1: Write failing frontend helper tests**

```typescript
it("formats uptime without hiding days", () => {
  expect(formatUptime(90061)).toBe("1天 1小时 1分钟");
});

it("marks a check older than 30 seconds stale", () => {
  expect(isStale("2026-07-20T10:00:00Z", new Date("2026-07-20T10:00:31Z"))).toBe(true);
});

it("keeps the previous snapshot on failure", () => {
  const failed = applyFailure({ snapshot, degraded: false });
  expect(failed.snapshot).toBe(snapshot);
  expect(failed.degraded).toBe(true);
});
```

- [ ] **Step 2: Verify RED**

Run: `cd webui && npm test`

Expected: FAIL because the new helpers and dashboard state do not exist.

- [ ] **Step 3: Implement types, helpers, and state**

```typescript
export type InstanceState = "healthy" | "degraded" | "offline" | "checking";

export interface ClusterSnapshot {
  summary: Record<InstanceState | "total", number>;
  source: { healthy: boolean; checked_at: string | null; error: string | null };
  instances: InstanceStatus[];
}

export async function fetchClusterStatus(): Promise<ClusterSnapshot> {
  const response = await fetch("/api/cluster/status");
  if (!response.ok) throw new Error(`cluster ${response.status}`);
  return response.json();
}
```

Implement pure formatting and reducer-style state functions so API-failure retention is testable without adding a browser testing dependency.

```typescript
export function formatUptime(seconds: number | null): string {
  if (seconds === null) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return [days && `${days}天`, (days || hours) && `${hours}小时`, `${minutes}分钟`].filter(Boolean).join(" ");
}

export function isStale(checkedAt: string | null, now = new Date()): boolean {
  return checkedAt === null || now.getTime() - new Date(checkedAt).getTime() > 30_000;
}

export const applySuccess = (state: DashboardState, snapshot: ClusterSnapshot): DashboardState => ({ snapshot, degraded: false });
export const applyFailure = (state: DashboardState): DashboardState => ({ ...state, degraded: true });
```

- [ ] **Step 4: Implement the read-only dashboard**

`App.tsx` fetches immediately and every 10 seconds, retains the previous snapshot on failure, renders five summary values, and renders non-link instance cards with Bot name, PM2 name, port, status, uptime, latency, and check time. It must render no `entry_url`, “进入工作台”, or control button.

```tsx
const [state, setState] = useState<DashboardState>({ snapshot: null, degraded: false });
useEffect(() => {
  let stopped = false;
  const refresh = async () => {
    try {
      const snapshot = await fetchClusterStatus();
      if (!stopped) setState((current) => applySuccess(current, snapshot));
    } catch {
      if (!stopped) setState(applyFailure);
    }
  };
  refresh();
  const timer = window.setInterval(refresh, 10_000);
  return () => { stopped = true; window.clearInterval(timer); };
}, []);

return <main>{state.snapshot?.instances.map((instance) => (
  <article className={`instance-card ${instance.status}`} key={instance.id}>
    <h2>{instance.name}</h2>
    <span>{instance.pm2_name}</span><span>:{instance.port}</span>
    <span>{formatUptime(instance.uptime_seconds)}</span>
    <span>{instance.latency_ms === null ? "—" : `${instance.latency_ms} ms`}</span>
  </article>
))}</main>;
```

- [ ] **Step 5: Implement focused styles and metadata**

Reuse the migrated Platform logo and existing design tokens. Add summary tiles, dense operational cards, explicit status labels, stale treatment, source-error banner, responsive one-column layout, and a non-interactive card cursor. Change the HTML title to `MetaBot Cluster Monitor`.

```css
.summary-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.instance-card { cursor: default; border-left: 4px solid var(--idle); }
.instance-card.healthy { border-left-color: var(--ok); }
.instance-card.degraded { border-left-color: var(--warn); }
.instance-card.offline { border-left-color: var(--down); }
.stale { opacity: 0.68; }
@media (max-width: 720px) {
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .instance-grid { grid-template-columns: 1fr; }
}
```

- [ ] **Step 6: Verify GREEN**

Run: `cd webui && npm test && npm run build`

Expected: all Vitest tests pass and Vite emits `dist` successfully.

- [ ] **Step 7: Commit**

```bash
git add webui/src/types.ts webui/src/api.ts webui/src/status.ts webui/src/status.test.ts webui/src/dashboard.ts webui/src/dashboard.test.ts webui/src/App.tsx webui/src/styles.css webui/index.html webui/public/platform-logo.svg
git commit -m "feat: replace Agent portal with cluster dashboard"
```

### Task 4: Local Production Deployment

**Files:**
- Create: `deploy/com.orbbec.ai-agent-platform.plist`
- Modify: `README.md`

**Interfaces:**
- Consumes: production `webui/dist`, backend `.venv`, cluster contract, and `registry.local.yaml`.
- Produces: launchd label `com.orbbec.ai-agent-platform` and dashboard URL `http://127.0.0.1:8000/`.

- [ ] **Step 1: Add the LaunchAgent definition**

Use exact absolute paths and these environment variables:

```xml
<key>Label</key><string>com.orbbec.ai-agent-platform</string>
<key>ProgramArguments</key>
<array>
  <string>/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/uvicorn</string>
  <string>app.main:create_app</string><string>--factory</string>
  <string>--host</string><string>127.0.0.1</string>
  <string>--port</string><string>8000</string>
</array>
<key>WorkingDirectory</key><string>/Users/neo/Developer/work/AI-Agent-Platform/backend</string>
<key>EnvironmentVariables</key>
<dict>
  <key>PLATFORM_REGISTRY_PATH</key><string>/Users/neo/Developer/work/AI-Agent-Platform/registry.local.yaml</string>
  <key>PLATFORM_METABOT_CONTRACT_PATH</key><string>/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json</string>
  <key>PLATFORM_CLUSTER_POLL_INTERVAL</key><string>10</string>
  <key>PLATFORM_STATIC_DIR</key><string>/Users/neo/Developer/work/AI-Agent-Platform/webui/dist</string>
</dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
```

Write stdout and stderr to `/Users/neo/Library/Logs/OrbbecAI-Agent-Platform.stdout.log` and `.stderr.log`.

- [ ] **Step 2: Document local monitor operation**

Update README so local development and production instructions use the runtime contract and describe the dashboard as read-only monitoring, not an Agent entry portal.

- [ ] **Step 3: Validate and commit deployment files**

Run: `plutil -lint deploy/com.orbbec.ai-agent-platform.plist`

Expected: `OK`.

```bash
git add deploy/com.orbbec.ai-agent-platform.plist README.md
git commit -m "chore: add local cluster dashboard service"
```

- [ ] **Step 4: Record MetaBot process IDs before deployment**

Run: `pgrep -f '/Users/agentops/AgentRuntime/metabot/src/index.ts' | sort -n`

Expected: nine PIDs. Save the displayed list in the execution notes; do not signal these processes.

- [ ] **Step 5: Build and install the LaunchAgent**

Run the production build, stop only the two temporary Platform development sessions, copy the validated plist to `/Users/neo/Library/LaunchAgents/com.orbbec.ai-agent-platform.plist`, then use `launchctl bootstrap gui/$(id -u)` and `launchctl kickstart -k` for label `com.orbbec.ai-agent-platform`. Do not use broad process-kill patterns.

- [ ] **Step 6: Verify deployment**

Run:

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/health
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/cluster/status
curl --noproxy '*' -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
launchctl print gui/$(id -u)/com.orbbec.ai-agent-platform
```

Expected: Platform health is `ok`; cluster snapshot contains nine instances; page returns `200`; LaunchAgent state is `running`.

- [ ] **Step 7: Verify MetaBot isolation**

Re-run the exact `pgrep` command from Step 4. Expected: the same nine MetaBot PIDs remain, proving deployment did not restart the monitored cluster.

### Task 5: Final Regression and Handoff

**Files:**
- Verify only; no new production files.

- [ ] **Step 1: Run full backend regression**

Run: `cd backend && .venv/bin/python -m pytest`

Expected: all tests pass with zero warnings.

- [ ] **Step 2: Run full frontend regression and audit**

Run: `cd webui && npm test && npm run build && npm audit --omit=dev`

Expected: tests and build pass; production dependency audit reports zero vulnerabilities.

- [ ] **Step 3: Inspect repository state**

Run: `git status --short --branch && git diff --check`

Expected: only unrelated pre-existing changes remain unstaged; no whitespace errors in implementation changes.

- [ ] **Step 4: Browser smoke test**

Open `http://127.0.0.1:8000/` and verify the summary reports nine total instances, every contract instance has a card, cards contain no links or controls, and the page remains readable at desktop and mobile widths.

- [ ] **Step 5: Final handoff**

Report the dashboard URL, LaunchAgent label, test counts, current cluster summary, remaining unrelated dirty files, and commands to inspect or restart only the Platform service.
