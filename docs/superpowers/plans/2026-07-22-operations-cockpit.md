# Operations Cockpit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an evidence-backed Daily Brief, durable operational event ledger, Activity History, and per-Agent recent activity without weakening the Platform's read-only Agent boundary.

**Architecture:** A new `operations` backend package persists derived events and rule state in a dedicated SQLite database. Deterministic rule groups consume existing Fleet, synchronization, lifecycle, Session, turn, and Trace facts; a scheduler evaluates them independently, while read-only APIs serve the Overview, Activity History, and Agent pages. Existing Fleet and observability APIs continue to work if Operations initialization or evaluation fails.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, standard-library `sqlite3`, psycopg 3 read-only source queries, React 19, TypeScript, Vitest, CSS, macOS LaunchAgent.

## Global Constraints

- Default Cockpit, Fleet totals, Activity History, Sessions, and Flywheel contain the nine Business Agents only.
- Test and Feishu Default remain monitored System Agents and are available only through explicit Agent or Session selection.
- Platform writes only its own derived SQLite state; Agent systems and source Flywheel PostgreSQL remain read-only.
- No LLM-generated summary, synthetic Agent score, universal Feedback metric, notification, acknowledgement workflow, remediation, restart, deployment, editing, access control, cost, port, CPU, memory, or latency dashboard.
- A runtime condition requires two consecutive normalized observations to open or clear.
- Remote synchronization failure is immediately actionable; staleness begins after 36 hours without a successful synchronization.
- Usage is stored in hourly `Asia/Shanghai` buckets and summarized over a rolling 24-hour interval.
- Unsupported telemetry never becomes an incident.
- Operations failure cannot break Fleet Overview, Agents, Sessions, Flywheel, or health polling.
- The last successful Brief remains readable with its evaluation timestamp; partial or stale evaluation cannot claim `No critical issues`.
- Preserve source-language Agent names, descriptions, questions, answers, and evidence. Do not translate existing content.

---

## File Structure

### Backend files to create

- `backend/app/operations/__init__.py` — package exports.
- `backend/app/operations/models.py` — event, Brief, filter, source observation, and run-health contracts.
- `backend/app/operations/repository.py` — SQLite schema, migrations, transactions, event queries, rule state, and run health.
- `backend/app/operations/rules.py` — deterministic runtime, synchronization, usage, lifecycle, and execution rules.
- `backend/app/operations/source.py` — read-only PostgreSQL queries for incremental usage and execution facts.
- `backend/app/operations/service.py` — Brief assembly and Activity pagination.
- `backend/app/operations/scheduler.py` — isolated rule-group scheduling.
- `backend/app/operations/routes.py` — `/api/operations/brief` and `/api/operations/events`.
- `backend/tests/test_operations_repository.py`
- `backend/tests/test_operations_rules.py`
- `backend/tests/test_operations_source.py`
- `backend/tests/test_operations_service.py`
- `backend/tests/test_operations_api.py`
- `backend/tests/test_operations_scheduler.py`

### Backend files to modify

- `backend/app/config.py` — configurable SQLite path and rule intervals.
- `backend/app/main.py` — initialize Operations defensively, run scheduler, include routes.
- `backend/app/observability/service.py` — expose synchronization facts to the scheduler without duplicating queries.
- `backend/tests/test_config.py`
- `backend/tests/test_main.py`
- `.gitignore` — exclude the runtime database and SQLite sidecar files.
- `deploy/com.orbbec.ai-agent-platform.plist` — explicit production database path.

### Frontend files to create

- `webui/src/components/DailyBrief.tsx` — two-column Attention and Last 24 Hours module.
- `webui/src/components/OperationalEventItem.tsx` — shared event presentation and target link.
- `webui/src/pages/ActivityPage.tsx` — complete filtered event history.
- `webui/src/operations.ts` — formatting, grouping, freshness, and severity helpers.
- `webui/src/operations.test.ts`
- `webui/src/operationsUi.test.tsx`

### Frontend files to modify

- `webui/src/types.ts` — Operations API contracts.
- `webui/src/api.ts` — Brief and Activity clients.
- `webui/src/pages/OverviewPage.tsx` — place Daily Brief above current insights.
- `webui/src/pages/AgentDetailPage.tsx` — add Recent Activity.
- `webui/src/router.ts` — `/activity` route without primary-navigation ownership.
- `webui/src/App.tsx` — render Activity page.
- `webui/src/styles.css` — Cockpit, event, filter, severity, stale, and responsive styles.
- `webui/src/api.test.ts`
- `webui/src/dashboard.test.ts`
- `webui/src/router.test.ts`
- `webui/src/pages/AgentDetailPage.test.tsx`
- `webui/src/styles.test.ts`
- `README.md` — describe the Operations Cockpit and new APIs.

---

### Task 1: SQLite Event Ledger and Contracts

**Files:**
- Create: `backend/app/operations/__init__.py`
- Create: `backend/app/operations/models.py`
- Create: `backend/app/operations/repository.py`
- Create: `backend/tests/test_operations_repository.py`
- Modify: `backend/app/config.py`
- Modify: `backend/tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `OperationalEvent`, `NewOperationalEvent`, `EventFilters`, `RuleState`, `RunHealth`, `OperationsRepository`.
- Produces: `OperationsRepository.migrate()`, `schema_version()`, `upsert_active()`, `record_historical()`, `resolve_active()`, `expire_active_occurrences()`, `get_rule_state()`, `put_rule_state()`, `record_run()`, `latest_run()`, `list_events()`, and `list_active_attention()`.
- Produces config: `operations_database_path`, `operations_usage_interval_seconds`, `operations_execution_interval_seconds`, and `operations_lifecycle_interval_seconds`.

- [ ] **Step 1: Write failing repository and config tests**

Add tests that create a repository against `tmp_path / "operations.db"` and assert:

```python
def migrated_repository(tmp_path) -> OperationsRepository:
    repo = OperationsRepository(str(tmp_path / "operations.db"))
    repo.migrate()
    return repo


def test_migrate_creates_versioned_operations_schema(tmp_path):
    repo = OperationsRepository(str(tmp_path / "operations.db"))
    repo.migrate()

    assert repo.schema_version() == 1


def test_upsert_active_is_idempotent_and_resolve_is_transactional(tmp_path):
    repo = migrated_repository(tmp_path)
    event = NewOperationalEvent(
        agent_id="ai-fae-agent",
        agent_visibility="business",
        event_type="runtime_offline",
        event_family="runtime",
        severity="critical",
        title="AI FAE Agent is offline",
        summary="Two consecutive runtime observations reported offline.",
        source_kind="fae",
        occurred_at=NOW,
        facts={"state": "offline", "observations": 2},
        target_kind="agent",
        target_id="ai-fae-agent",
        target_path="/agents/ai-fae-agent",
        fingerprint="runtime:ai-fae-agent:unavailable",
    )

    first = repo.upsert_active(event)
    second = repo.upsert_active(event.model_copy(update={"facts": {"state": "offline", "observations": 3}}))
    recovery = repo.resolve_active(
        fingerprint=event.fingerprint,
        resolved_at=NOW + timedelta(minutes=1),
        recovery_title="AI FAE Agent recovered",
        recovery_summary="Runtime returned to online.",
        recovery_facts={"state": "online"},
    )

    assert first.event_id == second.event_id
    assert repo.list_active_attention("business") == ()
    assert recovery is not None
    assert [item.event_type for item in repo.list_events(EventFilters(), 20, 0).items] == [
        "runtime_recovered", "runtime_offline",
    ]


def test_config_has_stable_operations_defaults(monkeypatch):
    monkeypatch.delenv("PLATFORM_OPERATIONS_DATABASE_PATH", raising=False)
    config = load_config()
    assert config.operations_database_path == "../data/platform-operations.db"
    assert config.operations_usage_interval_seconds == 300
    assert config.operations_execution_interval_seconds == 300
    assert config.operations_lifecycle_interval_seconds == 600
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_repository.py tests/test_config.py -q
```

Expected: collection fails because `app.operations` and the new Config fields do not exist.

- [ ] **Step 3: Define exact event contracts**

Create Pydantic contracts with these exact fields and literals:

```python
EventSeverity = Literal["info", "attention", "critical"]
EventStatus = Literal["active", "resolved", "historical"]
EventFamily = Literal["runtime", "data", "execution", "usage", "lifecycle", "recovery"]
AgentVisibility = Literal["business", "system"]


class NewOperationalEvent(BaseModel):
    agent_id: str | None
    agent_visibility: AgentVisibility
    event_type: str
    event_family: EventFamily
    severity: EventSeverity
    title: str
    summary: str
    source_kind: str
    occurred_at: datetime
    facts: dict = Field(default_factory=dict)
    target_kind: str | None = None
    target_id: str | None = None
    target_path: str | None = None
    fingerprint: str


class OperationalEvent(NewOperationalEvent):
    event_id: str
    status: EventStatus
    first_observed_at: datetime
    last_observed_at: datetime
    resolved_at: datetime | None = None


class RuleState(BaseModel):
    rule_key: str
    value: dict
    updated_at: datetime


class RunHealth(BaseModel):
    run_name: str
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    cursor: dict = Field(default_factory=dict)
    error_summary: str | None = None


class EventFilters(BaseModel):
    agent_id: str | None = None
    event_type: str | None = None
    severity: EventSeverity | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
```

Reuse `Page[OperationalEvent]` from `app.observability.models` for paginated results.

- [ ] **Step 4: Implement the schema and transactions**

Use `sqlite3.connect(path, timeout=5)` with `row_factory=sqlite3.Row`, `PRAGMA journal_mode=WAL`, foreign keys enabled, UTC ISO timestamps, and `json.dumps(value, ensure_ascii=False, sort_keys=True)` for every JSON column.

Create migration version 1 with:

```sql
create table if not exists operations_schema_version (
  version integer primary key,
  applied_at text not null
);
create table if not exists operational_events (
  event_id text primary key,
  agent_id text,
  agent_visibility text not null check (agent_visibility in ('business','system')),
  event_type text not null,
  event_family text not null,
  severity text not null check (severity in ('info','attention','critical')),
  status text not null check (status in ('active','resolved','historical')),
  title text not null,
  summary text not null,
  source_kind text not null,
  occurred_at text not null,
  first_observed_at text not null,
  last_observed_at text not null,
  resolved_at text,
  facts_json text not null,
  target_kind text,
  target_id text,
  target_path text,
  fingerprint text not null
);
create unique index if not exists uq_operational_active_fingerprint
  on operational_events(fingerprint) where status='active';
create index if not exists ix_operational_events_time
  on operational_events(occurred_at desc, event_id);
create index if not exists ix_operational_events_agent_time
  on operational_events(agent_id, occurred_at desc);
create table if not exists operational_rule_state (
  rule_key text primary key,
  value_json text not null,
  updated_at text not null
);
create table if not exists operational_runs (
  run_id text primary key,
  run_name text not null,
  status text not null,
  started_at text not null,
  finished_at text,
  cursor_json text not null,
  error_summary text
);
create index if not exists ix_operational_runs_name_time
  on operational_runs(run_name, started_at desc);
```

`upsert_active()` must execute one `BEGIN IMMEDIATE` transaction: select the active fingerprint, update its facts and `last_observed_at` if present, otherwise insert a UUID event. `resolve_active()` must update the active row and insert one `runtime_recovered`, `sync_recovered`, or `data_access_recovered` historical event in the same transaction. Return `None` when no active fingerprint exists.

`record_historical()` must select by `(fingerprint, occurred_at)` before insert and return the existing row during replay. `expire_active_occurrences(event_family, before)` marks matching occurrence events `historical` without inserting Recovery Events.

- [ ] **Step 5: Add Config and Git exclusions**

Add the four Config fields and load defaults:

```python
operations_database_path=os.getenv(
    "PLATFORM_OPERATIONS_DATABASE_PATH", "../data/platform-operations.db"
),
operations_usage_interval_seconds=float(
    os.getenv("PLATFORM_OPERATIONS_USAGE_INTERVAL", "300")
),
operations_execution_interval_seconds=float(
    os.getenv("PLATFORM_OPERATIONS_EXECUTION_INTERVAL", "300")
),
operations_lifecycle_interval_seconds=float(
    os.getenv("PLATFORM_OPERATIONS_LIFECYCLE_INTERVAL", "600")
),
```

Add these patterns to `.gitignore`:

```gitignore
data/platform-operations.db
data/platform-operations.db-shm
data/platform-operations.db-wal
```

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_repository.py tests/test_config.py -q
```

Expected: all selected tests pass.

Commit only Task 1 files:

```bash
git add .gitignore backend/app/config.py backend/app/operations backend/tests/test_config.py backend/tests/test_operations_repository.py
git commit -m "feat: add operational event ledger"
```

---

### Task 2: Runtime and Synchronization Rules

**Files:**
- Create: `backend/app/operations/rules.py`
- Create: `backend/tests/test_operations_rules.py`

**Interfaces:**
- Consumes: `OperationsRepository`, `NewOperationalEvent`.
- Produces: `RuntimeObservation`, `SyncObservation`, `DataAccessObservation`, and `OperationsRuleEngine`.
- Produces: `evaluate_runtime(observations, now)`, `evaluate_sync(observations, now)`, and `evaluate_data_access(observations, now)`.

- [ ] **Step 1: Write failing transition tests**

Cover the complete state machines:

```python
def make_engine(tmp_path):
    repo = OperationsRepository(str(tmp_path / "operations.db"))
    repo.migrate()
    return OperationsRuleEngine(repo), repo


def runtime(agent_id: str, visibility: str, state: str, observed_at: datetime):
    return RuntimeObservation(
        agent_id=agent_id,
        agent_name=agent_id,
        agent_visibility=visibility,
        source_kind="metabot",
        state=state,
        observed_at=observed_at,
    )


def event_types(repo: OperationsRepository) -> list[str]:
    return [item.event_type for item in repo.list_events(EventFilters(), 100, 0).items]


def test_runtime_requires_two_observations_to_open_and_clear(tmp_path):
    engine, repo = make_engine(tmp_path)
    offline = runtime("hr-bot", "business", "offline", NOW)
    online = runtime("hr-bot", "business", "online", NOW + timedelta(seconds=10))

    engine.evaluate_runtime([offline], NOW)
    assert repo.list_active_attention("business") == ()
    engine.evaluate_runtime([offline], NOW + timedelta(seconds=10))
    assert event_types(repo) == ["runtime_offline"]
    engine.evaluate_runtime([online], NOW + timedelta(seconds=20))
    assert event_types(repo) == ["runtime_offline"]
    engine.evaluate_runtime([online], NOW + timedelta(seconds=30))
    assert event_types(repo) == ["runtime_recovered", "runtime_offline"]


def test_sync_failure_and_staleness_share_one_active_event(tmp_path):
    engine, repo = make_engine(tmp_path)
    failed = SyncObservation(
        source_kind="fae", status="failed", completed_at=NOW,
        observed_at=NOW, last_success_at=NOW - timedelta(hours=35),
    )
    stale = failed.model_copy(update={
        "observed_at": NOW + timedelta(hours=2),
        "last_success_at": NOW - timedelta(hours=37),
    })

    engine.evaluate_sync([failed], NOW)
    engine.evaluate_sync([stale], NOW + timedelta(hours=2))

    active = repo.list_active_attention("business")
    assert len(active) == 1
    assert active[0].event_type == "remote_sync_unavailable"
    assert active[0].facts["stale"] is True


def test_system_runtime_event_is_not_business_attention(tmp_path):
    engine, repo = make_engine(tmp_path)
    observation = runtime("test-bot", "system", "offline", NOW)
    engine.evaluate_runtime([observation], NOW)
    engine.evaluate_runtime([observation], NOW + timedelta(seconds=10))

    assert repo.list_active_attention("business") == ()
    assert len(repo.list_active_attention("system")) == 1


def test_business_data_failure_opens_one_recoverable_attention(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_data_access([
        DataAccessObservation(source_name="flywheel", available=False, observed_at=NOW)
    ], NOW)
    assert event_types(repo) == ["business_data_unavailable"]
    engine.evaluate_data_access([
        DataAccessObservation(source_name="flywheel", available=True, observed_at=NOW + timedelta(minutes=1))
    ], NOW + timedelta(minutes=1))
    assert event_types(repo) == ["data_access_recovered", "business_data_unavailable"]
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_rules.py -q
```

Expected: collection fails because `app.operations.rules` does not exist.

- [ ] **Step 3: Implement observation contracts and deterministic rules**

Define:

```python
class RuntimeObservation(BaseModel):
    agent_id: str
    agent_name: str
    agent_visibility: AgentVisibility
    source_kind: str
    state: Literal["active", "online", "degraded", "offline", "checking", "unknown"]
    observed_at: datetime


class SyncObservation(BaseModel):
    source_kind: Literal["fae", "admin"]
    status: Literal["running", "succeeded", "failed"]
    completed_at: datetime | None
    observed_at: datetime
    last_success_at: datetime | None


class DataAccessObservation(BaseModel):
    source_name: str
    available: bool
    observed_at: datetime
```

Store runtime debounce state under `runtime:{agent_id}`:

```json
{"candidate":"offline","count":2,"stable":"offline"}
```

Normalize `active` and `online` to `healthy`, preserve `degraded` and `offline`, and treat `checking` or `unknown` as insufficient evidence. Open `runtime_offline` as `critical`; open `runtime_degraded` as `attention`. Resolve after two consecutive `healthy` observations.

Store synchronization state under `sync:{source_kind}`. Use fingerprint `sync:{source_kind}:unavailable` for both failure and staleness. A successful observation resolves the condition and creates one `sync_recovered` event. The Agent mapping is `fae → ai-fae-agent`, `admin → ai-admin-agent`, both Business visibility.

Store local data access under `data:{source_name}`. An unavailable observation immediately opens one fleet-level Business `business_data_unavailable` Attention item; a later available observation resolves it and inserts one `data_access_recovered` event. The event has `agent_id=None` and links to `/sessions` only when the source is the Flywheel read model.

- [ ] **Step 4: Run focused tests and commit**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_rules.py tests/test_operations_repository.py -q
```

Expected: all selected tests pass.

```bash
git add backend/app/operations/rules.py backend/tests/test_operations_rules.py
git commit -m "feat: detect operational state transitions"
```

---

### Task 3: Usage, Lifecycle, and Execution Signals

**Files:**
- Create: `backend/app/operations/source.py`
- Create: `backend/tests/test_operations_source.py`
- Modify: `backend/app/operations/models.py`
- Modify: `backend/app/operations/repository.py`
- Modify: `backend/app/operations/rules.py`
- Modify: `backend/tests/test_operations_repository.py`
- Modify: `backend/tests/test_operations_rules.py`

**Interfaces:**
- Consumes: local Flywheel PostgreSQL URL and `OperationsRepository` rule cursors.
- Produces: `UsageOccurrence`, `UsageBatch`, `UsageObservation`, `LifecycleObservation`, `ExecutionObservation`.
- Produces: source-filtered local/remote usage batch and execution reads.
- Produces: `evaluate_usage()`, `evaluate_lifecycle()`, and `evaluate_execution()`.
- Produces migration version 2 with `operational_usage_occurrences` and an atomic exact-usage unit of work.

- [ ] **Step 1: Write failing source-query and rule tests**

Tests must prove:

```python
class RecordingCursor:
    def __init__(self, rows):
        self.rows = rows
        self.statements: list[str] = []

    def execute(self, statement, _params):
        self.statements.append(statement)
        return self

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def fake_source(*, usage_rows=(), execution_rows=()):
    rows = list(usage_rows or execution_rows)
    cursor = RecordingCursor(rows)
    source = PsycopgOperationsSource(
        "postgresql://unused",
        connect=lambda *_args, **_kwargs: RecordingConnection(cursor),
    )
    return source, cursor


def usage(agent_id: str, cumulative: int, bucket_start: datetime, turn_keys: tuple[str, ...]):
    source_kind = "fae" if agent_id == "ai-fae-agent" else "metabot"
    return UsageObservation(
        agent_id=agent_id,
        agent_name=agent_id,
        agent_visibility="business",
        source_kind=source_kind,
        bucket_start=bucket_start,
        conversations=len(turn_keys),
        cumulative_conversations=cumulative,
        occurrences=tuple(
            UsageOccurrence(
                turn_key=turn_key,
                agent_id=agent_id,
                source_kind=source_kind,
                occurred_at=bucket_start,
            )
            for turn_key in turn_keys
        ),
    )


def execution(agent_id: str, signal_type: str, turn_key: str, occurred_at: datetime):
    session_key = "fae:session-2" if turn_key.endswith("2") else "fae:session-1"
    return ExecutionObservation(
        turn_key=turn_key,
        session_key=session_key,
        agent_id=agent_id,
        agent_name=agent_id,
        agent_visibility="business",
        source_kind="fae",
        signal_type=signal_type,
        occurred_at=occurred_at,
    )


def test_usage_query_returns_answered_turns_only():
    source, cursor = fake_source(usage_rows=[{
        "turn_key": "metabot:turn-1", "agent_id": "hr-bot",
        "source_kind": "metabot", "created_at": NOW,
    }])
    assert source.fetch_local_usage(NOW - timedelta(minutes=5), NOW)[0].turn_key == "metabot:turn-1"
    assert "nullif(btrim(t.answer), '') is not null" in cursor.statements[0]
    assert "t.source_kind='metabot'" in cursor.statements[0]
    assert "t.created_at > %s" in cursor.statements[0]


def test_execution_query_emits_only_supported_explicit_signals():
    source, _ = fake_source(execution_rows=[{
        "turn_key": "fae:turn-1", "session_key": "fae:session-1",
        "agent_id": "ai-fae-agent", "source_kind": "fae",
        "created_at": NOW, "signal_type": "fallback",
    }])
    signals = source.fetch_remote_execution("fae")
    assert [item.signal_type for item in signals] == ["fallback"]


def test_initial_baseline_backfills_usage_but_not_old_milestones(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_usage(
        [usage("ai-fae-agent", 237, NOW - timedelta(hours=1), ("fae:turn-1",))],
        NOW,
        initializing=True,
    )
    assert event_types(repo) == ["new_conversations"]
    assert repo.usage_occurrence_count() == 1
    assert repo.get_rule_state("milestone:ai-fae-agent").value["reached"] == 100


def test_usage_is_bucketed_and_milestone_is_emitted_once(tmp_path):
    engine, repo = make_engine(tmp_path)
    engine.evaluate_usage([usage("hr-bot", 99, NOW, ())], NOW, initializing=True)
    answered = usage("hr-bot", 100, NOW + timedelta(minutes=1), ("turn-100",))
    engine.evaluate_usage([answered], NOW, initializing=False)
    engine.evaluate_usage([answered], NOW + timedelta(minutes=1), initializing=False)
    assert event_types(repo).count("conversation_milestone") == 1


def test_execution_events_group_by_agent_signal_and_hour(tmp_path):
    engine, repo = make_engine(tmp_path)
    observations = [
        execution("ai-fae-agent", "fallback", "fae:turn-1", NOW),
        execution("ai-fae-agent", "fallback", "fae:turn-2", NOW + timedelta(minutes=2)),
    ]
    engine.evaluate_execution(observations, NOW + timedelta(minutes=2))
    events = repo.list_active_attention("business")
    assert len(events) == 1
    assert events[0].facts["count"] == 2
    assert events[0].target_path == "/sessions/fae%3Asession-2"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_source.py tests/test_operations_rules.py -q
```

Expected: failures for missing source repository and rule methods.

- [ ] **Step 3: Implement read-only source queries**

`fetch_local_usage()` queries MetaBot rows by true creation time:

```sql
select t.turn_key, t.agent_id, t.source_kind, t.created_at
from platform_read.turns t
where t.source_kind='metabot'
  and t.created_at > %s and t.created_at <= %s
  and nullif(btrim(t.answer), '') is not null
order by t.created_at, t.turn_key
```

`fetch_remote_usage(source_kind, created_after=None, created_through=None)`
filters one FAE or ADMIN snapshot by `source_kind`. Initialization supplies both
true `created_at` bounds for the preceding 24 hours; later generations omit the
bounds and scan the complete retained source snapshot.

Local and remote execution methods use the same split filters with `union all`
to return normalized signals. Each execution branch uses either the local
filter:

```sql
t.source_kind='metabot' and t.created_at > %s and t.created_at <= %s
```

or the remote snapshot filter:

```sql
t.source_kind=%s
```

Remote initialization adds `t.created_at > %s and t.created_at <= %s`; later
successful generations do not. All queries remain read-only and continue to
select `t.created_at` as the observation occurrence time.

Deduplicate identical `(turn_key, signal_type)` rows in Python before returning observations. Resolve Agent visibility through `AgentCatalog`; discard unknown Agent IDs rather than reporting them under Business.

- [ ] **Step 4: Implement usage, lifecycle, and execution rules**

Define exact observation fields:

```python
class UsageOccurrence(BaseModel):
    turn_key: str
    agent_id: str
    source_kind: str
    occurred_at: datetime


class UsageBatch(BaseModel):
    occurrences: tuple[UsageOccurrence, ...]
    cumulative_totals: dict[str, int]


class UsageObservation(BaseModel):
    agent_id: str
    agent_name: str
    agent_visibility: AgentVisibility
    source_kind: str
    bucket_start: datetime
    conversations: int
    cumulative_conversations: int
    occurrences: tuple[UsageOccurrence, ...]


class LifecycleObservation(BaseModel):
    agent_id: str
    agent_name: str
    agent_visibility: AgentVisibility
    source_kind: str
    live_since: datetime | None
    last_updated_at: datetime | None
    observed_at: datetime


class ExecutionObservation(BaseModel):
    turn_key: str
    session_key: str
    agent_id: str
    agent_name: str
    agent_visibility: AgentVisibility
    source_kind: str
    signal_type: Literal["tool_error", "fallback", "empty_answer", "incomplete"]
    occurred_at: datetime
```

Source ingestion is split. Local MetaBot queries use true `created_at` with a one-hour overlap before `local_through`. Remote FAE/ADMIN queries treat each new successful synchronization `completed_at` as a snapshot generation. Initialization scans only the generation's rows from the preceding 24 hours and seeds that generation as processed. A later successful generation performs one read-only full scan of that source's retained snapshot (currently 90 days and small); repeated polls of the same generation do not rescan, and a failed synchronization does not advance generation state. Recovery success therefore triggers the next generation scan. The per-group cursor containing `local_through` and `remote_generations` advances only after the complete rule application succeeds.

Each local or remote usage read is a typed `UsageBatch`. The source executes the exact occurrence query and the per-Agent cumulative answered-Turn total query for the same source filter on one PostgreSQL connection in one read-only, repeatable-read transaction. Both queries count distinct stable Turn keys, preventing duplicate joined run rows from inflating activity or cumulative totals. The scheduler groups those occurrences by Agent and local hour, then combines each exact typed occurrence tuple with its batch's source-aligned total to create `UsageObservation`. Fleet Overview is used only for Agent name and visibility metadata; `FleetAgent.total_conversations` and UsageCache are never usage-rule inputs. A missing or failed total fails the group, so neither `local_through` nor a remote generation can advance.

Migration version 2 adds `operational_usage_occurrences(occurrence_key primary key, agent_id, bucket_start, occurred_at, processed_at)`. It stores source Turn identifiers and timestamps only, never question or answer payloads. One repository transaction applies the complete observation batch and closed-bucket expiration: it inserts unseen keys, ignores replayed keys, recomputes all affected bucket counts from the ledger, creates/updates/finalizes hourly Events, and persists bucket, cumulative, and milestone state plus milestone Events. A failure in any later observation rolls back every earlier observation. Replay buckets retain the prior cumulative state; only the newest observation for an Agent/source carries the batch's final aligned total, and a newly crossed milestone uses that carrier's latest exact occurrence time rather than an old replay hour. Late unseen keys update only their actual source hour; overlap replays are idempotent through exact occurrence keys, and count-based duplicate budgets are prohibited. Execution overlap replays are idempotent through the stable Turn/signal grouping keys. Use `ZoneInfo("Asia/Shanghai")` and floor usage and execution fingerprints to the local hour. Milestones are `100, 250, 500, 1000`, then every additional 1,000. Initialization backfills the preceding 24 hours of hourly usage Events, bucket state, and occurrence keys while storing the highest reached milestone without emitting old milestones. It also creates an empty initializing `UsageObservation` for every Agent with a source-aligned cumulative total but no baseline occurrences, seeding cumulative and milestone state without creating a usage bucket or old milestone. Lifecycle initialization writes old dates as historical events using their actual occurrence time; only a later value change emits a new `deployment_updated` event.

Execution Events remain active through their one-hour bucket. A later scheduler pass after the bucket closes marks them `historical` without creating Recovery.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_source.py tests/test_operations_rules.py tests/test_operations_repository.py -q
```

Expected: all selected tests pass.

```bash
git add backend/app/operations/models.py backend/app/operations/repository.py backend/app/operations/rules.py backend/app/operations/source.py backend/tests/test_operations_repository.py backend/tests/test_operations_rules.py backend/tests/test_operations_source.py
git commit -m "feat: derive operational usage and execution events"
```

---

### Task 4: Isolated Scheduler and Application Integration

**Files:**
- Create: `backend/app/operations/scheduler.py`
- Create: `backend/tests/test_operations_scheduler.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/observability/service.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `FleetReadService` metadata, `ObservabilityService`, typed source-filtered `UsageBatch` reads from `PsycopgOperationsSource`, `OperationsRuleEngine`, Config intervals.
- Produces: `OperationsScheduler.startup()`, `run_runtime()`, `run_sync()`, `run_data_access()`, `run_usage()`, `run_execution()`, `run_lifecycle()`, and `operations_poll_loop()`.
- Produces app state: `operations_service` and `operations_scheduler`, each nullable when initialization fails.

- [ ] **Step 1: Write failing scheduler isolation tests**

```python
@pytest.mark.asyncio
async def test_failed_rule_group_does_not_block_other_groups(tmp_path):
    calls: list[str] = []

    async def failed(_now):
        calls.append("runtime")
        raise RuntimeError("runtime failed")

    async def succeeded(_now):
        calls.append("usage")
        return {"cursor": "complete"}

    repository = OperationsRepository(str(tmp_path / "operations.db"))
    repository.migrate()
    scheduler = OperationsScheduler(
        repository=repository,
        group_runners={"runtime": failed, "usage": succeeded},
        intervals={"runtime": 0, "usage": 0},
    )
    await scheduler.run_due(NOW)
    assert calls == ["runtime", "usage"]
    assert repository.latest_run("runtime").status == "failed"
    assert repository.latest_run("usage").status == "succeeded"


def test_operations_migration_failure_leaves_existing_health_route_available(tmp_path, monkeypatch):
    monkeypatch.setattr(OperationsRepository, "migrate", Mock(side_effect=OSError("disk unavailable")))
    config = replace(load_config(), operations_database_path=str(tmp_path / "operations.db"))
    service, scheduler = build_operations(config, None, None, None)
    assert service is None
    assert scheduler is None
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        operations_service=service,
        operations_scheduler=scheduler,
    )
    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}
    assert app.state.operations_service is None
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_scheduler.py tests/test_main.py -q
```

Expected: failures for missing scheduler and app state.

- [ ] **Step 3: Implement independent run groups**

`OperationsScheduler.run_due(now)` must evaluate each due group inside its own `try/except`, record a sanitized error containing only exception class and stable message, and continue. Use `asyncio.to_thread` for SQLite and psycopg work. `startup()` performs baseline initialization before periodic change emission.

The constructor accepts `repository`, optional production dependencies, and optional `group_runners` plus `intervals` maps. Tests inject the maps shown above. Production constructs the default runner map for `runtime`, `sync`, `data_access`, `usage`, `execution`, and `lifecycle` from the scheduler's bound methods and Config intervals.

`run_runtime()`, `run_data_access()`, and `run_lifecycle()` reuse one current `FleetReadService.overview()` result within a scheduler pass. `run_data_access()` creates `DataAccessObservation(source_name="flywheel", available=overview.usage_source.healthy, observed_at=now)`.

`run_usage()` is self-contained around two ingestion paths. Local MetaBot evaluation requests one `fetch_local_usage_batch(after, through)` using the one-hour overlap before `local_through`. Each newly completed FAE/ADMIN synchronization generation requests exactly one `fetch_remote_usage_batch(source_kind, ...)`; initialization constrains true `created_at` to the preceding 24 hours, while later generations read the full retained source snapshot. Every batch contains distinct exact `UsageOccurrence` identities plus per-Agent cumulative answered-Turn totals read from the same source filter, PostgreSQL connection, and read-only repeatable-read snapshot. The scheduler rejects source mismatches or missing totals, uses Fleet Overview only for Agent metadata, and never reads Fleet/UsageCache cumulative totals for rule evaluation. It groups occurrences by Agent/source/local hour without reducing replay identity to counts. The rule engine applies the aligned final total only to the newest observation for that Agent/source and dates a crossing milestone at its latest exact occurrence; old full-snapshot buckets cannot absorb a current crossing.

After all required batches are read, `run_usage()` constructs the candidate successful `RunHealth` containing `local_through` and `remote_generations` and passes it through `OperationsRuleEngine.evaluate_usage()` to `OperationsRepository.record_usage_batch()`. That repository method inserts the successful run/cursor on the same SQLite connection and inside the same transaction as the complete usage observation list, Events, rule states, and bucket expiration. `run_usage()` returns a typed committed outcome, and `_run_groups()` skips only its normal second successful-run write. Any source query, cumulative-total query, validation, metadata, rule-application, or successful-run insertion failure rolls back the complete usage batch. The scheduler then separately attempts to record a failed run with the prior cursor and continues other groups. Failed synchronization statuses never advance a generation. Execution follows the same local-overlap/remote-generation selection and advances only after successful application; its success cursor is outside this Task 4 usage-specific atomic contract.

The poll loop wakes every ten seconds, checks due timestamps, and never sleeps for an entire five-minute rule interval:

```python
async def operations_poll_loop(scheduler: OperationsScheduler) -> None:
    while True:
        await scheduler.run_due(datetime.now(timezone.utc))
        await asyncio.sleep(10)
```

- [ ] **Step 4: Integrate defensively in `create_app()`**

Create the SQLite parent directory, migrate, and construct Operations components inside one guarded `build_operations()` helper. On failure, log `operations initialization failed` and return `(None, None)`. Add optional `operations_service` and `operations_scheduler` injection parameters to `create_app()` for API and scheduler tests. When both are omitted and `start_poller` is false, do not create or write the default SQLite database. Add the Operations poll task only when the scheduler exists and `start_poller` is true. Existing task cancellation behavior remains unchanged.

Expose `ObservabilityService.sync_status()` as the synchronization input. Do not query remote production systems from the Operations package; it reads only the existing local synchronized status.

- [ ] **Step 5: Run focused and full backend tests, then commit**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_scheduler.py tests/test_main.py -q
.venv/bin/python -m pytest -q
```

Expected: focused tests and the full backend suite pass.

```bash
git add backend/app/main.py backend/app/observability/service.py backend/app/operations/scheduler.py backend/tests/test_main.py backend/tests/test_operations_scheduler.py
git commit -m "feat: schedule isolated Operations evaluation"
```

---

### Task 5: Daily Brief and Activity APIs

**Files:**
- Create: `backend/app/operations/service.py`
- Create: `backend/app/operations/routes.py`
- Create: `backend/tests/test_operations_service.py`
- Create: `backend/tests/test_operations_api.py`
- Modify: `backend/app/operations/models.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: repository events and `operational_runs` freshness.
- Produces: `OperationsBrief`, `BriefFreshness`, `UsageLeader`, `UsageBrief`, `OperationsService.brief()`, and `OperationsService.list_events()`.
- Produces routes: `GET /api/operations/brief`, `GET /api/operations/events`.

- [ ] **Step 1: Write failing service tests**

```python
def test_brief_contains_business_attention_and_five_changes(tmp_path):
    service = populated_service(tmp_path, business_changes=7, system_changes=2)
    brief = service.brief(NOW)
    assert all(item.agent_visibility == "business" for item in brief.attention)
    assert len(brief.changes) == 5
    assert brief.period_start == NOW - timedelta(hours=24)


def test_partial_or_stale_brief_cannot_claim_healthy(tmp_path):
    service = service_with_run_health(tmp_path, failed_group="execution")
    brief = service.brief(NOW)
    assert brief.freshness.status == "partial"
    assert brief.can_claim_healthy is False


def test_default_events_are_business_but_explicit_system_agent_is_allowed(tmp_path):
    service = populated_service(tmp_path, business_changes=1, system_changes=1)
    default_page = service.list_events(EventFilters(), 50, 0)
    system_page = service.list_events(EventFilters(agent_id="test-bot"), 50, 0)
    assert [item.agent_visibility for item in default_page.items] == ["business"]
    assert [item.agent_id for item in system_page.items] == ["test-bot"]
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_service.py tests/test_operations_api.py -q
```

Expected: failures for missing service, response models, and routes.

- [ ] **Step 3: Implement Brief contracts and assembly**

Add:

```python
class BriefFreshness(BaseModel):
    status: Literal["current", "partial", "stale", "unavailable"]
    evaluated_at: datetime | None
    failed_groups: list[str] = Field(default_factory=list)


class UsageLeader(BaseModel):
    agent_id: str
    agent_name: str
    conversations: int


class UsageBrief(BaseModel):
    conversations: int
    active_agents: int
    leaders: list[UsageLeader] = Field(default_factory=list)


class OperationsBrief(BaseModel):
    period_start: datetime
    period_end: datetime
    freshness: BriefFreshness
    can_claim_healthy: bool
    attention: list[OperationalEvent]
    usage: UsageBrief
    changes: list[OperationalEvent]
```

Ordering is critical severity first, then attention severity, then newest occurrence. Changes are newest first after fleet usage summary assembly and are capped at five. Freshness is `current` only when every required group has a recent successful run; `partial` when at least one group failed but another succeeded; `stale` when the last successful full evaluation is older than twice its scheduled interval; and `unavailable` when there is no successful baseline.

- [ ] **Step 4: Add read-only routes and unavailable behavior**

Route signatures:

```python
def _service(request: Request):
    service = request.app.state.operations_service
    if service is None:
        raise HTTPException(status_code=503, detail="operations unavailable")
    return service


@router.get("/brief", response_model=OperationsBrief)
async def brief(request: Request):
    return await asyncio.to_thread(_service(request).brief)


@router.get("/events", response_model=Page[OperationalEvent])
async def events(
    request: Request,
    agent_id: str | None = None,
    event_type: str | None = None,
    severity: EventSeverity | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = EventFilters(
        agent_id=agent_id,
        event_type=event_type,
        severity=severity,
        date_from=date_from,
        date_to=date_to,
    )
    return await asyncio.to_thread(
        _service(request).list_events, filters, limit, offset
    )
```

When `operations_service` is `None`, both routes return HTTP 503 with `detail="operations unavailable"`; all existing APIs continue to respond normally.

- [ ] **Step 5: Run focused and API regression tests, then commit**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations_service.py tests/test_operations_api.py tests/test_observability_api.py tests/test_fleet_api.py -q
```

Expected: all selected tests pass.

```bash
git add backend/app/main.py backend/app/operations/models.py backend/app/operations/routes.py backend/app/operations/service.py backend/tests/test_operations_api.py backend/tests/test_operations_service.py
git commit -m "feat: expose Daily Brief and Activity APIs"
```

---

### Task 6: Daily Brief Overview UI

**Files:**
- Create: `webui/src/components/DailyBrief.tsx`
- Create: `webui/src/components/OperationalEventItem.tsx`
- Create: `webui/src/operations.ts`
- Create: `webui/src/operations.test.ts`
- Create: `webui/src/operationsUi.test.tsx`
- Modify: `webui/src/types.ts`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/api.test.ts`
- Modify: `webui/src/pages/OverviewPage.tsx`
- Modify: `webui/src/dashboard.test.ts`

**Interfaces:**
- Consumes: `/api/operations/brief`.
- Produces: `fetchOperationsBrief()`, `DailyBrief`, and reusable `OperationalEventItem`.
- Produces helpers: `briefStatusLabel()`, `eventTimeLabel()`, and `eventTargetPath()`.

- [ ] **Step 1: Write failing type, API, helper, and render tests**

Use a fixture with one runtime Attention item, one Recovery change, and current freshness. Assert:

```tsx
it("renders evidence-backed Attention and Last 24 Hours", () => {
  const html = renderToStaticMarkup(<DailyBrief brief={briefFixture} />);
  expect(html).toContain("Needs Attention");
  expect(html).toContain("Last 24 Hours");
  expect(html).toContain("AI FAE Agent is offline");
  expect(html).toContain("Two consecutive runtime observations");
  expect(html).toContain("View all activity");
});


it("does not claim health for partial evaluation", () => {
  const html = renderToStaticMarkup(
    <DailyBrief brief={{ ...briefFixture, freshness: partialFreshness, can_claim_healthy: false, attention: [] }} />,
  );
  expect(html).not.toContain("No critical issues");
  expect(html).toContain("Brief partially evaluated");
});
```

API test must assert `fetchOperationsBrief()` requests `/api/operations/brief` and forwards the AbortSignal.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd webui
npm test -- --run src/operations.test.ts src/operationsUi.test.tsx src/api.test.ts src/dashboard.test.ts
```

Expected: failures for missing types, API client, helper, and components.

- [ ] **Step 3: Add exact frontend contracts**

```typescript
export type EventSeverity = "info" | "attention" | "critical";
export type EventStatus = "active" | "resolved" | "historical";
export type EventFamily = "runtime" | "data" | "execution" | "usage" | "lifecycle" | "recovery";

export interface OperationalEvent {
  event_id: string;
  agent_id: string | null;
  agent_visibility: AgentVisibility;
  event_type: string;
  event_family: EventFamily;
  severity: EventSeverity;
  status: EventStatus;
  title: string;
  summary: string;
  source_kind: string;
  occurred_at: string;
  first_observed_at: string;
  last_observed_at: string;
  resolved_at: string | null;
  facts: Record<string, unknown>;
  target_kind: string | null;
  target_id: string | null;
  target_path: string | null;
  fingerprint: string;
}

export interface OperationsBrief {
  period_start: string;
  period_end: string;
  freshness: { status: "current" | "partial" | "stale" | "unavailable"; evaluated_at: string | null; failed_groups: string[] };
  can_claim_healthy: boolean;
  attention: OperationalEvent[];
  usage: { conversations: number; active_agents: number; leaders: { agent_id: string; agent_name: string; conversations: number }[] };
  changes: OperationalEvent[];
}
```

- [ ] **Step 4: Implement Brief fetch and presentation**

Fetch Fleet Overview and Operations Brief independently. Fleet failure keeps existing last-known Fleet behavior. Operations failure omits the module when no prior Brief exists; after one success it preserves the last Brief and changes its local presentation to stale without replacing Overview.

`DailyBrief` renders:

- left panel: active Attention or `No critical issues` only when `can_claim_healthy` is true;
- right panel: fleet usage summary followed by up to five changes;
- evaluation time and partial/stale status;
- `/activity` `View all activity` link;
- severity label plus icon, never color alone.

Insert the module immediately after `summary-section` and before `insight-grid`.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
cd webui
npm test -- --run src/operations.test.ts src/operationsUi.test.tsx src/api.test.ts src/dashboard.test.ts
```

Expected: all selected tests pass.

```bash
git add webui/src/api.ts webui/src/api.test.ts webui/src/components/DailyBrief.tsx webui/src/components/OperationalEventItem.tsx webui/src/dashboard.test.ts webui/src/operations.ts webui/src/operations.test.ts webui/src/operationsUi.test.tsx webui/src/pages/OverviewPage.tsx webui/src/types.ts
git commit -m "feat: add Daily Brief to Overview"
```

---

### Task 7: Activity History Page

**Files:**
- Create: `webui/src/pages/ActivityPage.tsx`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/operationsUi.test.tsx`

**Interfaces:**
- Consumes: `GET /api/operations/events` and `GET /api/agents`.
- Produces: `fetchOperationalEvents(query, signal)` and the `/activity` route.
- Reuses: `OperationalEventItem` and `businessAgents()`.

- [ ] **Step 1: Write failing route and page tests**

```typescript
it("routes Activity without assigning it a primary navigation section", () => {
  expect(parseRoute("/activity")).toEqual({ name: "activity" });
  expect(routePath({ name: "activity" })).toBe("/activity");
  expect(routeSection({ name: "activity" })).toBeNull();
});
```

Render tests must verify the filter labels `Agent`, `Event type`, `Severity`, `From`, and `To`; date group headings `Today` and `Yesterday`; and a Business-only default selector that preserves an explicitly selected System Agent.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd webui
npm test -- --run src/router.test.ts src/operationsUi.test.tsx src/api.test.ts
```

Expected: failures for the missing route, page, and API client.

- [ ] **Step 3: Implement query and route contracts**

```typescript
export interface OperationsEventQuery {
  agent_id?: string;
  event_type?: string;
  severity?: EventSeverity;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}
```

Serialize non-empty fields with `URLSearchParams`. Add `{ name: "activity" }` to `Route`, map `/activity`, render `<ActivityPage />` in `App`, and return `null` from `routeSection()` so no primary tab receives a false active state.

- [ ] **Step 4: Implement Activity History behavior**

The page fetches Agents and the first 50 events. Filter submission resets offset to zero. `Load more` requests the next offset and appends events without reordering. Group events in `Asia/Shanghai` using exact headings `Today`, `Yesterday`, or localized date. Default Agent options are Business only; use `agentsForSelector(agents, selectedAgentId)` so an explicit System Agent deep link remains valid.

Show a contained stale/unavailable state for Activity only; do not replace the App shell or other pages.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
cd webui
npm test -- --run src/router.test.ts src/operationsUi.test.tsx src/api.test.ts
```

Expected: all selected tests pass.

```bash
git add webui/src/App.tsx webui/src/api.ts webui/src/pages/ActivityPage.tsx webui/src/router.ts webui/src/router.test.ts webui/src/operationsUi.test.tsx
git commit -m "feat: add operational Activity History"
```

---

### Task 8: Agent Recent Activity

**Files:**
- Modify: `webui/src/pages/AgentDetailPage.tsx`
- Modify: `webui/src/pages/AgentDetailPage.test.tsx`
- Modify: `webui/src/operationsUi.test.tsx`

**Interfaces:**
- Consumes: `fetchOperationalEvents({ agent_id, limit: 8 })`.
- Reuses: `OperationalEventItem`.
- Produces: per-Agent `Recent Activity` section and `/activity?agent_id=<encoded-agent-id>` link.

- [ ] **Step 1: Write the failing Agent activity test**

Assert the Agent page source requests explicit Agent events independently of Sessions and renders:

```tsx
<section className="detail-section agent-activity-section">
  <div className="section-heading">
    <div><p>OPERATIONS HISTORY</p><h2>Recent Activity</h2></div>
    <PlatformLink href={`/activity?agent_id=${encodeURIComponent(agent.id)}`}>View all activity →</PlatformLink>
  </div>
</section>
```

The render test must prove System Agent detail is allowed to display its explicitly requested activity.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd webui
npm test -- --run src/pages/AgentDetailPage.test.tsx src/operationsUi.test.tsx
```

Expected: failures because Agent Detail does not request or render activity.

- [ ] **Step 3: Implement independent activity loading**

Do not add Operations to the existing `Promise.all` that gates the profile. Agent and Session failures retain current behavior; activity failure renders a small unavailable state inside the Recent Activity section so the Agent profile and Sessions stay usable.

Render at most eight events between the profile card and Recent Sessions. Empty activity displays `No operational changes recorded yet.`

- [ ] **Step 4: Run focused tests and commit**

Run:

```bash
cd webui
npm test -- --run src/pages/AgentDetailPage.test.tsx src/operationsUi.test.tsx
```

Expected: all selected tests pass.

```bash
git add webui/src/pages/AgentDetailPage.tsx webui/src/pages/AgentDetailPage.test.tsx webui/src/operationsUi.test.tsx
git commit -m "feat: show recent Agent activity"
```

---

### Task 9: Visual System, Production Configuration, Documentation, and Deployment

**Files:**
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`
- Modify: `deploy/com.orbbec.ai-agent-platform.plist`
- Modify: `README.md`

**Interfaces:**
- Consumes: all prior backend and frontend behavior.
- Produces: production-ready responsive styling, durable database location, documented APIs, and a verified live deployment.

- [ ] **Step 1: Write failing visual contract tests**

Assert CSS contains:

```typescript
expect(css).toContain(".daily-brief-grid");
expect(css).toContain(".attention-panel");
expect(css).toContain(".activity-group");
expect(css).toContain(".event-severity-critical");
expect(css).toContain(".brief-freshness-stale");
expect(css).toMatch(/@media[^}]*max-width:\s*760px/s);
expect(css).toMatch(/\.daily-brief-grid\s*\{[^}]*grid-template-columns:\s*1fr/s);
```

- [ ] **Step 2: Run the style test and verify RED**

Run:

```bash
cd webui
npm test -- --run src/styles.test.ts
```

Expected: failures for missing Operations selectors.

- [ ] **Step 3: Implement the approved visual hierarchy**

Use the existing tokens and card language. Desktop uses two equal Brief columns; mobile stacks Attention first. Critical, Attention, Info, and Recovery use label, icon, border treatment, and color. Maintain the current minimum body and metadata font sizes; do not shrink text to fit. Event rows have clear hover/focus states only when linked. System Agent activity retains the quieter infrastructure treatment.

- [ ] **Step 4: Configure production runtime state**

Add to the LaunchAgent environment:

```xml
<key>PLATFORM_OPERATIONS_DATABASE_PATH</key>
<string>/Users/neo/Developer/work/AI-Agent-Platform/data/platform-operations.db</string>
```

Do not modify or restart any MetaBot, AI FAE, or AI ADMIN service.

- [ ] **Step 5: Update README**

Document:

- Overview Daily Brief behavior;
- `/activity` entry point;
- `GET /api/operations/brief`;
- `GET /api/operations/events` and filters;
- SQLite path and rebuildable derived-state boundary;
- `PLATFORM_OPERATIONS_DATABASE_PATH`;
- failure isolation and read-only guarantees.

Update stale wording that says Fleet reporting contains all eleven Agents: describe nine Business Agents plus two explicitly accessible System Agents.

- [ ] **Step 6: Run complete verification**

Run:

```bash
cd backend
.venv/bin/python -m pytest

cd ../webui
npm test
npm run build

cd ..
git diff --check
```

Expected: zero backend failures, zero frontend failures, successful TypeScript/Vite build, and no whitespace errors.

- [ ] **Step 7: Commit production polish**

```bash
git add README.md deploy/com.orbbec.ai-agent-platform.plist webui/src/styles.css webui/src/styles.test.ts
git commit -m "feat: finish Operations Cockpit experience"
```

- [ ] **Step 8: Deploy only the Platform**

Build has already completed in Step 6. Validate and install the updated Platform plist, then reload only this LaunchAgent so the new environment value is active:

```bash
plutil -lint deploy/com.orbbec.ai-agent-platform.plist
cp deploy/com.orbbec.ai-agent-platform.plist /Users/neo/Library/LaunchAgents/com.orbbec.ai-agent-platform.plist
launchctl bootout gui/$(id -u)/com.orbbec.ai-agent-platform
launchctl bootstrap gui/$(id -u) /Users/neo/Library/LaunchAgents/com.orbbec.ai-agent-platform.plist
launchctl kickstart gui/$(id -u)/com.orbbec.ai-agent-platform
```

Verify:

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/operations/brief | jq '{freshness, attention_count:(.attention|length), change_count:(.changes|length)}'
curl -fsS 'http://127.0.0.1:8000/api/operations/events?limit=5' | jq '{total, visibilities:[.items[].agent_visibility]}'
curl -fsS 'http://127.0.0.1:8000/api/operations/events?agent_id=test-bot&limit=5' | jq '{total, agent_ids:[.items[].agent_id]}'
curl -fsS http://127.0.0.1:8000/ | rg -o 'assets/index-[A-Za-z0-9_-]+\.(js|css)'
```

Expected:

- health is `{"status":"ok"}`;
- Brief contains a freshness object and no more than five changes;
- default Activity visibilities are all `business`;
- explicit `test-bot` Activity contains only `test-bot` when events exist;
- root serves the newly built hashed JS and CSS assets.

- [ ] **Step 9: Visual QA**

Inspect at 1440px desktop and 390px mobile:

- `/` — Key Facts, Daily Brief, trend, ranking, and nine Business Agent cards;
- `/activity` — filters, date groups, zero state, pagination, severity, and target links;
- `/agents/ai-fae-agent` — Recent Activity between profile and Sessions;
- `/agents/test-bot` — explicit System Agent activity remains available;
- partial/stale fixture or induced local Operations failure — no false healthy statement and no loss of existing Fleet content.

Record the final Platform PID, live asset hashes, backend test count, frontend test count, and any intentionally preserved unrelated working-tree changes in the handoff.
