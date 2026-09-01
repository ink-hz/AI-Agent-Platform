# FAE Governance Cockpit and Report Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore production access to real FAE analysis reports and replace the flat feedback ledger with an action-first, lifecycle-aware FAE governance cockpit.

**Architecture:** Keep the existing FAE workbench APIs and durable review projections as the single source of truth. Extend the exact authorization set for report reads, teach the cloud review repository and frontend decoder to trust schema-v1 lifecycle projections, and add an FAE-only presentation variant to the shared review workspace instead of duplicating mutation and evidence logic.

**Tech Stack:** FastAPI, Python 3.11, PostgreSQL read-only replica projections, React 19, TypeScript, Vitest, CSS, pytest.

## Global Constraints

- Do not modify AI ADMIN `/office/*`, the FAE public application, FAE containers, configuration, ports, or inference behavior.
- Use real structured report and review projection data; do not introduce Mock or hard-coded production counts.
- Only `platform_owner` and `platform_admin` may read FAE reports and repair details; unauthenticated/member/viewer access remains denied.
- Cloud mutation controls remain absent and cloud data remains read-only.
- Scope-invalid review projections remain quarantined and never enter business totals.
- Default Issue page size is exactly 20; server-side pagination remains authoritative.
- Work in the isolated `fix/fae-governance-cockpit` branch, merge locally, and push only `master`.

---

### Task 1: Authorize Exact FAE Report Reads

**Files:**
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/tests/test_r1_authorization.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

**Interfaces:**
- Consumes: `AuthorizationService.decide(context, method, route_template, agent_ids)`.
- Produces: exact read decisions for `/api/admin/fae/reports`, `/api/admin/fae/reports/latest`, and `/api/admin/fae/reports/{report_id}`.

- [ ] **Step 1: Write the failing authorization matrix test**

Add an exact `FAE_REPORT_READ_ROUTES` tuple and assert owner/admin/hard-stale owner/admin are allowed while member/viewer are denied:

```python
FAE_REPORT_READ_ROUTES = (
    ("GET", "/api/admin/fae/reports"),
    ("GET", "/api/admin/fae/reports/latest"),
    ("GET", "/api/admin/fae/reports/{report_id}"),
)

@pytest.mark.parametrize("method,route", FAE_REPORT_READ_ROUTES)
def test_fae_report_reads_allow_management_roles_and_hard_stale(method, route):
    service = AuthorizationService(Grants(), cloud_mode=True)
    assert service.decide(None, method, route, ()).status_code == 401
    assert service.decide(MEMBER, method, route, ()).status_code == 403
    assert service.decide(VIEWER, method, route, ()).status_code == 403
    for context in (OWNER, ADMIN, STALE_OWNER, STALE_ADMIN):
        assert service.decide(context, method, route, ()).allowed is True
```

- [ ] **Step 2: Run the exact test and verify RED**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q \
  backend/tests/test_r1_authorization.py -k fae_report_reads
```

Expected: FAIL because the three route templates are absent from `_FAE_WORKBENCH_READ_ROUTES`.

- [ ] **Step 3: Add the minimal exact route entries**

Extend `_FAE_WORKBENCH_READ_ROUTES`:

```python
    ("GET", "/api/admin/fae/reports"),
    ("GET", "/api/admin/fae/reports/latest"),
    ("GET", "/api/admin/fae/reports/{report_id}"),
```

Add a middleware integration test proving the real route snapshot resolves `/latest` before `/{report_id}` and still returns 403 for member/viewer.

- [ ] **Step 4: Run focused authorization tests and verify GREEN**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q \
  backend/tests/test_r1_authorization.py \
  backend/tests/test_dingtalk_auth_api.py \
  backend/tests/test_fae_report_api.py
```

Expected: all selected tests pass with zero failures.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/control_plane/authorization.py \
  backend/tests/test_r1_authorization.py backend/tests/test_dingtalk_auth_api.py
git commit -m "fix(fae-reports): authorize exact management reads"
```

### Task 2: Make Cloud Lifecycle Projection Authoritative

**Files:**
- Modify: `backend/app/cloud_replica/management_repository.py`
- Modify: `backend/tests/test_cloud_replica_management_repository.py`
- Modify: `backend/tests/test_fae_workbench_api.py`

**Interfaces:**
- Consumes: schema-v1 `review_issue_projection.progress` dictionaries.
- Produces: `ReplicaReviewRepository.overview(...)["statuses"]`, `lifecycle_status_available`, lifecycle filtering, and stable action-priority ordering.

- [ ] **Step 1: Write failing lifecycle aggregate and filter tests**

Create projections containing `pending_triage`, `awaiting_replay`, `closed`, a P1 fixing issue, and one invalid-scope issue. Assert:

```python
overview = repository.overview(agent_id="ai-fae-agent")
assert overview["statuses"] == {
    "pending_triage": 1,
    "fixing": 1,
    "awaiting_replay": 1,
    "closed": 1,
}
assert overview["lifecycle_status_available"] is True
assert overview["quarantined_issue_count"] == 1

page = repository.list_issue_page(
    agent_id="ai-fae-agent", status="awaiting_replay", limit=20, offset=0,
)
assert [item["progress"]["status"] for item in page["items"]] == ["awaiting_replay"]
```

Add an ordering assertion that a P1 `fixing` issue sorts before a P2 `pending_triage` issue and both sort before closed rows in the unfiltered page.

- [ ] **Step 2: Run repository tests and verify RED**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q \
  backend/tests/test_cloud_replica_management_repository.py -k 'lifecycle or action_priority'
```

Expected: FAIL because overview currently copies disposition into statuses and lifecycle filters return no rows.

- [ ] **Step 3: Implement lifecycle extraction, filtering, and stable sorting**

Add focused helpers:

```python
_LIFECYCLE_ORDER = {
    "awaiting_replay": 0,
    "awaiting_review": 1,
    "awaiting_deploy": 2,
    "awaiting_merge": 3,
    "fixing": 4,
    "pending_triage": 5,
    "closed": 6,
}
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

def _lifecycle(issue: dict) -> str:
    progress = issue.get("progress")
    return str(progress.get("status")) if isinstance(progress, dict) else "unknown"
```

Use only `_valid_issues()` for totals. Return `lifecycle_status_available=True` only when every valid record has `detail_schema_version == 1` and a recognized lifecycle. For an older projection, preserve fail-closed unavailable semantics.

For `status=open`, retain actionable rows whose lifecycle is not closed. For a concrete lifecycle, match `_lifecycle(issue)`. Apply all filters before pagination and sort by priority, lifecycle action rank, descending `updated_at`, then `record_key`.

- [ ] **Step 4: Verify backend GREEN**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q \
  backend/tests/test_cloud_replica_management_repository.py \
  backend/tests/test_fae_workbench_api.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/cloud_replica/management_repository.py \
  backend/tests/test_cloud_replica_management_repository.py \
  backend/tests/test_fae_workbench_api.py
git commit -m "feat(fae): project repair lifecycle into governance queues"
```

### Task 3: Decode Detailed Cloud Lifecycle and Use 20-Row Pages

**Files:**
- Modify: `webui/src/faeWorkbenchApi.ts`
- Modify: `webui/src/faeWorkbenchApi.test.ts`
- Modify: `webui/src/pages/FaeIssuesPage.tsx`
- Modify: `webui/src/pages/FaeIssuesPage.test.tsx`
- Modify: `webui/src/types.ts`

**Interfaces:**
- Consumes: backend `ReviewOverview` with `lifecycle_status_available`, `quarantined_issue_count`, and schema-v1 Issue `progress`.
- Produces: trusted `ReviewOverview.statuses`, detailed `FeedbackIssueSummary.progress`, and API requests with `limit=20`.

- [ ] **Step 1: Write failing frontend contract tests**

Assert a schema-v1 read-only projection keeps the real lifecycle instead of becoming unknown:

```typescript
expect(await api.issues()).toMatchObject({
  items: [{ progress: { status: "awaiting_replay", missing_gates: ["semantic_review"] } }],
});
expect(await api.overview()).toMatchObject({
  lifecycle_status_available: true,
  statuses: { pending_triage: 78, awaiting_replay: 1, closed: 6 },
  quarantined_issue_count: 7,
});
```

In `FaeIssuesPage.test.tsx`, assert the initial URL contains `limit=20&status=open` and page 2 uses `limit=20&offset=20`.

- [ ] **Step 2: Run frontend contract tests and verify RED**

Run:

```bash
cd webui
npm test -- --run src/faeWorkbenchApi.test.ts src/pages/FaeIssuesPage.test.tsx
```

Expected: FAIL because `normalizeOverview` infers projected lifecycle as unavailable and FAE requests use limit 200.

- [ ] **Step 3: Implement the minimal decoder and query changes**

Extend `ReviewOverview`:

```typescript
quarantined_issue_count?: number;
```

Read the server’s explicit `lifecycle_status_available` boolean. Preserve real status and gate fields whenever `replica_read_only === true && detail_schema_version === 1`; only legacy projected rows downgrade to unknown.

Set the FAE page default to `status=open`, `limit: 20`, and offset `(page - 1) * 20`. Keep existing safe URL normalization and deep links.

- [ ] **Step 4: Run frontend contract tests and verify GREEN**

Run the same Vitest command. Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add webui/src/faeWorkbenchApi.ts webui/src/faeWorkbenchApi.test.ts \
  webui/src/pages/FaeIssuesPage.tsx webui/src/pages/FaeIssuesPage.test.tsx \
  webui/src/types.ts
git commit -m "fix(fae): preserve projected repair lifecycle"
```

### Task 4: Build the Action-First FAE Governance Cockpit

**Files:**
- Modify: `webui/src/components/review/ReviewWorkspace.tsx`
- Modify: `webui/src/components/review/ReviewWorkspace.test.tsx`
- Modify: `webui/src/components/review/IssueList.tsx`
- Create: `webui/src/components/review/IssueList.test.tsx`
- Modify: `webui/src/pages/FaeIssuesPage.tsx`
- Modify: `webui/src/pages/FaeIssuesPage.test.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/cloudMode.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `ReviewWorkspaceProps`, real lifecycle overview, paginated Issue page, deployment context.
- Produces: `presentation="fae-governance"` without changing default `/admin/review` behavior.

- [ ] **Step 1: Write failing cockpit rendering tests**

Render `FaeIssuesPage` with cloud data and assert:

```typescript
expect(container.textContent).toContain("反馈与修复");
expect(container.textContent).toContain("待分诊");
expect(container.textContent).toContain("处理中");
expect(container.textContent).toContain("待复跑");
expect(container.textContent).toContain("已闭环");
expect(container.textContent).toContain("需要行动");
expect(container.textContent).not.toContain("Feedback Repair Ledger");
expect(container.textContent).not.toContain("生命周期状态暂不可用");
expect(container.querySelector(".review-issue-list")?.textContent).not.toContain("ai-fae-agent");
expect(container.querySelector(".fae-governance-readonly")).not.toBeNull();
```

Assert the generic `/admin/review` `ReviewWorkspace` retains its existing copy and filters. Assert the AppShell global replica banner is hidden only for `admin-fae-issues`, not for other admin pages.

- [ ] **Step 2: Run the cockpit tests and verify RED**

Run:

```bash
cd webui
npm test -- --run src/pages/FaeIssuesPage.test.tsx \
  src/components/review/ReviewWorkspace.test.tsx \
  src/components/review/IssueList.test.tsx src/cloudMode.test.tsx
```

Expected: FAIL on the new copy, KPI grouping, agent-label removal, and compact replica status.

- [ ] **Step 3: Add an FAE-only presentation contract**

Extend `ReviewWorkspaceProps` with:

```typescript
presentation?: "default" | "fae-governance";
replicaStatus?: { freshness: "current" | "stale" | "unavailable"; lastSuccessAt: string | null };
```

Pass `presentation="fae-governance"` and deployment freshness from `FaeIssuesPage`. In the FAE variant:

- render Chinese title and subtitle;
- render four KPI cards using grouped lifecycle counts;
- render feedback/negative/quarantine totals in a compact scope line;
- render the read-only state as a small `.fae-governance-readonly` badge;
- render an actionable empty-detail summary;
- pass `showAgentIdentity={false}` to `IssueList`;
- use lifecycle labels, not dispositions, for schema-v1 cloud rows.

Keep every mutation handler and evidence detail component unchanged.

- [ ] **Step 4: Apply the cockpit CSS without changing generic review styles**

Scope new rules under `.review-workspace.is-fae-governance`, `.fae-governance-hero`, `.fae-governance-summary`, and `.fae-governance-scope`. Use a 38/62 desktop grid, two-line title clamping, 20-row scroll-free pagination, and a one-column mobile layout. Do not replace global design tokens or add a dependency.

- [ ] **Step 5: Run cockpit tests and verify GREEN**

Run the same Vitest command. Expected: all selected tests pass and generic ReviewWorkspace assertions remain unchanged.

- [ ] **Step 6: Commit Task 4**

```bash
git add webui/src/components/review/ReviewWorkspace.tsx \
  webui/src/components/review/ReviewWorkspace.test.tsx \
  webui/src/components/review/IssueList.tsx \
  webui/src/components/review/IssueList.test.tsx \
  webui/src/pages/FaeIssuesPage.tsx webui/src/pages/FaeIssuesPage.test.tsx \
  webui/src/AppShell.tsx webui/src/cloudMode.test.tsx webui/src/styles.css
git commit -m "feat(fae): redesign repair workbench around actions"
```

### Task 5: Give FAE Reports Contextual Error States

**Files:**
- Modify: `webui/src/faeReportApi.ts`
- Modify: `webui/src/pages/FaeReportsPage.tsx`
- Modify: `webui/src/pages/FaeReportsPage.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: HTTP status from report GETs.
- Produces: `FaeReportApiError.status` and distinct empty/unavailable report states.

- [ ] **Step 1: Write failing report state tests**

Add tests for:

```typescript
vi.spyOn(faeReportApi, "latest").mockRejectedValue(new FaeReportApiError(404));
expect(container.textContent).toContain("尚无已发布的分析报告");

vi.spyOn(faeReportApi, "latest").mockRejectedValue(new FaeReportApiError(503));
expect(container.textContent).toContain("分析报告读取失败");
expect(container.textContent).toContain("重新尝试");
```

- [ ] **Step 2: Run report page tests and verify RED**

Run `cd webui && npm test -- --run src/pages/FaeReportsPage.test.tsx`.

Expected: FAIL because report failures currently collapse into the generic `ErrorState`.

- [ ] **Step 3: Implement typed API errors and contextual states**

Add:

```typescript
export class FaeReportApiError extends Error {
  constructor(public readonly status: number) {
    super(`FAE report API ${status}`);
  }
}
```

Track the error status in `FaeReportsPage`. Render a truthful empty state for 404 and a report-specific alert with retry for other failures. Keep successful report rendering unchanged.

- [ ] **Step 4: Run report tests and verify GREEN**

Run the same Vitest command. Expected: all report page tests pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add webui/src/faeReportApi.ts webui/src/pages/FaeReportsPage.tsx \
  webui/src/pages/FaeReportsPage.test.tsx webui/src/styles.css
git commit -m "fix(fae-reports): explain report availability states"
```

### Task 6: Full Verification, Merge, Deploy, and Real-Data Acceptance

**Files:**
- Verify without planned modification: `deploy/cloud/accept.sh`
- Verify without planned modification: `backend/tests/test_cloud_deployment.py`

**Interfaces:**
- Consumes: all prior commits.
- Produces: one verified local master commit, pushed `origin/master`, deployed immutable production release, and real-data evidence.

- [ ] **Step 1: Run the full local verification gate**

Run:

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q backend/tests
cd webui && npm test -- --run && npm run build
cd .. && git diff --check
bash -n deploy/cloud/*.sh
```

Expected: backend and frontend report zero failures, production build exits 0, shell syntax and diff checks exit 0.

- [ ] **Step 2: Merge locally and push only master**

From the primary repository, preserve existing untracked files, merge `fix/fae-governance-cockpit` with `--no-ff`, verify `git diff origin/master..master`, then push `master:master`. Do not push the feature branch.

- [ ] **Step 3: Deploy from a clean worktree**

Use the owner-only mode-0600 deploy configuration:

```bash
deploy/cloud/deploy.sh \
  "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"
```

Expected exact success marker:

```text
CLOUD_PLATFORM_DEPLOY_OK release=<40-character master SHA> mode=dingtalk
```

- [ ] **Step 4: Push a fresh real replica generation**

Use the existing mode-0600 sync configuration and `deploy/cloud/push-replica.sh`. Expected marker: `REPLICA_PUSH_OK sequence=<integer>`.

- [ ] **Step 5: Verify production through the API container without exposing identity secrets**

Construct `ReplicaReviewRepository` and `ReplicaFaeReportRepository` inside the healthy production API container and assert only aggregate facts:

```python
assert report_metrics["value.observed_included_sessions"] == 692
assert report_metrics["value.observed_included_turns"] == 1492
assert report_metrics["quality.reviewed_count"] == 654
assert issue_page["total"] == 87
assert overview["statuses"]["pending_triage"] == 78
assert overview["statuses"]["awaiting_replay"] == 1
assert overview["statuses"]["closed"] == 6
assert overview["quarantined_issue_count"] == 7
```

Also assert one schema-v1 Issue detail exposes available `links`, `evidence`, `replays`, and `events` arrays. Do not print titles, identities, raw answers, cookies, or encrypted payloads.

- [ ] **Step 6: Verify protected public routes and invariants**

Assert unauthenticated `/admin/fae/issues`, `/admin/fae/reports`, `/api/admin/fae/issues`, and `/api/admin/fae/reports/latest` return 401. Confirm production release pointer equals pushed master and API health is healthy. Reuse deploy evidence to prove `/office/*` and FAE managed files/containers were unchanged.

- [ ] **Step 7: Record final evidence and clean only task-owned worktrees**

Report release SHA, test counts, replica sequence, report metrics, lifecycle totals, and route status. Remove only the task-owned clean deploy worktree after verification; leave all pre-existing untracked files untouched.
