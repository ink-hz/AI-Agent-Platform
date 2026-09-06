# HR Local Intelligence Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only recruiting-intelligence factory that produces an immutable, evidence-grounded HR Intelligence Bundle, while production only validates, imports, publishes, serves, and injects the finished Bundle.

**Architecture:** Move collection, normalization, aggregation, Codex/GPT analysis orchestration, and document generation out of the production Python package into `backend/tools/hr_intelligence`. Add a network-isolated one-shot Bundle importer and keep the business API read-only. Finish and verify the first real Bundle locally before the single application release and Owner-approved import.

**Tech Stack:** Python 3.11/3.12, pytest, httpx, PostgreSQL/psycopg, FastAPI, ReportLab, openpyxl, React 19, TypeScript, Vitest, Docker Compose, Bash, SHA-256 content-addressed storage.

## Global Constraints

- `LOCAL_ONLY_COLLECTION=true`
- `LOCAL_ONLY_ANALYSIS=true`
- `PRODUCTION_COLLECTION=false`
- `PRODUCTION_PANORAMA_MODEL=false`
- `PRODUCTION_CONSUMPTION_ONLY=true`
- `OWNER_CONFIRMATION_BEFORE_IMPORT=true`
- `NO_INTERMEDIATE_DEPLOYMENTS=true`
- `RAW_AND_AI_DATA_SEPARATED=true`
- `SOURCE_PROVENANCE_REQUIRED=true`
- `PREVIOUS_PUBLICATION_MUST_SURVIVE_FAILURE=true`
- Local persistent root: `/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/`.
- Production Bundle root: `/data/orbbec-agent-platform/hr-intelligence/`.
- Production staging: `/data/staging/orbbec-agent-platform/<deployment_id>/`.
- Tasks 1–8 are local-only. Task 9 is the sole release/import task and starts with exact Owner approval.
- No task may modify shared Nginx or touch FAE, VOC, HR MetaBot, Marketing, Office, or administrative applications.
- Preserve user-owned untracked files and `backend/.venv`; never add or delete them.

---

## File and Interface Map

Local-only code, not copied by the production Dockerfile:

- `backend/tools/hr_intelligence/models.py`: evidence, job, analysis, usage, and Bundle contracts.
- `backend/tools/hr_intelligence/paths.py`: validated local data paths.
- `backend/tools/hr_intelligence/collectors.py`: public-source adapters.
- `backend/tools/hr_intelligence/evidence.py`: local content-addressed archive.
- `backend/tools/hr_intelligence/normalize.py`: stable job identity and normalization.
- `backend/tools/hr_intelligence/dimensions.py`: deterministic dimensions and aggregates.
- `backend/tools/hr_intelligence/analysis_units.py`: immutable requests and response acceptance.
- `backend/tools/hr_intelligence/bundle.py`: Bundle assembly and verification.
- `backend/tools/hr_intelligence/exports.py`: local Markdown, PDF, and Excel generation.
- `backend/tools/hr_intelligence/cli.py`: local operator commands.
- `backend/tools/hr_intelligence/source_catalog.v1.json`: approved 12-company catalog.

Production code without collection or model capability:

- `backend/app/hr/intelligence_bundle.py`: strict Bundle validation.
- `backend/app/hr/intelligence_import.py`: one-shot import coordinator.
- `backend/app/hr/intelligence_documents.py`: read-only verified file access.
- `backend/control_migrations/085_hr_intelligence_bundle_import.sql`: Bundle registry.
- `deploy/cloud/compose.hr-intelligence-import.yaml`: internal-only importer.
- `deploy/cloud/import-hr-intelligence.sh`: exact staging and import script.

Production execution surfaces to remove:

- `deploy/cloud/compose.hr-intelligence.yaml`
- `deploy/cloud/hr-panorama-producer.sh`
- `backend/app/hr/panorama_cli.py`
- `backend/app/hr/panorama_producer.py`
- `backend/app/hr/panorama_collection.py`
- `backend/app/hr/panorama_analysis.py`
- `backend/app/hr/panorama_runtime.py`
- Production evidence writes and dynamic PDF/XLSX generation after their local replacements pass.

---

### Task 1: Lock the execution boundary with failing tests

**Files:**
- Create: `backend/tests/test_hr_intelligence_architecture_boundary.py`
- Modify: `backend/tests/test_hr_panorama_deployment.py`

**Interfaces:**
- Consumes: repository files, Dockerfile, Compose files, and deployment scripts.
- Produces: executable assertions that production cannot collect or analyze.

- [ ] **Step 1: Write the failing boundary tests**

```python
from pathlib import Path
import yaml

ROOT = Path(__file__).parents[2]
APP_HR = ROOT / "backend/app/hr"
CLOUD = ROOT / "deploy/cloud"
LOCAL = ROOT / "backend/tools/hr_intelligence"


def test_production_image_cannot_collect_or_analyze_panorama() -> None:
    dockerfile = (CLOUD / "Dockerfile").read_text("utf-8")
    assert "COPY --chown=10001:10001 backend/tools" not in dockerfile
    for name in (
        "panorama_cli.py", "panorama_producer.py",
        "panorama_collection.py", "panorama_analysis.py",
        "panorama_runtime.py",
    ):
        assert not (APP_HR / name).exists()


def test_production_has_no_run_or_resume_surface() -> None:
    assert not (CLOUD / "compose.hr-intelligence.yaml").exists()
    assert not (CLOUD / "hr-panorama-producer.sh").exists()
    text = "\n".join(
        path.read_text("utf-8")
        for path in (CLOUD / "compose.yaml", CLOUD / "remote-stage.sh")
    )
    assert "platform-hr-intelligence" not in text
    assert "panorama_cli" not in text


def test_import_service_has_no_model_secret_or_external_network() -> None:
    service = yaml.safe_load(
        (CLOUD / "compose.hr-intelligence-import.yaml").read_text("utf-8")
    )["services"]["platform-hr-intelligence-import"]
    assert service["networks"] == ["platform-internal"]
    for forbidden in (
        "PLATFORM_BRAIN_PROVIDER", "brain-provider-api-key",
        "platform-edge", "source_catalog",
    ):
        assert forbidden not in repr(service)


def test_local_factory_is_outside_the_production_package() -> None:
    assert (LOCAL / "cli.py").is_file()
    assert (LOCAL / "source_catalog.v1.json").is_file()
```

- [ ] **Step 2: Run and verify failure against the current Producer**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_intelligence_architecture_boundary.py tests/test_hr_panorama_deployment.py
```

Expected: FAIL because current production collection/analysis files and entrypoints still exist.

- [ ] **Step 3: Commit only the red architecture contract**

```bash
git add backend/tests/test_hr_intelligence_architecture_boundary.py backend/tests/test_hr_panorama_deployment.py
git commit -m "test(hr): lock local intelligence execution boundary"
```

Do not deploy. Tests remain red until Tasks 2–5 complete.

---

### Task 2: Move deterministic collection and normalization to local tools

**Files:**
- Create: `backend/tools/__init__.py`
- Create: `backend/tools/hr_intelligence/__init__.py`
- Create: `backend/tools/hr_intelligence/models.py`
- Create: `backend/tools/hr_intelligence/paths.py`
- Create: `backend/tools/hr_intelligence/collectors.py`
- Create: `backend/tools/hr_intelligence/evidence.py`
- Create: `backend/tools/hr_intelligence/normalize.py`
- Create: `backend/tools/hr_intelligence/dimensions.py`
- Move: `backend/app/hr/panorama_source_catalog.v1.json` to `backend/tools/hr_intelligence/source_catalog.v1.json`
- Create: `backend/tests/test_hr_local_intelligence_paths.py`
- Create: `backend/tests/test_hr_local_intelligence_collection.py`
- Create: `backend/tests/test_hr_local_intelligence_dimensions.py`
- Modify: existing collector/dimension tests to import the local package.

**Interfaces:**
- Produces: `local_data_root()`, `collect_source()`, `EvidenceStore.put()`, `normalize_jobs()`, and `compile_dimensions()`.
- Consumes: approved public URLs and the explicit local data root.

- [ ] **Step 1: Write failing path and provenance tests**

```python
def test_local_root_is_absolute_and_outside_repository(monkeypatch):
    monkeypatch.delenv("HR_INTELLIGENCE_LOCAL_ROOT", raising=False)
    assert str(local_data_root()) == (
        "/Users/neo/Library/Application Support/"
        "OrbbecAI-Agent-Platform/hr-intelligence"
    )
    monkeypatch.setenv("HR_INTELLIGENCE_LOCAL_ROOT", "relative/path")
    with pytest.raises(ValueError, match="absolute"):
        local_data_root()


def test_normalized_job_requires_evidence_provenance():
    with pytest.raises(ValueError, match="evidence"):
        NormalizedJob(
            job_id=uuid4(), company_key="hesai",
            source_url="https://example.com/jobs/1", evidence_sha256="",
            title="算法工程师", location="上海", recruitment_track="social",
            technical_directions=("算法",), family="研发",
            duty="职责", requirement="要求", observed_at=NOW,
        )
```

- [ ] **Step 2: Run and verify missing local interfaces fail**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_local_intelligence_paths.py tests/test_hr_local_intelligence_collection.py tests/test_hr_local_intelligence_dimensions.py
```

Expected: FAIL with missing `tools.hr_intelligence` modules.

- [ ] **Step 3: Implement the exact local path boundary**

```python
DEFAULT_LOCAL_ROOT = Path(
    "/Users/neo/Library/Application Support/"
    "OrbbecAI-Agent-Platform/hr-intelligence"
)


def local_data_root() -> Path:
    raw = os.getenv("HR_INTELLIGENCE_LOCAL_ROOT")
    selected = Path(raw) if raw else DEFAULT_LOCAL_ROOT
    if not selected.is_absolute():
        raise ValueError("HR intelligence local root must be absolute")
    selected = selected.resolve()
    if REPOSITORY_ROOT.resolve() in (selected, *selected.parents):
        raise ValueError("HR intelligence data must be outside repository")
    return selected
```

- [ ] **Step 4: Migrate the proven source logic behind local interfaces**

Implement these exact public signatures and keep their bodies in the named local modules:

- `collect_source(client: httpx.AsyncClient, store: EvidenceStore, target: SourceTarget) -> CollectionResult`
- `normalize_jobs(result: CollectionResult, *, company_key: str) -> tuple[NormalizedJob, ...]`
- `compile_dimensions(jobs: tuple[NormalizedJob, ...]) -> dict[str, object]`

Move Moka, Feishu, Huawei, ELEGOO, JSON-LD, HTML parsing, evidence sanitization, stable identity, track separation, and multi-label directions without changing fixtures.

- [ ] **Step 5: Run deterministic data-plane tests**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_local_intelligence_paths.py tests/test_hr_local_intelligence_collection.py tests/test_hr_local_intelligence_dimensions.py tests/test_hr_panorama_collection_v2.py tests/test_hr_panorama_dimensions.py
```

Expected: PASS.

- [ ] **Step 6: Commit the local data plane**

```bash
git add backend/tools backend/tests/test_hr_local_intelligence_*.py backend/tests/test_hr_panorama_collection_v2.py backend/tests/test_hr_panorama_dimensions.py
git commit -m "feat(hr): create local recruiting evidence pipeline"
```

Do not perform a real collection and do not deploy.

---

### Task 3: Add resumable evidence-bound Codex/GPT analysis units

**Files:**
- Create: `backend/tools/hr_intelligence/analysis_units.py`
- Create: `backend/tools/hr_intelligence/analysis_schema.v1.json`
- Create: `backend/tests/test_hr_local_intelligence_analysis.py`
- Modify: `backend/tests/test_hr_panorama_analysis.py`

**Interfaces:**
- Produces: `prepare_units()`, `accept_unit_response()`, `analysis_cache_hit()`, and usage records.
- Consumes: normalized jobs, aggregates, and accepted prerequisite units.

- [ ] **Step 1: Write failing identity, evidence, resume, and usage tests**

```python
def test_completed_unit_reuses_only_matching_input_hash(tmp_path):
    unit = prepare_company_unit("zhiyuan", jobs, aggregates)
    accepted = accept_unit_response(unit, valid_response(unit), valid_usage(unit))
    save_accepted(tmp_path, accepted)
    assert analysis_cache_hit(tmp_path, unit) is True
    assert analysis_cache_hit(
        tmp_path, replace(unit, input_sha256="b" * 64)
    ) is False


def test_inference_requires_a_bound_fact():
    response = valid_response(unit)
    response["inferences"][0]["basis_fact_ids"] = ["missing"]
    with pytest.raises(AnalysisContractError, match="basis"):
        accept_unit_response(unit, response, valid_usage(unit))


def test_unavailable_usage_is_explicit_not_fabricated():
    usage = valid_usage(unit) | {
        "input_tokens": "unavailable", "output_tokens": "unavailable",
        "estimated_cost": "unavailable",
        "unavailable_reason": "Codex session has no per-call usage telemetry",
    }
    assert accept_unit_response(unit, valid_response(unit), usage).usage == usage
```

- [ ] **Step 2: Run and verify missing analysis interfaces fail**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_local_intelligence_analysis.py
```

Expected: FAIL.

- [ ] **Step 3: Implement stable unit identity and ordering**

```python
unit_id = uuid5(
    NAMESPACE_URL,
    f"orbbec:hr-intelligence:{bundle_id}:{kind}:{scope_key}:{input_sha256}",
)
```

Order: company facts, company signals, social, campus, intern, technical directions, product/business routes, talent competition, Orbbec implications, final synthesis.

- [ ] **Step 4: Implement strict response acceptance**

```python
def accept_unit_response(unit, response, usage) -> AcceptedAnalysis:
    validate_exact_analysis_keys(response)
    validate_fact_evidence_bindings(unit, response["facts"])
    validate_inference_basis(response["facts"], response["inferences"])
    validate_unknowns_and_alternatives(response)
    return AcceptedAnalysis.from_validated(
        unit, response, validate_usage(unit, usage)
    )
```

Codex reads immutable request files under `<bundle>/analysis/requests/` and writes local responses. No production model transport exists; `accept-analysis` is the only accepted-state transition.

- [ ] **Step 5: Run analysis tests and commit**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_local_intelligence_analysis.py tests/test_hr_panorama_analysis.py
git add tools/hr_intelligence/analysis_units.py tools/hr_intelligence/analysis_schema.v1.json tests/test_hr_local_intelligence_analysis.py tests/test_hr_panorama_analysis.py
git commit -m "feat(hr): add resumable local intelligence analysis"
```

Expected: PASS. Do not call a production model or deploy.

---

### Task 4: Build immutable Bundles and reports locally

**Files:**
- Create: `backend/tools/hr_intelligence/bundle.py`
- Create: `backend/tools/hr_intelligence/exports.py`
- Create: `backend/tools/hr_intelligence/cli.py`
- Create: `backend/tests/test_hr_intelligence_bundle.py`
- Create: `backend/tests/test_hr_intelligence_exports.py`
- Create: `backend/tests/test_hr_intelligence_cli.py`
- Create: `backend/tests/test_hr_panorama_export.py`

**Interfaces:**
- Produces: exact Bundle file set and `verify_bundle(path) -> VerifiedBundle`.
- Consumes: evidence, jobs, aggregates, accepted analyses, and usage records.

- [ ] **Step 1: Write failing completeness, checksum, and immutability tests**

```python
REQUIRED = {
    "manifest.json", "source-catalog.json", "source-coverage.json",
    "raw-evidence-index.json", "normalized-jobs.jsonl", "aggregates.json",
    "analysis.json", "analysis-usage.json", "report.md", "report.pdf",
    "report.xlsx", "checksums.sha256",
}


def test_verified_bundle_contains_every_required_file(tmp_path):
    path = build_bundle(valid_inputs(), root=tmp_path)
    assert {item.name for item in path.iterdir()} == REQUIRED | {"evidence"}
    assert verify_bundle(path).bundle_id == valid_inputs().bundle_id


def test_verification_fails_after_tampering(tmp_path):
    path = build_bundle(valid_inputs(), root=tmp_path)
    (path / "analysis.json").write_text("{}", "utf-8")
    with pytest.raises(BundleVerificationError, match="checksum"):
        verify_bundle(path)
```

- [ ] **Step 2: Run and verify missing Bundle interfaces fail**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_intelligence_bundle.py tests/test_hr_intelligence_exports.py
```

Expected: FAIL.

- [ ] **Step 3: Implement deterministic atomic Bundle assembly**

```python
def build_bundle(inputs: BundleInputs, *, root: Path) -> Path:
    staging = exact_staging_path(root, inputs.bundle_id)
    write_canonical_json_and_jsonl(staging, inputs)
    build_markdown(staging, inputs)
    build_pdf(staging, inputs)
    build_xlsx(staging, inputs)
    write_checksums(staging)
    verify_bundle(staging)
    return publish_local_directory(staging, inputs.bundle_id)
```

Use sorted UTF-8 JSON, trailing newlines, stable job ordering, and atomic rename from `<bundle_id>.building`. An existing final Bundle is verified and returned, never modified.

- [ ] **Step 4: Move report generation to local tools**

Excel sheets are exactly `原始岗位`, `AI分析`, `来源覆盖`, `证据索引`, and `成本记录`. PDF and Markdown contain analysis, limitations, sources, and an original-job appendix.

- [ ] **Step 5: Implement the local CLI**

```text
python -m tools.hr_intelligence.cli init --bundle-id <uuid> --catalog <absolute-json>
python -m tools.hr_intelligence.cli collect --bundle-id <uuid> --resume
python -m tools.hr_intelligence.cli validate --bundle-id <uuid> --require-company-count 12 --require-provenance
python -m tools.hr_intelligence.cli prepare-analysis --bundle-id <uuid>
python -m tools.hr_intelligence.cli analysis-status --bundle-id <uuid> [--require-complete]
python -m tools.hr_intelligence.cli accept-analysis --bundle-id <uuid> --unit <uuid> --response <absolute-json> --usage <absolute-json>
python -m tools.hr_intelligence.cli accept-analysis --bundle-id <uuid> --all-ready
python -m tools.hr_intelligence.cli build --bundle-id <uuid>
python -m tools.hr_intelligence.cli verify --bundle <absolute-directory> [--strict]
python -m tools.hr_intelligence.cli summary --bundle-id <uuid>
python -m tools.hr_intelligence.cli summary --bundle <absolute-directory>
```

Reject relative paths, repository-contained data roots, symlinks, incomplete analyses, checksum mismatches, and overwrite attempts.

- [ ] **Step 6: Run tests and commit**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_intelligence_bundle.py tests/test_hr_intelligence_exports.py tests/test_hr_intelligence_cli.py tests/test_hr_panorama_export.py
git add tools/hr_intelligence tests/test_hr_intelligence_bundle.py tests/test_hr_intelligence_exports.py tests/test_hr_intelligence_cli.py tests/test_hr_panorama_export.py
git commit -m "feat(hr): build immutable local intelligence bundles"
```

Expected: PASS. Do not generate the real Bundle or deploy.

---

### Task 5: Replace production analysis with a one-shot Bundle importer

**Files:**
- Create: `backend/control_migrations/085_hr_intelligence_bundle_import.sql`
- Create: `backend/app/hr/intelligence_bundle.py`
- Create: `backend/app/hr/intelligence_import.py`
- Create: `backend/tests/test_hr_intelligence_bundle_migration.py`
- Create: `backend/tests/test_hr_intelligence_import.py`
- Create: `backend/tests/test_hr_intelligence_import_database.py`
- Create: `deploy/cloud/compose.hr-intelligence-import.yaml`
- Create: `deploy/cloud/import-hr-intelligence.sh`
- Modify: `backend/app/hr/panorama_repository.py`
- Modify: `backend/tests/test_hr_panorama_deployment.py`
- Delete: all production execution surfaces listed in the file map.

**Interfaces:**
- Produces: `IntelligenceBundleImporter.import_bundle(path, owner_id)` and an immutable registry entry.
- Consumes: one verified Bundle mounted read-only at `/bundle`; no HTTP or model client.

- [ ] **Step 1: Write failing idempotence and rollback tests**

```python
def test_import_is_idempotent_and_failure_preserves_current(repository, bundle):
    first = importer.import_bundle(bundle.path, owner_id=bundle.owner_id)
    second = importer.import_bundle(bundle.path, owner_id=bundle.owner_id)
    assert second == first
    current = repository.current_publication()
    tamper(bundle.copy_path / "analysis.json")
    with pytest.raises(BundleVerificationError):
        importer.import_bundle(bundle.copy_path, owner_id=bundle.owner_id)
    assert repository.current_publication() == current


def test_import_has_no_http_or_model_dependency():
    source = (APP_HR / "intelligence_import.py").read_text("utf-8")
    for forbidden in ("httpx", "Anthropic", "OpenAI", "panorama_analysis"):
        assert forbidden not in source
```

- [ ] **Step 2: Run and verify failure**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_intelligence_bundle_migration.py tests/test_hr_intelligence_import.py tests/test_hr_intelligence_import_database.py tests/test_hr_panorama_deployment.py tests/test_hr_intelligence_architecture_boundary.py
```

Expected: FAIL until migration, importer, isolated Compose service, and removals exist.

- [ ] **Step 3: Add migration 085 Bundle registry**

```sql
create table platform_hr.intelligence_bundles (
  bundle_id uuid primary key,
  owner_internal_user_id uuid not null,
  schema_version integer not null check (schema_version=1),
  manifest_sha256 text not null check (manifest_sha256 ~ '^[a-f0-9]{64}$'),
  coverage_state text not null check (coverage_state in ('complete','partial')),
  observation_started_at timestamptz not null,
  observation_finished_at timestamptz not null,
  imported_at timestamptz not null default now(),
  publication_id uuid not null unique,
  document_index jsonb not null,
  unique (owner_internal_user_id,manifest_sha256),
  check (observation_finished_at>=observation_started_at)
);
```

Add security-definer create/read functions for `platform_control_app`; revoke table access from application roles. Reuse existing normalized source, attempt, snapshot, insight, and publication functions.

Revoke `platform_control_app` and `platform_control_app_preview` execution rights from legacy production-control functions `claim_next_panorama_run_v79`, `read_panorama_run_runtime_v79`, `transition_panorama_run_v79`, and `retry_panorama_analysis_v82`. Keep read-only history functions and only the minimum deterministic write functions required inside the import transaction.

- [ ] **Step 4: Implement verify-then-import without model/network clients**

```python
class IntelligenceBundleImporter:
    def import_bundle(self, path: Path, *, owner_id: UUID) -> ImportedBundle:
        verified = verify_import_bundle(path)
        existing = self._repository.bundle_by_manifest(
            owner_id, verified.manifest_sha256
        )
        if existing is not None:
            return existing
        return self._repository.import_verified_bundle(owner_id, verified)
```

Use deterministic IDs from the Bundle. Register and publish only after every file, evidence link, job, aggregate, and analysis contract passes.

- [ ] **Step 5: Implement isolated import deployment assets**

The import service mounts only control DB secrets, `/bundle:ro`, and the production intelligence root `:rw`; uses only `platform-internal`; and has no ports, model secret, source catalog, or `platform-edge`.

The operator script validates locally, checks `df -B1 / /data`, creates one exact staging directory, transfers only the Bundle, verifies remotely, runs one import, checks the published Bundle ID, cleans only that staging path through `trap`, and reports disk usage.

- [ ] **Step 6: Remove production execution surfaces and migrate tests**

Delete the listed CLI, Producer, collector, analyzer, runtime coordinator, Compose override, script, source catalog, and runbook commands. Remove runtime/claim/transition methods from `panorama_repository.py`; migrate useful deterministic fixtures and tests to `tools.hr_intelligence`, then delete obsolete Producer, runtime, CLI, and retry tests. Ensure the Dockerfile never copies `backend/tools`.

- [ ] **Step 7: Run focused tests and commit**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_intelligence_architecture_boundary.py tests/test_hr_intelligence_bundle_migration.py tests/test_hr_intelligence_import.py tests/test_hr_intelligence_import_database.py tests/test_hr_panorama_deployment.py
git add app/hr control_migrations/085_hr_intelligence_bundle_import.sql tests ../deploy/cloud ../docs/runbooks
git commit -m "feat(hr): replace production analysis with bundle import"
```

Expected: PASS and no executable production `run` or `resume`. Do not deploy or import.

---

### Task 6: Serve published Bundle data and prebuilt downloads

**Files:**
- Create: `backend/app/hr/intelligence_documents.py`
- Create: `backend/tests/test_hr_intelligence_documents.py`
- Modify: `backend/app/hr/panorama_models.py`
- Modify: `backend/app/hr/panorama_repository.py`
- Modify: `backend/app/hr/panorama_service.py`
- Modify: `backend/app/hr/panorama_routes.py`
- Modify: `backend/app/main.py`
- Delete: `backend/app/hr/panorama_export.py` after local migration.
- Modify: `backend/tests/test_hr_panorama_api.py`
- Modify: `backend/tests/test_hr_panorama_service.py`
- Modify: `webui/src/hrPanoramaTypes.ts`
- Modify: `webui/src/hrPanoramaApi.ts`
- Modify: `webui/src/workspaces/hr/HrPanoramaReport.tsx`
- Modify: `webui/src/workspaces/hr/HrPanoramaWorkspace.tsx`
- Modify: `webui/src/hrPanoramaApi.test.ts`
- Modify: `webui/src/workspaces/hr/HrPanoramaReport.test.tsx`
- Modify: `webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx`

**Interfaces:**
- Produces: existing GET report routes plus verified prebuilt `pdf`, `xlsx`, and `md` responses.
- Consumes: current Bundle registry and files; no POST routes or online generation.

- [ ] **Step 1: Write failing backend read/download tests**

```python
def test_export_returns_verified_prebuilt_document(client, published):
    response = client.get(
        f"/api/hr/panorama/reports/{published.publication_id}/export?format=pdf"
    )
    assert response.status_code == 200
    assert response.content == published.pdf_bytes
    assert response.headers["x-content-sha256"] == published.pdf_sha256
    assert exporter.calls == []


def test_panorama_router_has_only_get_routes(app):
    methods = {
        method for route in app.routes
        if route.path.startswith("/api/hr/panorama")
        for method in getattr(route, "methods", set())
    }
    assert methods == {"GET"}
```

- [ ] **Step 2: Write failing frontend semantics tests**

Accept coverage states `succeeded`, `empty_confirmed`, `partial`, `failed`, and `not_observed`; never render failed/not-observed as zero; request immutable downloads without filter parameters; remove copy about background preparation or continuous collection.

- [ ] **Step 3: Run focused tests and verify failure**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_panorama_api.py tests/test_hr_panorama_service.py tests/test_hr_intelligence_documents.py
cd ../webui
npm test -- --run src/hrPanoramaApi.test.ts src/workspaces/hr/HrPanoramaReport.test.tsx src/workspaces/hr/HrPanorama.acceptance.test.tsx
```

Expected: FAIL because production still generates documents and lacks Bundle provenance.

- [ ] **Step 4: Implement hash-verifying document reads**

```python
class IntelligenceDocumentStore:
    def read(self, bundle_id: UUID, name: DocumentName) -> VerifiedDocument:
        record = self._repository.bundle_document(bundle_id, name)
        body = self._root.joinpath(record.locator).read_bytes()
        if sha256(body).hexdigest() != record.sha256:
            raise PanoramaUnavailable("intelligence document checksum mismatch")
        return VerifiedDocument(name, record.mime, record.sha256, body)
```

Remove online PDF/XLSX construction. Browser filtering remains for displayed jobs; downloads always return immutable Bundle files.

- [ ] **Step 5: Update business copy, run tests, and commit**

Use “已发布招聘情报”“数据截至”“来源覆盖”“当前没有已发布情报”. Do not mention collection, preparation, model execution, retries, or Provider state.

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_panorama_api.py tests/test_hr_panorama_service.py tests/test_hr_intelligence_documents.py
cd ../webui
npm test -- --run src/hrPanoramaApi.test.ts src/workspaces/hr/HrPanoramaReport.test.tsx src/workspaces/hr/HrPanorama.acceptance.test.tsx
npm run build
cd ..
git add backend/app/hr backend/app/main.py backend/tests webui/src
git commit -m "feat(hr): consume published intelligence bundles"
```

Expected: PASS. Do not deploy.

---

### Task 7: Inject published intelligence and provenance into recruiting tasks

**Files:**
- Modify: `backend/app/hr/panorama_context.py`
- Modify: `backend/app/hr/task_context.py`
- Modify: `backend/app/agent_brain/conversation_context.py`
- Modify: `backend/tests/test_hr_panorama_context.py`
- Modify: `backend/tests/test_hr_task_context.py`
- Modify: `backend/tests/test_agent_brain_conversation_context.py`
- Modify: `backend/tests/test_hr_p0_recruiting_loop.py`
- Modify: `webui/src/workspaces/hr/HrTaskReferences.tsx`
- Modify: `webui/src/workspaces/hr/HrTaskReferences.test.tsx`

**Interfaces:**
- Consumes: only the currently published, already imported Bundle.
- Produces: a bounded `PanoramaContextFragment` containing `bundle_id`, `insight_version_id`, `observed_at`, evidence-grounded excerpts, source links, evidence hashes, and explicit unknowns.
- Must never trigger collection, analysis, publication, or model execution.

- [ ] **Step 1: Write failing backend context tests**

Cover all of these cases:

- a position task receives only company, direction, geography, and track excerpts relevant to that position;
- every injected claim retains `source_url`, `evidence_sha256`, `observed_at`, and `bundle_id`;
- the prompt distinguishes source facts, deterministic aggregates, AI interpretation, and unknowns;
- no published Bundle produces an explicit `intelligence_status=unavailable` fragment instead of a collection attempt;
- a missing company or dimension produces an explicit gap and does not call an operator, collector, analyzer, or model;
- a later failed import does not change the Bundle referenced by an existing task.

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_panorama_context.py tests/test_hr_task_context.py tests/test_agent_brain_conversation_context.py tests/test_hr_p0_recruiting_loop.py
```

Expected: FAIL because current task context does not carry the complete immutable Bundle provenance contract.

- [ ] **Step 3: Implement one bounded context contract**

Add one frozen value object with these fields:

```python
@dataclass(frozen=True)
class PanoramaContextFragment:
    bundle_id: UUID | None
    insight_version_id: UUID | None
    observed_at: datetime | None
    status: Literal["available", "partial", "unavailable"]
    source_facts: tuple[GroundedExcerpt, ...]
    aggregates: tuple[GroundedExcerpt, ...]
    interpretations: tuple[GroundedExcerpt, ...]
    unknowns: tuple[str, ...]
```

Resolve it from the published Bundle once per task. Enforce a prompt-size budget, rank excerpts deterministically by position relevance, and emit references to the UI through the existing task-reference path.

- [ ] **Step 4: Make provenance visible without exposing operator controls**

Render compact references for the Bundle version, data cutoff time, source URL, and evidence hash. Do not add update, refresh, crawl, analyze, retry, run, or resume controls.

- [ ] **Step 5: Run focused and regression tests**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_panorama_context.py tests/test_hr_task_context.py tests/test_hr_task_context_recovery.py tests/test_agent_brain_conversation_context.py tests/test_hr_p0_recruiting_loop.py
cd ../webui
npm test -- --run src/workspaces/hr/HrTaskReferences.test.tsx
```

Expected: PASS; spy assertions show zero producer, collector, analyzer, and operator calls.

- [ ] **Step 6: Commit the context integration**

```bash
cd ..
git add backend/app/hr/panorama_context.py backend/app/hr/task_context.py backend/app/agent_brain/conversation_context.py backend/tests/test_hr_panorama_context.py backend/tests/test_hr_task_context.py backend/tests/test_agent_brain_conversation_context.py backend/tests/test_hr_p0_recruiting_loop.py webui/src/workspaces/hr/HrTaskReferences.tsx webui/src/workspaces/hr/HrTaskReferences.test.tsx
git commit -m "feat(hr): bind intelligence provenance to recruiting tasks"
```

Expected: one local commit. Do not deploy.

---

### Task 8: Produce and review the first real Bundle entirely locally

**Data location:**
- Create only under: `/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/`
- Never create raw evidence, normalized data, model requests, model responses, reports, or Bundle payloads inside the repository.

**Required companies:** 联合光电、速腾聚创、禾赛科技、拓竹、创想三维、智能派、知象光电、先临三维、思看科技、智元机器人、影石创新、华为.

- [ ] **Step 1: Run the complete local test gate before touching live public sources**

```bash
cd backend
.venv/bin/pytest -q tests/test_hr_intelligence_architecture_boundary.py tests/test_hr_local_intelligence_paths.py tests/test_hr_local_intelligence_collection.py tests/test_hr_local_intelligence_dimensions.py tests/test_hr_local_intelligence_analysis.py tests/test_hr_intelligence_bundle.py tests/test_hr_intelligence_exports.py tests/test_hr_intelligence_cli.py
```

Expected: PASS. If any test fails, stop; do not collect partial live data.

- [ ] **Step 2: Create one local run and collect all approved sources**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
export HR_INTELLIGENCE_LOCAL_ROOT="/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence"
export HR_INTELLIGENCE_BUNDLE_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
.venv/bin/python -m tools.hr_intelligence.cli init --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID" --catalog "$PWD/tools/hr_intelligence/source_catalog.v1.json"
.venv/bin/python -m tools.hr_intelligence.cli collect --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID" --resume
.venv/bin/python -m tools.hr_intelligence.cli summary --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID"
```

Record the generated Bundle ID. Collection may retry public channels locally, but a failed or blocked source must remain `failed`, `partial`, or `not_observed`; it must never be converted into zero openings.

- [ ] **Step 3: Validate coverage and raw evidence before analysis**

```bash
.venv/bin/python -m tools.hr_intelligence.cli validate --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID" --require-company-count 12 --require-provenance
```

Manually review `source-coverage.json` and a sample from every successful adapter. Confirm original URLs, capture times, MIME types, hashes, source channel, and raw payloads. Record blocked channels and limitations explicitly.

- [ ] **Step 4: Prepare immutable evidence-bound analysis requests**

```bash
.venv/bin/python -m tools.hr_intelligence.cli prepare-analysis --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID" --units company,track,direction,comparison,executive-summary
.venv/bin/python -m tools.hr_intelligence.cli analysis-status --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID"
```

Each request must include an input hash, evidence manifest, schema version, maximum claim count, and instruction to return `unknown` when evidence is insufficient.

- [ ] **Step 5: Complete each analysis unit locally with Codex/GPT**

For each pending request under the local run directory:

1. Read the immutable request and only its referenced evidence.
2. Produce schema-valid JSON in the corresponding local response file.
3. Attach every factual claim to evidence hashes and source URLs.
4. Write actual provider/model, input/output tokens, and cost when available; otherwise write `unavailable` plus a concrete reason.
5. Accept the response only through the CLI so input-hash and evidence-reference checks run.

```bash
.venv/bin/python -m tools.hr_intelligence.cli accept-analysis --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID" --all-ready
.venv/bin/python -m tools.hr_intelligence.cli analysis-status --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID" --require-complete
```

Resume only incomplete units after interruption. Never repeat already accepted units with the same input hash.

- [ ] **Step 6: Build and cryptographically verify the immutable Bundle**

```bash
.venv/bin/python -m tools.hr_intelligence.cli build --bundle-id "$HR_INTELLIGENCE_BUNDLE_ID"
.venv/bin/python -m tools.hr_intelligence.cli verify --bundle "$HR_INTELLIGENCE_LOCAL_ROOT/bundles/$HR_INTELLIGENCE_BUNDLE_ID" --strict
```

Required payload: `manifest.json`, `source-catalog.json`, `source-coverage.json`, `raw-evidence-index.json`, `normalized-jobs.jsonl`, `aggregates.json`, `analysis.json`, `analysis-usage.json`, `report.md`, `report.pdf`, `report.xlsx`, `checksums.sha256`, and content-addressed evidence files.

- [ ] **Step 7: Visually and structurally inspect all deliverables**

Use the PDF skill workflow to render every page of `report.pdf` and inspect for clipping, missing fonts, blank pages, unreadable tables, and broken source references. Open `report.xlsx` read-only and verify the `原始岗位`, `AI分析`, `来源覆盖`, `证据索引`, and `成本记录` sheets, filters, row counts, hashes, and source URLs.

- [ ] **Step 8: Integrate finished code into local master without pushing**

Use the `finishing-a-development-branch` skill. If implementation occurred on an isolated feature branch, merge it into local `master` with `--no-ff`; if it occurred directly on local `master`, do not create a synthetic merge. Run the complete backend test suite and frontend tests/build on local `master`. Do not push and do not deploy.

- [ ] **Step 9: Present the local review packet and stop for approval**

Report:

- exact Bundle ID and absolute local path;
- exact 40-character local `master` commit SHA that passed the final gate;
- company/source coverage and all failed or partial channels;
- social/campus counts, locations, functions, role families, product/technology directions, seniority, time series, and company comparisons;
- AI conclusions, evidence links/hashes, confidence, disagreements, and limitations;
- token/cost totals or explicit unavailable reasons;
- PDF/XLSX verification result;
- proof that no SSH, deployment, production write, production model, or production analysis occurred.

Do not continue to Task 9 until the Owner replies with both exact approval values defined below.

---

### Task 9: Perform the sole application release and Owner-approved Bundle import

**Hard approval gate:** The Owner must provide both lines, matching the reviewed artifacts exactly:

```text
APPROVE_RELEASE_SHA=<40 lowercase hexadecimal Git commit SHA>
APPROVE_HR_BUNDLE_ID=<reviewed Bundle UUID>
```

Any different wording, shortened SHA, different Bundle, or later code/data change invalidates approval and requires a new review. This task is the only task allowed to access production.

- [ ] **Step 1: Re-verify the approved code and Bundle without modifying either**

```bash
git status --short
git rev-parse HEAD
cd backend
.venv/bin/pytest -q
cd ../webui
npm test -- --run
npm run build
cd ../backend
.venv/bin/python -m tools.hr_intelligence.cli verify --bundle "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/$APPROVE_HR_BUNDLE_ID" --strict
```

Expected: clean tracked tree, HEAD exactly equals `APPROVE_RELEASE_SHA`, all tests/build pass, Bundle ID and checksums exactly match the approved review packet. Preserve unrelated untracked files.

- [ ] **Step 2: Push only the already approved local master**

Confirm the checked-out branch is local `master`, its commit exactly equals `APPROVE_RELEASE_SHA`, and the remote has not advanced unexpectedly. Push only `master`. Do not merge, amend, rebase, or create any new commit after approval; do not push a feature branch and do not include `backend/.venv` or unrelated untracked files.

- [ ] **Step 3: Enforce the production disk and release gate**

Before upload or build, record:

```bash
df -B1 / /data
```

Stop if root free space is below 25 GB, predicted post-staging/image free space is below 20 GB, predicted post-release root usage exceeds 75%, or unexplained net growth exceeds 1 GB. Stage only in `/data/staging/orbbec-agent-platform/<deployment_id>/` and install an exact-directory cleanup trap before copying anything.

- [ ] **Step 4: Deploy the application exactly once**

Deploy the approved `master` commit with the repository deployment procedure. The release contains code and build artifacts only. It must not contain data, uploads, logs, indexes, review data, databases, virtual environments, node modules, model caches, local intelligence runs, or Bundles. Do not modify shared Nginx or restart unrelated services.

- [ ] **Step 5: Prove the production execution boundary before import**

Verify all of the following on the deployed release:

- no panorama producer service, timer, scheduler, run/resume route, or analysis CLI;
- no public egress network on the importer;
- no model/provider credentials or model client dependency in importer configuration;
- panorama business routes are GET-only;
- the previously published Bundle still serves successfully before the new import.

If any assertion fails, stop, roll back code if needed, and do not import.

- [ ] **Step 6: Import the approved Bundle once**

```bash
deploy/cloud/import-hr-intelligence.sh "<absolute approved deploy.env>" "<absolute approved Bundle directory>"
```

The script must validate every checksum before database writes, copy to a temporary deployment-ID directory on `/data`, commit records and publication pointer transactionally, atomically rename the verified Bundle, and clean only its own staging directory through `trap` on success or failure. A failed import must leave the previous publication current.

- [ ] **Step 7: Run authenticated business acceptance**

Verify through the actual HR workspace:

- current report shows the approved Bundle ID, cutoff time, 12-company coverage, limitations, and source provenance;
- company, social/campus, role-family, direction, location, seniority, and time-series views use the imported normalized data;
- report PDF and XLSX downloads match the approved local hashes byte-for-byte;
- evidence links resolve to the correct immutable evidence;
- one representative position task receives relevant Bundle-backed context and shows its references;
- there is no update/analyze/run/resume button or background-analysis wording;
- missing data is shown as unknown/partial/failed, never as zero.

- [ ] **Step 8: Publish the mandatory deployment report**

Include:

- `df -B1 / /data` before and after;
- added file/directory sizes and explanation of any net growth;
- deployed version and current Bundle ID;
- current release plus exactly two rollback releases;
- deleted or archived releases, with archive retention no greater than 10 versions or 30 days;
- confirmation that the exact staging directory is empty;
- current and two rollback Docker images for only this service;
- authenticated HTTP/business acceptance results and document hashes;
- confirmation that no other application or shared Nginx configuration changed.

---

## Plan Completion Gate

The work is complete only when all nine tasks pass, the first Bundle was created and reviewed locally, exact Owner approval preceded production access, production contains no collection/model execution capability, the Bundle imported atomically, task context carries immutable provenance, downloads match local hashes, and the full deployment report is delivered. Passing unit tests alone is not completion.
