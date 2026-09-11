# E1c deploy/preflight review — 6577898 and bounded coordinator diff

SPEC_APPROVED: false
QUALITY_APPROVED: false
VERDICT: changes_required

Reviewed actual preflight.py, its test, cloud cutover runbook, coordinator's rollback guard/test and cloud compose expected-service correction. Other cutover implementation changes were excluded except reading final interfaces for compatibility. No implementation edits, full suite, model/network call, or production access.

## Required changes

1. **P1 — Preflight renders unvalidated protected budget data.** `_runtime_report` includes `settings.budget_profile.get("id")` verbatim. `load_hr_agent_settings` validates numerical budgets but does not validate this optional field. A focused local probe set budget.id to an object containing endpoint and credential sentinels: the report included SECRET-SENTINEL and still returned runtime_match=true. Omit raw budget ID or validate a bounded safe identifier before rendering. Add a regression asserting arbitrary object/string values cannot disclose protected content.

2. **P1 — Published migration commands cannot read the provisioned credentials.** Section 4 uses Docker `--user 10001:10001` with the host private directory mounted directly. Existing provisioning owns these DSNs root:root mode0600, and `read_secret_file` requires current-user ownership as well as read access. Existing bootstrap-control-db.sh:499 consequently uses user0:0 for the same mount. Correct the commands to the supported root migrator container pattern, or explicitly provision a dedicated uid10001-owned secret volume before use. This is an executable-path failure, not a production permission request.

3. **P2 — 102 function permissions can be absent while database section passes.** `_database_report` checks ledger identity, gate SELECT and legacy check_schema_ready; it does not check maintenance EXECUTE on counts/initialize/transition. A disposable PostgreSQL gate fixture with maintenance transition EXECUTE revoked returned checked=true, schema_ready=true, initialized=true and no database blockers. Check the actual mapped maintenance role's required 102 functions, and ensure app cannot execute mutators/directly write the gate if claiming permission completeness. The global product blockers remain intact, so this is a false database-readiness diagnostic, not an overall production approval bypass.

4. **P2 — Drain instructions strand accepted work.** Section 6 says cloud->draining_cloud immediately stops new claims. Exit implementer independently confirms drains stop only new root admissions; workers must continue claiming/recovering already accepted queued work and derived parse/candidate work until terminal/cancel evidence. Correct both drain narratives to distinguish root admission from continuation; otherwise operators can prevent their own drain from completing.

5. **P2 — Preflight execution context is not assembled.** Section 3 invokes host `python -m tools.hr_agent.preflight` against actual container environment snapshots. Those snapshots point at `/run/hr-agent-secrets` and `/data/hr-*`, which the host command does not mount. No host Python dependency environment is established, while cloud Dockerfile copies app/migrations but excludes backend/tools. Supply one concrete supported invocation with the release tool, dependency environment, protected file ownership, knowledge/work mounts and snapshot paths aligned; do not imply raw container env snapshots are directly executable on the host.

## Rollback guard and compose correction

The new rollback filter excludes only platform-attachments after control bootstrap starts or the marker is unknown, preserves unrelated consumer ordering, and is called before restoring previous consumers. The flag is set before the potentially partially committing bootstrap command. Independent dedicated test run: **4 passed in 0.02s**. This is a useful conservative guard within one deployment attempt, not proof of a complete independent attachment hotfix workflow or long-term image safety on subsequent deployments. No Docker lifecycle was executed.

The cloud test correction matches the existing base compose HR worker: explicit hr-agent profile, uid10001, no host port, read-only filesystem and dropped capabilities. It does not enable HR implicitly.

## Other verified constraints and boundaries

- CLI database connections set default_transaction_read_only=on with statement timeout; actual preflight SQL contains no DML/DDL or model request.
- Migration checks cover96–102 and scrub malformed checksum values. Missing gate does not become zero; it produces cutover_gate_not_initialized. Unknown database failure produces database_read_unavailable.
- Hardcoded personal-processing/D7 blockers prevent caller booleans from manufacturing product approval. Process/browser/production checks remain explicitly unverified.
- Runbook separates attachment100 hotfix from HR release, requires stopped old worker before migration100, and prohibits automatic old attachment-image recovery.
- Current operational signatures confirmed with exit implementer: initialize_hr_execution_cutover_v102(uuid), transition_hr_execution_cutover_v102(text,uuid), hr_execution_cutover_counts_v102() -> legacy_nonterminal/cloud_nonterminal. Current gate fields match preflight.

Focused verification used protected temporary local profile fixtures and disposable PostgreSQL only. One initial initializer probe failed safely because its required legacy coverage was absent; the permission probe then explicitly seeded an isolated gate fixture solely to test the preflight diagnostic, without claiming real operational initialization success. No API, process-fault, browser, or production acceptance is asserted.
