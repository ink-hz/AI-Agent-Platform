# HR R1.2 Candidate Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver confirmed candidates, isolated batch resume parsing, versioned position-relative analysis, comparison, interview plans, and durable human correction.

**Architecture:** Migration 069 owns candidate-domain objects and security-definer mutations. Candidate modules expose one `CandidateEnvelopeProvider` to the position task-context builder; file bytes and task execution remain in existing Attachment and Conversation services. The number was advanced after master assigned migration 067 to access-history indexing and position intelligence moved to 068.

**Tech Stack:** PostgreSQL, Python 3.11, psycopg 3, FastAPI, Pydantic, pytest.

## Global Constraints

- A name in chat never creates or merges a Candidate.
- Candidate facts are owner-scoped; analysis and feedback are PositionCandidate-scoped.
- Missing evidence is `unknown`, never negative ability evidence.
- AI analysis and HumanFeedback are separate and append-only.
- No ATS stages, scheduling, Offer, onboarding, automatic contact, rejection, or hiring fields.
- Reuse Attachment Service one-year retention, erasure, preview, and download.
- Preserve `backend/.venv`; do not touch unrelated applications or Nginx.

---

### Task 1: Add migration 069 and bounded models

**Files:**
- Create: `backend/control_migrations/069_hr_candidate_intelligence.sql`
- Create: `backend/app/hr/candidate_models.py`
- Create: `backend/tests/test_hr_candidate_migration.py`
- Create: `backend/tests/test_hr_candidate_models.py`

**Interfaces:**
- Produces: `CandidateDraft`, `Candidate`, `CandidateDocument`, `PositionCandidate`, `CandidateAnalysisVersion`, `HumanFeedback`, `CandidateEnvelopeFragment`

- [ ] **Step 1: Write failing schema and model tests**

```python
def test_candidate_schema_has_owner_composite_references_and_no_ats_fields():
    sql = migration_sql("069_hr_candidate_intelligence.sql")
    assert "foreign key (position_id,owner_internal_user_id)" in sql
    assert all(word not in sql for word in ("offer_status", "pipeline_stage", "interview_schedule"))

def test_analysis_requires_exact_context_and_documents():
    value = CreateCandidateAnalysis(position_candidate_id=PC, context_version_id=CONTEXT,
                                    document_ids=(DOCUMENT,), analysis_kind="match",
                                    client_request_id=REQUEST)
    assert value.document_ids == (DOCUMENT,)
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_candidate_migration.py tests/test_hr_candidate_models.py`

Expected: FAIL because migration 069 and models do not exist.

- [ ] **Step 3: Implement owner-scoped append-only schema**

CandidateDraft processing states are `pending/processing/ready/failed/confirmed/dismissed`. PositionCandidate uses only `active/archived`, not recruiting workflow state. Revoke table DML and grant exact functions.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_candidate_migration.py tests/test_hr_candidate_models.py
git add control_migrations/069_hr_candidate_intelligence.sql app/hr/candidate_models.py tests/test_hr_candidate_migration.py tests/test_hr_candidate_models.py
git commit -m "feat(hr): add candidate intelligence schema"
```

Expected: PASS.

### Task 2: Implement repository, service, and per-file batch recovery

**Files:**
- Create: `backend/app/hr/candidate_repository.py`
- Create: `backend/app/hr/candidate_service.py`
- Create: `backend/app/hr/resume_batch.py`
- Create: `backend/tests/test_hr_candidate_repository.py`
- Create: `backend/tests/test_hr_candidate_service.py`
- Create: `backend/tests/test_hr_resume_batch.py`

**Interfaces:**
- Produces: `CandidateRepository`, `CandidateService`, `ResumeBatchCoordinator`

- [ ] **Step 1: Write RED tests for replay, ambiguity, isolation, and partial failure**

```python
def test_one_failed_resume_does_not_roll_back_ready_siblings(coordinator):
    batch = coordinator.enqueue(BATCH_WITH_THREE_ATTACHMENTS)
    coordinator.complete_item(batch.items[0].draft_id, PARSED_A)
    coordinator.fail_item(batch.items[1].draft_id, "parse_failed")
    coordinator.complete_item(batch.items[2].draft_id, PARSED_C)
    assert [item.state for item in coordinator.read(batch.batch_id)] == ["ready", "failed", "ready"]

def test_similar_identity_requires_explicit_merge_target(service):
    with pytest.raises(CandidateIdentityConflict):
        service.confirm_draft(CONFLICTING_DRAFT)
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_candidate_repository.py tests/test_hr_candidate_service.py tests/test_hr_resume_batch.py`

Expected: FAIL because the modules are absent.

- [ ] **Step 3: Implement exact service boundaries**

```python
class CandidateService:
    def create_drafts(self, command: CreateCandidateDraftBatch) -> tuple[CandidateDraft, ...]: ...
    def retry_draft(self, command: RetryCandidateDraft) -> CandidateDraft: ...
    def confirm_draft(self, command: ConfirmCandidateDraft) -> ConfirmedCandidate: ...
    def add_analysis(self, command: CreateCandidateAnalysis) -> CandidateAnalysisVersion: ...
    def append_feedback(self, command: AppendHumanFeedback) -> HumanFeedback: ...
    def compare(self, command: ComparePositionCandidates) -> CandidateAnalysisVersion: ...
```

Each item ID derives from batch request ID plus attachment ID. Retry retains the CandidateDraft and never duplicates successful siblings.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_candidate_repository.py tests/test_hr_candidate_service.py tests/test_hr_resume_batch.py
git add app/hr/candidate_repository.py app/hr/candidate_service.py app/hr/resume_batch.py tests/test_hr_candidate_repository.py tests/test_hr_candidate_service.py tests/test_hr_resume_batch.py
git commit -m "feat(hr): add recoverable candidate intelligence service"
```

Expected: PASS.

### Task 3: Add APIs for batch, candidate, analysis, comparison, and feedback

**Files:**
- Create: `backend/app/hr/candidate_routes.py`
- Create: `backend/tests/test_hr_candidate_api.py`

**Interfaces:**
- Consumes: `CandidateService`
- Produces: `build_candidate_router(service, require_hr_access)`

- [ ] **Step 1: Write RED API tests**

```python
def test_candidate_api_supports_batch_retry_confirm_match_compare_and_feedback(client):
    assert client.post(f"/api/hr/positions/{POSITION}/candidate-drafts:batch", json=BATCH).status_code == 202
    assert client.post(f"/api/hr/candidate-drafts/{DRAFT}:confirm", json=CONFIRM).status_code == 201
    assert client.post(f"/api/hr/position-candidates/{PC}/analyses", json=MATCH).status_code == 201
    assert client.post(f"/api/hr/position-candidates/{PC}/feedback", json=FEEDBACK).status_code == 201
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_candidate_api.py`

Expected: FAIL because the router is absent.

- [ ] **Step 3: Implement bounded routes**

Expose position candidate list, draft batch/status/retry/confirm/dismiss, candidate detail/documents, analysis list/create, feedback append, and same-context comparison. Return 404 for scope violations, 409 for replay/baseline conflict, 422 for malformed payloads, and never return storage locators.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_candidate_api.py
git add app/hr/candidate_routes.py tests/test_hr_candidate_api.py
git commit -m "feat(hr): expose candidate intelligence APIs"
```

Expected: PASS.

### Task 4: Build candidate context and immutable analysis versions

**Files:**
- Create: `backend/app/hr/candidate_context.py`
- Create: `backend/tests/test_hr_candidate_context.py`
- Modify: `backend/app/hr/candidate_service.py`

**Interfaces:**
- Produces: `CandidateEnvelopeProvider.for_task(owner_id, position_id, candidate_id, position_candidate_id)`

- [ ] **Step 1: Write RED context tests**

```python
def test_fragment_contains_only_exact_documents_and_feedback(provider):
    fragment = provider.for_task(OWNER, POSITION, CANDIDATE, PC)
    assert fragment.document_attachment_ids == (RESUME,)
    assert fragment.human_feedback_ids == (FEEDBACK,)
    assert OTHER_POSITION_RESUME not in fragment.document_attachment_ids
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_candidate_context.py`

Expected: FAIL because the provider is absent.

- [ ] **Step 3: Implement fail-closed context and append-only output**

Reject archived relations, erased documents, cross-position relations, and tasks without a confirmed position context. Match, interview-plan, comparison, and re-analysis always create new versions and include applicable feedback IDs.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_candidate_context.py tests/test_hr_candidate_service.py
git add app/hr/candidate_context.py app/hr/candidate_service.py tests/test_hr_candidate_context.py
git commit -m "feat(hr): bind candidate context and analysis versions"
```

Expected: PASS.

### Task 5: Verify candidate security and recovery

**Files:**
- Modify: only Task 1–4 files when a test demonstrates a defect

- [ ] **Step 1: Run focused regressions**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_candidate_*.py tests/test_hr_resume_batch.py tests/test_conversation_attachment_binding.py`

Expected: PASS.

- [ ] **Step 2: Run static checks**

Run: `cd backend && ./.venv/bin/python -m compileall -q app && ./.venv/bin/ruff check --select I app/hr tests/test_hr_candidate_*.py tests/test_hr_resume_batch.py && git diff --check`

Expected: PASS.
