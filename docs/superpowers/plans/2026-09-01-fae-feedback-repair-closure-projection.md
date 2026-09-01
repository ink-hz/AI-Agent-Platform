# FAE Feedback Repair Closure Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production FAE workbench show the real feedback-to-repair evidence chain from its encrypted read-only cloud replica while quarantining invalid historical records and preserving FAE, Office, and cloud write boundaries.

**Architecture:** Extend the existing `review_issue_projection` with versioned, sanitized nested closure data. The exporter reads Issues, links, evidence, replays, and the latest previous lifecycle state in one repeatable-read snapshot; the cloud repository serves only `scope_valid=true` records through the existing Review Workspace. The source Review domain owns lifecycle calculation, the cloud remains read-only, and legacy summary-only projections remain readable with explicit unavailable sections.

**Tech Stack:** Python 3.11, dataclasses, psycopg 3, PostgreSQL JSON projections, FastAPI, React 19, TypeScript 5.6, Vitest 3.2, pytest 9.

## Global Constraints

- Work only in `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/fae-feedback-closure-view` until merge review.
- Do not modify AI-FAE-Agent, AI ADMIN, `/office/*`, `fae.orbbec.com.cn`, or their data stores.
- Do not create a second Review state machine or copy the FAE `/app/review` UI.
- Do not export `user_identity`, provider user IDs, native Session IDs, attachments, source context, raw Provider payloads, or full audit events.
- Preserve existing question and answer fidelity for authorized Owner/Admin readers after credential stripping; stronger PII redaction is not part of this release.
- Only projections with `scope_valid is True` are readable. Mixed valid/invalid data serves valid rows; all-invalid data fails closed; no Issue rows is a normal empty set.
- The cloud repository remains read-only and exposes no mutation methods.
- Legacy summary-only projections remain readable and mark `links`, `evidence`, `replays`, and `events` unavailable.
- The importer-compatible release must be deployed before the detailed exporter is allowed to publish.
- Use TDD for every behavior change and make one focused local commit per task.

---

## File and Boundary Map

### Review domain

- Create `backend/app/review/progress_projection.py` for the pure raw-detail-to-`IssueProgress` conversion.
- Modify `backend/app/review/repository.py` so the writable/local repository calls that pure function.
- Create `backend/tests/test_review_progress_projection.py` and extend `backend/tests/test_review_repository.py` for parity.

### Cloud exporter and importer

- Modify `backend/app/cloud_replica/models.py` with typed link, evidence, replay, and progress projections.
- Modify `backend/app/cloud_replica/source.py` with bulk snapshot queries and deterministic assembly.
- Modify `backend/app/cloud_replica/sanitize.py` with nested text sanitization and stable identifier derivation.
- Modify `backend/app/cloud_replica/store.py` to accept exactly the legacy or version-1 detailed shapes and reject unknown nested keys.
- Extend `backend/tests/test_cloud_source.py`, `test_cloud_sanitizer.py`, and `test_cloud_store.py`.

### Cloud reader and FAE facade

- Modify `backend/app/cloud_replica/management_repository.py` to quarantine invalid rows and reconstruct full read-only details.
- Modify `backend/app/fae_workbench/models.py` and `backend/app/fae_workbench/service.py` to expose a safe quarantined-row count.
- Extend `backend/tests/test_cloud_management_repository.py`, `test_fae_workbench_service.py`, and `test_fae_workbench_api.py`.

### Web UI

- Modify `webui/src/faeWorkbenchApi.ts` so read-only is a permission fact rather than a reason to erase available detail.
- Modify `webui/src/types.ts` and `webui/src/components/review/ReviewWorkspace.tsx` for quarantine metadata.
- Modify `webui/src/components/review/IssueDetail.tsx` for the original Session link and explicit unavailable audit copy.
- Modify FAE navigation, overview copy, page title, and their tests from “问题治理” to “反馈与修复”.

### Release proof

- Modify `deploy/cloud/accept.sh` and `docs/runbooks/cloud-platform.md` with detailed-projection compatibility, mixed-scope, page, rollback, and FAE/Office invariance checks.
- Create `docs/reviews/2026-09-01-fae-feedback-repair-closure-projection-review.md` only after real acceptance evidence exists.

---

### Task 1: Extract the Single Review Progress Projection Function

**Files:**
- Create: `backend/app/review/progress_projection.py`
- Modify: `backend/app/review/repository.py`
- Create: `backend/tests/test_review_progress_projection.py`
- Modify: `backend/tests/test_review_repository.py`

**Interfaces:**
- Produces: `progress_from_detail(detail: Mapping[str, Any]) -> IssueProgress`.
- Consumes: raw Issue, link, evidence, replay, and event mappings already returned by `PsycopgReviewRepository._load_issue_detail()`.

- [ ] **Step 1: Write failing pure-function and repository parity tests**

```python
def test_progress_from_detail_preserves_all_hard_gates():
    detail = closed_issue_detail_fixture()
    progress = progress_from_detail(detail)
    assert progress.status == "closed"
    assert progress.missing_gates == ()
    assert progress.replay_passed_turns == 2
    assert progress.replay_required_turns == 2
    assert progress.reopened is False


def test_repository_and_pure_projection_are_identical(review_repository):
    detail = review_repository.get_issue_detail(ISSUE_ID)
    assert detail["progress"] == progress_from_detail(detail)
```

Cover actionable, duplicate, not-actionable, missing merge, missing deployment, runtime failure, semantic-review failure, and reopened-from-closed fixtures.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q \
  tests/test_review_progress_projection.py \
  tests/test_review_repository.py
```

Expected: FAIL because `app.review.progress_projection` does not exist.

- [ ] **Step 3: Move the raw-detail conversion into the pure module**

Implement the existing `_calculate_detail_progress` behavior without database access:

```python
def progress_from_detail(detail: Mapping[str, Any]) -> IssueProgress:
    raw_issue = detail["issue"]
    evidence = detail["evidence"]
    verified_merges = [
        item for item in evidence
        if item["evidence_type"] == "merge"
        and item["verification_status"] == "verified"
    ]
    deployments = [
        item for item in evidence
        if item["evidence_type"] == "deployment"
        and item["verification_status"] == "verified"
        and bool((item.get("verification_details") or {}).get("contains_merge"))
    ]
    latest_deployment = deployments[-1] if deployments else None
    deployment_details = (
        latest_deployment.get("verification_details") or {}
        if latest_deployment else {}
    )
    deployment_sha = deployment_details.get("deployment_sha", "")
    deployment_at = latest_deployment.get("observed_at") if latest_deployment else None
    previous_status = next(
        (
            (event.get("after") or {}).get("status")
            for event in reversed(detail["events"])
            if (event.get("after") or {}).get("status")
        ),
        None,
    )
    issue = IssueRecord(
        id=raw_issue["id"], agent_id=raw_issue["agent_id"],
        title=raw_issue["title"], priority=raw_issue["priority"],
        failure_layer=raw_issue["failure_layer"],
        secondary_layers=tuple(raw_issue["secondary_layers"] or ()),
        root_cause=raw_issue["root_cause"],
        impact_scope=raw_issue["impact_scope"], owner=raw_issue["owner"],
        fix_ready=raw_issue["fix_ready_at"] is not None,
        verified_merge=bool(verified_merges),
        verified_deployment=bool(deployments), merge_ancestor=bool(deployments),
        disposition=raw_issue["disposition"], previous_status=previous_status,
        row_version=int(raw_issue["row_version"]),
    )
    latest_by_link = {
        replay["issue_link_id"]: replay for replay in detail["replays"]
    }
    links = []
    for raw_link in detail["links"]:
        replay = latest_by_link.get(raw_link["id"])
        deployed_replay = bool(
            replay and deployment_sha
            and replay["actual_git_sha"] == deployment_sha
            and deployment_at is not None
            and replay["started_at"] >= deployment_at
        )
        echo = ((replay or {}).get("done") or {}).get("loop", {}).get(
            "provider_model_echo", {}
        )
        links.append(LinkGate(
            id=raw_link["id"], active=bool(raw_link["active"]),
            link_role=raw_link["link_role"],
            runtime_gate_passed=bool(
                replay and replay["runtime_gate"] == "passed" and deployed_replay
            ),
            runtime_failure_reason=(replay or {}).get("runtime_failure_reason", ""),
            build_identity_matches=(
                None if replay is None else replay["actual_git_sha"] == deployment_sha
            ),
            model_echo_available=(
                None if replay is None else bool(
                    echo.get("complete") and echo.get("consistent")
                    and replay["actual_model"]
                )
            ),
            actual_model_matches=(
                None if replay is None else bool(
                    replay["actual_model"]
                    and replay["actual_model"] == replay["configured_model"]
                )
            ),
            semantic_verdict=(replay or {}).get("semantic_verdict", "pending"),
            review_method=(replay or {}).get("review_method"),
            reviewer=(replay or {}).get("reviewer"),
            review_reason=(replay or {}).get("review_reason", ""),
        ))
    return calculate_progress(issue, links)
```

Keep `calculate_progress()` in `review/state.py` unchanged. Replace the body of `PsycopgReviewRepository._calculate_detail_progress()` with `return progress_from_detail(detail)` so existing callers and tests retain their public seam.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2.

Expected: all selected tests pass with zero failures.

- [ ] **Step 5: Commit**

```bash
git add backend/app/review/progress_projection.py \
  backend/app/review/repository.py \
  backend/tests/test_review_progress_projection.py \
  backend/tests/test_review_repository.py
git commit -m "refactor(review): centralize closure progress projection"
```

---

### Task 2: Read Complete Closure Facts in One Source Snapshot

**Files:**
- Modify: `backend/app/cloud_replica/models.py`
- Modify: `backend/app/cloud_replica/source.py`
- Modify: `backend/tests/test_cloud_source.py`

**Interfaces:**
- Produces: `ReviewIssueProjection` with `detail_schema_version=1`, typed nested `links`, `evidence`, `replays`, and a serialized `IssueProgress` projection.
- Consumes: `progress_from_detail()` from Task 1 and the existing read-only `platform_read`/`platform_review` views.

- [ ] **Step 1: Write failing bulk-query and projection tests**

```python
def test_management_source_assembles_complete_review_issue_projection():
    source = ReplicaSource("postgres://readonly", connection_factory=fake_snapshot())
    issue = next(
        item for item in source.fetch_management_projections(through=NOW)
        if isinstance(item, ReviewIssueProjection)
    )
    assert issue.detail_schema_version == 1
    assert issue.origin_turn_key == "fae-turn-7"
    assert issue.links[0].source_session_key == "fae-session-3"
    assert issue.links[0].source_answer == "原始回答"
    assert issue.evidence[0].verification_status == "verified"
    assert issue.replays[0].semantic_verdict == "passed"
    assert issue.progress.status == "closed"


def test_management_source_uses_five_bulk_queries_not_n_plus_one():
    connection = counting_snapshot(issue_count=25)
    ReplicaSource("postgres://readonly", connection_factory=lambda *_a, **_k: connection) \
        .fetch_management_projections(through=NOW)
    assert connection.review_query_names == {
        "issues", "links", "evidence", "replays", "previous_statuses"
    }
```

Also prove all queries execute inside the same repeatable-read transaction, links join `platform_read.turns`, and an evidence/replay ownership mismatch sets only that Issue's `scope_valid` to false.

- [ ] **Step 2: Run the tests and verify RED**

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_cloud_source.py
```

Expected: FAIL because source projections contain summary fields only.

- [ ] **Step 3: Add typed nested projection models**

Add frozen dataclasses with exact bounded fields:

```python
@dataclass(frozen=True, slots=True)
class ReviewIssueLinkProjection:
    id: UUID
    active: bool
    link_role: str
    source_turn_key: str
    source_session_key: str | None
    source_feedback_keys: tuple[str, ...]
    source_trace_key: str | None
    source_turn_index: int | None
    source_created_at: datetime | None
    source_question: str | None
    source_answer: str | None
    source_outcome: str | None
    source_fallback_used: bool | None
```

Define equally explicit `ReviewFixEvidenceProjection` and `ReviewReplayProjection`. Extend `ReviewIssueProjection` with `origin_turn_key`, `secondary_layers`, `root_cause`, `impact_scope`, `fix_ready`, `progress`, nested tuples, and `detail_schema_version: int = 1`.

- [ ] **Step 4: Add bulk SQL and deterministic assembly**

Keep `REVIEW_ISSUE_SQL` as the canonical scope check. Add four bulk SQL constants for links, evidence, replays, and latest previous lifecycle status. Execute them once each with all Issue IDs, group by `issue_id`, build a raw detail mapping, call `progress_from_detail()`, and preserve ordering by source timestamps plus stable IDs.

Do not select `source_context`, attachment content, native Session IDs, `user_identity`, or full event bodies.

- [ ] **Step 5: Run tests and commit**

Run the command from Step 2; expect zero failures.

```bash
git add backend/app/cloud_replica/models.py \
  backend/app/cloud_replica/source.py \
  backend/tests/test_cloud_source.py
git commit -m "feat(cloud-replica): export FAE closure detail facts"
```

---

### Task 3: Sanitize and Strictly Import the Detailed Projection

**Files:**
- Modify: `backend/app/cloud_replica/sanitize.py`
- Modify: `backend/app/cloud_replica/store.py`
- Modify: `backend/tests/test_cloud_sanitizer.py`
- Modify: `backend/tests/test_cloud_store.py`

**Interfaces:**
- Produces: one encrypted `review_issue_projection` record in either legacy summary shape or `detail_schema_version=1` shape.
- Consumes: Task 2 dataclasses, existing `sanitize_text()`, `stable_id()`, `FieldCipher`, and the exact management-key allowlist.

- [ ] **Step 1: Write failing privacy, stable-ID, and strict-shape tests**

```python
def test_detailed_issue_projection_is_sanitized_and_pseudonymized(policy, identity_key):
    record = sanitize_management_projection(detailed_issue(), policy, identity_key)
    assert record["detail_schema_version"] == 1
    assert record["links"][0]["source_session_key"] != "native-session"
    assert record["links"][0]["source_turn_key"] != "native-turn"
    assert "Bearer secret-token" not in json.dumps(record, ensure_ascii=False)
    assert record["evidence"][0]["url"] == ""
    assert record["evidence"][0]["verification_details"] == {}
    assert "source_context" not in record["links"][0]
    assert "attachment_manifest" not in record["replays"][0]


def test_store_accepts_legacy_and_v1_detail_but_rejects_unknown_nested_key(store):
    store.prepare_management(legacy_issue_record())
    store.prepare_management(detailed_issue_record())
    invalid = detailed_issue_record()
    invalid["links"][0]["raw_customer_id"] = "customer-7"
    with pytest.raises(ReplicaStoreError, match="record_invalid"):
        store.prepare_management(invalid)
```

Also test stable hashes for Feedback, Link, Evidence, Replay, and Trace IDs; invalid enum values; non-list nested fields; malformed timestamps; oversized text already rejected by batch record limits; and full plaintext absence after encryption.

- [ ] **Step 2: Run and verify RED**

```bash
cd backend
.venv/bin/python -m pytest -q \
  tests/test_cloud_sanitizer.py \
  tests/test_cloud_store.py
```

Expected: FAIL because detailed fields are unsupported.

- [ ] **Step 3: Implement nested sanitization**

Use `sanitize_text()` for title, root cause, impact, question, answer, reference, review reason, and runtime failure reason. Use `_safe_identifier()` or explicit allowlists for enums. Derive stable IDs with domain-separated labels such as `review-link`, `review-evidence`, `review-replay`, `turn`, `session`, `feedback`, and `trace`.

Return `url=""`, `verification_details={}`, omit full events, and include:

```python
"detail_schema_version": 1,
"progress": {
    "status": projection.progress.status,
    "missing_gates": list(projection.progress.missing_gates),
    "replay_passed_turns": projection.progress.replay_passed_turns,
    "replay_required_turns": projection.progress.replay_required_turns,
    "reopened": projection.progress.reopened,
},
```

- [ ] **Step 4: Implement exact legacy-or-v1 store validation**

Keep the current legacy key set as `_LEGACY_REVIEW_ISSUE_KEYS`. Define `_DETAIL_REVIEW_ISSUE_KEYS` and exact nested key sets. `prepare_management()` accepts only one of those top-level shapes; for version 1 it checks `detail_schema_version == 1`, all nested values, safe timestamps, safe IDs, and exact nested key equality before encryption.

Do not bump the batch schema version: the same record kind is extended with an explicit detail schema, and the release order guarantees the new importer is deployed before the exporter. This avoids colliding with the separately planned FAE analysis-report protocol bump.

- [ ] **Step 5: Run tests and commit**

Run the command from Step 2; expect zero failures.

```bash
git add backend/app/cloud_replica/sanitize.py \
  backend/app/cloud_replica/store.py \
  backend/tests/test_cloud_sanitizer.py \
  backend/tests/test_cloud_store.py
git commit -m "feat(cloud-replica): secure FAE closure detail records"
```

---

### Task 4: Reconstruct Read-Only Details and Quarantine Invalid Rows

**Files:**
- Modify: `backend/app/cloud_replica/management_repository.py`
- Modify: `backend/tests/test_cloud_management_repository.py`

**Interfaces:**
- Produces: full `get_issue_detail()`, valid-only list/overview/turn summaries, and `issue_scope_health(agent_id) -> {total, readable, quarantined, status}`.
- Consumes: decrypted legacy and version-1 records from `_ProjectionReader._records()`.

- [ ] **Step 1: Write failing full-detail and mixed-scope tests**

```python
def test_detailed_projection_returns_real_read_only_closure(repository):
    detail = repository.get_issue_detail(ISSUE_ID)
    assert detail["issue"]["root_cause"] == "证据覆盖不足"
    assert detail["links"][0]["source_answer"] == "原始回答"
    assert detail["evidence"][0]["verification_status"] == "verified"
    assert detail["replays"][0]["semantic_verdict"] == "passed"
    assert detail["availability"] == {
        "links": "resolved", "evidence": "resolved",
        "replays": "resolved", "events": "unavailable",
    }
    assert detail["replica_read_only"] is True


def test_mixed_scope_quarantines_only_invalid_issue(repository_with_mixed_scope):
    page = repository_with_mixed_scope.list_issue_page(agent_id="ai-fae-agent")
    assert [item["id"] for item in page["items"]] == [str(VALID_ISSUE_ID)]
    assert page["quarantined_count"] == 1
    assert repository_with_mixed_scope.get_issue_detail(INVALID_ISSUE_ID) is None
    assert repository_with_mixed_scope.agent_issue_scope_valid("ai-fae-agent") is True
```

Also test all-invalid raises/returns the existing unavailable signal, no records is valid and empty, direct invalid UUID returns `None`, invalid linked Turn keys never enter governance summaries, and legacy detail remains summary-readable with all four sections unavailable.

- [ ] **Step 2: Run and verify RED**

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_cloud_management_repository.py
```

Expected: FAIL because the current global `all(...)` gate rejects mixed data and details are unavailable.

- [ ] **Step 3: Centralize scope partitioning**

Implement one helper used by overview, inbox, issue page, detail, and turn summaries:

```python
def _partition_scope(records: list[dict]) -> tuple[list[dict], int]:
    valid = [item for item in records if item.get("scope_valid") is True]
    return valid, len(records) - len(valid)
```

`agent_issue_scope_valid()` returns true for no records or at least one valid record, false for all-invalid non-empty records. Invalid rows never contribute titles, counts, filters, pagination, direct lookup, or turn summaries.

- [ ] **Step 4: Map version-1 data without re-deriving progress**

For version 1, return projected root cause, impact scope, nested arrays, resolved availability, and the exact projected progress. For legacy records, preserve current unavailable sections and unknown lifecycle. Never infer closed/fixing from evidence fields in the cloud reader.

- [ ] **Step 5: Run tests and commit**

```bash
cd backend
.venv/bin/python -m pytest -q \
  tests/test_cloud_management_repository.py \
  tests/test_cloud_mode.py \
  tests/test_fae_workbench_service.py
```

Expected: all selected tests pass.

```bash
git add backend/app/cloud_replica/management_repository.py \
  backend/tests/test_cloud_management_repository.py
git commit -m "feat(fae-workbench): read complete repair closure projections"
```

---

### Task 5: Expose Safe Quarantine Metadata Through the FAE Facade

**Files:**
- Modify: `backend/app/fae_workbench/models.py`
- Modify: `backend/app/fae_workbench/service.py`
- Modify: `backend/app/fae_workbench/routes.py`
- Modify: `backend/tests/test_fae_workbench_service.py`
- Modify: `backend/tests/test_fae_workbench_api.py`

**Interfaces:**
- Produces: `quarantined_issue_count` on issue overview/page responses and unchanged 404/503 route semantics.
- Consumes: Task 4 valid-only repository and `issue_scope_health()`.

- [ ] **Step 1: Write failing API behavior tests**

```python
def test_issue_page_reports_safe_quarantine_count_without_identifiers(client):
    response = client.get("/api/admin/fae/issues?limit=50")
    assert response.status_code == 200
    body = response.json()
    assert body["quarantined_issue_count"] == 2
    assert "quarantined_issue_ids" not in body


def test_direct_quarantined_issue_is_indistinguishable_from_missing(client):
    response = client.get(f"/api/admin/fae/issues/{QUARANTINED_ID}")
    assert response.status_code == 404
    assert response.json() == {"detail": "fae resource not found"}
```

Cover mixed, all-invalid, empty, old-summary, and unauthorized member requests. Verify all-invalid returns the established `503 feedback review unavailable` without leaking row counts or IDs.

- [ ] **Step 2: Run and verify RED**

```bash
cd backend
.venv/bin/python -m pytest -q \
  tests/test_fae_workbench_service.py \
  tests/test_fae_workbench_api.py
```

Expected: FAIL because quarantine metadata is absent.

- [ ] **Step 3: Add bounded metadata without changing authorization**

Add `quarantined_issue_count: int = Field(ge=0)` to the relevant response model. The service copies only the integer count from the repository. Keep the router's fixed FAE agent/source scope and Owner/Admin dependency unchanged. No new endpoint is required.

- [ ] **Step 4: Run tests and commit**

Run the command from Step 2; expect zero failures.

```bash
git add backend/app/fae_workbench/models.py \
  backend/app/fae_workbench/service.py \
  backend/app/fae_workbench/routes.py \
  backend/tests/test_fae_workbench_service.py \
  backend/tests/test_fae_workbench_api.py
git commit -m "feat(fae-workbench): report quarantined closure records safely"
```

---

### Task 6: Render the Real Closure in the Existing Review Workspace

**Files:**
- Modify: `webui/src/types.ts`
- Modify: `webui/src/faeWorkbenchApi.ts`
- Modify: `webui/src/faeWorkbenchApi.test.ts`
- Modify: `webui/src/components/review/ReviewWorkspace.tsx`
- Modify: `webui/src/components/review/ReviewWorkspace.test.tsx`
- Modify: `webui/src/components/review/IssueDetail.tsx`
- Modify: `webui/src/components/review/IssueDetail.test.tsx`
- Modify: `webui/src/components/fae-workbench/FaeWorkbenchShell.tsx`
- Modify: `webui/src/components/fae-workbench/FaeWorkbenchShell.test.tsx`
- Modify: `webui/src/pages/FaeIssuesPage.test.tsx`
- Modify: `webui/src/pages/FaeOverviewPage.tsx`
- Modify: `webui/src/pages/FaeOverviewPage.test.tsx`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`

**Interfaces:**
- Produces: real read-only closure rendering, original Session deep link, safe quarantine notice, and “反馈与修复” navigation copy.
- Consumes: Task 5 API responses and the existing `IssueDetail`, `ReplayMatrix`, and `/admin/fae/sessions/:session_key` route.

- [ ] **Step 1: Write failing response-normalization tests**

```typescript
it("preserves available lifecycle and closure detail on a read-only replica", async () => {
  const detail = await faeWorkbenchApi.review("csrf").issue(ISSUE_ID);
  expect(detail.issue.root_cause).toBe("证据覆盖不足");
  expect(detail.progress.status).toBe("closed");
  expect(detail.links[0].source_session_key).toBe("safe-session-key");
  expect(detail.evidence).toHaveLength(1);
  expect(detail.replays).toHaveLength(1);
});

it("keeps legacy summary detail explicitly unavailable", async () => {
  const detail = await faeWorkbenchApi.review("csrf").issue(LEGACY_ID);
  expect(detail.section_availability?.links).toBe("unavailable");
  expect(detail.issue.root_cause).toBeNull();
  expect(detail.progress.status).toBe("unknown");
});
```

- [ ] **Step 2: Write failing UI tests**

Assert that a full read-only detail shows root cause, impact, original answer, evidence, replay result, and no mutation controls. Assert that “打开原始 Session” links to `/admin/fae/sessions/{encoded-safe-key}`. Assert that events unavailable renders “完整审计时间线保留在源端，云端未复制”. Assert `quarantined_issue_count=2` renders “2 条历史异常记录已安全隔离，未计入当前结果” without IDs.

Assert shell, overview link, and document title use “反馈与修复”, while the page hero remains “反馈修复闭环”.

- [ ] **Step 3: Run and verify RED**

```bash
cd webui
npm test -- --run \
  src/faeWorkbenchApi.test.ts \
  src/components/review/ReviewWorkspace.test.tsx \
  src/components/review/IssueDetail.test.tsx \
  src/components/fae-workbench/FaeWorkbenchShell.test.tsx \
  src/pages/FaeIssuesPage.test.tsx \
  src/pages/FaeOverviewPage.test.tsx \
  src/documentTitle.test.tsx
```

Expected: FAIL because replica normalization erases progress/root cause and the new copy/link are absent.

- [ ] **Step 4: Preserve fields based on availability, not write capability**

Remove the `projected ? null/unknown : value` branches from `normalizeIssue()` and `normalizeProgress()`. Parse real fields when present. Legacy projections already communicate unavailable sections and missing progress, so missing data remains null/unknown without using `replica_read_only` as a data-erasure switch.

Extend `ReviewIssuePage` with optional `quarantined_issue_count`, render its notice above the workspace, and keep read-only mutation hiding driven by `write_available`/hard-stale state.

- [ ] **Step 5: Add Session deep link and copy updates**

In `IssueDetail`, render:

```tsx
{link.source_session_key && (
  <PlatformLink href={`/admin/fae/sessions/${encodeURIComponent(link.source_session_key)}`}>
    打开原始 Session
  </PlatformLink>
)}
```

Do not render a link when the key is absent. Replace only FAE workbench navigation/overview/document-title copies; do not rename the generic `/admin/review` product.

- [ ] **Step 6: Run tests, build, and commit**

Run the command from Step 3, then:

```bash
npm run build
```

Expected: selected tests and production build pass.

```bash
git add webui/src/types.ts webui/src/faeWorkbenchApi.ts \
  webui/src/faeWorkbenchApi.test.ts \
  webui/src/components/review/ReviewWorkspace.tsx \
  webui/src/components/review/ReviewWorkspace.test.tsx \
  webui/src/components/review/IssueDetail.tsx \
  webui/src/components/review/IssueDetail.test.tsx \
  webui/src/components/fae-workbench/FaeWorkbenchShell.tsx \
  webui/src/components/fae-workbench/FaeWorkbenchShell.test.tsx \
  webui/src/pages/FaeIssuesPage.test.tsx \
  webui/src/pages/FaeOverviewPage.tsx \
  webui/src/pages/FaeOverviewPage.test.tsx \
  webui/src/documentTitle.ts webui/src/documentTitle.test.tsx
git commit -m "feat(fae-workbench): show feedback repair closure evidence"
```

---

### Task 7: Prove Synchronization, Compatibility, and Production Invariance

**Files:**
- Modify: `backend/tests/test_cloud_exporter.py`
- Modify: `backend/tests/test_cloud_protocol.py`
- Modify: `backend/tests/test_cloud_mode.py`
- Modify: `backend/tests/test_cloud_deployment.py`
- Modify: `deploy/cloud/accept.sh`
- Modify: `docs/runbooks/cloud-platform.md`
- Create: `docs/reviews/2026-09-01-fae-feedback-repair-closure-projection-review.md`

**Interfaces:**
- Produces: importer-first release gates, real sync evidence, read-only page acceptance, rollback proof, and unchanged FAE/Office evidence.
- Consumes: Tasks 1-6 and the existing signed/encrypted replica deployment path.

- [ ] **Step 1: Add failing end-to-end compatibility tests**

Test these exact paths:

1. new importer + legacy exporter accepts and reads summary-only records;
2. new importer + detailed exporter accepts and reads full closure;
3. detailed exporter + legacy importer rejects before generation swap, preserving the previous generation;
4. mixed valid/invalid detailed records expose only valid rows;
5. a malformed nested record rejects the whole incoming batch;
6. cloud mode has no Review writer or mutation availability.

- [ ] **Step 2: Run backend regression and verify GREEN**

```bash
cd backend
.venv/bin/python -m pytest -q \
  tests/test_review_progress_projection.py \
  tests/test_review_repository.py \
  tests/test_cloud_source.py \
  tests/test_cloud_sanitizer.py \
  tests/test_cloud_store.py \
  tests/test_cloud_exporter.py \
  tests/test_cloud_protocol.py \
  tests/test_cloud_management_repository.py \
  tests/test_cloud_mode.py \
  tests/test_fae_workbench_service.py \
  tests/test_fae_workbench_api.py \
  tests/test_cloud_deployment.py
```

Expected: zero failures.

- [ ] **Step 3: Extend production acceptance without touching FAE or Office**

Add acceptance assertions for:

- all `platform-*` containers use the current release SHA;
- replica generation is inside its freshness threshold;
- Owner can GET `/admin/fae/issues` and one detailed Issue;
- detail field names include `links`, `evidence`, `replays`, `progress`, `replica_read_only` and exclude structured raw identities;
- direct member/viewer access is 403;
- all FAE issue mutation methods remain denied in cloud mode;
- `/office/` returns its pre-release expected status;
- `ai-fae-backend` container ID, image ID, StartedAt, RestartCount, Config hash, and Mounts hash match the recorded pre-release values;
- `https://fae.orbbec.com.cn/` returns its pre-release expected status.

Do not print Session content, cookies, bearer tokens, source identifiers, or private projection plaintext in acceptance logs.

- [ ] **Step 4: Document exact rollout and rollback**

The runbook sequence is:

```text
record pre-release evidence
-> deploy full Platform importer/reader release
-> prove every platform container has the same release SHA
-> pause one scheduled exporter window
-> push one detailed replica generation
-> complete Owner page and read-only acceptance
-> resume scheduled exporter
```

Rollback pauses the detailed exporter first, restores the previous full Platform release and matching environment, verifies legacy summary reads, and never modifies/restarts FAE or changes Office data.

- [ ] **Step 5: Run full verification**

```bash
cd backend
.venv/bin/python -m pytest -q
cd ../webui
npm test -- --run
npm run build
cd ..
bash -n deploy/cloud/accept.sh
git diff --check
```

Expected: all tests and build pass, shell syntax is valid, and `git diff --check` is clean.

- [ ] **Step 6: Perform real deployment acceptance and record evidence**

Run the existing cloud release transaction and `deploy/cloud/accept.sh` only after creating the rollback release pointer and recording FAE/Office evidence. Populate the review document with actual release SHA, replica sequence/freshness, HTTP statuses, FAE invariance hashes, rollback target, test counts, and any unresolved limitation. Do not write success markers before the commands pass.

- [ ] **Step 7: Commit release proof**

```bash
git add backend/tests/test_cloud_exporter.py \
  backend/tests/test_cloud_protocol.py \
  backend/tests/test_cloud_mode.py \
  backend/tests/test_cloud_deployment.py \
  deploy/cloud/accept.sh docs/runbooks/cloud-platform.md \
  docs/reviews/2026-09-01-fae-feedback-repair-closure-projection-review.md
git commit -m "docs(fae-workbench): prove closure projection release"
```

---

## Design Coverage Map

| Design requirement | Plan coverage |
|---|---|
| Existing Review state machine remains authoritative | Tasks 1-2 |
| Complete structured Issue/link/evidence/replay projection | Tasks 2-4 |
| Single lifecycle calculation implementation | Task 1 |
| No N+1 source reads | Task 2 |
| Stable IDs, credential stripping, encryption, strict nested allowlists | Task 3 |
| Mixed-scope quarantine and all-invalid fail-closed | Tasks 4-5 |
| Legacy projection compatibility | Tasks 3-4, 7 |
| Cloud remains read-only | Tasks 4-7 |
| Existing Review Workspace and Session deep link | Task 6 |
| “反馈与修复” product copy | Task 6 |
| Importer-first deployment and previous-generation rollback | Task 7 |
| FAE and Office invariance | Task 7 |
