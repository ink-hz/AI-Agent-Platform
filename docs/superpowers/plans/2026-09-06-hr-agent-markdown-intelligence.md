# HR Agent Markdown Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an evidence-grounded Markdown intelligence layer that every core HR Agent task can retrieve from, while upgrading the local recruiting analysis to company-specific, multi-source, second-level technical intelligence.

**Architecture:** The local-only intelligence factory remains the sole producer. It compiles verified JSON facts and accepted GPT analyses into immutable Markdown documents plus a deterministic chunk index inside Bundle v2; production only verifies, imports, stores, reads, ranks, and pins those prebuilt chunks. Existing Bundle v1 remains readable as last-known-good, and Markdown failures fall back to the current structured retrieval path without triggering collection or analysis.

**Tech Stack:** Python 3.12, dataclasses, standard-library JSON/YAML-subset parsing, PostgreSQL JSONB migrations, pytest, existing FastAPI/React HR workbench, ReportLab, openpyxl.

## Global Constraints

- All collection, normalization, GPT analysis, Markdown generation, PDF generation, and Excel generation run locally only.
- Production has no collector, model client, model credential, scheduler, run, resume, retry-analysis, or update endpoint.
- `normalized-jobs.jsonl`, `aggregates.json`, `analysis.json`, and archived evidence remain the canonical audit substrate; Markdown is the Agent-readable semantic layer.
- Every factual Markdown claim keeps a source URL, evidence SHA-256, and observation time; every inference names its supporting fact IDs.
- An unavailable or failed source is never represented as zero hiring.
- A single observation baseline never produces month-over-month growth, contraction, HC, budget, or R&D-spend claims.
- Each Turn pins one Bundle ID, Manifest SHA-256, ordered chunk IDs, chunk hashes, retrieval version, and exact Markdown context.
- The total stored prompt document remains at or below 32 KiB UTF-8.
- A Markdown read or verification failure falls back to verified structured data and never starts production work.
- Finished Bundles are immutable; any code, data, analysis, or document change creates a new Bundle ID.
- Do not modify or delete `backend/.venv` or any unrelated untracked file.
- Do not push, deploy, import a Bundle, access production, or modify Nginx without a later exact Owner approval for the final code SHA and Bundle ID.

---

## File and Responsibility Map

- Create `backend/tools/hr_intelligence/agent_markdown.py`: compile accepted facts and analysis into safe, deterministic Markdown documents.
- Create `backend/tools/hr_intelligence/chunk_index.py`: split Markdown by headings, calculate hashes, validate routing metadata, and serialize the index.
- Create `backend/tools/hr_intelligence/taxonomy.py`: normalize locations and emit second-level technical directions.
- Create `backend/tools/hr_intelligence/analysis_quality.py`: reject generic duplicated inferences, orphan claims, missing alternatives, and unsupported recommendations.
- Create `backend/tools/hr_intelligence/public_documents.py`: represent and archive approved non-recruitment company evidence for analysis.
- Create `backend/tools/hr_intelligence/source_catalog.v2.json`: add typed official company/product/business sources without removing recruitment sources.
- Modify `backend/tools/hr_intelligence/models.py`: preserve raw location and carry normalized location safely.
- Modify `backend/tools/hr_intelligence/dimensions.py`: add secondary directions and normalized company comparisons.
- Modify `backend/tools/hr_intelligence/analysis_units.py`: add v2 claim identities, alternatives, recommendations, topic/task units, and non-recruitment evidence.
- Modify `backend/tools/hr_intelligence/bundle.py`: build Bundle v2, include `agent/`, and keep v1 verification compatibility.
- Modify `backend/tools/hr_intelligence/exports.py`: make `report.md` human-readable instead of embedding raw JSON and all jobs.
- Modify `backend/tools/hr_intelligence/cli.py`: collect documents, run quality checks, build v2, and print local acceptance summaries.
- Create `backend/app/hr/intelligence_markdown.py`: hash-verify and read exact immutable Markdown chunks from the published Bundle filesystem.
- Modify `backend/app/hr/intelligence_bundle.py`: strictly verify Bundle v2 and expose its chunk index to the importer.
- Create `backend/control_migrations/087_hr_intelligence_markdown_context.sql`: import v2 metadata and persist immutable position and general-conversation chunk retrieval records.
- Modify `backend/app/hr/intelligence_import.py`: pass the verified v2 chunk index into the v87 import boundary.
- Modify `backend/app/hr/intelligence_documents.py`: read any Manifest-indexed Agent Markdown path through the existing verified Bundle root.
- Modify `backend/app/hr/panorama_repository.py`: read v2 chunk metadata through restricted SQL functions.
- Modify `backend/app/hr/panorama_context.py`: rank relevant chunks, inject Markdown, pin exact content, and retain structured fallback.
- Modify `backend/app/main.py`: inject the existing read-only Bundle filesystem store into the context provider.
- Modify `backend/app/agent_brain/conversation_context.py`: size and carry the Markdown prompt document without changing the 96 KiB overall context limit.
- Create `backend/tools/hr_intelligence/retrieval_eval.py`: evaluate deterministic retrieval against a fixed case set.
- Create `backend/tools/hr_intelligence/retrieval_eval_cases.v1.json`: at least 40 cases covering general chat, seven concrete task kinds, and evidence gaps.

---

### Task 1: Compile deterministic Agent Markdown and a safe chunk index

**Files:**
- Create: `backend/tools/hr_intelligence/agent_markdown.py`
- Create: `backend/tools/hr_intelligence/chunk_index.py`
- Create: `backend/tests/test_hr_intelligence_agent_markdown.py`

**Interfaces:**
- Consumes: Bundle ID, generated time, source catalog, source coverage, aggregates, and accepted-analysis dictionaries.
- Produces: `AgentMarkdownPackage(files: Mapping[str, bytes], chunks: tuple[MarkdownChunk, ...])`.
- Produces: `compile_agent_markdown(...) -> AgentMarkdownPackage` and `validate_chunk_index(files, chunks) -> None`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_compiles_company_markdown_with_provenance_and_claim_classes():
    package = compile_agent_markdown(
        bundle_id=BUNDLE_ID,
        generated_at=NOW,
        catalog=CATALOG,
        coverage=COVERAGE,
        aggregates=AGGREGATES,
        analyses=(COMPANY_ANALYSIS,),
    )
    body = package.files["agent/companies/hesai.md"].decode("utf-8")
    assert f"bundle_id: {BUNDLE_ID}" in body
    assert "## 可引用事实" in body
    assert "事实 F-company-hesai-1" in body
    assert "研判 I-company-hesai-1" in body
    assert "未知 U-company-hesai-1" in body
    assert "替代解释 X-company-hesai-1" in body
    assert "建议 R-company-hesai-1" in body
    assert "https://example.com/jobs/1" in body
    assert "a" * 64 in body


def test_chunk_index_is_deterministic_and_covers_every_agent_document():
    first = compile_agent_markdown(**INPUTS)
    second = compile_agent_markdown(**INPUTS)
    assert first.files == second.files
    assert [item.as_dict() for item in first.chunks] == [
        item.as_dict() for item in second.chunks
    ]
    validate_chunk_index(first.files, first.chunks)
    assert {item.path for item in first.chunks} == set(first.files) - {
        "agent/chunk-index.json"
    }


def test_rejects_html_script_path_escape_and_orphan_evidence():
    with pytest.raises(MarkdownContractError):
        render_markdown_document(
            metadata=VALID_METADATA,
            title="<script>alert(1)</script>",
            claims=(ORPHAN_FACT,),
        )
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_agent_markdown.py`

Expected: FAIL because `tools.hr_intelligence.agent_markdown` and `chunk_index` do not exist.

- [ ] **Step 3: Implement the immutable package types and safe renderer**

```python
@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    chunk_id: str
    path: str
    heading: str
    byte_start: int
    byte_end: int
    sha256: str
    scope: str
    scope_key: str
    companies: tuple[str, ...]
    tracks: tuple[str, ...]
    job_families: tuple[str, ...]
    directions: tuple[str, ...]
    secondary_directions: tuple[str, ...]
    locations: tuple[str, ...]
    seniority: tuple[str, ...]
    skills: tuple[str, ...]
    task_kinds: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    priority: int


@dataclass(frozen=True, slots=True)
class AgentMarkdownPackage:
    files: Mapping[str, bytes]
    chunks: tuple[MarkdownChunk, ...]


def compile_agent_markdown(
    *, bundle_id: UUID, generated_at: datetime,
    catalog: Mapping[str, object], coverage: Mapping[str, object],
    aggregates: Mapping[str, object],
    analyses: tuple[Mapping[str, object], ...],
) -> AgentMarkdownPackage:
    documents = _compile_documents(
        bundle_id, generated_at, catalog, coverage, aggregates, analyses
    )
    chunks = build_chunk_index(documents)
    index_body = canonical_json([item.as_dict() for item in chunks]) + "\n"
    files = dict(documents) | {"agent/chunk-index.json": index_body.encode("utf-8")}
    validate_chunk_index(files, chunks)
    return AgentMarkdownPackage(files, chunks)
```

The renderer must HTML-escape untrusted source text, reject NUL/control characters, allow only declared Front Matter keys, use LF newlines, sort all maps and claims deterministically, and cap each section at 24 KiB before indexing.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_agent_markdown.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/tools/hr_intelligence/agent_markdown.py backend/tools/hr_intelligence/chunk_index.py backend/tests/test_hr_intelligence_agent_markdown.py
git commit -m "feat(hr): compile agent markdown intelligence"
```

---

### Task 2: Build and verify Bundle v2 while preserving Bundle v1 reads

**Files:**
- Modify: `backend/tools/hr_intelligence/bundle.py`
- Modify: `backend/tools/hr_intelligence/exports.py`
- Modify: `backend/tests/test_hr_intelligence_bundle.py`
- Modify: `backend/tests/test_hr_intelligence_exports.py`

**Interfaces:**
- Consumes: `compile_agent_markdown(...) -> AgentMarkdownPackage` from Task 1.
- Produces: Bundle schema version 2 with a hash-covered `agent/` tree and `agent_document_index`.
- Extends: `VerifiedBundle` with `schema_version: int`.
- Preserves: `verify_bundle(path, strict=True)` for completed v1 Bundles.

- [ ] **Step 1: Write failing Bundle v2 and human-report tests**

```python
def test_v2_bundle_contains_hash_verified_agent_markdown(tmp_path):
    path = build_bundle(_inputs(tmp_path), root=tmp_path / "bundles")
    manifest = json.loads((path / "manifest.json").read_text("utf-8"))
    chunk_index = json.loads((path / "agent/chunk-index.json").read_text("utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["agent_chunk_count"] == len(chunk_index) > 0
    assert "agent/index.md" in manifest["agent_document_index"]
    assert verify_bundle(path, strict=True).schema_version == 2


def test_human_markdown_does_not_embed_raw_json_or_all_jobs():
    body = build_markdown(_report_input()).decode("utf-8")
    assert "```json" not in body
    assert "## 原始岗位" not in body
    assert "原始岗位明细请查看 report.xlsx" in body


def test_v1_bundle_remains_verifiable(v1_bundle_fixture):
    assert verify_bundle(v1_bundle_fixture, strict=True).schema_version == 1
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_bundle.py tests/test_hr_intelligence_exports.py`

Expected: FAIL because the builder still emits schema v1 without `agent/`.

- [ ] **Step 3: Add version-specific required-file validation and v2 assembly**

```python
def _required_top_level(schema_version: int) -> frozenset[str]:
    base = frozenset({
        "manifest.json", "source-catalog.json", "source-coverage.json",
        "raw-evidence-index.json", "normalized-jobs.jsonl", "aggregates.json",
        "analysis.json", "analysis-usage.json", "report.md", "report.pdf",
        "report.xlsx", "checksums.sha256", "evidence",
    })
    return base if schema_version == 1 else base | {"agent"}
```

Build the Agent package before the Manifest, write every Agent file atomically, add every Markdown file to `agent_document_index`, include `agent_chunk_count`, and let `checksums.sha256` cover the complete tree. Strict verification must reject unexpected Agent files, chunk/file hash mismatches, path escapes, symlinks, oversized sections, and orphan evidence IDs.

- [ ] **Step 4: Run Bundle and export regression tests**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_bundle.py tests/test_hr_intelligence_exports.py tests/test_hr_intelligence_cli.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/tools/hr_intelligence/bundle.py backend/tools/hr_intelligence/exports.py backend/tests/test_hr_intelligence_bundle.py backend/tests/test_hr_intelligence_exports.py
git commit -m "feat(hr): package markdown intelligence bundle v2"
```

---

### Task 3: Normalize geography and add second-level technical taxonomy

**Files:**
- Create: `backend/tools/hr_intelligence/taxonomy.py`
- Modify: `backend/tools/hr_intelligence/models.py`
- Modify: `backend/tools/hr_intelligence/normalize.py`
- Modify: `backend/tools/hr_intelligence/dimensions.py`
- Modify: `backend/tests/test_hr_local_intelligence_dimensions.py`
- Create: `backend/tests/test_hr_intelligence_taxonomy.py`

**Interfaces:**
- Produces: `normalize_location(raw: str) -> NormalizedLocation`.
- Produces: `secondary_directions(job: NormalizedJob) -> tuple[str, ...]`.
- Extends: serialized jobs with `raw_location` while retaining normalized `location`.
- Extends: aggregates schema v3 with `secondary_directions` and `company_comparison`.

- [ ] **Step 1: Write failing normalization and taxonomy tests**

```python
@pytest.mark.parametrize(("raw", "normalized", "valid"), [
    ("广东省·深圳市", "深圳", True),
    ("深圳市/东莞市", "深圳、东莞", True),
    ("上海市", "上海", True),
    ("北揽", "未规范", False),
])
def test_normalize_location_preserves_raw_without_inventing_a_city(
    raw, normalized, valid
):
    result = normalize_location(raw)
    assert (result.raw, result.normalized, result.valid) == (raw, normalized, valid)


def test_secondary_taxonomy_distinguishes_point_cloud_and_slam(job):
    selected = secondary_directions(replace(
        job,
        title="点云 SLAM 算法工程师",
        duty_excerpt="负责激光雷达定位、建图和点云配准",
    ))
    assert selected == ("算法/点云", "算法/SLAM")


def test_company_comparison_contains_counts_shares_and_coverage(job_set):
    result = compile_panorama_dimensions(job_set)
    hesai = result["company_comparison"]["hesai"]
    assert hesai["absolute"]["job_count"] > 0
    assert 0 <= hesai["internal_share"]["算法"] <= 1
    assert hesai["sample_confidence"] in {"low", "medium", "high"}
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_taxonomy.py tests/test_hr_local_intelligence_dimensions.py`

Expected: FAIL because the new taxonomy interfaces and aggregate layers do not exist.

- [ ] **Step 3: Implement auditable normalization and v3 aggregates**

```python
@dataclass(frozen=True, slots=True)
class NormalizedLocation:
    raw: str
    normalized: str
    parts: tuple[str, ...]
    valid: bool


def normalize_location(raw: str) -> NormalizedLocation:
    selected = _clean(raw)
    parts = tuple(dict.fromkeys(_normalize_city(part) for part in _split(selected)))
    valid = bool(parts) and all(part in APPROVED_LOCATIONS for part in parts)
    return NormalizedLocation(
        raw=selected,
        normalized="、".join(parts) if valid else "未规范",
        parts=parts if valid else (),
        valid=valid,
    )
```

Do not guess a city for invalid values. Preserve the raw field, exclude invalid values from geographic conclusions, and list them under `data_quality.invalid_locations`. Implement all second-level names from the approved design as explicit, tested regex rules. Sort multi-label results by a stable taxonomy order.

- [ ] **Step 4: Run taxonomy, collection, dimensions, and Bundle tests**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_taxonomy.py tests/test_hr_local_intelligence_collection.py tests/test_hr_local_intelligence_dimensions.py tests/test_hr_intelligence_bundle.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add backend/tools/hr_intelligence/taxonomy.py backend/tools/hr_intelligence/models.py backend/tools/hr_intelligence/normalize.py backend/tools/hr_intelligence/dimensions.py backend/tests/test_hr_intelligence_taxonomy.py backend/tests/test_hr_local_intelligence_dimensions.py
git commit -m "feat(hr): add auditable recruiting taxonomy"
```

---

### Task 4: Enforce company-specific evidence, alternatives, and actions

**Files:**
- Create: `backend/tools/hr_intelligence/analysis_schema.v2.json`
- Create: `backend/tools/hr_intelligence/analysis_quality.py`
- Modify: `backend/tools/hr_intelligence/analysis_units.py`
- Modify: `backend/tools/hr_intelligence/cli.py`
- Modify: `backend/tests/test_hr_local_intelligence_analysis.py`
- Create: `backend/tests/test_hr_intelligence_analysis_quality.py`

**Interfaces:**
- Produces: analysis response schema v2 with identified facts, inferences, alternatives, unknowns, and recommendations.
- Produces: `validate_analysis_set(analyses: tuple[AcceptedAnalysis, ...]) -> AnalysisQualityReport`.
- Adds stable unit kinds `secondary-direction`, `topic`, and `task`, without breaking verification of accepted v1 analyses.

- [ ] **Step 1: Write failing v2 response and cross-unit quality tests**

```python
def test_v2_analysis_requires_grounded_alternative_and_task_recommendation(unit):
    response = {
        "schema_version": 2,
        "facts": [FACT],
        "inferences": [{
            "inference_id": "I-hesai-1",
            "text": "器件到量产质量的纵向能力建设信号",
            "basis_fact_ids": ["F-hesai-1"],
        }],
        "alternatives": [{
            "alternative_id": "X-hesai-1",
            "text": "常年开放岗位可能放大当期需求",
            "basis_fact_ids": ["F-hesai-1"],
            "challenged_inference_ids": ["I-hesai-1"],
        }],
        "unknowns": ["实际 HC 未公开"],
        "recommendations": [{
            "recommendation_id": "R-hesai-1",
            "text": "建立光机电和失效分析人才池",
            "basis_fact_ids": ["F-hesai-1"],
            "target_tasks": ["talent_profile", "sourcing_strategy"],
        }],
        "summary": "禾赛公开信号摘要",
        "confidence": "high",
    }
    assert accept_unit_response(unit, response, USAGE).response_sha256


def test_rejects_repeated_generic_inferences_across_companies(accepted_analyses):
    duplicated = replace_company_inference(
        accepted_analyses, "多个不同岗位样本共同出现，支持把该信号视为能力组合"
    )
    with pytest.raises(AnalysisContractError, match="generic duplicate"):
        validate_analysis_set(duplicated)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_local_intelligence_analysis.py tests/test_hr_intelligence_analysis_quality.py`

Expected: FAIL because schema v2 and cross-unit quality validation do not exist.

- [ ] **Step 3: Implement the versioned contract and quality gate**

```python
@dataclass(frozen=True, slots=True)
class AnalysisQualityReport:
    unit_count: int
    factual_claim_count: int
    inference_count: int
    recommendation_count: int
    duplicate_pairs: tuple[tuple[str, str], ...]
    passed: bool


def validate_analysis_set(
    analyses: tuple[AcceptedAnalysis, ...],
) -> AnalysisQualityReport:
    _require_all_claim_references(analyses)
    _require_company_alternatives(analyses)
    _require_task_recommendations(analyses)
    duplicate_pairs = _generic_duplicate_pairs(analyses, threshold=0.88)
    if duplicate_pairs:
        raise AnalysisContractError("analysis generic duplicate")
    return _report(analyses)
```

V2 request instructions must explicitly ask for company-specific product/technology/organization signals, Orbbec implications, contrary evidence, and uncertainty. `prepare_units` must generate company, track, first-level direction, second-level direction, product-route, talent-competition, geography, trend, executive-summary, and five task-playbook units in stable order.

- [ ] **Step 4: Run analysis and CLI tests**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_local_intelligence_analysis.py tests/test_hr_intelligence_analysis_quality.py tests/test_hr_intelligence_cli.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add backend/tools/hr_intelligence/analysis_schema.v2.json backend/tools/hr_intelligence/analysis_quality.py backend/tools/hr_intelligence/analysis_units.py backend/tools/hr_intelligence/cli.py backend/tests/test_hr_local_intelligence_analysis.py backend/tests/test_hr_intelligence_analysis_quality.py
git commit -m "feat(hr): require deep grounded recruiting analysis"
```

---

### Task 5: Add approved non-recruitment company evidence locally

**Files:**
- Create: `backend/tools/hr_intelligence/public_documents.py`
- Create: `backend/tools/hr_intelligence/source_catalog.v2.json`
- Modify: `backend/tools/hr_intelligence/collectors.py`
- Modify: `backend/tools/hr_intelligence/evidence.py`
- Modify: `backend/tools/hr_intelligence/analysis_units.py`
- Modify: `backend/tools/hr_intelligence/cli.py`
- Create: `backend/tests/test_hr_intelligence_public_documents.py`
- Modify: `backend/tests/test_hr_local_intelligence_collection.py`

**Interfaces:**
- Produces: `PublicIntelligenceDocument` with company, source type, URL, title, text excerpt, evidence hash, observed time, and trust tier.
- Produces: `PublicDocumentTarget` as the validated, approved input to the local document collector.
- Produces: `collect_public_document(target, archive, client) -> PublicIntelligenceDocument`.
- Extends analysis requests with `public_documents`, separate from `jobs`.
- Extends analysis evidence references with `evidence_kind: Literal["job", "public_document"]` and `evidence_id: UUID`, so non-job evidence never pretends to be a job.

- [ ] **Step 1: Write failing approved-source and prompt-separation tests**

```python
def test_collects_only_approved_official_document_and_archives_evidence(tmp_path):
    document = collect_public_document(
        PublicDocumentTarget(
            company_key="hesai",
            source_type="official_product",
            source_url="https://www.hesaitech.com/product/",
            trust_tier="primary",
        ),
        archive=EvidenceArchive(tmp_path),
        client=FakeClient(OFFICIAL_PRODUCT_HTML),
    )
    assert document.company_key == "hesai"
    assert document.source_type == "official_product"
    assert document.evidence_sha256 == hashlib.sha256(OFFICIAL_PRODUCT_HTML).hexdigest()


def test_analysis_request_keeps_jobs_and_company_documents_separate(unit):
    request = json.loads(unit.request_json)
    assert request["jobs"]
    assert request["public_documents"]
    assert request["public_documents"][0]["trust_tier"] == "primary"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_public_documents.py tests/test_hr_local_intelligence_collection.py`

Expected: FAIL because public-document contracts and typed catalog entries do not exist.

- [ ] **Step 3: Implement local-only document collection and catalog v2**

```python
@dataclass(frozen=True, slots=True)
class PublicDocumentTarget:
    company_key: str
    source_type: str
    source_url: str
    trust_tier: Literal["primary", "secondary"]


@dataclass(frozen=True, slots=True)
class PublicIntelligenceDocument:
    document_id: UUID
    company_key: str
    source_type: Literal[
        "official_product", "official_solution", "official_news",
        "official_financial", "official_campus", "patent_or_paper",
        "reviewed_industry_interview",
    ]
    source_url: str
    title: str
    text_excerpt: str
    evidence_sha256: str
    observed_at: datetime
    trust_tier: Literal["primary", "secondary"]
```

Catalog v2 must preserve every existing recruitment URL and add these typed official-root targets:

```json
{
  "union-optech": "https://www.union-optech.com",
  "robosense": "https://www.robosense.ai",
  "hesai": "https://www.hesaitech.com",
  "bambu-lab": "https://bambulab.com",
  "creality": "https://www.creality.cn",
  "elegoo": "https://www.elegoo.com.cn",
  "revopoint": "https://www.revopoint3d.com",
  "shining3d": "https://www.shining3d.com",
  "scantech": "https://www.3d-scantech.com.cn",
  "agibot": "https://www.zhiyuan-robot.com",
  "insta360": "https://www.insta360.com",
  "huawei": "https://www.huawei.com/cn"
}
```

At execution time, the collector accepts a root only when TLS succeeds, the final origin remains approved, and the page identifies the expected company. A mismatch is recorded as `source_identity_mismatch`; it is not silently replaced with an unapproved domain. Redirects remain restricted to approved origins. HTML extraction strips navigation, scripts, forms, cookies, and prompt-like control text; PDF extraction stores the original bytes and normalized text hash. A failed document source is recorded independently and cannot change a successful recruitment channel to failed.

- [ ] **Step 4: Require cross-source grounding for strategic claims**

Add an analysis quality rule: any high-confidence inference tagged `product_route`, `business_direction`, or `organization_chain` must cite at least one recruitment fact and one primary non-recruitment fact. Otherwise the response must use medium/low confidence and include the missing cross-check in `unknowns`.

- [ ] **Step 5: Run collection, evidence, analysis, and architecture tests**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_public_documents.py tests/test_hr_local_intelligence_collection.py tests/test_hr_panorama_evidence.py tests/test_hr_local_intelligence_analysis.py tests/test_hr_intelligence_architecture_boundary.py`

Expected: PASS, and architecture tests prove all network-capable code remains under `backend/tools/hr_intelligence`.

- [ ] **Step 6: Commit Task 5**

```bash
git add backend/tools/hr_intelligence/public_documents.py backend/tools/hr_intelligence/source_catalog.v2.json backend/tools/hr_intelligence/collectors.py backend/tools/hr_intelligence/evidence.py backend/tools/hr_intelligence/analysis_units.py backend/tools/hr_intelligence/cli.py backend/tests/test_hr_intelligence_public_documents.py backend/tests/test_hr_local_intelligence_collection.py
git commit -m "feat(hr): ground recruiting intelligence in company sources"
```

---

### Task 6: Import Bundle v2 and read immutable Markdown chunks without execution

**Files:**
- Modify: `backend/app/hr/intelligence_bundle.py`
- Create: `backend/app/hr/intelligence_markdown.py`
- Create: `backend/control_migrations/087_hr_intelligence_markdown_context.sql`
- Modify: `backend/app/hr/intelligence_import.py`
- Modify: `backend/app/hr/intelligence_documents.py`
- Modify: `backend/app/hr/panorama_repository.py`
- Modify: `backend/tests/test_hr_intelligence_bundle_migration.py`
- Create: `backend/tests/test_hr_intelligence_markdown_migration.py`
- Modify: `backend/tests/test_hr_intelligence_import.py`
- Modify: `backend/tests/test_hr_intelligence_import_database.py`
- Modify: `backend/tests/test_hr_intelligence_documents.py`
- Create: `backend/tests/test_hr_intelligence_markdown.py`

**Interfaces:**
- Extends: `VerifiedImportBundle` with `schema_version`, `agent_chunk_index`, and `agent_document_index`.
- Produces: `IntelligenceMarkdownStore.read_chunk(bundle_id, chunk_record) -> VerifiedMarkdownChunk`.
- Produces: `IntelligenceDocumentStore.read_indexed_path(bundle_id, relative_path, expected_mime) -> VerifiedDocument`.
- Produces: restricted SQL functions `import_intelligence_bundle_v87` and `read_intelligence_bundle_chunks_v87`.

- [ ] **Step 1: Write failing migration and filesystem-reader tests**

```python
def test_v87_imports_v2_chunk_index_but_exposes_no_execution_surface():
    sql = MIGRATION.read_text("utf-8").lower()
    assert "agent_chunk_index jsonb" in sql
    assert "conversation_intelligence_bundle_references" in sql
    assert "import_intelligence_bundle_v87" in sql
    assert "read_intelligence_bundle_chunks_v87" in sql
    assert "create_conversation_intelligence_reference_v87" in sql
    for forbidden in ("collect", "analyze", "model_secret", "http", "scheduler"):
        assert forbidden not in sql


def test_reads_exact_hash_verified_markdown_chunk(tmp_path):
    store, record, chunk = markdown_fixture(tmp_path)
    selected = store.read_chunk(record["bundle_id"], chunk)
    assert selected.text.startswith("## 可引用事实")
    assert selected.sha256 == chunk["sha256"]


def test_rejects_tampered_or_out_of_bounds_chunk(tmp_path):
    store, record, chunk = markdown_fixture(tmp_path)
    chunk["byte_end"] += 1
    with pytest.raises(PanoramaUnavailable):
        store.read_chunk(record["bundle_id"], chunk)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_bundle_migration.py tests/test_hr_intelligence_markdown_migration.py tests/test_hr_intelligence_import.py tests/test_hr_intelligence_import_database.py tests/test_hr_intelligence_documents.py tests/test_hr_intelligence_markdown.py`

Expected: FAIL because v87 and the Markdown reader do not exist.

- [ ] **Step 3: Implement v2 verification and read-only storage interfaces**

```python
@dataclass(frozen=True, slots=True)
class VerifiedMarkdownChunk:
    chunk_id: str
    path: str
    sha256: str
    text: str


class IntelligenceMarkdownStore:
    def read_chunk(
        self, bundle_id: UUID, record: Mapping[str, object]
    ) -> VerifiedMarkdownChunk:
        document = self._documents.read_indexed_path(
            bundle_id, str(record["path"]), expected_mime="text/markdown"
        )
        body = document.body[
            _offset(record["byte_start"]):_offset(record["byte_end"])
        ]
        if hashlib.sha256(body).hexdigest() != record["sha256"]:
            raise PanoramaUnavailable("intelligence Markdown chunk checksum mismatch")
        return VerifiedMarkdownChunk(
            str(record["chunk_id"]), str(record["path"]),
            str(record["sha256"]), body.decode("utf-8"),
        )
```

Migration v87 must allow schema versions 1 and 2, add a non-null default empty chunk index for existing v1 rows, validate v2 counts and JSON shapes, preserve idempotency, revoke direct table access, and grant only explicit import/read functions. It must also add an immutable general-conversation Turn reference keyed by owner, conversation, and Turn so explicit company-intelligence questions outside a position can pin Markdown. It must not modify or delete existing v1 Bundle rows or v86 position references.

- [ ] **Step 4: Run import, reader, migration, and architecture tests**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_bundle_migration.py tests/test_hr_intelligence_markdown_migration.py tests/test_hr_intelligence_import.py tests/test_hr_intelligence_import_database.py tests/test_hr_intelligence_documents.py tests/test_hr_intelligence_markdown.py tests/test_hr_intelligence_architecture_boundary.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add backend/app/hr/intelligence_bundle.py backend/app/hr/intelligence_markdown.py backend/app/hr/intelligence_documents.py backend/control_migrations/087_hr_intelligence_markdown_context.sql backend/app/hr/intelligence_import.py backend/app/hr/panorama_repository.py backend/tests/test_hr_intelligence_bundle_migration.py backend/tests/test_hr_intelligence_markdown_migration.py backend/tests/test_hr_intelligence_import.py backend/tests/test_hr_intelligence_import_database.py backend/tests/test_hr_intelligence_documents.py backend/tests/test_hr_intelligence_markdown.py
git commit -m "feat(hr): import and read markdown intelligence"
```

---

### Task 7: Retrieve relevant Markdown and pin exact Turn context

**Files:**
- Modify: `backend/app/hr/panorama_context.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/agent_brain/conversation_context.py`
- Modify: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/app/hr/task_repository.py`
- Modify: `backend/tests/test_hr_panorama_context.py`
- Modify: `backend/tests/test_agent_brain_conversation_context.py`
- Modify: `backend/tests/test_hr_task_context_recovery.py`

**Interfaces:**
- Produces: prompt-document schema v3 with `markdown_context`, ordered `chunks`, `retrieval_version`, `manifest_sha256`, and optional `degraded_reason`.
- Preserves: `PanoramaContextProvider.for_turn(...) -> PanoramaContextFragment | None`.
- Produces: `PanoramaContextProvider.for_conversation_turn(...) -> PanoramaContextFragment | None` for explicit recruiting-intelligence chat outside a position.
- Consumes: `IntelligenceMarkdownStore.read_chunk(...)` from Task 6.

- [ ] **Step 1: Write failing retrieval, task coverage, retry, and fallback tests**

```python
@pytest.mark.parametrize("task_kind", [
    "jd", "jr", "talent_profile", "sourcing_strategy",
    "candidate_match", "position_interview_plan", "candidate_interview_plan",
])
def test_all_core_hr_tasks_receive_relevant_markdown(task_kind):
    fragment = provider().for_turn(
        OWNER, POSITION, "处理当前岗位", TURN,
        task_kind=task_kind,
        position_context={"title": "点云 SLAM 算法工程师", "location": "深圳"},
    )
    document = fragment.as_prompt_document()
    assert document["schema_version"] == 3
    assert "## 招聘情报上下文" in document["markdown_context"]
    assert "算法/点云" in document["markdown_context"]
    assert document["chunks"]
    assert len(json.dumps(document, ensure_ascii=False).encode()) <= 32 * 1024


def test_replay_uses_exact_pinned_markdown_after_current_bundle_changes():
    first = provider().for_turn(OWNER, POSITION, QUERY, TURN, **CONTEXT)
    source.publish_new_bundle()
    replay = provider().for_turn(OWNER, POSITION, QUERY, TURN, **CONTEXT)
    assert replay.as_prompt_document() == first.as_prompt_document()


def test_tampered_markdown_falls_back_without_running_any_producer():
    fragment = provider(tampered=True).for_turn(
        OWNER, POSITION, QUERY, TURN, **CONTEXT
    )
    document = fragment.as_prompt_document()
    assert document["degraded_reason"] == "markdown_verification_failed"
    assert document["source_facts"]
    assert producer_calls == []


def test_general_hr_chat_can_retrieve_named_company_intelligence():
    fragment = provider().for_conversation_turn(
        OWNER, CONVERSATION, "分析禾赛当前招聘和产品路线", TURN
    )
    assert "禾赛" in fragment.markdown_context
    assert fragment.bundle_id is not None
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_panorama_context.py tests/test_agent_brain_conversation_context.py tests/test_hr_task_context_recovery.py`

Expected: FAIL because schema v3 Markdown retrieval is not implemented.

- [ ] **Step 3: Implement deterministic ranking and bounded assembly**

```python
@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    companies: tuple[str, ...]
    tracks: tuple[str, ...]
    job_families: tuple[str, ...]
    directions: tuple[str, ...]
    secondary_directions: tuple[str, ...]
    locations: tuple[str, ...]
    seniority: tuple[str, ...]
    skills: tuple[str, ...]
    task_kind: str


def _score(chunk: Mapping[str, object], query: RetrievalQuery) -> tuple[int, str]:
    score = 0
    score += 100 * len(set(chunk["companies"]) & set(query.companies))
    score += 50 * len(set(chunk["secondary_directions"]) & set(query.secondary_directions))
    score += 35 * len(set(chunk["job_families"]) & set(query.job_families))
    score += 30 * int(query.task_kind in chunk["task_kinds"])
    score += 10 * len(set(chunk["locations"]) & set(query.locations))
    score += 5 * len(set(chunk["skills"]) & set(query.skills))
    return (-score - int(chunk["priority"]), str(chunk["chunk_id"]))
```

Always include the usage-boundary chunk from `agent/index.md`. Then rank explicit company, second-level direction, family, task, location, skill, topic, and executive content. Deduplicate evidence and text, stop before 30 KiB so the serialized prompt document remains below 32 KiB, and store the exact selected Markdown in the immutable v86/v87 Turn reference. Position tasks use the v86 position reference; explicit intelligence questions in general HR chat use the v87 conversation reference.

- [ ] **Step 4: Wire the read-only store and Markdown prompt through Agent Brain**

`main.py` must construct one `IntelligenceDocumentStore`, wrap it in `IntelligenceMarkdownStore`, and pass it to `PanoramaContextProvider`. `ConversationContextBuilder` invokes position retrieval for bound position conversations and general retrieval only when a non-position HR conversation explicitly asks for a company, recruitment intelligence, panorama analysis, or external job-market evidence. `orchestrator.py` must carry the v3 prompt document unchanged under `hr_panorama_context`; it must not parse Markdown into instructions or invoke tools from source text.

- [ ] **Step 5: Run context, task, and orchestration regressions**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_panorama_context.py tests/test_agent_brain_conversation_context.py tests/test_hr_task_context.py tests/test_hr_task_context_recovery.py tests/test_hr_position_intelligence_api.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 7**

```bash
git add backend/app/hr/panorama_context.py backend/app/main.py backend/app/agent_brain/conversation_context.py backend/app/agent_brain/orchestrator.py backend/app/hr/task_repository.py backend/tests/test_hr_panorama_context.py backend/tests/test_agent_brain_conversation_context.py backend/tests/test_hr_task_context_recovery.py
git commit -m "feat(hr): ground agent tasks in markdown intelligence"
```

---

### Task 8: Add a 40-case retrieval and complete HR-use acceptance gate

**Files:**
- Create: `backend/tools/hr_intelligence/retrieval_eval.py`
- Create: `backend/tools/hr_intelligence/retrieval_eval_cases.v1.json`
- Modify: `backend/tools/hr_intelligence/cli.py`
- Create: `backend/tests/test_hr_intelligence_retrieval_eval.py`
- Modify: `backend/tests/test_hr_panorama_context.py`
- Modify: `backend/tests/test_hr_position_intelligence_api.py`

**Interfaces:**
- Produces: `evaluate_retrieval(provider, cases) -> RetrievalEvaluation`.
- Adds CLI command: `evaluate-retrieval --bundle <absolute-path> --cases <absolute-path>`.
- Requires: at least 40 cases covering general chat and all seven concrete task kinds, named-company cases, partial-source cases, and no-match cases.

- [ ] **Step 1: Create the fixed case schema and failing gate test**

```json
{
  "schema_version": 1,
  "cases": [
    {
      "case_id": "interview-hesai-point-cloud",
      "task_kind": "position_interview_plan",
      "query": "为点云算法工程师生成面试方案",
      "position_context": {"title": "点云算法工程师", "location": "深圳"},
      "expected_companies": ["hesai"],
      "expected_tags": ["算法/点云", "interview"],
      "forbidden_companies": ["agibot"],
      "requires_evidence": true
    }
  ]
}
```

```python
def test_retrieval_acceptance_suite_meets_release_threshold(bundle_fixture):
    result = evaluate_retrieval(provider(bundle_fixture), load_cases(CASES))
    assert result.case_count >= 40
    assert result.task_kinds == {
        "jd", "jr", "talent_profile", "sourcing_strategy",
        "candidate_match", "position_interview_plan", "candidate_interview_plan",
        "general_chat",
    }
    assert result.context_relevance >= Decimal("0.90")
    assert result.provenance_coverage == Decimal("1")
    assert result.fabricated_source_count == 0
    assert result.replay_mismatch_count == 0
```

- [ ] **Step 2: Run the evaluator test and confirm RED**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_retrieval_eval.py`

Expected: FAIL because the evaluator and case file do not exist.

- [ ] **Step 3: Implement deterministic scoring and populate all cases**

Populate five cases for each of eight contexts: general chat, JD, JR, talent profile, sourcing strategy, candidate match, position interview plan, and candidate interview plan. Across the complete set, cover all 12 companies, social/campus/intern tracks, at least eight first-level directions, at least twelve second-level directions, explicit company aliases, partial coverage, invalid location, absent evidence, and replay after a new Bundle is current.

```python
@dataclass(frozen=True, slots=True)
class RetrievalEvaluation:
    case_count: int
    task_kinds: frozenset[str]
    context_relevance: Decimal
    provenance_coverage: Decimal
    fabricated_source_count: int
    replay_mismatch_count: int
    failures: tuple[str, ...]
```

- [ ] **Step 4: Add CLI output and release-failing exit status**

The CLI prints canonical JSON with every metric and failing case ID. It exits nonzero when relevance is below `0.90`, provenance below `1.00`, fabricated sources exceed `0`, replay mismatches exceed `0`, or fewer than 40 cases are loaded.

- [ ] **Step 5: Run the full focused acceptance set**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_intelligence_retrieval_eval.py tests/test_hr_panorama_context.py tests/test_hr_position_intelligence_api.py tests/test_agent_brain_conversation_context.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 8**

```bash
git add backend/tools/hr_intelligence/retrieval_eval.py backend/tools/hr_intelligence/retrieval_eval_cases.v1.json backend/tools/hr_intelligence/cli.py backend/tests/test_hr_intelligence_retrieval_eval.py backend/tests/test_hr_panorama_context.py backend/tests/test_hr_position_intelligence_api.py
git commit -m "test(hr): gate markdown intelligence retrieval"
```

---

### Task 9: Produce and verify a new local top-tier intelligence Bundle

**Files:**
- Modify only when analysis reveals a verified data defect: `backend/tools/hr_intelligence/source_catalog.v2.json`
- Create outside git: `$HOME/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/work/$NEW_BUNDLE_ID/...`
- Create outside git: `$HOME/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/$NEW_BUNDLE_ID/...`
- Modify: `docs/runbooks/hr-intelligence-bundle.md`

**Interfaces:**
- Consumes: all Tasks 1–8.
- Produces: a new immutable Bundle v2 and an exact local acceptance report.
- Does not produce: a push, deploy, production import, or production mutation.

- [ ] **Step 1: Run the complete repository baseline before real data work**

Run: `cd backend && ./.venv/bin/pytest -q`

Expected: all backend tests PASS.

Run: `cd webui && npm test -- --run && npm run build`

Expected: all frontend tests PASS and the production build succeeds.

- [ ] **Step 2: Initialize a fresh UUID Bundle using catalog v2**

```bash
NEW_BUNDLE_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
cd backend
./.venv/bin/python -m tools.hr_intelligence.cli init \
  --bundle-id "$NEW_BUNDLE_ID" \
  --catalog "$PWD/tools/hr_intelligence/source_catalog.v2.json"
```

Expected: canonical JSON identifies the new Bundle and 12 companies.

- [ ] **Step 3: Collect recruitment and approved company documents locally**

```bash
./.venv/bin/python -m tools.hr_intelligence.cli collect \
  --bundle-id "$NEW_BUNDLE_ID" --resume
./.venv/bin/python -m tools.hr_intelligence.cli collect-documents \
  --bundle-id "$NEW_BUNDLE_ID" --resume
./.venv/bin/python -m tools.hr_intelligence.cli validate \
  --bundle-id "$NEW_BUNDLE_ID" \
  --require-company-count 12 --require-provenance
```

Expected: every company has a five-state coverage record; every successful job/document references locally archived evidence; partial/failed sources remain explicit.

- [ ] **Step 4: Prepare v2 analysis units and complete them locally with GPT**

```bash
./.venv/bin/python -m tools.hr_intelligence.cli prepare-analysis \
  --bundle-id "$NEW_BUNDLE_ID" \
  --units company,track,direction,secondary-direction,topic,task,comparison,executive-summary
```

For each request under `analysis/requests`, generate one schema-v2 response locally. Save the exact response and usage record through the existing accepted-analysis path. Do not reuse generic inference text between companies. If the interactive channel does not expose per-unit token/cost telemetry, record `unavailable` with the exact reason rather than estimating.

Run: `./.venv/bin/python -m tools.hr_intelligence.cli accept-analysis --bundle-id "$NEW_BUNDLE_ID" --all-ready`

Expected: every response and usage pair is accepted exactly once.

Run: `./.venv/bin/python -m tools.hr_intelligence.cli analysis-status --bundle-id "$NEW_BUNDLE_ID" --require-complete`

Expected: pending count is `0`.

- [ ] **Step 5: Run analysis quality before Bundle assembly**

```bash
./.venv/bin/python -m tools.hr_intelligence.cli quality-check \
  --bundle-id "$NEW_BUNDLE_ID" --strict
```

Expected: zero orphan facts, zero unsupported inferences, zero high-confidence cross-source violations, zero generic duplicate company conclusions, and no missing alternatives/recommendations.

- [ ] **Step 6: Build and strictly verify Bundle v2**

```bash
./.venv/bin/python -m tools.hr_intelligence.cli build --bundle-id "$NEW_BUNDLE_ID"
BUNDLE_PATH="$HOME/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/$NEW_BUNDLE_ID"
./.venv/bin/python -m tools.hr_intelligence.cli verify --bundle "$BUNDLE_PATH" --strict
```

Expected: schema version `2`, 12 companies, nonzero jobs, nonzero accepted analyses, nonzero Agent Markdown documents, nonzero chunks, and a Manifest SHA-256.

- [ ] **Step 7: Run the 40-case retrieval gate against the real Bundle**

```bash
./.venv/bin/python -m tools.hr_intelligence.cli evaluate-retrieval \
  --bundle "$BUNDLE_PATH" \
  --cases "$PWD/tools/hr_intelligence/retrieval_eval_cases.v1.json"
```

Expected: relevance at least `0.90`, provenance exactly `1.00`, fabricated sources `0`, replay mismatches `0`, and exit status `0`.

- [ ] **Step 8: Inspect human and Agent artifacts**

Verify `report.md` is readable and does not contain raw JSON/all-job dumps. Inspect every company Markdown, every task playbook, executive brief, source coverage, unknowns, alternatives, and evidence links. Render `report.pdf` to images and visually inspect every page. Open `report.xlsx` in LibreOffice, inspect every sheet, filters, frozen headers, numeric cells, and evidence hashes.

- [ ] **Step 9: Document the exact local result and rerun full verification**

Update `docs/runbooks/hr-intelligence-bundle.md` with Bundle v2 file structure, local quality commands, schema compatibility, and the rule that production still cannot generate Markdown.

Run: `cd backend && ./.venv/bin/pytest -q`

Expected: all backend tests PASS.

Run: `cd webui && npm test -- --run && npm run build`

Expected: all frontend tests PASS and the production build succeeds.

Run: `git diff --check && git status --short`

Expected: no tracked changes beyond the runbook before its commit; unrelated user-owned untracked files remain untouched.

- [ ] **Step 10: Commit the runbook only**

```bash
git add docs/runbooks/hr-intelligence-bundle.md
git commit -m "docs(hr): operate markdown intelligence bundles"
```

- [ ] **Step 11: Stop at the production approval gate**

Report the final full code SHA, Bundle ID, Bundle path, Manifest/PDF/XLSX/Markdown hashes, source coverage, analysis limitations, retrieval metrics, full test counts, and proof that no production execution path exists. Do not push, deploy, or import. Require a new exact two-line Owner approval containing that final full SHA and new Bundle ID before any production action.

---

## Final Verification Checklist

- [ ] Bundle v1 strict verification still passes.
- [ ] Bundle v2 strict verification covers the complete `agent/` tree.
- [ ] `report.md` is human-readable and bounded.
- [ ] Agent Markdown contains facts, aggregates, inferences, alternatives, unknowns, recommendations, and evidence.
- [ ] All 12 company documents are present, including explicit partial/insufficient-evidence states.
- [ ] All six HR task types retrieve task-specific Markdown.
- [ ] Context is at most 32 KiB and exact Turn replay is stable.
- [ ] Structured fallback works after Markdown tampering without producer calls.
- [ ] At least 40 retrieval cases pass the release thresholds.
- [ ] Complete backend test suite passes.
- [ ] Complete frontend test suite and production build pass.
- [ ] No production host, database, Bundle, Nginx, or unrelated application was changed.
- [ ] Final response stops at the exact Owner approval gate.
