# E1a inventory review — 6a9cef6..9c22682

SPEC_APPROVED: false
QUALITY_APPROVED: false
VERDICT: changes_required

Scope: inventory.py, its dedicated test and runbook, checked against current migration and result contracts. Other uncommitted cutover/preflight work excluded. Only this report was written. Supplied five-passing-test evidence was read; no full suite, production access, model call or browser action was performed.

## Findings

1. **P1 — Runtime UPDATE both violates the SELECT-only inventory boundary and prevents pre-HR inventory.** `_write_probe` issues `update platform_hr_agent.works ...` before collecting assets. A missing new schema raises an error other than ReadOnlySqlTransaction, then RELEASE fails in the aborted transaction. Independent disposable PostgreSQL reproduction (rename new schema, run inventory) returned `InFailedSqlTransaction` and no report. Remove runtime mutation probe; inspect transaction read-only mode and keep an explicit write-refusal test in disposable tests only. Parent has announced this fix; not yet reviewed here.

2. **P2 — Safe allowlists erase supported business classifications.** `old_candidate_drafts` admits `queued` but migration 070 defines `pending`; valid pending drafts therefore become unknown. `new_results` admits research/analysis/plan/report/draft while contracts.schema.json:634 and :1670 support role_calibration, jd, requirements, standard_proposal, sourcing, candidate_assessment, interview_plan, interview_record, retrospective, research. Nine supported result kinds become unknown. Use the actual supported enum sets and add non-research / pending coverage. Unknown future values should remain redacted.

3. **P2 — Malformed position identity defeats reference aggregation.** `new_result_link_refs` uses `^[0-9a-f-]{36}$` before UUID cast. Thirty-six hyphens pass this regex but fail the cast, converting the entire asset into query_error. Independent disposable PostgreSQL insertion of this legal text object_id reproduced query_error. A canonical UUID regex with case-insensitive hex avoids cast failures and permits upper-case equivalent UUIDs; tests should include malformed and upper-case identities. Wrong-owner position links are currently reported as missing rather than explicitly wrong_owner, unlike candidate-position refs; distinguish this if owner diagnostics are promised.

4. **P2 — Joined table requirements are not prechecked.** Reference specs list only source relation requirements. Removing platform_hr makes candidate-position reference output query_error even though the cause is a missing required relation, independently reproduced. Missing target columns and denied SELECT are similarly collapsed. Declare/check join dependencies or translate the specific database errors to missing_table/missing_column/unreadable without exposing messages.

## Positive evidence and limits

Registry contains only fixed aggregate queries; output sanitizes uncontrolled state/kind values and never returns identities or business content. Savepoints isolate normal per-asset failures and unavailable assets carry no zero total. Runbook explicitly bounds reference resolution and rejects treating reference-edge counts as proof of all valid links. The inventory is a bounded E1a first implementation; it does not establish complete historical data coverage, ownership mapping, all current-revision validity, or production readiness.

Dedicated CLI test's output-symlink assertion currently uses an invalid DSN, so it fails on connection before exercising output handling; a mocked successful inventory or direct output test is needed for evidence of the stated path guarantees. CLI only catches ValueError/psycopg.Error, so filesystem OSError during read/write can still print path-bearing tracebacks, contrary to runbook's path-redaction promise; sanitize filesystem failures too.

Verification performed: one disposable local PostgreSQL process, actual migrated schema, three focused probes described above. No API, process-fault, frontend component, browser, or production acceptance was claimed.
