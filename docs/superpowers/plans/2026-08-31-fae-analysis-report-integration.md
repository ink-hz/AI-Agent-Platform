# FAE Analysis Report Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish reviewed FAE production-conversation analysis as a strict, privacy-safe, immutable report bundle; import it into Agent Platform; and provide Owner/Admin report reading, evidence drill-down, Issue association and freshness-aware overview integration.

**Approved design:** `docs/superpowers/specs/2026-08-31-fae-management-workbench-design.md`

**Depends on:**

- `docs/superpowers/plans/2026-08-31-fae-management-workbench-foundation.md` completed in Agent Platform.
- `/Users/neo/Developer/work/AI-FAE-Agent/docs/superpowers/plans/2026-08-31-production-conversation-analytics.md` completed through a verified `report_ready` run in AI-FAE-Agent.

**Architecture:** AI-FAE-Agent remains the semantic-analysis producer and Agent Platform remains the management/read model. The producer converts an already reviewed `report_ready` analysis into a versioned `fae.analysis-report/v1` ready bundle, or a blocked terminal attempt into a sanitized failed envelope. It resolves private HMAC analysis IDs back to canonical Platform evidence keys in memory and emits no raw conversation content or standalone source IDs. An explicit Platform import command validates contract digest, privacy, evidence existence and FAE scope, then stores an immutable report version in PostgreSQL. Runtime APIs read only PostgreSQL; they never read another repository or an analysis output directory.

**Tech Stack:** Python 3.11+, Pydantic 2, JSON Schema Draft 2020-12, JCS canonical JSON, psycopg 3, PostgreSQL, FastAPI, React 19, TypeScript 5.6, Vite 7, Vitest 3.2.

## Release-1 Boundaries

- The Platform UI does not start production analysis. AI-FAE-Agent analysis remains an explicitly operated, reviewed offline pipeline.
- No “立即生成”, “重新生成” or “实时报告” control is shipped. Durable scheduling and remote job control require a separate approved design.
- The only transferable producer states are `ready` and `failed`. `generating` is a future runtime/job state and is not persisted by the release-1 importer.
- A `ready` bundle is legal only when the source analysis manifest is `report_ready`, all required semantic and human FAE review gates have passed, and every published claim has evidence.
- A `failed` bundle contains only sanitized failure metadata. It contains no findings, metrics, recommendations or evidence references.
- The Platform never reads `executive_summary.md`, `full_report.md`, `audit_appendix.md`, `report.html`, private canonical JSONL or raw snapshots at request time. Structured JSON is the sole runtime input.
- Report versions are immutable. Re-importing identical bytes is idempotent; reusing `(report_id, report_version)` with a different digest is rejected.
- Contract release 1 starts at schema `1.0.0`, so it has no predecessor to support. Unknown schema versions are rejected explicitly. When the first successor is introduced, its change plan must keep the immediately preceding schema and fixtures in the consumer compatibility matrix before switching the producer.
- Evidence links use canonical Platform keys and are restricted to `agent_id=ai-fae-agent`, `source_kind=fae`. A malformed or known cross-Agent reference blocks import. A well-formed `fae:` reference absent from the current mirror is retained as unavailable with a reason, so a newer report can remain readable while the daily mirror catches up.
- Published JSON contains no raw question, raw answer, Feedback comment, attachment content/name/hash, employee/customer identity, standalone source Session/Turn ID outside the required canonical key, trace ID, HMAC key, analysis-to-source mapping or unrestricted metadata.
- Finding-to-Issue association is mutable Platform governance state. It is stored separately from the immutable imported report and produces audit events.
- Report `data_cutoff_at` is distinct from `generated_at` and `imported_at`. The UI shows all three with `Asia/Shanghai` presentation.
- If the FAE mirror has a later successful source sync than `data_cutoff_at`, the report is labelled “数据已有更新”; it is not silently described as current.
- Only `platform_owner` and `platform_admin` can list, read or mutate finding-to-Issue links. Bundle import is restricted to a deployment operator holding the dedicated secret-file credential and an approved auditable service or corporate actor. Existing cloud-replica and hard-stale mutation policies remain authoritative.
- Production `agent.orbbec.com.cn` remains a sanitized read-only cloud replica: it can read replicated reports and existing Issue associations, but it cannot create/link/unlink Issues. Those actions remain available only on the writable local management deployment until a separately approved command-relay design exists.
- Use TDD for every behavior change and make one focused commit per task in the repository modified by that task.

---

## Contract: `fae.analysis-report/v1`

The canonical schema lives in Agent Platform at `contracts/fae-analysis-report/v1/schema.json`. AI-FAE-Agent carries a pinned copy and the expected SHA-256 digest. Both repositories validate the same valid and invalid fixtures.

### Top-level object

All objects use `additionalProperties: false`. All timestamps are RFC 3339 strings with an explicit offset or `Z`. All IDs are 1-80 ASCII characters matching `^[A-Za-z0-9][A-Za-z0-9._:-]*$` unless a stricter rule is stated.

| Field | Exact rule |
|---|---|
| `schema_name` | constant `fae.analysis-report` |
| `schema_version` | constant `1.0.0` |
| `report_id` | `^fae-(weekly|topic)-[a-z0-9][a-z0-9-]{2,63}$` |
| `report_version` | integer `>= 1` |
| `report_type` | `weekly` or `topic` |
| `status` | `ready` or `failed` |
| `title` | 1-160 characters |
| `period` | object with `start_at < end_at` |
| `data_cutoff_at` | timestamp `>= period.end_at` |
| `generated_at` | timestamp `>= data_cutoff_at` |
| `analysis_version` | 1-80 characters |
| `source` | fixed FAE production identity and aggregate counts |
| `summary` | required object for `ready`, `null` for `failed` |
| `metrics` | required array for `ready`, empty for `failed`; unique `metric_id` |
| `findings` | required array for `ready`, empty for `failed`; unique `finding_id` |
| `recommendations` | required array for `ready`, empty for `failed`; unique `recommendation_id` |
| `cases` | business-approved sanitized cases; empty when no case has approval |
| `artifact_digests` | seven sanitized producer artifact names and SHA-256 digests for `ready`, empty for `failed` |
| `failure` | `null` for `ready`; required sanitized object for `failed` |

`source` contains exactly:

```json
{
  "agent_id": "ai-fae-agent",
  "source_kind": "fae",
  "environment": "production",
  "source_snapshot_at": "2026-08-31T03:20:00+08:00",
  "session_count": 120,
  "turn_count": 438,
  "feedback_event_count": 19,
  "reviewed_session_count": 32
}
```

`summary` contains `headline` (1-160 characters), `overview` (1-2000), `top_finding_ids` and `top_recommendation_ids`. Each ID must resolve within the same report and each list has at most five entries.

Each metric contains exactly:

```json
{
  "metric_id": "feedback.negative_turn_rate",
  "dimension": "answer_effectiveness",
  "label": "负向反馈回答占比",
  "value": 0.04,
  "unit": "ratio",
  "numerator": 4,
  "denominator": 100,
  "filters": ["population=included"],
  "assumptions": [],
  "evidence_artifact_refs": ["metrics.json", "claim_ledger.jsonl"]
}
```

`dimension` is one of `usage|business_value|answer_effectiveness|insights_improvement`.
Every ready report must contain at least one metric in each dimension. Findings and
recommendations carry the same `dimension` enum so the reader never guesses presentation
semantics from ID prefixes or prose.

`unit` is one of `count|ratio|percent|milliseconds|seconds|distribution|milliseconds_distribution`. Count metrics require integer `value`, and ratio/percent metrics require non-null non-negative numerator and positive denominator. A distribution metric contains a bounded object of semantic category names to non-negative integer counts or the exact small-cell marker `少于 5`; it requires a positive denominator and no numerator. `milliseconds_distribution` contains bounded non-negative numeric percentile values (for example p50/p90/p95), a positive denominator, and no numerator. This preserves governed public distributions such as `product.signal_counts_public` and latency percentiles without publishing private hashed cell IDs or inventing unreviewed metrics. All arrays contain unique strings and are capped at 20 entries.

Each evidence reference contains exactly `kind`, `canonical_key` and `label`. `kind` is `session|turn|feedback|issue`; `canonical_key` must start with `fae:` for the first three kinds, while Issue keys are UUIDs. `label` is a short neutral locator such as `Session 03` or `Turn 2`; it is not a content excerpt.

Each finding contains `finding_id`, `dimension`, `severity`, `title`, `description`, `root_cause_hypothesis`, `impact_scope`, `metric_ids`, `evidence_refs`, `recommendation_ids`, and `linked_issue_ids`. `severity` is `critical|high|medium|low|opportunity`. Descriptive fields are capped at 2,000 characters. Every referenced metric and recommendation must resolve within the bundle; a `ready` finding requires at least one metric and one evidence reference. `linked_issue_ids` contains only FAE Issue UUIDs known to the producer at publication time and is normally empty; later Platform-local associations are returned separately and never rewrite this array.

Each recommendation contains `recommendation_id`, `dimension`, `priority`, `title`, `rationale`, `proposed_action`, `owner_role`, `finding_ids`, and `success_metric_ids`. `priority` is `p0|p1|p2|p3`; `owner_role` is a bounded label, not an employee identity. All references must resolve within the bundle.

Each case contains `case_id`, `dimension`, `title`, `scenario`, `outcome`, `evidence_refs`, and the constant `business_case_approved: true`. Case prose is already business-approved and sanitized, remains bounded to 2,000 characters per field, and requires at least one evidence reference. The publisher never creates a case from raw conversation text. A report with no approved cases publishes `cases: []`.

`artifact_digests` has exactly these names: `metrics.json`, `claim_ledger.jsonl`, `action_backlog.jsonl`, `executive_summary.md`, `full_report.md`, `audit_appendix.md`, `report.html`. Each value is a lowercase 64-character SHA-256 string. It proves provenance; the Platform importer does not ingest or render these files.

`failure` contains exactly `stage`, `code`, `message`, and `retryable`. `stage` is `snapshot|population|classification|annotation|review|reporting|publication`. `code` is `snapshot_failed|population_blocked|classification_failed|annotation_incomplete|review_incomplete|report_blocked|publication_failed|sanitized_failure`. `message` is sanitized and capped at 500 characters. It may not contain filesystem paths, SQL, raw exception representation or source identifiers.

The whole JSON document is capped at 5 MiB, metrics at 200, findings at 100, recommendations at 100, and evidence references at 1,000. Duplicate JSON object keys, NaN/Infinity, invalid Unicode and non-canonical number forms are rejected before digesting.

---

## File and Boundary Map

### Agent Platform

- Create `contracts/fae-analysis-report/v1/schema.json`.
- Create `contracts/fae-analysis-report/v1/fixtures/valid-ready.json`.
- Create `contracts/fae-analysis-report/v1/fixtures/valid-failed.json`.
- Create focused invalid fixtures under `contracts/fae-analysis-report/v1/fixtures/invalid/`.
- Create `backend/app/fae_reports/__init__.py`, `contract.py`, `models.py`, `repository.py`, `service.py`, `importer.py`, `cli.py`, and `routes.py`.
- Create `backend/migrations/012_fae_analysis_reports.sql`.
- Create `backend/migrations/013_fae_report_cloud_projection.sql` for the least-privilege export read grant.
- Modify `backend/app/main.py`, `backend/app/config.py`, `backend/app/control_plane/authorization.py`, and the foundation `backend/app/fae_workbench/service.py`.
- Modify the signed/encrypted cloud-replica projection so production `agent.orbbec.com.cn` can read sanitized reports without receiving canonical source keys.
- Create backend unit/API/migration tests named in the tasks below.
- Create `webui/src/faeReportTypes.ts`, `faeReportApi.ts`, report components and report pages.
- Modify the foundation overview, Issue detail, router integration and styles.

### AI-FAE-Agent

- Create `contracts/platform/fae_analysis_report_v1.schema.json` as a byte-identical pinned copy.
- Create `contracts/platform/fae_analysis_report_v1.sha256`.
- Create `contracts/platform/fae_analysis_report_v1.commit`.
- Create synthetic contract fixtures under `contracts/platform/fixtures/` as byte-identical pinned copies.
- Create `scripts/pin_platform_report_contract.py`.
- Create `src/production_analytics/platform_publication.py`.
- Modify `src/production_analytics/runner.py`.
- Modify `scripts/run_production_conversation_analytics.py`.
- Modify `.gitignore` and dependency declarations.
- Create `tests/unit/test_production_analytics_platform_publication.py`.
- Create `tests/integration/test_platform_report_bundle.py`.

---

### Task 1: Freeze and Validate the Cross-Repository Contract

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `contracts/fae-analysis-report/v1/schema.json`
- Create: `contracts/fae-analysis-report/v1/fixtures/valid-ready.json`
- Create: `contracts/fae-analysis-report/v1/fixtures/valid-failed.json`
- Create: `contracts/fae-analysis-report/v1/fixtures/invalid/raw-content.json`
- Create: `contracts/fae-analysis-report/v1/fixtures/invalid/unresolved-reference.json`
- Create: `contracts/fae-analysis-report/v1/fixtures/invalid/failed-with-findings.json`
- Create: `contracts/fae-analysis-report/v1/fixtures/invalid/unbounded-array.json`
- Create: `backend/app/fae_reports/__init__.py`
- Create: `backend/app/fae_reports/contract.py`
- Create: `backend/app/fae_reports/models.py`
- Test: `backend/tests/test_fae_report_contract.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: `SCHEMA_NAME`, `SCHEMA_VERSION`, `CONTRACT_SHA256`, strict Pydantic report models, `load_report_document(bytes)`, `validate_report_references(report)`, and the canonical JSON Schema.
- Consumes: no database and no runtime FAE connection.

- [ ] **Step 1: Write failing valid/invalid fixture tests**

```python
def test_ready_fixture_matches_schema_and_cross_references():
    report = load_report_document(READY_FIXTURE.read_bytes())
    assert report.schema_name == "fae.analysis-report"
    assert report.schema_version == "1.0.0"
    assert report.status == "ready"
    assert report.findings[0].metric_ids == [report.metrics[0].metric_id]
    assert {metric.dimension for metric in report.metrics} == {
        "usage", "business_value", "answer_effectiveness",
        "insights_improvement",
    }
    assert all(finding.dimension in {
        "usage", "business_value", "answer_effectiveness",
        "insights_improvement",
    } for finding in report.findings)


@pytest.mark.parametrize(
    "name, code",
    [
        ("raw-content.json", "forbidden_content_field"),
        ("unresolved-reference.json", "unresolved_report_reference"),
        ("failed-with-findings.json", "invalid_failed_report"),
        ("unbounded-array.json", "report_limit_exceeded"),
    ],
)
def test_invalid_contract_fixtures_are_rejected(name, code):
    with pytest.raises(ReportContractError, match=code):
        load_report_document((INVALID_FIXTURES / name).read_bytes())
```

Also test duplicate JSON keys, a naive timestamp, `NaN`, a non-FAE evidence key, duplicate IDs, bad numerator/denominator combinations, a missing artifact digest, an unknown schema version with error `unsupported_report_schema`, and a document over 5 MiB.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_report_contract.py`

Expected: FAIL because the contract package and fixtures do not exist.

- [ ] **Step 3: Add strict Pydantic models and JSON parsing**

Use `ConfigDict(extra="forbid", strict=True)`. Decode JSON with an `object_pairs_hook` that rejects duplicate keys and `parse_constant` that rejects NaN/Infinity. Enforce the status-dependent shape in a model validator:

```python
@model_validator(mode="after")
def validate_status_shape(self) -> Self:
    if self.status == "ready":
        if self.summary is None or self.failure is not None:
            raise ValueError("invalid_ready_report")
        if not self.metrics:
            raise ValueError("empty_ready_report")
        if {metric.dimension for metric in self.metrics} != {
            "usage", "business_value", "answer_effectiveness",
            "insights_improvement",
        }:
            raise ValueError("incomplete_report_dimensions")
    else:
        if self.failure is None:
            raise ValueError("invalid_failed_report")
        if self.summary is not None or self.metrics or self.findings or self.recommendations:
            raise ValueError("invalid_failed_report")
    return self
```

Define all contract enums as `Literal` values. Validate timestamp ordering, uniqueness, limits and cross references after Pydantic structural validation. Reject keys named `question`, `answer`, `comment`, `raw_text`, `source_id`, `trace_id`, `attachment_name`, `employee_id`, `email`, `phone`, `metadata` at any object depth.

- [ ] **Step 4: Add and verify the canonical schema**

Generate the Pydantic JSON Schema once, normalize it with sorted keys and two-space indentation, then check it in. `contract.py` loads the checked-in file and verifies its SHA-256 against `CONTRACT_SHA256`; it does not regenerate schema at runtime. Add `jsonschema>=4.23,<5` to `backend/requirements.txt` and validate both valid fixtures against Draft 2020-12.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_report_contract.py`

Expected: PASS.

```bash
git add contracts/fae-analysis-report/v1 backend/app/fae_reports backend/tests/test_fae_report_contract.py backend/requirements.txt
git commit -m "feat(fae-reports): define versioned publication contract"
```

---

### Task 2: Publish a Privacy-Safe Platform Bundle from AI-FAE-Agent

**Repository:** `/Users/neo/Developer/work/AI-FAE-Agent`

**Prerequisite:** The production analytics plan has created `src/production_analytics/*` and its synthetic integration test passes. If those files are absent, complete that approved plan before starting this task; do not recreate the analytics pipeline here.

**Files:**
- Create: `contracts/platform/fae_analysis_report_v1.schema.json`
- Create: `contracts/platform/fae_analysis_report_v1.sha256`
- Create: `contracts/platform/fae_analysis_report_v1.commit`
- Create: `contracts/platform/fixtures/valid-ready.json`
- Create: `contracts/platform/fixtures/valid-failed.json`
- Create: `contracts/platform/fixtures/invalid/raw-content.json`
- Create: `contracts/platform/fixtures/invalid/unresolved-reference.json`
- Create: `contracts/platform/fixtures/invalid/failed-with-findings.json`
- Create: `contracts/platform/fixtures/invalid/unbounded-array.json`
- Create: `scripts/pin_platform_report_contract.py`
- Create: `src/production_analytics/platform_publication.py`
- Create: `tests/unit/test_production_analytics_platform_publication.py`
- Create: `tests/integration/test_platform_report_bundle.py`
- Modify: `src/production_analytics/runner.py`
- Modify: `scripts/run_production_conversation_analytics.py`
- Modify: `.gitignore`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `PublicationMetadata`, `PlatformReportPublisher.publish(analysis_dir, output_path, metadata)`, a CLI stage `platform-bundle`, and private `platform_report_v1.json` with mode `0600`.
- Consumes: verified `report_ready` manifest, metrics, claim ledger, action backlog, sanitized reports, private snapshot/canonical records, and `AnalyticsIdentityKey`.

- [ ] **Step 1: Pin the Platform contract exactly**

Implement `scripts/pin_platform_report_contract.py --platform-repo PATH`. It resolves the Platform repository's checked-out 40-character commit with `git rev-parse HEAD`, reads the canonical schema and synthetic fixtures, validates them, copies them byte-for-byte, and atomically writes the schema's lowercase SHA-256 and Platform commit to their respective one-line files. It rejects a dirty canonical contract tree or a Platform HEAD that does not contain those exact blobs. Run the script only after Task 1's contract commit:

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
python scripts/pin_platform_report_contract.py \
  --platform-repo /Users/neo/Developer/work/AI-Agent-Platform
```

```python
def test_pinned_contract_digest_and_commit_are_well_formed():
    declared = DIGEST_FILE.read_text().strip()
    commit = COMMIT_FILE.read_text().strip()
    assert hashlib.sha256(SCHEMA.read_bytes()).hexdigest() == declared
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
```

The integration test runs `git show {commit}:contracts/fae-analysis-report/v1/schema.json` against the configured Platform checkout and requires byte equality.

- [ ] **Step 2: Write failing publication and privacy tests**

```python
def test_publisher_requires_reviewed_report_ready_run(tmp_path):
    analysis = analysis_fixture(tmp_path, status="review_complete")
    with pytest.raises(PublicationBlocked, match="analysis_not_report_ready"):
        publisher().publish(
            analysis.root,
            tmp_path / "platform_report_v1.json",
            weekly_metadata(),
        )


def test_publisher_resolves_canonical_keys_without_exporting_source_ids(tmp_path):
    analysis = report_ready_fixture(tmp_path, source_session_id="7b60", source_turn_id="88")
    output = publisher(identity_key=analysis.identity_key).publish(
        analysis.root,
        tmp_path / "platform_report_v1.json",
        weekly_metadata(),
    )
    payload = output.read_text()
    assert '"canonical_key":"fae:7b60"' in payload
    assert '"canonical_key":"fae:88"' in payload
    assert analysis.identity_key.key_bytes.hex() not in payload
    assert "analysis_session_id" not in payload
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
```

Also assert that raw questions/answers, Feedback comments, source IDs outside canonical keys, trace IDs, attachment names/hashes, identities and private paths do not occur in output; all seven sanitized artifact digests match the source run; an unknown analysis ID blocks publication. A separate test proves a terminal `blocked` manifest produces a `failed` envelope with only sanitized failure fields, while a nonterminal manifest cannot be published as failed.

- [ ] **Step 3: Run and verify RED**

Run: `cd /Users/neo/Developer/work/AI-FAE-Agent && pytest -q tests/unit/test_production_analytics_platform_publication.py tests/integration/test_platform_report_bundle.py`

Expected: FAIL because the publisher is missing.

- [ ] **Step 4: Resolve analysis IDs in memory**

Build a one-run reverse index by reading the private canonical Session/Turn rows and recomputing their HMAC analysis IDs with the active `AnalyticsIdentityKey`. Map only to canonical Platform keys:

```python
session_key = f"fae:{source_session_id}"
turn_key = f"fae:{source_turn_id}"
feedback_key = f"fae:{source_feedback_id}"
```

Never serialize the reverse index, original IDs, HMAC digest inputs or key material. Reject duplicate analysis IDs, unsupported key versions and references missing from the snapshot. Issue evidence already expressed as a UUID is copied only after UUID validation.

- [ ] **Step 5: Build the structured report from accepted artifacts**

Map reviewed claims to metrics, reviewed management/product cases to findings, and accepted backlog rows to recommendations. Preserve claims as observations; do not invent prose or priority. Use stable IDs from the source artifacts. `summary.top_*_ids` must reference the highest-priority reviewed items, with deterministic ordering `(severity/priority, id)`.

Assign dimensions by an explicit, tested publication mapping rather than prefix inference in
the Platform reader:

```python
DIMENSION_METRICS = {
    "usage": {
        "value.observed_included_sessions", "value.observed_included_turns",
        "value.observed_multiturn_sessions", "value.observed_attachment_sessions",
        "value.observed_non_work_hour_sessions", "product.family_counts_public",
        "demand.intent_capability_counts_public",
    },
    "business_value": {
        "value.assisted_reviewed_sessions",
        "value.scenario_potential_conversion_sessions",
    },
    "answer_effectiveness": {
        "quality.reviewed_count", "quality.reviewed_fully_resolved_rate",
        "quality.reviewed_first_turn_resolution_rate",
        "quality.reviewed_multiturn_convergence_rate", "feedback.bad_affected_sessions",
        "feedback.bad_affected_turns", "reliability.fallback_turn_rate",
        "latency.overall_ms",
    },
    "insights_improvement": {
        "feedback.canonical_issues", "product.signal_counts_public",
        "product.scenario_counts_public", "workflow.failure_layer_counts_public",
    },
}
```

Resolve the exact metric IDs against `metrics.json` during publication; an absent required
metric blocks the ready bundle with `incomplete_report_dimensions`. Map accepted action backlog
rows to `insights_improvement` unless their reviewed source explicitly assigns another legal
dimension. Publish a typical case only when its source row has
`business_case_approved is True`; the current v5 has no approved case and therefore publishes
an empty case collection rather than conversation excerpts.

The mapping above is frozen against the reviewed v5 artifact. Category-level product signals
remain inside the governed `product.signal_counts_public` object because its cell IDs are
privacy-stable hashes rather than semantic contract names; the publisher must not invent
unreviewed `signals.*` metrics from those cells.

`PublicationMetadata` is a strict frozen model containing `report_id`, positive `report_version`, `report_type`, and `title`. It validates the Platform contract patterns. Period, cutoff, source snapshot time and analysis version always come from the verified manifest/snapshot, never from CLI flags.

The ready publisher calculates artifact digests, validates the complete document against the pinned schema, serializes with JCS, writes via a same-directory temporary file opened with mode `0600`, `fsync`s the file, and atomically replaces the destination. The failed-envelope publisher accepts only a terminal `blocked` manifest, allowlists its stage/code/retryability, replaces the raw exception with a bounded operator-safe message, and emits empty artifacts/metrics/findings/recommendations. Refuse symlinks and paths outside the configured private analysis root.

- [ ] **Step 6: Add the explicit CLI stage**

Add:

```text
python scripts/run_production_conversation_analytics.py platform-bundle \
  --analysis-dir analysis/results/{analysis_id} \
  --output analysis/results/{analysis_id}/platform_report_v1.json \
  --report-id fae-weekly-2026-w35 \
  --report-version 1 \
  --report-type weekly \
  --title 'FAE 生产会话周报 · 2026 W35'
```

For a terminal blocked attempt, add `--publish-failure`; without it, `platform-bundle` requires `report_ready`. The command validates all prior manifest hashes and prints only `report_id`, `report_version`, status, output digest and counts. It never uploads, modifies production data or prints evidence keys. Add `**/platform_report_v1.json` to `.gitignore` and test with `git check-ignore`. Add `jsonschema>=4.23,<5` to `requirements.txt`; retain the existing `jcs` dependency.

- [ ] **Step 7: Run tests and commit**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
pytest -q tests/unit/test_production_analytics_platform_publication.py tests/integration/test_platform_report_bundle.py
pytest -q tests/unit/test_production_analytics_runner.py
git check-ignore -q analysis/results/test/platform_report_v1.json
```

Expected: PASS and the bundle is ignored.

```bash
git add contracts/platform scripts/pin_platform_report_contract.py src/production_analytics/platform_publication.py src/production_analytics/runner.py scripts/run_production_conversation_analytics.py tests/unit/test_production_analytics_platform_publication.py tests/integration/test_platform_report_bundle.py .gitignore requirements.txt
git commit -m "feat(analytics): publish Platform report bundles"
```

Keep `pyproject.toml` project metadata unchanged; runtime dependencies in this repository are managed by `requirements.txt`.

---

### Task 3: Add Immutable Report Storage and an Explicit Import Command

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/migrations/012_fae_analysis_reports.sql`
- Create: `backend/app/fae_reports/repository.py`
- Create: `backend/app/fae_reports/importer.py`
- Create: `backend/app/fae_reports/cli.py`
- Test: `backend/tests/test_fae_report_migration.py`
- Test: `backend/tests/test_fae_report_repository.py`
- Test: `backend/tests/test_fae_report_importer.py`
- Modify: `backend/app/config.py`

**Interfaces:**
- Produces: report/read-model tables, `PsycopgFaeReportRepository`, `FaeReportImporter.import_path(path, actor)`, and `python -m app.fae_reports.cli import --path /absolute/private/platform_report_v1.json --actor service:fae-report-importer`.
- Consumes: strict models from Task 1 and canonical observability views.

- [ ] **Step 1: Write failing migration and repository tests**

```python
def test_same_report_version_is_idempotent_but_conflict_is_rejected(repository):
    first = repository.insert_report(ready_record(digest="a" * 64), actor="corp:42")
    second = repository.insert_report(ready_record(digest="a" * 64), actor="corp:42")
    assert second.report_pk == first.report_pk
    with pytest.raises(ReportVersionConflict):
        repository.insert_report(ready_record(digest="b" * 64), actor="corp:42")


def test_mutable_finding_issue_link_does_not_change_imported_payload(repository):
    before = repository.get_report(REPORT_ID, 1).payload_digest
    repository.link_finding(REPORT_ID, 1, "finding-1", ISSUE_ID, actor="corp:42")
    assert repository.get_report(REPORT_ID, 1).payload_digest == before
```

Migration tests assert foreign keys, uniqueness, check constraints, read/write grants and that application roles cannot update/delete imported report rows.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_report_migration.py tests/test_fae_report_repository.py tests/test_fae_report_importer.py`

Expected: FAIL because schema and importer are absent.

- [ ] **Step 3: Create normalized immutable storage**

Create schema `platform_fae_reports` with:

```text
reports(
  report_pk uuid primary key,
  report_id text not null,
  report_version integer not null,
  report_type text not null,
  status text not null,
  title text not null,
  period_start timestamptz not null,
  period_end timestamptz not null,
  data_cutoff_at timestamptz not null,
  generated_at timestamptz not null,
  imported_at timestamptz not null,
  imported_by text not null,
  analysis_version text not null,
  source_snapshot_at timestamptz not null,
  payload_digest char(64) not null,
  payload jsonb not null,
  unique(report_id, report_version)
)

report_evidence(
  report_pk uuid references reports,
  finding_id text not null,
  evidence_ordinal integer not null,
  evidence_kind text not null,
  canonical_key text not null,
  label text not null,
  import_availability text not null,
  import_unavailable_reason text,
  primary key(report_pk, finding_id, evidence_ordinal)
)

finding_issue_links(
  link_id uuid primary key,
  report_pk uuid references reports,
  finding_id text not null,
  issue_id uuid not null references platform_review.feedback_issues(id),
  linked_at timestamptz not null,
  linked_by text not null,
  unlinked_at timestamptz,
  unlinked_by text
)

report_audit_events(
  event_id uuid primary key,
  report_pk uuid references reports,
  event_type text not null,
  actor text not null,
  occurred_at timestamptz not null,
  details jsonb not null
)
```

Add a partial unique index on `(report_pk, finding_id, issue_id) where unlinked_at is null`. Add database rules or trigger functions that reject `UPDATE` and `DELETE` on `reports` and `report_evidence`; only link rows may change through repository methods. Store the exact validated JSON payload and its JCS SHA-256.

- [ ] **Step 4: Validate import evidence inside the import transaction before inserting**

For every evidence reference:

- `session`: require `platform_read.sessions.session_key=canonical_key`, `agent_id=ai-fae-agent`, `source_kind=fae`.
- `turn`: require the same scope in `platform_read.turns`.
- `feedback`: require the same scope through `platform_read.feedback` joined to its Turn.
- `issue`: require the Review Issue to have `agent_id=ai-fae-agent`.

Batch lookups by kind. A malformed key or a key that resolves to a known non-FAE record rejects the bundle with `invalid_evidence_scope:<kind>:<key>` without exposing content. A syntactically valid `fae:` key absent from the mirror is stored with `import_availability=unavailable` and `import_unavailable_reason=not_synced` when `data_cutoff_at` is ahead of the latest successful Platform mirror sync, otherwise `missing`. A `failed` bundle has no evidence to resolve. Importing a report ahead of the mirror is allowed and audited; evidence resolution is repeated on reads so a later daily sync can make the link available without changing the immutable report payload.

- [ ] **Step 5: Implement safe, explicit import semantics**

The CLI requires an absolute regular file, rejects symlinks, caps reads at 5 MiB, and requires an actor matching `^corp:[0-9a-f-]{36}$` or the exact service identity `service:fae-report-importer`. It validates contract and evidence and imports in one transaction. It prints exactly `report_id`, `report_version`, `status`, `result`, and the computed 64-character lowercase `payload_digest` as one compact JSON object.

Never print report narrative or evidence keys. Add `FAE_REPORT_IMPORT_DATABASE_URL_FILE` configuration following existing secret-file loading patterns; do not accept a database URL directly on the command line.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_report_migration.py tests/test_fae_report_repository.py tests/test_fae_report_importer.py`

Expected: PASS, including PostgreSQL integration cases when the documented test database is available.

```bash
git add backend/migrations/012_fae_analysis_reports.sql backend/app/fae_reports backend/app/config.py backend/tests/test_fae_report_migration.py backend/tests/test_fae_report_repository.py backend/tests/test_fae_report_importer.py
git commit -m "feat(fae-reports): import immutable report versions"
```

---

### Task 4: Replicate Sanitized Report Projections to the Production Cloud Reader

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/cloud_replica/models.py`
- Modify: `backend/app/cloud_replica/source.py`
- Modify: `backend/app/cloud_replica/sanitize.py`
- Modify: `backend/app/cloud_replica/protocol.py`
- Modify: `backend/app/cloud_replica/store.py`
- Modify: `backend/app/cloud_replica/management_repository.py`
- Modify: `backend/migrations/008_cloud_replica.sql`
- Create: `backend/migrations/013_fae_report_cloud_projection.sql`
- Modify: `backend/tests/test_cloud_source.py`
- Modify: `backend/tests/test_cloud_sanitizer.py`
- Modify: `backend/tests/test_cloud_protocol.py`
- Modify: `backend/tests/test_cloud_store.py`
- Modify: `backend/tests/test_cloud_management_repository.py`
- Modify: `backend/tests/test_cloud_replica_migration.py`
- Modify: `backend/tests/test_cloud_mode.py`
- Modify: `backend/tests/test_fae_report_migration.py`
- Create: `backend/tests/test_cloud_fae_report_projection.py`

**Interfaces:**
- Produces: signed/encrypted `fae_report_header|metric|finding|recommendation_projection` records and `ReplicaFaeReportRepository` implementing the report read boundary.
- Consumes: immutable local reports, active local finding-to-Issue links, the existing cloud replica identity key and protocol.

- [ ] **Step 1: Write failing privacy and reassembly tests**

```python
def test_report_evidence_uses_replica_keys_not_canonical_source_keys(identity_key):
    record = sanitize_management_projection(raw_report_finding(), POLICY, identity_key)
    evidence = record["finding"]["evidence_refs"][0]
    assert evidence["session_key"] == stable_id("session", "fae:session-1", identity_key)
    assert evidence["turn_key"] == stable_id("turn", "fae:turn-1", identity_key)
    assert "fae:session-1" not in json.dumps(record)
    assert "fae:turn-1" not in json.dumps(record)


def test_cloud_reader_reassembles_one_version_and_rejects_partial_projection():
    repository = replica_repository(report_projection_records())
    assert repository.get_report(REPORT_ID, 1).title == "FAE 生产会话周报"
    repository.remove_projection("fae_report_metric_projection", "metric-1")
    with pytest.raises(FaeReportReplicaError, match="report_projection_incomplete"):
        repository.get_report(REPORT_ID, 1)
```

Also test every record is below 1 MiB, raw/canonical keys and producer artifact paths are absent, narrative credential patterns are redacted, stale replica generation returns unavailable, active Issue links reassemble, and the cloud repository exposes no mutation methods.

- [ ] **Step 2: Run and verify RED**

```bash
cd backend
.venv/bin/pytest -q \
  tests/test_cloud_source.py \
  tests/test_cloud_sanitizer.py \
  tests/test_cloud_protocol.py \
  tests/test_cloud_store.py \
  tests/test_cloud_management_repository.py \
  tests/test_cloud_replica_migration.py \
  tests/test_fae_report_migration.py \
  tests/test_cloud_fae_report_projection.py
```

Expected: FAIL because report projections are not part of the replica protocol.

- [ ] **Step 3: Split reports into bounded source projections**

`ReplicaSource.fetch_management_projections()` reads report rows in its existing repeatable-read, read-only transaction and emits:

```text
FaeReportHeaderProjection                 one per (report_id, report_version)
FaeReportMetricProjection                 one per metric
FaeReportFindingProjection                one per finding
FaeReportRecommendationProjection         one per recommendation
```

The header contains identity/version/type/status/title/times/current summary/failure/counts/artifact digests. Findings include producer Issue UUIDs and active Platform-local Issue UUID associations. Before returning raw projections, join every Session/Turn/Feedback evidence reference to `platform_read` to obtain its exact parent Session/Turn key. A missing reference retains availability/reason and no target key. Migration 013 grants the cloud export read role SELECT on only the report tables/views needed by this query; it receives no report-import write privilege.

Do not emit one full-report record: the current signed protocol caps each record at 1 MiB. Enforce 64 KiB per projection after JSON encoding and block export with `report_projection_too_large` rather than truncating prose.

- [ ] **Step 4: Re-sanitize and pseudonymize every projection**

Run all title, headline, overview, finding, recommendation and failure strings through the existing credential/text sanitizer even though the producer contract already rejects sensitive fields. Convert local evidence targets as follows:

```python
safe_session_key = stable_id("session", canonical_session_key, identity_key)
safe_turn_key = stable_id("turn", canonical_turn_key, identity_key)
```

This is the same derivation used by cloud Session records, so the report deep link resolves. Keep Issue UUIDs because existing cloud Review projections use those UUIDs. Do not export local canonical evidence keys, report database primary keys, importer actor, audit details or inactive associations.

- [ ] **Step 5: Version and store the new projection kinds**

Raise the replica protocol `SCHEMA_VERSION` from 2 to 3 and accept schemas 1, 2 and 3 on import. Add the four exact kinds to `_MANAGEMENT_KEYS`, the management time-field map and both idempotent check-constraint declarations in migration 008. Stable record keys are derived from `(report_id, report_version, item kind, item id)` using the replica identity key. Continue signed batch chaining, field-level encryption, one-year cloud retention and replay/conflict rejection unchanged.

- [ ] **Step 6: Implement a read-only cloud report repository**

`ReplicaFaeReportRepository` reads/decrypts the four projection kinds, groups by safe report identity/version, verifies header counts and unique item IDs, and returns the same list/detail model used by the local repository. It never attempts to reconstruct canonical evidence keys; it returns already-safe Session/Turn href inputs. Partial or conflicting groups fail that report version closed while other complete versions remain listable.

In cloud-replica mode, `create_app()` injects this repository into `FaeReportService`. Local mode keeps `PsycopgFaeReportRepository`. Report GET routes are therefore identical across deployment modes, while Task 7 mutation routes remain denied by the existing cloud read-only authorization gate.

- [ ] **Step 7: Run tests and commit**

```bash
cd backend
.venv/bin/pytest -q \
  tests/test_cloud_source.py \
  tests/test_cloud_sanitizer.py \
  tests/test_cloud_protocol.py \
  tests/test_cloud_store.py \
  tests/test_cloud_management_repository.py \
  tests/test_cloud_replica_migration.py \
  tests/test_fae_report_migration.py \
  tests/test_cloud_fae_report_projection.py \
  tests/test_cloud_mode.py
```

Expected: PASS and existing schema-1/schema-2 replica fixtures remain readable.

```bash
git add backend/app/cloud_replica backend/migrations/008_cloud_replica.sql backend/migrations/013_fae_report_cloud_projection.sql backend/tests/test_cloud_source.py backend/tests/test_cloud_sanitizer.py backend/tests/test_cloud_protocol.py backend/tests/test_cloud_store.py backend/tests/test_cloud_management_repository.py backend/tests/test_cloud_replica_migration.py backend/tests/test_fae_report_migration.py backend/tests/test_cloud_fae_report_projection.py backend/tests/test_cloud_mode.py
git commit -m "feat(fae-reports): replicate sanitized report projections"
```

---

### Task 5: Expose Report Reads, Freshness and Evidence Resolution APIs

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/app/fae_reports/service.py`
- Create: `backend/app/fae_reports/routes.py`
- Test: `backend/tests/test_fae_report_service.py`
- Test: `backend/tests/test_fae_report_api.py`
- Modify: `backend/app/fae_reports/models.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/tests/test_main.py`
- Modify: `backend/tests/test_r1_authorization.py`

**Interfaces:**
- Produces: report list/detail/filter APIs, latest-report preview, typed evidence targets, and currentness computed against mirror freshness.
- Consumes: Task 3 local repository, Task 4 cloud repository, Observability lookup and Review Issue detail.

- [ ] **Step 1: Write failing service tests for visibility and currentness**

```python
def test_latest_ready_report_is_marked_outdated_when_mirror_advanced():
    service = service_with(
        report=ready_report(data_cutoff_at=dt("2026-08-30T00:00:00+08:00")),
        latest_fae_sync=dt("2026-08-31T03:20:00+08:00"),
    )
    detail = service.detail(REPORT_ID, version=1)
    assert detail.currentness == "source_updated"
    assert detail.latest_source_sync_at == dt("2026-08-31T03:20:00+08:00")


def test_evidence_target_never_contains_conversation_content():
    target = service.resolve_evidence(REPORT_ID, 1, "finding-1", 0)
    assert target.model_dump() == {
        "kind": "turn",
        "label": "Turn 2",
        "availability": "available",
        "session_key": "fae:session-1",
        "turn_key": "fae:turn-1",
        "issue_id": None,
        "href": "/admin/fae/sessions/fae%3Asession-1?turn=fae%3Aturn-1",
        "unavailable_reason": None,
    }
```

Also test a missing report is 404, version omission selects the highest version, `status=failed` has no detail findings, filter-by-linked-Issue returns only active links, a not-yet-synced evidence target returns `href=null`, and repository failure maps to an unavailable response rather than fabricated data.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_report_service.py tests/test_fae_report_api.py tests/test_r1_authorization.py tests/test_main.py`

Expected: FAIL because report routes are absent.

- [ ] **Step 3: Define exact response models**

```python
class ReportListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    report_type: Literal["weekly", "topic"] | None = None
    status: Literal["ready", "failed"] | None = None
    linked_issue_id: UUID | None = None
    period_from: datetime | None = None
    period_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ReportCurrentness(StrEnum):
    CURRENT = "current"
    SOURCE_UPDATED = "source_updated"
    REPORT_AHEAD_OF_MIRROR = "report_ahead_of_mirror"
    SOURCE_FRESHNESS_UNKNOWN = "source_freshness_unknown"


class FaeReportEvidenceTarget(BaseModel):
    kind: Literal["session", "turn", "feedback", "issue"]
    label: str
    availability: Literal["available", "not_synced", "missing", "forbidden"]
    session_key: str | None
    turn_key: str | None
    issue_id: UUID | None
    href: str | None
    unavailable_reason: str | None
```

`FaeReportSummary` includes report identity/version/type/status/title/period/data cutoff/generated/imported/currentness, counts, summary headline and linked Issue count. `FaeReportLatest` contains `latest_attempt` and nullable `latest_ready`, allowing a failed new attempt and the preceding readable report to coexist. `FaeReportDetail` adds structured summary, metrics, public finding DTOs, recommendations, evidence targets, artifact digests and active finding-to-Issue links. The service omits contract `canonical_key` values from HTTP responses and returns only resolved local or cloud-safe target keys in `FaeReportEvidenceTarget`. It never returns source/private paths.

- [ ] **Step 4: Implement exact routes**

```text
GET /api/admin/fae/reports
GET /api/admin/fae/reports/latest
GET /api/admin/fae/reports/{report_id}
GET /api/admin/fae/reports/{report_id}/versions/{report_version}
GET /api/admin/fae/reports/{report_id}/versions/{report_version}/findings/{finding_id}/evidence/{evidence_ordinal}
```

The collection uses the query model above. The latest route returns `FaeReportLatest`: the largest imported version as `latest_attempt` and the newest ready version as `latest_ready`; it returns 404 `fae_report_not_available` only when no report of either status exists. Detail without a version returns the largest version. Evidence resolution maps:

- Session to `/admin/fae/sessions/{encoded_session_key}`.
- Turn or Feedback to the parent Session plus `?turn={encoded_turn_key}`.
- Issue to `/admin/fae/issues/{issue_id}`.

When evidence does not currently resolve, return HTTP 200 with `href=null`, `availability=not_synced|missing`, and a short reason; do not open an empty detail route. When it resolves, the response includes only a navigation target, not a second copy of Session/Turn data. `report_ahead_of_mirror` applies when the latest successful mirror sync precedes `data_cutoff_at`; `source_updated` applies when it is later than `data_cutoff_at`; equality is `current`.

- [ ] **Step 5: Mount services and exact authorization**

Inject `FaeReportRepository` and `FaeReportService` in `create_app()` following its current dependency-injection pattern. Add every GET route above to the exact Owner/Admin allowlist. Test `platform_owner` and `platform_admin` as 200, `member` and `management_viewer` as 403, unauthenticated as 401, and malformed IDs as 422/404 without information leakage.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_report_service.py tests/test_fae_report_api.py tests/test_r1_authorization.py tests/test_main.py`

Expected: PASS.

```bash
git add backend/app/fae_reports backend/app/main.py backend/app/control_plane/authorization.py backend/tests/test_fae_report_service.py backend/tests/test_fae_report_api.py backend/tests/test_r1_authorization.py backend/tests/test_main.py
git commit -m "feat(fae-reports): expose scoped report reads"
```

---

### Task 6: Replace the Placeholder with a Technical Report Reader

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `webui/src/faeReportTypes.ts`
- Create: `webui/src/faeReportApi.ts`
- Create: `webui/src/faeReportApi.test.ts`
- Create: `webui/src/components/fae-reports/ReportStatusStrip.tsx`
- Create: `webui/src/components/fae-reports/ReportFinding.tsx`
- Create: `webui/src/components/fae-reports/ReportMetricGrid.tsx`
- Create: `webui/src/pages/FaeReportsPage.tsx`
- Create: `webui/src/pages/FaeReportsPage.test.tsx`
- Create: `webui/src/pages/FaeReportDetailPage.tsx`
- Create: `webui/src/pages/FaeReportDetailPage.test.tsx`
- Delete: `webui/src/pages/FaeReportsPlaceholderPage.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`
- Modify: `webui/src/pages/FaeSessionDetailPage.tsx`
- Modify: `webui/src/pages/FaeSessionDetailPage.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Produces: typed report list/detail reads and a calm reading surface under the foundation routes.
- Consumes: Task 5 APIs and `FaeWorkbenchShell currentSection="reports"`.

- [ ] **Step 1: Write failing API parsing tests**

```typescript
it("keeps all three report times and currentness", async () => {
  mockJson(reportDetailFixture);
  const report = await faeReportApi.report("fae-weekly-2026-w35", 1);
  expect(report.data_cutoff_at).toBe("2026-08-30T00:00:00+08:00");
  expect(report.generated_at).toBe("2026-08-31T10:00:00+08:00");
  expect(report.imported_at).toBe("2026-08-31T11:00:00+08:00");
  expect(report.currentness).toBe("source_updated");
});

it("rejects a response containing raw conversation fields", async () => {
  mockJson({ ...reportDetailFixture, question: "raw" });
  await expect(faeReportApi.report(REPORT_ID, 1)).rejects.toThrow("invalid_fae_report_response");
});
```

The client serializes only `report_type`, `status`, `linked_issue_id`, `period_from`, `period_to`, `limit`, and `offset`.

- [ ] **Step 2: Write failing reader tests**

```typescript
await renderReportDetail(reportDetailFixture);
expect(screen.getByRole("heading", { name: reportDetailFixture.title })).toBeVisible();
expect(screen.getByText("数据已有更新")).toBeVisible();
expect(screen.getByText("数据截止")).toBeVisible();
expect(screen.getByRole("link", { name: "打开 Turn 2" }))
  .toHaveAttribute("href", "/admin/fae/sessions/fae%3Asession-1?turn=fae%3Aturn-1");
expect(screen.queryByText("artifact path")).not.toBeInTheDocument();
```

Also test empty state `暂无已发布的分析报告`, a newer failed attempt beside its preceding ready report, weekly/topic filters, version switcher, browser back navigation, loading retry, narrow viewport and keyboard focus order.

- [ ] **Step 3: Run and verify RED**

Run: `cd webui && npm test -- faeReportApi.test.ts FaeReportsPage.test.tsx FaeReportDetailPage.test.tsx router.test.ts documentTitle.test.tsx`

Expected: FAIL because the placeholder is still mounted.

- [ ] **Step 4: Implement strict client and stable routing**

```typescript
export interface FaeReportApi {
  list(query: FaeReportQuery, signal?: AbortSignal): Promise<Page<FaeReportSummary>>;
  latest(signal?: AbortSignal): Promise<FaeReportLatest>;
  report(reportId: string, version?: number, signal?: AbortSignal): Promise<FaeReportDetail>;
  resolveEvidence(
    reportId: string,
    version: number,
    findingId: string,
    evidenceOrdinal: number,
    signal?: AbortSignal,
  ): Promise<FaeReportEvidenceTarget>;
}
```

Keep `/admin/fae/reports` and `/admin/fae/reports/:report_id`. Use query `?version=N` for historical versions so report identity keeps one canonical route. The parser accepts only positive integer versions and uses the latest when absent. Remove the placeholder component after both routes render real states.

Extend `FaeSessionDetailPage` to parse `?turn={canonical_turn_key}` after the Session loads. If the Turn exists, add a persistent selected style, set a programmatic focus target and call `scrollIntoView({ block: "center" })` once. If it does not exist, show `报告引用的回答当前不可用` without hiding the rest of the Session. This makes report evidence links land on the exact Answer instead of only the Session top.

- [ ] **Step 5: Build a layered management report, not a 7 KB summary or a BI dashboard**

The list page contains one compact latest-attempt status block, preserves a separate `最近可读报告` link when that attempt failed, and follows it with a chronological table. The detail page order is:

1. title, type, version and status;
2. period/data-cutoff/generated/imported strip with currentness;
3. executive headline and overview;
4. a four-item management outcome strip: service scale, complex-work coverage, realized value, and conversion potential;
5. `使用情况`: trend, multi-turn, image/attachment, non-work-hour, product and intent metrics;
6. `业务价值`: work accepted, complex consultation, realized value, conversion potential, and only business-approved sanitized cases;
7. `回答效果`: review coverage, quality distribution, full/first-turn resolution, multi-turn convergence, feedback, fallback and latency;
8. `业务洞察与改进`: product/demand signals, canonical root-cause families, prioritized actions and next-stage recommendations;
9. provenance/artifact digests in a collapsed technical details element.

All four dimension headings must render for every ready report. A dimension with no legal
published metric renders an explicit unavailable explanation rather than disappearing. Realized
value and conversion potential are separate visual groups and retain their reviewed denominators.
If the producer has no business-approved cases, render `典型案例待业务批准` and do
not derive excerpts from private conversations.

Use the existing content width and typography system. Do not render a wall of cards, charts without explanatory text, raw JSON, Markdown/HTML from the producer, fake avatar, decorative gradient or generation controls. Long evidence and finding lists wrap; the main reading column remains 760-900px with an optional 260px contents rail at wide viewports and one column below 1100px.

- [ ] **Step 6: Add truthful failure and stale states**

- No reports: `暂无已发布的分析报告` plus an explanation that the reviewed FAE analysis pipeline has not published one.
- Source updated: `数据已有更新` and both cutoff/sync times.
- Report ahead of mirror: `平台会话尚未同步到报告截止时间`; unavailable evidence remains visibly disabled until synchronization catches up.
- Freshness unknown: `暂时无法判断数据新旧`, never “最新”.
- Failed report: show sanitized stage/code/message and no finding sections.
- API failure: preserve navigation and render `报告读取失败` with retry.

- [ ] **Step 7: Run tests and commit**

```bash
cd webui
npm test -- faeReportApi.test.ts FaeReportsPage.test.tsx FaeReportDetailPage.test.tsx router.test.ts documentTitle.test.tsx
npm run build
```

Expected: PASS and production build succeeds.

```bash
git add webui/src/faeReportTypes.ts webui/src/faeReportApi.ts webui/src/faeReportApi.test.ts webui/src/components/fae-reports webui/src/pages/FaeReportsPage.tsx webui/src/pages/FaeReportsPage.test.tsx webui/src/pages/FaeReportDetailPage.tsx webui/src/pages/FaeReportDetailPage.test.tsx webui/src/pages/FaeSessionDetailPage.tsx webui/src/pages/FaeSessionDetailPage.test.tsx webui/src/App.tsx webui/src/router.ts webui/src/router.test.ts webui/src/documentTitle.ts webui/src/documentTitle.test.tsx webui/src/styles.css
git rm webui/src/pages/FaeReportsPlaceholderPage.tsx
git commit -m "feat(fae-reports): add evidence-backed report reader"
```

---

### Task 7: Connect Findings to the Existing Issue Governance Loop

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/fae_reports/models.py`
- Modify: `backend/app/fae_reports/repository.py`
- Modify: `backend/app/fae_reports/service.py`
- Modify: `backend/app/fae_reports/routes.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/tests/test_fae_report_repository.py`
- Modify: `backend/tests/test_fae_report_service.py`
- Modify: `backend/tests/test_fae_report_api.py`
- Modify: `backend/tests/test_r1_authorization.py`
- Modify: `webui/src/faeReportApi.ts`
- Modify: `webui/src/faeReportApi.test.ts`
- Modify: `webui/src/components/fae-reports/ReportFinding.tsx`
- Modify: `webui/src/pages/FaeReportDetailPage.tsx`
- Modify: `webui/src/pages/FaeReportDetailPage.test.tsx`
- Modify: `webui/src/pages/FaeIssuesPage.tsx`
- Modify: `webui/src/pages/FaeIssuesPage.test.tsx`

**Interfaces:**
- Produces: audited link/unlink APIs, direct Issue navigation, and report-seeded Issue creation without duplicating Review state.
- Consumes: foundation FAE Issue facade and Task 3 mutable association table.

- [ ] **Step 1: Write failing link, unlink and scope tests**

```python
def test_link_finding_requires_existing_fae_issue(client):
    response = client.post(
        f"/api/admin/fae/reports/{REPORT_ID}/versions/1/findings/finding-1/issues",
        json={"issue_id": str(NON_FAE_ISSUE_ID)},
    )
    assert response.status_code == 404


def test_link_and_unlink_are_audited(client, audit_repository):
    linked = client.post(
        f"/api/admin/fae/reports/{REPORT_ID}/versions/1/findings/finding-1/issues",
        json={"issue_id": str(FAE_ISSUE_ID)},
    )
    assert linked.status_code == 201
    removed = client.delete(
        f"/api/admin/fae/reports/{REPORT_ID}/versions/1/findings/finding-1/issues/{FAE_ISSUE_ID}"
    )
    assert removed.status_code == 204
    assert audit_repository.event_types == ["finding_issue_linked", "finding_issue_unlinked"]
```

Also test unknown finding, failed report, duplicate active link idempotency, already-unlinked association, cloud replica write denial, hard-stale write denial and server-derived actor.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/test_fae_report_repository.py tests/test_fae_report_service.py tests/test_fae_report_api.py tests/test_r1_authorization.py`

Expected: FAIL because mutation routes do not exist.

- [ ] **Step 3: Add exact mutation routes**

```text
POST   /api/admin/fae/reports/{report_id}/versions/{report_version}/findings/{finding_id}/issues
DELETE /api/admin/fae/reports/{report_id}/versions/{report_version}/findings/{finding_id}/issues/{issue_id}
```

POST body is strict `{ "issue_id": "uuid" }`. The server derives actor from `request.state.auth_context`, loads an immutable `ready` report, verifies the finding, loads the Issue through the FAE-scoped Review facade, then inserts the association and audit event in one transaction. DELETE soft-unlinks and audits; it never deletes history.

- [ ] **Step 4: Implement create-Issue handoff from a finding**

A finding with one or more Turn evidence links offers `创建问题`. Navigate to:

```text
/admin/fae/issues?report_id={report_id}&report_version={version}&finding_id={finding_id}&session_key={session_key}&turn_key={turn_key}
```

`FaeIssuesPage` resolves the report/finding server-side, confirms the selected Turn is listed evidence, and pre-fills only:

- title from finding title;
- problem statement from finding description;
- impact scope from finding `impact_scope`;
- cause hypothesis from finding root-cause hypothesis;
- source Turn link with its real Feedback keys, which may be empty.

After the existing Review API creates the Issue, call the finding-link API and navigate to `/admin/fae/issues/{issue_id}`. If Issue creation succeeds but association fails, retain the Issue, show `问题已创建，报告关联失败`, and offer an idempotent retry. Do not roll back or duplicate the Issue.

- [ ] **Step 5: Add existing-Issue association UI**

Each finding shows active linked Issues with current Review status and a direct link. On the writable local deployment, `关联已有问题` opens an accessible selector populated only from the FAE-scoped Issue API; `取消关联` requires confirmation and explains that the Issue itself remains unchanged. In cloud-replica or hard-stale read-only mode, creation/link/unlink controls are absent, existing associations remain readable, and a persistent `当前为只读副本` explanation is shown.

When a FAE Issue detail is selected, call the report list API with `linked_issue_id`. Render `来源分析报告` links for active associations so an operator can return from the Issue to the exact report and finding anchor. Producer-supplied `linked_issue_ids` and Platform-local links are labelled separately when both exist.

- [ ] **Step 6: Run backend and frontend tests**

```bash
cd backend
.venv/bin/pytest -q tests/test_fae_report_repository.py tests/test_fae_report_service.py tests/test_fae_report_api.py tests/test_r1_authorization.py tests/test_fae_workbench_api.py
cd ../webui
npm test -- faeReportApi.test.ts FaeReportDetailPage.test.tsx FaeIssuesPage.test.tsx ReviewPage.test.tsx
```

Expected: PASS and generic Review remains unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/app/fae_reports backend/app/control_plane/authorization.py backend/tests/test_fae_report_repository.py backend/tests/test_fae_report_service.py backend/tests/test_fae_report_api.py backend/tests/test_r1_authorization.py webui/src/faeReportApi.ts webui/src/faeReportApi.test.ts webui/src/components/fae-reports/ReportFinding.tsx webui/src/pages/FaeReportDetailPage.tsx webui/src/pages/FaeReportDetailPage.test.tsx webui/src/pages/FaeIssuesPage.tsx webui/src/pages/FaeIssuesPage.test.tsx
git commit -m "feat(fae-reports): connect findings to Issue governance"
```

---

### Task 8: Integrate the Latest Report into the FAE Overview

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/fae_workbench/models.py`
- Modify: `backend/app/fae_workbench/service.py`
- Modify: `backend/tests/test_fae_workbench_service.py`
- Modify: `backend/tests/test_fae_workbench_api.py`
- Modify: `webui/src/faeWorkbenchTypes.ts`
- Modify: `webui/src/faeWorkbenchApi.test.ts`
- Modify: `webui/src/pages/FaeOverviewPage.tsx`
- Modify: `webui/src/pages/FaeOverviewPage.test.tsx`

**Interfaces:**
- Produces: real latest-report preview in the overview's existing reports section with independent failure handling.
- Consumes: Task 5 `FaeReportService.latest()`.

- [ ] **Step 1: Write failing partial-failure tests**

```python
async def test_overview_preserves_operations_when_report_repository_fails():
    overview = await service_with(report_error=DatabaseError("down")).overview(NOW)
    assert overview.summary.state.available is True
    assert overview.reports.state.available is False
    assert overview.reports.state.error_code == "reports_unavailable"


async def test_overview_exposes_real_latest_report_without_findings_payload():
    overview = await service_with(
        report=FaeReportLatest(
            latest_attempt=latest_summary(),
            latest_ready=latest_summary(),
        )
    ).overview(NOW)
    assert overview.reports.state.available is True
    assert overview.reports.payload.report_id == REPORT_ID
    assert not hasattr(overview.reports.payload, "findings")
```

Frontend tests assert title/headline/currentness/link render, operations remain usable on report failure, and no-report state differs from API failure.

- [ ] **Step 2: Run and verify RED**

```bash
cd backend
.venv/bin/pytest -q tests/test_fae_workbench_service.py tests/test_fae_workbench_api.py
cd ../webui
npm test -- faeWorkbenchApi.test.ts FaeOverviewPage.test.tsx
```

Expected: FAIL because overview still returns `reports_not_integrated`.

- [ ] **Step 3: Replace only the reports section provider**

Inject `FaeReportService` into `FaeWorkbenchService`. Fetch operational aggregates, issue aggregates and latest report concurrently with `asyncio.gather(..., return_exceptions=True)`. Map:

- no imported report of either status → available empty state `no_published_report`;
- report repository failure → unavailable `reports_unavailable`;
- ready latest attempt → compact preview with report ID/version/title/type/period/data cutoff/generated/currentness/headline/findings count/recommendations count;
- a newer failed attempt plus an older ready report → show the failure status and keep the ready report link;
- imported failed report only → available status preview that links to its detail, without treating it as successful analysis.

Do not place finding bodies or metrics arrays in the overview payload.

- [ ] **Step 4: Replace the placeholder preview UI**

Use one quiet preview block with title, period, headline, currentness label and `阅读报告`. No report shows `暂无已发布报告`; failure shows `报告状态暂不可用`. Keep the operational overview's first visual priority.

- [ ] **Step 5: Run tests and commit**

```bash
cd backend
.venv/bin/pytest -q tests/test_fae_workbench_service.py tests/test_fae_workbench_api.py tests/test_fae_report_service.py
cd ../webui
npm test -- faeWorkbenchApi.test.ts FaeOverviewPage.test.tsx FaeReportsPage.test.tsx
```

Expected: PASS.

```bash
git add backend/app/fae_workbench backend/tests/test_fae_workbench_service.py backend/tests/test_fae_workbench_api.py webui/src/faeWorkbenchTypes.ts webui/src/faeWorkbenchApi.test.ts webui/src/pages/FaeOverviewPage.tsx webui/src/pages/FaeOverviewPage.test.tsx
git commit -m "feat(fae-workbench): surface latest analysis report"
```

---

### Task 9: Prove the Cross-Repository Release and Document Operations

**Repositories:** Agent Platform and AI-FAE-Agent

**Files:**
- Create: `backend/tests/integration/test_fae_report_end_to_end.py`
- Create: `docs/runbooks/fae-analysis-report-publication.md`
- Create: `docs/reviews/2026-08-31-fae-analysis-report-integration-review.md`
- Modify: `deploy/cloud/accept.sh`
- Modify: `docs/runbooks/cloud-platform.md`
- Modify: `backend/tests/test_cloud_deployment.py`
- Modify earlier implementation files only for defects revealed by verification.

**Interfaces:**
- Produces: synthetic producer-to-reader evidence and an operator-safe publication/import runbook.
- Consumes: every interface from Tasks 1-8.

- [ ] **Step 1: Add a synthetic end-to-end test**

Use no production data. From the AI-FAE-Agent synthetic `report_ready` fixture:

1. generate `platform_report_v1.json`;
2. verify the pinned Platform contract digest;
3. seed matching FAE canonical Session/Turn/Feedback and one FAE Review Issue in PostgreSQL;
4. import version 1 twice and assert `inserted`, then `already_imported`;
5. assert conflicting bytes for version 1 are rejected;
6. call Platform list/detail/evidence APIs as Owner;
7. associate one finding to the Issue and assert audit history;
8. render the frontend detail fixture derived from the API response;
9. assert no raw content or identity crosses the producer boundary.

The test is skipped only when the documented PostgreSQL integration database is unavailable; contract and pure-Python stages never skip.

- [ ] **Step 2: Write the exact operator runbook**

`docs/runbooks/fae-analysis-report-publication.md` documents:

- prerequisites and expected source analysis state;
- how to verify contract commit/digest in both repositories;
- the `platform-bundle` command;
- file mode, owner and Git-ignore checks;
- approved secure transfer into a Platform-local staging directory;
- the Platform import command and secret-file requirement;
- idempotent re-run behavior;
- how to distinguish contract, privacy, evidence, scope and database failures;
- how to delete the transferred bundle after a successful import using an explicit path and the organization's approved secure-delete/retention procedure;
- rollback: imported report rows are immutable, so publish a higher version; never edit or delete version 1;
- statement that Web requests never access the transfer directory.

Do not document production credentials, hostnames, employee identities or example raw content.

- [ ] **Step 3: Run Agent Platform verification**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/pytest -q \
  tests/test_fae_report_contract.py \
  tests/test_fae_report_migration.py \
  tests/test_fae_report_repository.py \
  tests/test_fae_report_importer.py \
  tests/test_fae_report_service.py \
  tests/test_fae_report_api.py \
  tests/test_fae_workbench_service.py \
  tests/test_fae_workbench_api.py \
  tests/test_cloud_fae_report_projection.py \
  tests/test_cloud_protocol.py \
  tests/test_cloud_store.py \
  tests/test_cloud_management_repository.py \
  tests/test_r1_authorization.py \
  tests/integration/test_fae_report_end_to_end.py
.venv/bin/pytest -q
cd ../webui
npm test
npm run build
```

Expected: all available tests PASS, full backend/frontend suites have zero failures, and production build succeeds. Record exact PostgreSQL skips.

- [ ] **Step 4: Run AI-FAE-Agent verification**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
pytest -q tests/unit/test_production_analytics_platform_publication.py tests/integration/test_platform_report_bundle.py
pytest -q
git check-ignore -q analysis/results/test/platform_report_v1.json
```

Expected: zero failures and the private bundle is ignored.

- [ ] **Step 5: Perform privacy, placeholder and boundary scans**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
rg -n "question|answer|raw_text|source_id|trace_id|attachment_name|employee_id|email|phone" \
  contracts/fae-analysis-report/v1/fixtures/valid-ready.json \
  backend/app/fae_reports webui/src/faeReportTypes.ts
rg -n "立即生成|重新生成|实时报告|sample report|demo report|fixture report" \
  webui/src/pages/FaeReport* webui/src/components/fae-reports
rg -n "read_text|read_bytes|open\(" webui/src backend/app/fae_reports/routes.py backend/app/fae_reports/service.py
git diff --check

cd /Users/neo/Developer/work/AI-FAE-Agent
rg -n "question|answer|comment|source_id|trace_id|attachment_name|employee_id|email|phone" \
  src/production_analytics/platform_publication.py contracts/platform
git diff --check
```

Expected: forbidden fields are absent from the valid contract payload and response types; any matches in validators/tests are explicit denylist assertions. There are no generation controls or sample reports. Platform runtime routes/services contain no filesystem reads. Both diffs are whitespace-clean.

- [ ] **Step 6: Extend and run the production cloud acceptance contract**

Add Owner checks for `/admin/fae`, `/admin/fae/sessions`, `/admin/fae/issues`, `/admin/fae/reports`, `GET /api/admin/fae/overview`, `GET /api/admin/fae/sessions?limit=1`, `GET /api/admin/fae/issues`, and `GET /api/admin/fae/reports?limit=1`. Page and collection reads must be 200 even with no imported report. Add direct member/viewer 403 checks and one report-link mutation 403 check proving the cloud remains read-only. When a report exists, inspect only its response field names and assert no `canonical_key`, private path or raw identity field.

Update the cloud runbook with migration 008/schema-3 compatibility, projection-count diagnostics and rollback behavior. After a clean release is built, backed up and staged under the existing runbook, run `deploy/cloud/accept.sh`; do not invent a new deployment path or bypass its rollback gates.

```bash
cd backend
.venv/bin/pytest -q tests/test_cloud_deployment.py tests/test_cloud_mode.py
cd ..
bash -n deploy/cloud/accept.sh
```

Expected: PASS, and the shell acceptance script has syntax-valid fail-closed checks.

- [ ] **Step 7: Record the release review**

`docs/reviews/2026-08-31-fae-analysis-report-integration-review.md` records:

- exact commit ranges from both repositories;
- schema digest and pinned producer reference;
- focused/full test and build output;
- one ready and one failed bundle contract result;
- idempotency and conflict result;
- missing/not-yet-synced evidence availability and cross-Agent evidence rejection;
- raw-content/identity privacy result;
- Owner/Admin allow and other-role deny result;
- local-to-cloud report projection privacy, schema-2 compatibility and production read-only result;
- source-updated/currentness result;
- finding-to-Issue link/unlink audit result;
- explicit statement that automatic scheduling/retry is outside release 1.

- [ ] **Step 8: Commit Platform release evidence**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git add backend/tests/integration/test_fae_report_end_to_end.py backend/tests/test_cloud_deployment.py deploy/cloud/accept.sh docs/runbooks/fae-analysis-report-publication.md docs/runbooks/cloud-platform.md docs/reviews/2026-08-31-fae-analysis-report-integration-review.md
git commit -m "docs(fae-reports): verify publication and operations"
```

---

## Design Coverage Map

| Approved design requirement | Implemented by |
|---|---|
| FAE produces semantic reports under a versioned contract | Tasks 1-2 |
| Reviewed evidence only; no raw content or identity leakage | Tasks 1-4, 9 |
| Sanitized production cloud availability | Task 4 |
| Stable report list/detail reading experience | Tasks 5-6 |
| Weekly/topic reports and immutable versions | Tasks 1, 3-6 |
| Data cutoff, generation, import and source freshness shown separately | Tasks 3-6, 8 |
| Evidence drill-down to exact Session/Turn/Feedback/Issue | Tasks 2-6 |
| Finding creates or links an existing FAE Issue | Task 7 |
| Existing Review closure remains authoritative | Task 7 |
| Overview shows latest report with independent failure state | Task 8 |
| No runtime direct file/repository coupling | Tasks 2-6, 9 |
| No fake report or dishonest realtime claim | Tasks 6, 8-9 |
| Owner/Admin authorization and cloud/hard-stale mutation policy | Tasks 5, 7, 9 |
| Manual reviewed release first; automation deferred explicitly | Release-1 boundaries, Task 9 |
