# AI Agent Fleet Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first production slice of the approved AI Agent fleet explorer: a read-only team cockpit backed by real MetaBot health and non-synthetic flywheel usage data.

**Architecture:** Keep the existing cluster monitor as the runtime truth source. Add a read-only PostgreSQL repository using the existing `flywheel_analyst` Keychain credential, a 60-second in-process usage cache, and a service that merges runtime state, curated Agent identity, canonical Bot aliases, and answered-turn counts into `/api/fleet/overview`. Replace the technical monitor homepage with the approved usage-first React cockpit while preserving partial-data degradation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, psycopg 3, PostgreSQL, React 19, TypeScript, Vitest, Vite, CSS

## Global Constraints

- The feature is local-only, single-user, and read-only.
- Do not add Agent controls, entry links, task dispatch, restart, edit, approval, or publish actions.
- Use only real `flywheel_analytics` data; synthetic/canary rows must never enter usage totals.
- Count one conversation as one distinct `turn_id` with a persisted `role = 'assistant'` message.
- Keep `marketing-bot` history mapped to `marketing-prospecting-bot`; unresolved legacy IDs do not become current Agent cards.
- Runtime status refreshes every 10 seconds; usage aggregation is cached for 60 seconds.
- PostgreSQL failure must preserve runtime status and return usage values as unavailable, never fake zeroes.
- Do not expose database URLs, Keychain output, stack traces, workdirs, ports, latency, PM2 names, or health URLs in the new homepage API.
- Do not modify or restart any MetaBot process.
- Preserve unrelated working-tree changes in `backend/app/health/normalizer.py`, `backend/tests/test_health_normalizer.py`, and existing untracked files.

---

### Task 1: Read-only flywheel configuration and secret resolution

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/config.py`
- Create: `backend/app/fleet/__init__.py`
- Create: `backend/app/fleet/database.py`
- Create: `backend/tests/test_fleet_database.py`

**Interfaces:**
- Consumes: optional `PLATFORM_FLYWHEEL_DATABASE_URL`; otherwise macOS Keychain service `flywheel-analyst-database-url` for account `neo`.
- Produces: `resolve_flywheel_database_url(config: Config, runner=subprocess.run) -> str | None` without logging the secret.

- [ ] **Step 1: Write the failing configuration and secret tests**

```python
from subprocess import CompletedProcess

from app.config import load_config
from app.fleet.database import resolve_flywheel_database_url


def test_flywheel_config_defaults(monkeypatch):
    monkeypatch.delenv("PLATFORM_FLYWHEEL_ENABLED", raising=False)
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL", raising=False)
    config = load_config()
    assert config.flywheel_enabled is True
    assert config.flywheel_keychain_service == "flywheel-analyst-database-url"
    assert config.usage_cache_seconds == 60
    assert config.active_window_minutes == 15


def test_environment_database_url_wins(monkeypatch):
    monkeypatch.setenv("PLATFORM_FLYWHEEL_DATABASE_URL", "postgresql://example")
    config = load_config()
    assert resolve_flywheel_database_url(config) == "postgresql://example"


def test_keychain_failure_disables_usage_without_leaking(monkeypatch):
    monkeypatch.delenv("PLATFORM_FLYWHEEL_DATABASE_URL", raising=False)
    config = load_config()

    def failed(*_args, **_kwargs):
        return CompletedProcess([], 44, stdout="", stderr="secret detail")

    assert resolve_flywheel_database_url(config, runner=failed) is None
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_database.py -q`

Expected: FAIL because the fleet config fields and database module do not exist.

- [ ] **Step 3: Add psycopg and implement safe secret resolution**

Add `psycopg[binary]>=3.2` to `backend/requirements.txt` and extend `Config` with:

```python
flywheel_enabled: bool
flywheel_database_url: str | None
flywheel_keychain_service: str
flywheel_keychain_account: str
usage_cache_seconds: float
active_window_minutes: int
```

Parse booleans with `os.getenv("PLATFORM_FLYWHEEL_ENABLED", "1") not in {"0", "false", "False"}` and use defaults `flywheel-analyst-database-url`, `neo`, `60`, and `15`.

Implement `backend/app/fleet/database.py`:

```python
import subprocess
from collections.abc import Callable

from app.config import Config


Runner = Callable[..., subprocess.CompletedProcess[str]]


def resolve_flywheel_database_url(
    config: Config,
    runner: Runner = subprocess.run,
) -> str | None:
    if not config.flywheel_enabled:
        return None
    if config.flywheel_database_url:
        return config.flywheel_database_url
    result = runner(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            config.flywheel_keychain_account,
            "-s",
            config.flywheel_keychain_service,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None
```

- [ ] **Step 4: Install dependencies and verify GREEN**

Run: `backend/.venv/bin/pip install -r backend/requirements.txt && backend/.venv/bin/pytest backend/tests/test_fleet_database.py -q`

Expected: all database configuration tests PASS and no secret appears in output.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/requirements.txt backend/app/config.py backend/app/fleet/__init__.py backend/app/fleet/database.py backend/tests/test_fleet_database.py
git commit -m "feat: configure read-only flywheel access"
```

### Task 2: Agent catalog and fleet response models

**Files:**
- Create: `backend/app/fleet/catalog.yaml`
- Create: `backend/app/fleet/catalog.py`
- Create: `backend/app/fleet/models.py`
- Create: `backend/tests/test_fleet_catalog.py`

**Interfaces:**
- Consumes: runtime `bot_id` and optional historical Bot aliases.
- Produces: `AgentCatalog.profile(bot_id, fallback_name) -> AgentProfile`, `AgentCatalog.canonical_id(bot_id) -> str | None`, and Pydantic response types for the overview API.

- [ ] **Step 1: Write failing catalog tests**

```python
from app.fleet.catalog import AgentCatalog


def test_catalog_has_identity_for_all_current_bots():
    catalog = AgentCatalog.default()
    ids = {
        "feishu-default", "hr-bot", "marketing-prospecting-bot",
        "marketing-inbound-bot", "marketing-voice-bot", "fae-bot",
        "test-bot", "marketing-gtm-bot", "marketing-intelligence-bot",
    }
    assert {catalog.profile(item, item).id for item in ids} == ids


def test_catalog_maps_only_confirmed_legacy_aliases():
    catalog = AgentCatalog.default()
    assert catalog.canonical_id("marketing-bot") == "marketing-prospecting-bot"
    assert catalog.canonical_id("pc-bot") is None
    assert catalog.canonical_id("quality-bot") is None


def test_unknown_runtime_bot_gets_generic_profile():
    profile = AgentCatalog.default().profile("new-bot", "new-bot")
    assert profile.name == "new-bot"
    assert profile.glyph == "AI"
    assert profile.domain == "MetaBot 实例"
```

- [ ] **Step 2: Run and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_catalog.py -q`

Expected: FAIL because catalog files do not exist.

- [ ] **Step 3: Implement the catalog**

Create the complete YAML catalog:

```yaml
profiles:
  feishu-default:
    name: 飞书默认助手
    domain: 通用协作
    description: 承接飞书默认会话与日常协作任务。
    glyph: 飞
    accent: collaboration
  hr-bot:
    name: HR 助手
    domain: 人力资源
    description: 支持招聘、人事与员工服务流程。
    glyph: HR
    accent: people
  marketing-prospecting-bot:
    name: 营销拓客助手
    domain: Marketing · Prospecting
    description: 发现、筛选并跟进潜在客户线索。
    glyph: 拓
    accent: prospecting
  marketing-inbound-bot:
    name: 营销获客助手
    domain: Marketing · Inbound
    description: 承接入站线索、内容触达与客户咨询。
    glyph: 入
    accent: inbound
  marketing-voice-bot:
    name: 营销语音助手
    domain: Marketing · Voice
    description: 支持语音触达、通话沟通与结果整理。
    glyph: 声
    accent: voice
  fae-bot:
    name: FAE 技术助手
    domain: 技术支持
    description: 支持产品咨询、问题诊断与现场应用。
    glyph: FAE
    accent: support
  test-bot:
    name: 测试助手
    domain: 开发验证
    description: 用于接口联调、集成测试与运行验证。
    glyph: 测
    accent: testing
  marketing-gtm-bot:
    name: GTM 策略助手
    domain: Marketing · GTM
    description: 支持市场进入策略、节奏规划与执行协同。
    glyph: GTM
    accent: strategy
  marketing-intelligence-bot:
    name: 市场情报助手
    domain: Marketing · Intelligence
    description: 收集并整理市场动态与竞争情报。
    glyph: 情
    accent: intelligence
aliases:
  marketing-bot: marketing-prospecting-bot
unresolved_aliases:
  - pc-bot
  - quality-bot
```

Implement frozen `AgentProfile(id, name, domain, description, glyph, accent)` and an `AgentCatalog` that loads the YAML with `yaml.safe_load`. Unknown runtime IDs receive a generic profile; unresolved historical aliases return `None` so they do not create cards or inflate current totals.

- [ ] **Step 4: Define stable API models**

Create `backend/app/fleet/models.py` with:

```python
from typing import Literal
from pydantic import BaseModel

FleetState = Literal["active", "online", "degraded", "offline", "checking"]

class TrendPoint(BaseModel):
    date: str
    conversations: int

class FleetAgent(BaseModel):
    id: str
    name: str
    domain: str
    description: str
    glyph: str
    accent: str
    state: FleetState
    uptime_seconds: int | None
    total_conversations: int | None
    conversations_last_7d: int | None
    last_activity_at: str | None
    recent_summary: str | None

class FleetSummary(BaseModel):
    total_agents: int
    running_agents: int
    active_agents: int
    degraded_agents: int
    offline_agents: int
    checking_agents: int
    total_conversations: int | None
    conversations_last_7d: int | None
    conversations_previous_7d: int | None
    change_percent: float | None

class DataSourceStatus(BaseModel):
    healthy: bool
    checked_at: str | None
    stale: bool = False
    error: str | None = None

class FleetOverview(BaseModel):
    summary: FleetSummary
    trend: list[TrendPoint]
    agents: list[FleetAgent]
    runtime_source: DataSourceStatus
    usage_source: DataSourceStatus
```

- [ ] **Step 5: Verify and commit Task 2**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_catalog.py -q`

Expected: PASS.

```bash
git add backend/app/fleet/catalog.yaml backend/app/fleet/catalog.py backend/app/fleet/models.py backend/tests/test_fleet_catalog.py
git commit -m "feat: add fleet Agent catalog"
```

### Task 3: Real flywheel usage repository and cache

**Files:**
- Create: `backend/app/fleet/repository.py`
- Create: `backend/app/fleet/cache.py`
- Create: `backend/tests/test_fleet_repository.py`
- Create: `backend/tests/test_fleet_cache.py`

**Interfaces:**
- Produces: `UsageSnapshot(records, trend, checked_at)` where records are raw historical Bot IDs; `UsageCache.get() -> CachedUsage` preserves last successful data after failures.
- SQL truth: `flywheel_analytics.messages` joined to `flywheel_analytics.conversations`, grouped by distinct assistant `turn_id`.

- [ ] **Step 1: Write failing repository tests**

Use this complete injected connection fake so the test never needs a real database:

```python
class FakeConnect:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed_sql = []

    def __call__(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self

    def execute(self, sql, _params=None):
        self.executed_sql.append(sql)
        self.current = self.responses.pop(0)
        return self

    def fetchall(self):
        return self.current
```

Assert row mapping plus query invariants:

```python
from app.fleet.repository import PsycopgFlywheelRepository


def test_usage_query_counts_distinct_assistant_turns_and_uses_analytics():
    fake = FakeConnect([
        [{
            "bot_id": "hr-bot",
            "total_conversations": 14,
            "conversations_last_7d": 4,
            "conversations_previous_7d": 2,
            "last_activity_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
            "recent_summary": "入职需要哪些材料？",
        }],
        [{"date": date(2026, 7, 21), "conversations": 4}],
    ])
    repository = PsycopgFlywheelRepository("postgresql://unused", connect=fake)
    snapshot = repository.fetch_usage()
    sql = " ".join(fake.executed_sql)
    assert "flywheel_analytics.messages" in sql
    assert "flywheel_analytics.conversations" in sql
    assert "role = 'assistant'" in sql
    assert "count(distinct" in sql.lower()
    assert snapshot.records[0].bot_id == "hr-bot"


def test_repository_wraps_driver_errors_without_secret():
    def broken(*_args, **_kwargs):
        raise RuntimeError("postgresql://user:password@localhost/private")
    repository = PsycopgFlywheelRepository("postgresql://secret", connect=broken)
    with pytest.raises(FlywheelReadError, match="flywheel query failed") as error:
        repository.fetch_usage()
    assert "password" not in str(error.value)
```

The fake cursor returns rows for per-Bot totals, 14-day windows, last activity, recent user summary, and seven daily trend buckets.

- [ ] **Step 2: Run repository tests and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_repository.py -q`

Expected: FAIL because the repository does not exist.

- [ ] **Step 3: Implement repository SQL and mapping**

Define immutable internal records:

```python
@dataclass(frozen=True)
class UsageRecord:
    bot_id: str
    total_conversations: int
    conversations_last_7d: int
    conversations_previous_7d: int
    last_activity_at: datetime | None
    recent_summary: str | None

@dataclass(frozen=True)
class DailyUsage:
    date: date
    conversations: int

@dataclass(frozen=True)
class UsageSnapshot:
    records: tuple[UsageRecord, ...]
    trend: tuple[DailyUsage, ...]
    checked_at: datetime

@dataclass(frozen=True)
class CachedUsage:
    snapshot: UsageSnapshot | None
    source: DataSourceStatus
```

The first query must use this shape:

```sql
with answer_turns as (
  select c.bot_id, m.turn_id, min(m.occurred_at) as answered_at
  from flywheel_analytics.messages m
  join flywheel_analytics.conversations c on c.id = m.conversation_id
  where m.role = 'assistant'
  group by c.bot_id, m.turn_id
), latest_user as (
  select distinct on (c.bot_id) c.bot_id, m.content
  from flywheel_analytics.messages m
  join flywheel_analytics.conversations c on c.id = m.conversation_id
  where m.role = 'user'
  order by c.bot_id, m.occurred_at desc
)
select a.bot_id,
  count(distinct a.turn_id)::bigint as total_conversations,
  count(distinct a.turn_id) filter (
    where a.answered_at >= now() - interval '7 days')::bigint as conversations_last_7d,
  count(distinct a.turn_id) filter (
    where a.answered_at >= now() - interval '14 days'
      and a.answered_at < now() - interval '7 days')::bigint as conversations_previous_7d,
  max(a.answered_at) as last_activity_at,
  left(max(u.content), 120) as recent_summary
from answer_turns a
left join latest_user u using (bot_id)
group by a.bot_id
order by a.bot_id
```

Use this second query to group the same distinct answered turns by Asia/Shanghai calendar date for the last seven days:

```sql
with answer_turns as (
  select c.bot_id, m.turn_id, min(m.occurred_at) as answered_at
  from flywheel_analytics.messages m
  join flywheel_analytics.conversations c on c.id = m.conversation_id
  where m.role = 'assistant'
  group by c.bot_id, m.turn_id
)
select (answered_at at time zone 'Asia/Shanghai')::date as date,
  count(distinct (bot_id, turn_id))::bigint as conversations
from answer_turns
where (answered_at at time zone 'Asia/Shanghai')::date
  >= (now() at time zone 'Asia/Shanghai')::date - 6
group by date
order by date
```

Open psycopg with `connect_timeout=3`, `row_factory=dict_row`, and `options="-c statement_timeout=5000"`. Wrap all driver exceptions in `FlywheelReadError("flywheel query failed")`.

- [ ] **Step 4: Write failing stale-cache tests**

```python
@pytest.mark.asyncio
async def test_cache_reuses_data_for_60_seconds(repository, clock):
    cache = UsageCache(repository, ttl_seconds=60, clock=clock)
    first = await cache.get()
    second = await cache.get()
    assert first.source.healthy is True
    assert second.snapshot == first.snapshot
    assert repository.calls == 1

@pytest.mark.asyncio
async def test_cache_preserves_last_success_when_refresh_fails(repository, clock):
    cache = UsageCache(repository, ttl_seconds=60, clock=clock)
    first = await cache.get()
    clock.advance(61)
    repository.error = FlywheelReadError("flywheel query failed")
    stale = await cache.get()
    assert stale.snapshot == first.snapshot
    assert stale.source.healthy is False
    assert stale.source.stale is True
    assert stale.source.error == "usage_unavailable"
```

- [ ] **Step 5: Implement `UsageCache`**

Use an `asyncio.Lock`, `asyncio.to_thread(repository.fetch_usage)`, monotonic TTL calculation, and a last-success snapshot. If no success exists, return `snapshot=None` with a safe unavailable source. Never expose exception text.

- [ ] **Step 6: Verify and commit Task 3**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_repository.py backend/tests/test_fleet_cache.py -q`

Expected: PASS.

```bash
git add backend/app/fleet/repository.py backend/app/fleet/cache.py backend/tests/test_fleet_repository.py backend/tests/test_fleet_cache.py
git commit -m "feat: read real flywheel usage"
```

### Task 4: Merge runtime and usage into the fleet overview service

**Files:**
- Create: `backend/app/fleet/service.py`
- Create: `backend/tests/test_fleet_service.py`

**Interfaces:**
- Consumes: `ClusterMonitor.snapshot()`, `AgentCatalog`, and `UsageCache.get()`.
- Produces: `async FleetReadService.overview(now: datetime | None = None) -> FleetOverview`.

- [ ] **Step 1: Write failing service tests**

Use deterministic monitor and cache fakes:

```python
class StaticMonitor:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


class StaticCache:
    def __init__(self, records=(), healthy=True):
        self._value = CachedUsage(
            snapshot=UsageSnapshot(
                records=tuple(records),
                trend=(),
                checked_at=NOW,
            ) if healthy else None,
            source=DataSourceStatus(
                healthy=healthy,
                checked_at=NOW.isoformat() if healthy else None,
                error=None if healthy else "usage_unavailable",
            ),
        )

    async def get(self):
        return self._value


def make_service(*records, healthy=True):
    statuses = [
        InstanceStatus(
            id=bot_id,
            name=bot_id,
            pm2_name=f"metabot-{bot_id}",
            port=9100 + index,
            status="healthy",
            uptime_seconds=3600,
            checked_at=NOW.isoformat(),
        )
        for index, bot_id in enumerate(CURRENT_BOT_IDS)
    ]
    monitor = StaticMonitor(build_snapshot(
        statuses,
        SourceStatus(healthy=True, checked_at=NOW.isoformat()),
    ))
    return FleetReadService(
        monitor,
        AgentCatalog.default(),
        StaticCache(records, healthy=healthy),
        active_window_minutes=15,
    )
```

Cover these exact behaviors:

```python
@pytest.mark.asyncio
async def test_overview_merges_legacy_usage_into_current_agent():
    overview = await make_service(
        UsageRecord("marketing-bot", 14, 4, 2, RECENT, "legacy question"),
        UsageRecord("marketing-prospecting-bot", 44, 8, 3, LATEST, "current question"),
    ).overview(now=NOW)
    card = next(item for item in overview.agents if item.id == "marketing-prospecting-bot")
    assert card.total_conversations == 58
    assert card.conversations_last_7d == 12
    assert card.last_activity_at == LATEST.isoformat()

@pytest.mark.asyncio
async def test_recent_healthy_agent_is_active_and_idle_healthy_agent_is_online():
    overview = await make_service(
        UsageRecord("hr-bot", 14, 4, 2, NOW - timedelta(minutes=2), "recent"),
        UsageRecord("fae-bot", 4, 0, 1, NOW - timedelta(days=2), "old"),
    ).overview(now=NOW)
    assert state(overview, "hr-bot") == "active"
    assert state(overview, "fae-bot") == "online"

@pytest.mark.asyncio
async def test_missing_usage_is_unknown_not_zero():
    overview = await make_service(healthy=False).overview(now=NOW)
    assert overview.summary.total_conversations is None
    assert all(agent.total_conversations is None for agent in overview.agents)
    assert overview.summary.running_agents == 9
```

Also test 7-day trend fills missing dates with zero only when the usage source is healthy, unresolved legacy IDs are ignored, change percent is `None` when the previous period is zero, and offline runtime state wins over recent usage.

- [ ] **Step 2: Run and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_service.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement service aggregation**

Use catalog canonical IDs to merge raw records. Determine state in this order: `offline`, `degraded`, `checking`, then `active` when `last_activity_at >= now - active_window`, otherwise `online`. Sum only current runtime Agent IDs. Sort cards by offline/degraded first, then total conversations descending, then name.

Calculate:

```python
change_percent = (
    round((last_7d - previous_7d) / previous_7d * 100, 1)
    if previous_7d > 0
    else None
)
```

When usage is unavailable, keep all usage fields and trend unavailable while preserving runtime counts.

- [ ] **Step 4: Verify and commit Task 4**

Run: `backend/.venv/bin/pytest backend/tests/test_fleet_service.py -q`

Expected: PASS.

```bash
git add backend/app/fleet/service.py backend/tests/test_fleet_service.py
git commit -m "feat: aggregate fleet overview"
```

### Task 5: Expose and wire `/api/fleet/overview`

**Files:**
- Create: `backend/app/fleet/routes.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**
- Produces: `GET /api/fleet/overview` returning the `FleetOverview` model.
- App factory accepts optional injected `fleet_service` for deterministic tests; production constructs repository/cache/service from Config and Keychain.

- [ ] **Step 1: Write failing API tests**

```python
def test_fleet_overview_returns_product_fields_without_technical_details(client):
    response = client.get("/api/fleet/overview")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"summary", "trend", "agents", "runtime_source", "usage_source"}
    assert body["agents"][0]["name"] == "HR 助手"
    serialized = response.text
    for forbidden in ("pm2_name", "port", "latency_ms", "health_url", "workdir"):
        assert forbidden not in serialized


def test_fleet_route_is_async():
    assert inspect.iscoroutinefunction(fleet_overview)
```

Update the test app factory to inject a fake `FleetReadService` rather than access Keychain or PostgreSQL.

- [ ] **Step 2: Run and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_api.py backend/tests/test_main.py -q`

Expected: FAIL with a 404 for `/api/fleet/overview`.

- [ ] **Step 3: Implement route and app wiring**

Create an async router at prefix `/api/fleet`. Store `fleet_service` on `app.state`, include the router before static mounting, and construct the production service as follows:

```python
database_url = resolve_flywheel_database_url(config)
repository = (
    PsycopgFlywheelRepository(database_url)
    if database_url
    else UnavailableFlywheelRepository()
)
usage_cache = UsageCache(repository, ttl_seconds=config.usage_cache_seconds)
fleet_service = FleetReadService(
    cluster_monitor,
    AgentCatalog.default(),
    usage_cache,
    active_window_minutes=config.active_window_minutes,
)
```

Do not resolve Keychain credentials when an injected service is supplied.

- [ ] **Step 4: Run backend regression suite**

Run: `backend/.venv/bin/pytest backend/tests -q`

Expected: all backend tests PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add backend/app/fleet/routes.py backend/app/main.py backend/tests/test_api.py backend/tests/test_main.py
git commit -m "feat: expose fleet overview API"
```

### Task 6: Replace the technical homepage with the AI team cockpit

**Files:**
- Modify: `webui/src/types.ts`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/api.test.ts`
- Create: `webui/src/fleet.ts`
- Create: `webui/src/fleet.test.ts`
- Create: `webui/src/FleetAgentCard.tsx`
- Create: `webui/src/FleetAgentCard.test.tsx`
- Create: `webui/src/UsageTrend.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/styles.css`
- Delete: `webui/src/AgentCard.tsx`
- Delete: `webui/src/AgentCard.test.tsx`

**Interfaces:**
- Consumes: `FleetOverview` from `/api/fleet/overview` every 10 seconds.
- Produces: approved AI team cockpit with usage-first summary, seven-day trend, active ranking, and rich identity cards; no technical port/latency surface.

- [ ] **Step 1: Write failing TypeScript API and formatting tests**

Define API types matching backend property names and tests:

```ts
expect(await fetchFleetOverview()).toEqual(overviewFixture);
expect(formatCount(8642)).toBe("8,642");
expect(formatCount(null)).toBe("—");
expect(formatRelativeActivity("2026-07-21T01:00:00Z", NOW)).toBe("2分钟前");
expect(formatChange(18)).toBe("较上期 +18%");
expect(formatChange(null)).toBe("暂无对比");
```

Verify polling retains the last successful overview and marks it degraded after API failure, using the existing recursive-timeout pattern rather than overlapping intervals.

- [ ] **Step 2: Run and verify RED**

Run: `npm test -- api.test.ts fleet.test.ts`

Expected: FAIL because the fleet API and helpers do not exist.

- [ ] **Step 3: Implement types, fetcher, state, and formatters**

Add `fetchFleetOverview(signal?)`, `FleetDashboardState`, `applyFleetSuccess`, `applyFleetFailure`, count/change/relative-time helpers, and keep the 10-second recursive polling behavior.

- [ ] **Step 4: Write failing Agent card rendering tests**

Using `renderToStaticMarkup`, assert that a card contains identity, status, cumulative conversations, seven-day conversations, uptime, recent activity, and recent summary. Assert it does not contain `端口`, `延迟`, `pm2`, `<button`, or an Agent entry `href`.

- [ ] **Step 5: Run and verify card test RED**

Run: `npm test -- FleetAgentCard.test.tsx`

Expected: FAIL because `FleetAgentCard` does not exist.

- [ ] **Step 6: Implement the approved cockpit**

Structure `App.tsx` as:

```tsx
<Topbar />
<main className="page">
  <FleetHero overview={overview} />
  <SummaryStrip summary={overview.summary} />
  <section className="insight-grid">
    <UsageTrend points={overview.trend} available={overview.usage_source.healthy || overview.usage_source.stale} />
    <ActiveRanking agents={overview.agents} />
  </section>
  <section className="fleet-section">
    <FleetAgentGrid agents={overview.agents} />
  </section>
</main>
```

Follow the approved visual hierarchy from the companion: four summary cards, a seven-day CSS bar chart, top-three active ranking, and a three-column Agent card grid. Each card emphasizes cumulative conversations, then seven-day usage, uptime, and recent activity. Use explicit “活跃/在线/异常/离线/检测中” labels. When usage is unavailable, render em dashes and a scoped banner while runtime state remains visible.

- [ ] **Step 7: Verify frontend regression and production build**

Run: `npm test && npm run build && npm audit --omit=dev`

Expected: all tests PASS, Vite build succeeds, and audit reports zero production vulnerabilities.

- [ ] **Step 8: Commit Task 6**

```bash
git add webui/src webui/package-lock.json
git commit -m "feat: launch AI team cockpit"
```

### Task 7: Live data and deployment acceptance

**Files:**
- Generated and ignored: `webui/dist/`
- No MetaBot files are modified.

**Interfaces:**
- Consumes: production build, live Keychain analyst credential, current runtime contract, and live flywheel PostgreSQL.
- Produces: live local cockpit at `http://127.0.0.1:8000/`.

- [ ] **Step 1: Build with the final committed source**

Run: `cd webui && npm test && npm run build && npm audit --omit=dev`

Expected: tests, build, and audit pass.

- [ ] **Step 2: Run the full backend suite**

Run: `backend/.venv/bin/pytest backend/tests -q`

Expected: all tests pass.

- [ ] **Step 3: Verify the live overview API**

Run: `curl --noproxy '*' -fsS http://127.0.0.1:8000/api/fleet/overview | jq '{summary, runtime_source, usage_source, agents: [.agents[] | {id,state,total_conversations,uptime_seconds}]}'`

Expected:

- 9 current Agent cards;
- runtime source healthy;
- usage source healthy;
- current answered-turn totals derived from real data;
- no `port`, `latency_ms`, `pm2_name`, `health_url`, or `workdir` fields.

- [ ] **Step 4: Verify the live page uses the final hashed assets**

Run: fetch `/`, then fetch its JS asset and confirm the strings `AI 团队总览`, `累计对话`, `近 7 天对话趋势`, and `全部 Agent` are present.

Expected: HTTP 200 and all product strings present.

- [ ] **Step 5: Confirm service and MetaBot safety**

Check LaunchAgent state and direct health for ports 9100–9108 before and after Platform deployment. Do not restart MetaBots.

Expected: Platform is running, all available MetaBot PIDs remain unchanged, and the cockpit reflects the live state without controlling it.

- [ ] **Step 6: Independent review and final commit audit**

Review the diff from the plan commit through HEAD for Critical/Important issues, fix any findings with new failing tests, rerun Tasks 7.1–7.5, and confirm unrelated working-tree changes remain untouched.
