# FAE Management Workbench Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an Owner/Admin-only FAE 工作台 with freshness-aware overview, scoped production Sessions, Session-to-Issue creation, and the existing full Review closure workflow, while leaving a truthful unavailable state for analysis reports.

**Approved design:** `docs/superpowers/specs/2026-08-31-fae-management-workbench-design.md`

**Architecture:** Add a thin `fae_workbench` backend facade that injects the immutable `ai-fae-agent`/`fae` scope and composes the existing Observability and Review services. The frontend extracts reusable Session and Review workspace components, then mounts them under `/admin/fae` with stable URLs and FAE-specific navigation. A small FAE aggregate repository computes overview metrics from the current mirror; it does not replace canonical Session storage.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, psycopg 3, PostgreSQL, React 19, TypeScript 5.6, Vite 7, Vitest 3.2, CSS.

## Global Constraints

- Only `platform_owner` and `platform_admin` may see or call the workbench in release 1.
- Production `agent.orbbec.com.cn` remains the existing sanitized read-only cloud replica. It shows FAE overview, Sessions and projected Issue state, but all Review mutations remain disabled there; the full Issue closure workflow is available only on the writable local management deployment until a separately approved relay exists.
- The backend scope is always `agent_id=ai-fae-agent`, `source_kind=fae`, `source_environment=production`; the browser cannot override it.
- Reuse `platform_read.sessions`, `ObservabilityService`, `ReviewService`, existing Review state transitions and existing audit events. Do not create a second Session, Feedback or Issue truth source.
- Current FAE business data is synchronized daily. Every overview and Session surface shows `source_synced_at`/last-success freshness and must not say “实时”.
- All display timestamps use `Asia/Shanghai`; API timestamps remain timezone-aware ISO-8601 values.
- Release 1 exposes only filters backed by the current canonical model: keyword, channel, Feedback sentiment, Review status, Outcome and date range. It does not fabricate user/department, Fallback, latency or Platform-Issue-status filters before those indexed presentation-safe projections exist; Fallback/latency remain visible through attention items and Issue status remains available in 问题治理.
- Do not add model, Prompt, Knowledge, Tool, deployment, restart or production-configuration controls.
- Do not add a manual force-close action. Existing merge, deployment, replay and independent semantic-review gates remain authoritative.
- Allow an Owner/Admin to open an Issue from any real FAE Turn, even when that Turn has no Feedback. Empty `source_feedback_keys` is valid, but the Turn itself must exist in the FAE scope.
- The reports view must say “分析报告尚未接入” until the second plan is complete. Never ship fixture or sample report data as production content.
- Preserve current `/admin/sessions`, `/admin/sessions/:session_key` and `/admin/review` behavior and links.
- Use TDD for every behavior change and make one focused commit per task.

---

## File and Boundary Map

### Backend

- Create `backend/app/fae_workbench/__init__.py` — package marker and scope constants export.
- Create `backend/app/fae_workbench/models.py` — overview, freshness, trend and attention response models.
- Create `backend/app/fae_workbench/repository.py` — FAE-only aggregate queries and Turn ownership lookup.
- Create `backend/app/fae_workbench/service.py` — scoped composition over Observability and Review.
- Create `backend/app/fae_workbench/routes.py` — `/api/admin/fae/*` read/write facade.
- Create `backend/app/review/http_models.py` — shared strict Review request models used by both routers.
- Modify `backend/app/review/routes.py` — import shared request models; keep public paths and behavior unchanged.
- Modify `backend/app/review/service.py` — expose the existing detail lookup needed by the facade's scope guard; do not duplicate transition logic.
- Modify `backend/app/main.py` — construct, store and mount `FaeWorkbenchService`.
- Modify `backend/app/control_plane/authorization.py` — exact allowlist for workbench endpoints and cloud/hard-stale write denial.
- Modify `backend/app/control_plane/audit.py` — register privacy-safe FAE Session Detail privileged-read events.
- Modify `backend/app/cloud_replica/repository.py` — expose a bounded FAE operational aggregate over decrypted sanitized replica records.

### Frontend

- Create `webui/src/faeWorkbenchTypes.ts` — workbench API models.
- Create `webui/src/faeWorkbenchApi.ts` — typed scoped API client.
- Create `webui/src/components/fae-workbench/FaeWorkbenchShell.tsx` — workbench heading and four-view navigation.
- Create `webui/src/components/session/SessionReplay.tsx` — shared loaded Session presentation.
- Create `webui/src/components/session/useSessionDetail.ts` — shared fetch/closure-summary state.
- Create `webui/src/components/review/ReviewWorkspace.tsx` — reusable Review UI and mutation orchestration.
- Create `webui/src/pages/FaeOverviewPage.tsx`.
- Create `webui/src/pages/FaeSessionsPage.tsx`.
- Create `webui/src/pages/FaeSessionDetailPage.tsx`.
- Create `webui/src/pages/FaeIssuesPage.tsx`.
- Create `webui/src/pages/FaeReportsPlaceholderPage.tsx`.
- Modify `webui/src/pages/SessionsPage.tsx`, `SessionDetailPage.tsx`, `ReviewPage.tsx` into compatibility wrappers around shared components.
- Modify `webui/src/components/SessionListItem.tsx`, `TurnCard.tsx`, `review/IssueList.tsx` to accept explicit workspace links/scope presentation.
- Modify `webui/src/sessionNavigation.ts`, `api.ts`, `types.ts`, `router.ts`, `documentTitle.ts`, `App.tsx`, `AppShell.tsx`, `auth.ts`, and `styles.css`.

### Tests

- Create `backend/tests/test_fae_workbench_repository.py`.
- Modify `backend/tests/test_cloud_repository.py` for cloud FAE aggregate parity and privacy.
- Create `backend/tests/test_fae_workbench_service.py`.
- Create `backend/tests/test_fae_workbench_api.py`.
- Modify `backend/tests/test_review_api.py`, `test_review_service.py`, `test_r1_authorization.py`, and `test_main.py`.
- Modify `backend/tests/test_control_plane_audit.py` for FAE Session privileged-read event validation.
- Create `webui/src/faeWorkbenchApi.test.ts`.
- Create `webui/src/components/fae-workbench/FaeWorkbenchShell.test.tsx`.
- Create `webui/src/pages/FaeOverviewPage.test.tsx`.
- Create `webui/src/pages/FaeSessionsPage.test.tsx`.
- Create `webui/src/pages/FaeSessionDetailPage.test.tsx`.
- Create `webui/src/pages/FaeIssuesPage.test.tsx`.
- Modify the existing router, shell, Session and Review tests for regression coverage.

---

### Task 1: Add FAE Aggregate Models and Repository

**Files:**
- Create: `backend/app/fae_workbench/__init__.py`
- Create: `backend/app/fae_workbench/models.py`
- Create: `backend/app/fae_workbench/repository.py`
- Test: `backend/tests/test_fae_workbench_repository.py`
- Modify: `backend/app/cloud_replica/repository.py`
- Modify: `backend/tests/test_cloud_repository.py`

**Interfaces:**
- Produces: `FAE_AGENT_ID`, `FAE_SOURCE_KIND`, `FaeOperationalSnapshot`, `FaeTrendPoint`, `FaeSessionAttention`, `FaeWorkbenchRepository`, local `PsycopgFaeWorkbenchRepository`, cloud `ReplicaFaeWorkbenchRepository`, `snapshot(period_start, period_end)`, and `fae_turn_exists(turn_key)`.
- Consumes: local `platform_read.sessions|turns|feedback` and `platform_source_fae.chat_sessions`, or the existing decrypted sanitized cloud Session read boundary.

- [ ] **Step 1: Write failing model and repository tests**

```python
def test_snapshot_uses_one_period_and_keeps_feedback_units_distinct():
    repository, connection = repository_with_rows(
        summary={
            "session_count": 12, "active_subject_count": 7,
            "negative_feedback_events": 3, "negative_turn_count": 2,
            "abnormal_session_count": 1, "p50_duration_ms": 820,
            "p95_duration_ms": 3100, "data_as_of": NOW,
        },
        trend=[{"day": date(2026, 8, 31), "sessions": 12, "negative_turns": 2}],
        attention=[{
            "session_key": "fae:session-1", "title": "设备掉线",
            "last_active_at": NOW, "reason": "fallback",
        }],
    )

    result = repository.snapshot(PERIOD_START, PERIOD_END)

    assert result.session_count == 12
    assert result.negative_feedback_events == 3
    assert result.negative_turn_count == 2
    assert result.data_as_of == NOW
    normalized = " ".join(connection.executed[0][0].split())
    assert "s.agent_id = 'ai-fae-agent'" in normalized
    assert "s.source_kind = 'fae'" in normalized


def test_turn_scope_requires_both_fae_agent_and_source():
    repository, connection = repository_with_turn_exists(True)
    assert repository.fae_turn_exists("fae:turn-1") is True
    statement, params = connection.executed[-1]
    assert "agent_id='ai-fae-agent'" in "".join(statement.split())
    assert "source_kind='fae'" in "".join(statement.split())
    assert params == ("fae:turn-1",)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_workbench_repository.py tests/test_cloud_repository.py`

Expected: FAIL because `app.fae_workbench` does not exist.

- [ ] **Step 3: Define exact models and constants**

```python
FAE_AGENT_ID = "ai-fae-agent"
FAE_SOURCE_KIND = "fae"


class FaeTrendPoint(BaseModel):
    day: date
    sessions: int = Field(ge=0)
    negative_turns: int = Field(ge=0)


class FaeSessionAttention(BaseModel):
    session_key: str
    title: str | None = None
    last_active_at: datetime
    reason: Literal["fallback", "failed_outcome", "empty_answer"]


class FaeOperationalSnapshot(BaseModel):
    period_start: datetime
    period_end: datetime
    data_as_of: datetime | None
    session_count: int = Field(ge=0)
    active_subject_count: int = Field(ge=0)
    negative_feedback_events: int = Field(ge=0)
    negative_turn_count: int = Field(ge=0)
    abnormal_session_count: int = Field(ge=0)
    p50_duration_ms: int | None
    p95_duration_ms: int | None
    trend: list[FaeTrendPoint] = Field(default_factory=list)
    attention: list[FaeSessionAttention] = Field(default_factory=list)
```

- [ ] **Step 4: Implement exact aggregate semantics**

`snapshot()` selects Sessions whose `last_active_at` is in `[period_start, period_end)`. Its Turn and Feedback denominators are restricted to those Session keys. `active_subject_count` uses exact non-null `coalesce(external_user_id,user_id)` values only for counting and never returns those values. An abnormal Session has at least one Turn with blank answer, `fallback_used=true`, or an Outcome outside `resolved|completed|succeeded`.

Use `percentile_cont(0.5)` and `percentile_cont(0.95)` over non-negative non-null `duration_ms`. Trend buckets use `(last_active_at at time zone 'Asia/Shanghai')::date`. Attention is capped at 10 and ordered by latest activity.

```python
class PsycopgFaeWorkbenchRepository:
    def snapshot(self, period_start: datetime, period_end: datetime) -> FaeOperationalSnapshot:
        with self._connection() as connection, connection.cursor() as cursor:
            summary = cursor.execute(SUMMARY_SQL, (period_start, period_end)).fetchone()
            trend = cursor.execute(TREND_SQL, (period_start, period_end)).fetchall()
            attention = cursor.execute(ATTENTION_SQL, (period_start, period_end, 10)).fetchall()
        return FaeOperationalSnapshot(
            period_start=period_start,
            period_end=period_end,
            trend=[FaeTrendPoint(**row) for row in trend],
            attention=[FaeSessionAttention(**row) for row in attention],
            **summary,
        )

    def fae_turn_exists(self, turn_key: str) -> bool:
        with self._connection() as connection, connection.cursor() as cursor:
            row = cursor.execute(
                """select exists(select 1 from platform_read.turns
                   where turn_key=%s and agent_id='ai-fae-agent'
                     and source_kind='fae') as found""",
                (turn_key,),
            ).fetchone()
        return bool(row and row["found"])
```

- [ ] **Step 5: Cover unavailable data and stable freshness**

Add tests proving SQL errors raise `FaeWorkbenchReadError("fae_workbench_query_failed")`, null `data_as_of` remains null, zero duration rows return null percentiles, and raw user IDs never appear in the model or exception text.

For cloud-replica mode, add a bounded aggregate method to `CloudReplicaRepository` that decrypts only FAE Session records whose `last_active_at` is in the period, counts its stable pseudonymous `user_id` values, and computes the same abnormal/duration/trend/attention semantics from sanitized Turns. `ReplicaFaeWorkbenchRepository` delegates to that method and never receives raw local canonical keys. Feedback totals and Issue counts still come from the existing cloud Review projections, so absence stays unavailable rather than a false zero. Add a local-vs-cloud synthetic parity test for all metrics that survive sanitization.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_workbench_repository.py tests/test_cloud_repository.py`

Expected: PASS.

```bash
git add backend/app/fae_workbench backend/app/cloud_replica/repository.py backend/tests/test_fae_workbench_repository.py backend/tests/test_cloud_repository.py
git commit -m "feat(fae-workbench): add operational aggregate repository"
```

---

### Task 2: Compose a Scoped Overview and Session Service

**Files:**
- Modify: `backend/app/fae_workbench/models.py`
- Create: `backend/app/fae_workbench/service.py`
- Test: `backend/tests/test_fae_workbench_service.py`

**Interfaces:**
- Consumes: `ObservabilityService`, `ReviewService`, and `FaeWorkbenchRepository` from Task 1.
- Produces: `FaeWorkbenchService.overview(now)`, `list_sessions(filters, limit, offset)`, `get_session(session_key)`, and strict FAE Issue facade methods used by Task 6.

- [ ] **Step 1: Write failing scope and partial-failure tests**

```python
@pytest.mark.asyncio
async def test_list_sessions_overrides_browser_agent_and_source():
    observability = RecordingObservability()
    service = service_for(observability=observability)
    supplied = SessionFilters(agent_id="ai-admin-agent", source_kind="admin", query="Gemini")

    await service.list_sessions(supplied, limit=50, offset=0)

    sent = observability.filters
    assert sent.agent_id == "ai-fae-agent"
    assert sent.source_kind == "fae"
    assert sent.query == "Gemini"


@pytest.mark.asyncio
async def test_non_fae_session_is_hidden_as_missing():
    service = service_for(observability=StaticObservability(admin_session()))
    assert await service.get_session("admin:session-1") is None


@pytest.mark.asyncio
async def test_review_failure_does_not_remove_operational_summary():
    overview = await service_for(review=UnavailableReview()).overview(NOW)
    assert overview.summary.state.status == "available"
    assert overview.issues.state.status == "unavailable"
    assert overview.reports.state.error_code == "reports_not_integrated"
```

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_workbench_service.py`

Expected: FAIL because `FaeWorkbenchService` is missing.

- [ ] **Step 3: Add typed section responses**

```python
SectionStatus = Literal["available", "unavailable"]


class FaeSectionState(BaseModel):
    status: SectionStatus
    as_of: datetime | None = None
    error_code: str | None = None


class FaeSummary(BaseModel):
    session_count: int
    active_subject_count: int
    negative_feedback_events: int
    negative_turn_count: int
    abnormal_session_count: int
    open_issue_count: int
    p50_duration_ms: int | None
    p95_duration_ms: int | None


class FaeOverview(BaseModel):
    period_start: datetime
    period_end: datetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    freshness: FaeFreshness
    summary: FaeSummarySection
    attention: FaeAttentionSection
    trends: FaeTrendSection
    issues: FaeIssueSection
    reports: FaeReportPreviewSection
```

Each section has `state: FaeSectionState` plus its typed payload. The reports payload is empty in this plan and has `unavailable/reports_not_integrated`.

- [ ] **Step 4: Implement immutable Session scope**

```python
def _fae_filters(filters: SessionFilters) -> SessionFilters:
    return filters.model_copy(update={
        "agent_id": FAE_AGENT_ID,
        "source_kind": FAE_SOURCE_KIND,
    })


async def get_session(self, session_key: str):
    value = await self._observability.get_session(session_key)
    if value is None:
        return None
    if value.agent_id != FAE_AGENT_ID or value.source_kind != FAE_SOURCE_KIND:
        return None
    return value
```

- [ ] **Step 5: Compose the overview without all-or-nothing failure**

The default period is the seven complete local calendar days ending at `now`, expressed as timezone-aware instants. Run aggregate and Review reads concurrently with `asyncio.gather(..., return_exceptions=True)`. Compute `open_issue_count` as all statuses except `closed|duplicate|not_actionable|wont_fix`. Preserve the successful section when the other source fails.

Freshness is:

```python
if snapshot.data_as_of is None:
    freshness = FaeFreshness(status="unavailable", data_as_of=None)
elif now - snapshot.data_as_of > timedelta(hours=36):
    freshness = FaeFreshness(status="stale", data_as_of=snapshot.data_as_of)
else:
    freshness = FaeFreshness(status="fresh", data_as_of=snapshot.data_as_of)
```

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_workbench_service.py`

Expected: PASS.

```bash
git add backend/app/fae_workbench/models.py backend/app/fae_workbench/service.py backend/tests/test_fae_workbench_service.py
git commit -m "feat(fae-workbench): compose scoped overview and sessions"
```

---

### Task 3: Mount Owner/Admin Workbench Read APIs

**Files:**
- Create: `backend/app/fae_workbench/routes.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/app/control_plane/audit.py`
- Test: `backend/tests/test_fae_workbench_api.py`
- Test: `backend/tests/test_r1_authorization.py`
- Test: `backend/tests/test_main.py`
- Modify: `backend/tests/test_control_plane_audit.py`

**Interfaces:**
- Consumes: `FaeWorkbenchService` from Task 2.
- Produces: `GET /api/admin/fae/overview`, `/sessions`, and `/sessions/{session_key}`.

- [ ] **Step 1: Write failing API scope tests**

```python
def test_fae_session_api_ignores_conflicting_scope(client, service):
    response = client.get(
        "/api/admin/fae/sessions?agent_id=ai-admin-agent&source_kind=admin&q=335"
    )
    assert response.status_code == 200
    assert service.filters.agent_id == "ai-fae-agent"
    assert service.filters.source_kind == "fae"
    assert service.filters.query == "335"


def test_fae_detail_returns_404_for_other_agent(client):
    assert client.get("/api/admin/fae/sessions/admin%3Asession-1").status_code == 404


def test_fae_detail_records_privileged_read_without_content_or_raw_key(client, audit):
    response = client.get("/api/admin/fae/sessions/fae%3Asession-1")
    assert response.status_code == 200
    assert audit.events[-1].event_type == "fae_session_detail_read_completed"
    assert audit.events[-1].target_type == "fae_session"
    assert audit.events[-1].target_id == hashlib.sha256(b"fae:session-1").hexdigest()
    assert "question" not in repr(audit.events)


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN])
def test_exact_management_roles_can_read_fae_workbench(role):
    decision = AuthorizationService(Grants()).decide(
        AuthContext(uuid4(), role, uuid4(), False),
        "GET", "/api/admin/fae/overview", (),
    )
    assert decision.allowed is True
```

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_workbench_api.py tests/test_r1_authorization.py`

Expected: FAIL because the routes are absent/default-denied.

- [ ] **Step 3: Implement read routes with bounded filters**

```python
router = APIRouter(prefix="/api/admin/fae", tags=["fae-workbench"])


@router.get("/overview")
async def overview(request: Request):
    return await request.app.state.fae_workbench_service.overview(
        datetime.now(timezone.utc)
    )


@router.get("/sessions")
async def sessions(
    request: Request,
    q: str | None = None,
    channel: str | None = None,
    sentiment: Literal["positive", "negative", "other"] | None = None,
    review_status: str | None = None,
    outcome: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = SessionFilters(
        query=q, channel=channel, sentiment=sentiment,
        review_status=review_status, outcome=outcome,
        date_from=date_from, date_to=date_to,
    )
    return await request.app.state.fae_workbench_service.list_sessions(
        filters, limit, offset
    )
```

Do not accept `agent_id`, `source_kind`, or `source_environment` parameters on this router.

- [ ] **Step 4: Wire the service into `create_app`**

Add optional `fae_workbench_service=None` to `create_app` for tests. When not injected, create `PsycopgFaeWorkbenchRepository(database_url)` in local mode, `ReplicaFaeWorkbenchRepository(cloud_observability_repository)` in cloud-replica mode, or an unavailable repository when neither read boundary exists. Store it at `app.state.fae_workbench_service` and include the router before middleware route snapshots are constructed.

- [ ] **Step 5: Add exact authorization allowlist entries**

```python
_FAE_WORKBENCH_READ_ROUTES = frozenset({
    ("GET", "/api/admin/fae/overview"),
    ("GET", "/api/admin/fae/sessions"),
    ("GET", "/api/admin/fae/sessions/{session_key}"),
})
```

Union these into `_OWNER_ROUTES`, not `VIEWER_R1_ROUTES`. Add tests for unauthenticated 401, member/viewer 403, Owner/Admin 200, and hard-stale Owner/Admin read access.

- [ ] **Step 6: Audit protected Session Detail reads**

Register `fae_session_detail_read_requested|completed|failed` in the existing control-plane audit vocabulary with reason `privileged_read` and target type `fae_session`. The target ID is the lowercase SHA-256 of the canonical Session key; metadata contains only operation/link IDs, result and bounded error code. The route derives the actor from `request.state.auth_context`, records requested before loading, then completed or failed. If the required audit append is unavailable, fail closed with 503 and do not return Session content. List and overview reads remain covered by ordinary access logs.

- [ ] **Step 7: Run tests and commit**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_workbench_api.py tests/test_r1_authorization.py tests/test_main.py tests/test_control_plane_audit.py`

Expected: PASS.

```bash
git add backend/app/fae_workbench/routes.py backend/app/main.py backend/app/control_plane/authorization.py backend/app/control_plane/audit.py backend/tests/test_fae_workbench_api.py backend/tests/test_r1_authorization.py backend/tests/test_main.py backend/tests/test_control_plane_audit.py
git commit -m "feat(fae-workbench): expose scoped management reads"
```

---

### Task 4: Add Workbench Routes, Navigation and Typed Client

**Files:**
- Create: `webui/src/faeWorkbenchTypes.ts`
- Create: `webui/src/faeWorkbenchApi.ts`
- Create: `webui/src/faeWorkbenchApi.test.ts`
- Create: `webui/src/components/fae-workbench/FaeWorkbenchShell.tsx`
- Create: `webui/src/components/fae-workbench/FaeWorkbenchShell.test.tsx`
- Create: `webui/src/pages/FaeReportsPlaceholderPage.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/router.brain.test.ts`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/AppShell.brain.test.tsx`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/auth.test.ts`

**Interfaces:**
- Produces: route names `admin-fae-overview|sessions|session|issues|issue|reports|report`, `FaeWorkbenchShell`, `faeWorkbenchApi`, and exact TypeScript overview types.
- Consumes: backend APIs from Task 3.

- [ ] **Step 1: Write failing route and navigation tests**

```typescript
expect(parseRoute("/admin/fae")).toEqual({ name: "admin-fae-overview" });
expect(parseRoute("/admin/fae/sessions/fae%3Asession-1")).toEqual({
  name: "admin-fae-session", sessionKey: "fae:session-1",
});
expect(parseRoute("/admin/fae/issues/00000000-0000-0000-0000-000000000001")).toEqual({
  name: "admin-fae-issue", issueId: "00000000-0000-0000-0000-000000000001",
});
expect(routePath({ name: "admin-fae-reports" })).toBe("/admin/fae/reports");
expect(routeSection({ name: "admin-fae-overview" })).toBe("admin");
```

Shell test:

```typescript
await act(async () => root.render(
  <AppShell route={{ name: "admin-fae-overview" }} account={owner}>
    <p>内容</p>
  </AppShell>,
));
expect(container.querySelector('a[href="/admin/fae"]')?.textContent).toBe("FAE 工作台");
```

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- router.test.ts router.brain.test.ts AppShell.brain.test.tsx auth.test.ts`

Expected: FAIL because FAE routes/navigation do not exist.

- [ ] **Step 3: Add exact route parsing and safe login returns**

Add the seven Route union members. Parse detail routes before collection routes. Use `decodeURIComponent` through the existing `decode()` helper. Extend `safeLoginReturnPath()` with:

```typescript
/^\/admin\/fae(?:\/(?:sessions(?:\/[A-Za-z0-9:._-]+)?|issues(?:\/[0-9a-fA-F-]{36})?|reports(?:\/[A-Za-z0-9._:-]+)?))?$/
```

Malformed encoded paths remain `not-found`.

- [ ] **Step 4: Add the management entry and workbench shell**

Insert `{ label: "FAE 工作台", path: "/admin/fae", section: "admin" }` after `Session` in `ADMIN_NAVIGATION`. Owner/Admin see it; management viewers continue to see only `VOC 管理`.

```tsx
const ITEMS = [
  ["概览", "/admin/fae", "overview"],
  ["Sessions", "/admin/fae/sessions", "sessions"],
  ["问题治理", "/admin/fae/issues", "issues"],
  ["分析报告", "/admin/fae/reports", "reports"],
] as const;

type FaeSection = "overview" | "sessions" | "issues" | "reports";

interface Props {
  currentSection: FaeSection;
  children: React.ReactNode;
}

export function FaeWorkbenchShell({ currentSection, children }: Props) {
  return <section className="fae-workbench">
    <aside className="fae-workbench__sidebar">
      <div><p>AI FAE OPERATIONS</p><h1>FAE 工作台</h1></div>
      <nav aria-label="FAE 工作台">{ITEMS.map(([label, href, section]) =>
        <PlatformLink aria-current={currentSection === section ? "page" : undefined} href={href} key={href}>{label}</PlatformLink>
      )}</nav>
    </aside>
    <main className="fae-workbench__content">{children}</main>
  </section>;
}
```

`FaeWorkbenchShell` is a two-column workspace above 900px: a quiet 216px left rail and a minmax content column. The global management navigation still has only one `FAE 工作台` entry; it does not repeat the four internal views as top-level management tabs. The shell test asserts sidebar/content order, all four links, and detail-route section selection.

- [ ] **Step 5: Implement the strict API client**

`faeWorkbenchApi.listSessions()` serializes only `q`, `channel`, `sentiment`, `review_status`, `outcome`, `date_from`, `date_to`, `limit`, and `offset`. It has no Agent or Source fields.

```typescript
export interface FaeWorkbenchApi {
  overview(signal?: AbortSignal): Promise<FaeOverview>;
  listSessions(query: FaeSessionQuery, signal?: AbortSignal): Promise<Page<SessionSummary>>;
  session(sessionKey: string, signal?: AbortSignal): Promise<SessionDetail>;
}
```

Tests assert an attempted cast containing `agent_id` is not serialized and response parsing rejects missing section state, invalid freshness or non-timezone timestamps.

- [ ] **Step 6: Add the truthful reports placeholder**

```tsx
export function FaeReportsPlaceholderPage() {
  return <FaeWorkbenchShell currentSection="reports">
    <section className="fae-workbench__empty" role="status">
      <h2>分析报告尚未接入</h2>
      <p>Sessions 与问题治理可以正常使用；这里不会用演示数据代替 FAE 的真实分析结果。</p>
    </section>
  </FaeWorkbenchShell>;
}
```

Render the same truthful placeholder for both `/admin/fae/reports` and `/admin/fae/reports/:report_id` during the foundation release. Change the global `AppShell` management-item active check from exact equality to the existing route-section plus path-prefix rule so every `/admin/fae/*` detail route keeps `FAE 工作台` selected; tests cover Session, Issue and report detail paths.

- [ ] **Step 7: Run tests and commit**

Run: `cd webui && npm test -- faeWorkbenchApi.test.ts router.test.ts router.brain.test.ts AppShell.brain.test.tsx auth.test.ts documentTitle.test.tsx`

Expected: PASS.

```bash
git add webui/src/faeWorkbenchTypes.ts webui/src/faeWorkbenchApi.ts webui/src/faeWorkbenchApi.test.ts webui/src/components/fae-workbench webui/src/pages/FaeReportsPlaceholderPage.tsx webui/src/router.ts webui/src/router.test.ts webui/src/router.brain.test.ts webui/src/documentTitle.ts webui/src/documentTitle.test.tsx webui/src/App.tsx webui/src/AppShell.tsx webui/src/AppShell.brain.test.tsx webui/src/auth.ts webui/src/auth.test.ts
git commit -m "feat(fae-workbench): add routes navigation and shell"
```

---

### Task 5: Reuse Session Listing Under the FAE Scope

**Files:**
- Modify: `webui/src/sessionNavigation.ts`
- Modify: `webui/src/sessionNavigation.test.ts`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/pages/SessionsPage.tsx`
- Create: `webui/src/pages/FaeSessionsPage.tsx`
- Create: `webui/src/pages/FaeSessionsPage.test.tsx`
- Modify: `webui/src/pages/SessionsPage.test.tsx`
- Modify: `webui/src/components/SessionListItem.tsx`

**Interfaces:**
- Produces: shared `SessionsView`, `sessionsPath(filters, basePath)`, and `SessionListItem.detailHref`.
- Consumes: `faeWorkbenchApi.listSessions()` and existing generic `fetchSessions()`.

- [ ] **Step 1: Write failing reuse and URL-state tests**

```typescript
window.history.replaceState({}, "", "/admin/fae/sessions?channel=fae&sentiment=negative&page=2");
await act(async () => root.render(<FaeSessionsPage />));

expect(requests.at(-1)).toBe(
  "/api/admin/fae/sessions?channel=fae&sentiment=negative&limit=50&offset=50",
);
expect(container.querySelector('select[name="agent_id"]')).toBeNull();
expect(container.querySelector('select[name="source_kind"]')).toBeNull();
expect(container.querySelector('a.session-row')?.getAttribute("href")).toBe(
  "/admin/fae/sessions/fae%3Asession-1",
);
```

Regression assertion:

```typescript
expect(sessionsPath({ ...EMPTY_SESSION_FILTERS, page: 1 })).toBe("/admin/sessions");
expect(sessionsPath({ ...EMPTY_SESSION_FILTERS, page: 1 }, "/admin/fae/sessions"))
  .toBe("/admin/fae/sessions");
```

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- FaeSessionsPage.test.tsx SessionsPage.test.tsx sessionNavigation.test.ts`

Expected: FAIL because only the generic Session page exists.

- [ ] **Step 3: Extend exact filter state**

Add `channel`, `sentiment`, `review_status`, `outcome`, `date_from`, and `date_to` to `SessionFilters`. Parse only known values; invalid page values canonicalize to 1. `sessionsPath` accepts `basePath="/admin/sessions"` and preserves the existing query order.

- [ ] **Step 4: Extract `SessionsView` without changing generic behavior**

```typescript
interface SessionsViewProps {
  basePath: string;
  title: string;
  description: string;
  fixedScope?: Pick<SessionQuery, "agent_id" | "source_kind">;
  showScopeFilters: boolean;
  load: (query: SessionQuery, signal: AbortSignal) => Promise<Page<SessionSummary>>;
  detailHref: (session: SessionSummary) => string;
}
```

Generic `SessionsPage` passes the current title, filters and `/admin/sessions`. `FaeSessionsPage` passes `showScopeFilters=false`, the scoped API and `/admin/fae/sessions`. `SessionListItem` receives its exact `detailHref`; it no longer hardcodes `/admin/sessions`.

- [ ] **Step 5: Render FAE-relevant filters**

The FAE page renders keyword, channel, Feedback sentiment, Review status, Outcome and date range. It does not invent department, Fallback, latency or Issue-status selectors while their indexed canonical projections are unavailable. Session rows use the existing presentation-safe identity fields when populated and otherwise show no fabricated user name.

- [ ] **Step 6: Run tests and commit**

Run: `cd webui && npm test -- FaeSessionsPage.test.tsx SessionsPage.test.tsx sessionNavigation.test.ts api.test.ts`

Expected: PASS.

```bash
git add webui/src/sessionNavigation.ts webui/src/sessionNavigation.test.ts webui/src/api.ts webui/src/pages/SessionsPage.tsx webui/src/pages/FaeSessionsPage.tsx webui/src/pages/FaeSessionsPage.test.tsx webui/src/pages/SessionsPage.test.tsx webui/src/components/SessionListItem.tsx
git commit -m "feat(fae-workbench): reuse scoped Session listing"
```

---

### Task 6: Build FAE Session Replay with Governance Actions

**Files:**
- Create: `webui/src/components/session/useSessionDetail.ts`
- Create: `webui/src/components/session/SessionReplay.tsx`
- Modify: `webui/src/pages/SessionDetailPage.tsx`
- Create: `webui/src/pages/FaeSessionDetailPage.tsx`
- Create: `webui/src/pages/FaeSessionDetailPage.test.tsx`
- Modify: `webui/src/pages/SessionDetailPage.test.tsx`
- Modify: `webui/src/components/TurnCard.tsx`
- Modify: `webui/src/components/TurnCard.test.tsx`

**Interfaces:**
- Produces: `useSessionDetail(loader, sessionKey, closureMode)`, `SessionReplay`, and per-Turn `governanceHref`.
- Consumes: the scoped Session endpoint from Task 3 and workbench Issue URL from Task 4.

- [ ] **Step 1: Write failing FAE replay tests**

```typescript
await renderFaeSession({ ...session, turns: [turnWithoutFeedback, negativeTurn] });

const actions = [...container.querySelectorAll<HTMLAnchorElement>(".review-entry a")];
expect(actions).toHaveLength(2);
expect(actions[0].getAttribute("href")).toBe(
  "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1",
);
expect(container.querySelector(".fae-session-governance")?.textContent).toContain("数据截止时间");
expect(container.querySelector(".fae-session-governance")?.textContent).toContain("DingTalk");
```

Also assert generic `SessionDetailPage` still links negative Feedback to `/admin/review?agent_id=ai-fae-agent&turn_key=fae%3Aturn-2` and does not show an Issue action for an ordinary Turn.

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- FaeSessionDetailPage.test.tsx SessionDetailPage.test.tsx TurnCard.test.tsx`

Expected: FAIL because replay loading/presentation is not reusable.

- [ ] **Step 3: Extract loading and presentation once**

```typescript
export function useSessionDetail(
  loader: (key: string, signal: AbortSignal) => Promise<SessionDetail>,
  sessionKey: string,
  closureMode: "negative-only" | "all-turns",
): SessionDetailState
```

The hook loads one Session, then batches at most 200 Turn keys per `fetchReviewTurnSummaries` request. Generic mode asks only for negative Turn keys; FAE mode asks for all Turn keys. Abort prevents late state writes.

`SessionReplay` owns the existing header and Turn stack and accepts:

```typescript
interface SessionReplayProps {
  session: SessionDetail;
  closureSummaries: Record<string, TurnClosureSummary>;
  governanceHref?: (turn: TurnDetail) => string | null;
}
```

- [ ] **Step 4: Add the FAE governance side panel**

The panel shows only evidence-backed values: channel, Outcome, Turn count, Feedback count, Review count, `source_synced_at`, freshness and presentation-safe identity fields. Missing identity reads `身份信息暂不可用`; it never falls back to a raw source ID.

- [ ] **Step 5: Make any real Turn actionable**

Add optional `governanceHref` to `TurnCard`. When present, render `创建或查看问题` for every Turn. If a closure summary exists, show its real status; otherwise show `尚未纳管`. Generic behavior remains negative-only.

- [ ] **Step 6: Run tests and commit**

Run: `cd webui && npm test -- FaeSessionDetailPage.test.tsx SessionDetailPage.test.tsx TurnCard.test.tsx`

Expected: PASS.

```bash
git add webui/src/components/session webui/src/pages/SessionDetailPage.tsx webui/src/pages/FaeSessionDetailPage.tsx webui/src/pages/FaeSessionDetailPage.test.tsx webui/src/pages/SessionDetailPage.test.tsx webui/src/components/TurnCard.tsx webui/src/components/TurnCard.test.tsx
git commit -m "feat(fae-workbench): add governed Session replay"
```

---

### Task 7: Expose a Strict FAE Issue Facade and Allow Non-Feedback Turns

**Files:**
- Create: `backend/app/review/http_models.py`
- Modify: `backend/app/review/routes.py`
- Modify: `backend/app/fae_workbench/service.py`
- Modify: `backend/app/fae_workbench/routes.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/tests/test_review_api.py`
- Modify: `backend/tests/test_fae_workbench_service.py`
- Modify: `backend/tests/test_fae_workbench_api.py`
- Modify: `backend/tests/test_r1_authorization.py`

**Interfaces:**
- Produces: scoped Issue reads/writes under `/api/admin/fae`, with the same Review domain state machine.
- Consumes: shared Review HTTP models and Task 1 `fae_turn_exists()`.

- [ ] **Step 1: Write failing non-Feedback and cross-Agent tests**

```python
def test_link_accepts_real_turn_without_feedback(client, repository):
    response = client.post(
        f"/api/admin/fae/issues/{ISSUE_ID}/links",
        json={
            "source_turn_key": "fae:turn-ordinary",
            "source_feedback_keys": [],
            "link_role": "primary",
            "reason": "create from inspected answer",
        },
        headers={"X-Review-Actor": "codex"},
    )
    assert response.status_code == 201
    assert repository.linked_agent_id == "ai-fae-agent"


def test_non_fae_issue_is_hidden_from_fae_facade(client, review_service):
    review_service.detail = admin_issue_detail()
    assert client.get(f"/api/admin/fae/issues/{ISSUE_ID}").status_code == 404


def test_fae_facade_rejects_unknown_turn_before_review_write(client):
    response = client.post(
        f"/api/admin/fae/issues/{ISSUE_ID}/links",
        json={"source_turn_key": "fae:missing", "source_feedback_keys": [], "link_role": "primary", "reason": "inspect"},
        headers={"X-Review-Actor": "codex"},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/test_review_api.py tests/test_fae_workbench_service.py tests/test_fae_workbench_api.py tests/test_r1_authorization.py`

Expected: FAIL because `LinkTurn` requires one Feedback and FAE Issue routes are absent.

- [ ] **Step 3: Move strict request models without behavior change**

Move `StrictModel`, `CreateIssue`, `UpdateIssue`, `LinkTurn`, `MoveLink`, `MergeIssue`, `FixReady`, `AddEvidence`, `VerifyEvidence`, `StartReplay`, `SemanticReview`, and `SetDisposition` into `review/http_models.py`; import them from `review/routes.py` so existing endpoint tests remain green.

Change only this field:

```python
class LinkTurn(StrictModel):
    source_turn_key: str = Field(min_length=1)
    source_feedback_keys: list[str] = Field(default_factory=list)
    link_role: Literal["primary", "secondary"] = "primary"
    reason: str = "turn linked"
```

The FAE facade does not accept `agent_id`; generic `/api/review` keeps it for compatibility.

- [ ] **Step 4: Implement scope checks before every Issue operation**

`FaeWorkbenchService` adds `_fae_issue(issue_id)` and calls it before update, link, move, merge, fix-ready, evidence, replay, semantic review and disposition. It validates the target issue on move/merge too. Create overrides `agent_id`; link overrides `agent_id` and checks `fae_turn_exists()`.

Expose exactly:

```text
GET  /api/admin/fae/issue-overview
GET  /api/admin/fae/issue-inbox
GET  /api/admin/fae/issues
GET  /api/admin/fae/issues/{issue_id}
GET  /api/admin/fae/turn-summaries
POST /api/admin/fae/issues
PATCH /api/admin/fae/issues/{issue_id}
POST /api/admin/fae/issues/{issue_id}/links
POST /api/admin/fae/issues/{issue_id}/links/{link_id}/move
POST /api/admin/fae/issues/{issue_id}/merge
POST /api/admin/fae/issues/{issue_id}/fix-ready
POST /api/admin/fae/issues/{issue_id}/evidence
POST /api/admin/fae/evidence/{evidence_id}/verify
POST /api/admin/fae/issues/{issue_id}/replays
POST /api/admin/fae/replays/{replay_id}/semantic-review
POST /api/admin/fae/issues/{issue_id}/disposition
```

- [ ] **Step 5: Preserve hard-stale and cloud read-only behavior**

Add exact read routes to `_OWNER_ROUTES`; add exact mutation routes to `_FAE_WORKBENCH_MUTATION_ROUTES`. Deny mutations with 503 when directory state is hard-stale and with 403/`cloud_review_read_only` in cloud replica mode, matching `/api/review` behavior.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/pytest -q tests/test_review_api.py tests/test_review_service.py tests/test_fae_workbench_service.py tests/test_fae_workbench_api.py tests/test_r1_authorization.py`

Expected: PASS.

```bash
git add backend/app/review/http_models.py backend/app/review/routes.py backend/app/fae_workbench/service.py backend/app/fae_workbench/routes.py backend/app/control_plane/authorization.py backend/tests/test_review_api.py backend/tests/test_fae_workbench_service.py backend/tests/test_fae_workbench_api.py backend/tests/test_r1_authorization.py
git commit -m "feat(fae-workbench): scope the full Issue closure facade"
```

---

### Task 8: Reuse the Review Workspace in FAE Issue Routes

**Files:**
- Create: `webui/src/components/review/ReviewWorkspace.tsx`
- Modify: `webui/src/pages/ReviewPage.tsx`
- Create: `webui/src/pages/FaeIssuesPage.tsx`
- Create: `webui/src/pages/FaeIssuesPage.test.tsx`
- Modify: `webui/src/pages/ReviewPage.test.tsx`
- Modify: `webui/src/components/review/IssueList.tsx`
- Modify: `webui/src/faeWorkbenchApi.ts`
- Modify: `webui/src/faeWorkbenchApi.test.ts`

**Interfaces:**
- Produces: `ReviewWorkspace` driven by a `ReviewApi` interface and stable FAE Issue detail URLs.
- Consumes: Task 7 Issue facade and Task 6 `session_key`/`turn_key` deep links.

- [ ] **Step 1: Write failing reuse and any-Turn creation tests**

```typescript
window.history.replaceState({}, "", "/admin/fae/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1");
await act(async () => root.render(<FaeIssuesPage account={owner} />));

expect(container.textContent).toContain("普通回答");
expect(container.textContent).toContain("创建事项并纳管");
await click("创建事项并纳管");

const linkRequest = writes.find((item) => item.path.endsWith("/links"));
expect(linkRequest?.body).toMatchObject({
  source_turn_key: "fae:turn-1",
  source_feedback_keys: [],
});
expect(window.location.pathname).toBe("/admin/fae/issues/00000000-0000-0000-0000-000000000001");
```

Regression: existing `ReviewPage` still uses `/api/review/*`, supports `?issue=`, and renders its actor field in legacy identity-disabled mode.

Cloud-replica regression: the FAE Issue workspace renders projected Issue state and `当前为只读副本`, does not render create/update/link/evidence/replay/disposition controls, and does not attempt a mutation request.

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- FaeIssuesPage.test.tsx ReviewPage.test.tsx faeWorkbenchApi.test.ts`

Expected: FAIL because Review behavior is tied to global paths and negative inbox rows.

- [ ] **Step 3: Define the injected Review API**

```typescript
export interface ReviewApi {
  overview(signal?: AbortSignal): Promise<ReviewOverview>;
  inbox(signal?: AbortSignal): Promise<ReviewInboxItem[]>;
  issues(signal?: AbortSignal): Promise<FeedbackIssueSummary[]>;
  turnSummaries(turnKeys: string[], signal?: AbortSignal): Promise<TurnClosureSummary[]>;
  issue(id: string, signal?: AbortSignal): Promise<FeedbackIssueDetail>;
  create(payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  link(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  update(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  move(issueId: string, linkId: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  fixReady(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  merge(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  addEvidence(id: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  verifyEvidence(evidenceId: string, actor: string): Promise<FeedbackIssueDetail>;
  replay(issueId: string, payload: Record<string, unknown>, actor: string): Promise<ReplayRun>;
  semanticReview(replayId: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
  disposition(issueId: string, payload: Record<string, unknown>, actor: string): Promise<FeedbackIssueDetail>;
}
```

Implement every named method in both the generic adapter and `faeWorkbenchApi.review`; do not leave comments in production in place of methods.

- [ ] **Step 4: Extract `ReviewWorkspace` and preserve behavior**

Move state, loading, conflict handling and mutation orchestration from `ReviewPage` into:

```typescript
interface ReviewWorkspaceProps {
  api: ReviewApi;
  agentId: string;
  basePath: string;
  initialIssueId: string | null;
  initialTurn: ReviewInboxItem | null;
  actor: string;
  showActorField: boolean;
  showAgentFilter: boolean;
}
```

`IssueList` accepts `showAgentFilter`; the FAE workspace passes false. FAE `chooseIssue()` navigates to `/admin/fae/issues/:issue_id`; generic Review keeps its current query URL.

- [ ] **Step 5: Resolve a deep-linked real Turn**

When both `session_key` and `turn_key` exist, `FaeIssuesPage` loads the scoped Session, finds the exact Turn, and constructs:

```typescript
const seed: ReviewInboxItem = {
  agent_id: "ai-fae-agent",
  turn_key: turn.turn_key,
  question: turn.question,
  answer: turn.answer,
  feedback_keys: turn.feedback.map((item) => item.feedback_key),
  first_feedback_at: turn.feedback[0]?.created_at ?? turn.created_at,
};
```

If Session or Turn is missing, show an explicit `找不到原始回答` error and do not permit Issue creation.

- [ ] **Step 6: Use the authenticated account as display identity**

Pass `actor={`corp:${account.internal_user_id}`}` and `showActorField=false` for FAE. The backend still derives the authoritative actor from `request.state.auth_context`; the browser value is not trusted. Generic Review preserves its current session-only actor input.

- [ ] **Step 7: Run tests and commit**

Run: `cd webui && npm test -- FaeIssuesPage.test.tsx ReviewPage.test.tsx faeWorkbenchApi.test.ts`

Expected: PASS.

```bash
git add webui/src/components/review/ReviewWorkspace.tsx webui/src/components/review/IssueList.tsx webui/src/pages/ReviewPage.tsx webui/src/pages/ReviewPage.test.tsx webui/src/pages/FaeIssuesPage.tsx webui/src/pages/FaeIssuesPage.test.tsx webui/src/faeWorkbenchApi.ts webui/src/faeWorkbenchApi.test.ts
git commit -m "feat(fae-workbench): reuse full Issue governance workflow"
```

---

### Task 9: Add the Freshness-Aware Overview and Responsive Styling

**Files:**
- Create: `webui/src/pages/FaeOverviewPage.tsx`
- Create: `webui/src/pages/FaeOverviewPage.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Produces: the release-1 overview and final route rendering for all four views.
- Consumes: `faeWorkbenchApi.overview()` and stable links from Tasks 4-8.

- [ ] **Step 1: Write failing overview behavior tests**

```typescript
await renderOverview(freshOverview);
expect(container.textContent).toContain("数据截止 08月31日 17:00");
expect(container.textContent).toContain("12 个 Session");
expect(container.querySelector('a[href="/admin/fae/sessions?sentiment=negative"]')).not.toBeNull();
expect(container.textContent).not.toContain("实时");

await renderOverview({ ...freshOverview, summary: unavailableSection("summary_unavailable") });
expect(container.textContent).toContain("运营摘要暂不可用");
expect(container.textContent).toContain("问题治理");
```

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npm test -- FaeOverviewPage.test.tsx styles.test.ts`

Expected: FAIL because the overview/styling is absent.

- [ ] **Step 3: Render the approved information order**

`FaeOverviewPage` renders:

1. freshness strip with period and `data_as_of`;
2. Session, active subject, negative Turn, abnormal Session, open Issue and p95 latency summary cards;
3. actionable Issue and abnormal Session lists;
4. seven-day Session/negative-Turn trend as accessible HTML/CSS bars with numeric labels;
5. report unavailable preview linking to `/admin/fae/reports`.

Every available card links to a meaningful filtered destination. A null metric renders `暂不可用`, never `0`.

- [ ] **Step 4: Add compact responsive layout**

Use a maximum workspace width of 1440px, a 216px left rail, a content column capped at 1180px, readable 15-16px body text, and a two-column Session detail layout above 1040px. Below 1040px the governance panel stacks after the Session header and before Turns. Below 900px the workbench rail becomes a compact horizontal, keyboard-scrollable navigation above the content; below 720px summary cards become one column. Core actions remain visible without hover.

- [ ] **Step 5: Wire every final route in `App.tsx`**

Map overview, Sessions, Session detail, Issues, Issue detail and reports placeholder to their pages. Pass `account` only where authenticated identity is needed. `viewerRouteAllowed()` must not allow any `admin-fae-*` route.

- [ ] **Step 6: Run frontend verification and commit**

```bash
cd webui
npm test -- FaeOverviewPage.test.tsx FaeSessionsPage.test.tsx FaeSessionDetailPage.test.tsx FaeIssuesPage.test.tsx router.test.ts AppShell.brain.test.tsx styles.test.ts
npm run build
```

Expected: all selected tests PASS and TypeScript/Vite build succeeds.

```bash
git add webui/src/pages/FaeOverviewPage.tsx webui/src/pages/FaeOverviewPage.test.tsx webui/src/App.tsx webui/src/styles.css webui/src/styles.test.ts
git commit -m "feat(fae-workbench): add operational overview"
```

---

### Task 10: Verify Security, Regressions and Release Readiness

**Files:**
- Modify implementation files from Tasks 1-9 only for defects revealed by verification.
- Create: `docs/reviews/2026-08-31-fae-management-workbench-foundation-review.md`
- Modify: `deploy/cloud/accept.sh`
- Modify: `backend/tests/test_cloud_deployment.py`

**Interfaces:**
- Produces: review evidence that the foundation is ready for deployment and that reports remain explicitly unavailable.
- Consumes: all earlier tasks.

- [ ] **Step 1: Run focused backend tests**

```bash
cd backend
.venv/bin/pytest -q \
  tests/test_fae_workbench_repository.py \
  tests/test_fae_workbench_service.py \
  tests/test_fae_workbench_api.py \
  tests/test_review_api.py \
  tests/test_review_service.py \
  tests/test_observability_api.py \
  tests/test_observability_repository.py \
  tests/test_cloud_repository.py \
  tests/test_r1_authorization.py \
  tests/test_control_plane_audit.py \
  tests/test_main.py
```

Expected: zero failures.

- [ ] **Step 2: Run the full backend suite**

Run: `cd backend && .venv/bin/pytest -q`

Expected: zero failures. PostgreSQL-marked tests may skip only when their documented local dependency is absent; record exact skip count.

- [ ] **Step 3: Run frontend tests and build**

```bash
cd webui
npm test
npm run build
```

Expected: zero Vitest failures and successful production build.

- [ ] **Step 4: Perform exact security and content scans**

```bash
rg -n "实时|sample report|demo report|fixture report|force.close|强制关闭" \
  webui/src/pages/Fae* webui/src/components/fae-workbench backend/app/fae_workbench
rg -n "agent_id|source_kind" webui/src/faeWorkbenchApi.ts
git diff --check HEAD~1..HEAD
```

Expected: no “实时” claim, no sample report, no force-close control, and no scope field serialized by the FAE client. Constants and response fields may explain the second scan; no caller-supplied scope may exist.

- [ ] **Step 5: Extend the production cloud acceptance contract**

Add Owner checks for `/admin/fae`, `/admin/fae/sessions`, `/admin/fae/issues`, `GET /api/admin/fae/overview`, `GET /api/admin/fae/sessions?limit=1`, and `GET /api/admin/fae/issues`. Add direct member/viewer 403 checks and one FAE Issue mutation 403 check proving `agent.orbbec.com.cn` remains read-only. Validate the placeholder report page is 200 and contains no fixture data.

```bash
cd backend
.venv/bin/pytest -q tests/test_cloud_deployment.py tests/test_cloud_mode.py tests/test_cloud_repository.py
cd ..
bash -n deploy/cloud/accept.sh
```

Expected: PASS and syntax-valid fail-closed cloud acceptance checks.

- [ ] **Step 6: Review against the approved design**

Record in `docs/reviews/2026-08-31-fae-management-workbench-foundation-review.md`:

- exact commit range and test/build results;
- Owner/Admin allow and member/viewer deny evidence;
- privileged FAE Session Detail read audit and audit-unavailable fail-closed evidence;
- daily-sync freshness evidence;
- one FAE Session with Feedback and one without Feedback entering the same Issue workflow;
- generic Sessions/Review regression evidence;
- explicit statement that report integration remains pending the second plan.

- [ ] **Step 7: Commit review evidence**

```bash
git add backend/tests/test_cloud_deployment.py deploy/cloud/accept.sh docs/reviews/2026-08-31-fae-management-workbench-foundation-review.md
git commit -m "docs(fae-workbench): review foundation release"
```

---

## Design Coverage Map

| Approved design requirement | Implemented by |
|---|---|
| Owner/Admin-only entry and APIs | Tasks 3-4, 7, 9 |
| Dedicated four-view workbench | Tasks 4, 9 |
| FAE-scoped Session list and stable URLs | Tasks 2-5 |
| Protected Session Detail access audit | Tasks 3, 10 |
| Session replay plus governance panel | Task 6 |
| Create/link Issue from any Turn | Tasks 6-8 |
| Existing full Review closure and audit | Tasks 7-8 |
| Freshness-aware operational overview | Tasks 1-3, 9 |
| Partial failure and old-data behavior | Tasks 2, 9 |
| No duplicate Session/Review source | Tasks 1-2, 5-8 |
| No fake reports before integration | Tasks 4, 9-10 |
| Generic management page compatibility | Tasks 5-8, 10 |
