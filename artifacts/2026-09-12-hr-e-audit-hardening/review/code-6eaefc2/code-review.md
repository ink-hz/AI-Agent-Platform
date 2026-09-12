# Independent ROOT code review: 6eaefc2

Review object is the exact commit recorded in fingerprints.json, baseline5978eaa. Task1 at3504c20 was reviewed separately and is not repeated. All code/tests/report/plan were read from git show/diff of the target, not a moving checkout. Complete baseline-to-target and root-only patches, relevant exact file bytes, Git blob identities and SHA256 are preserved here. No tests rerun, product/test/runbook/review-body edits, production/model/browser actions, or overlap with root's ongoing integration.

## Findings

Critical: none. Important: none. No source changes required by this bounded review. Acceptable ROOT implementation for engineering integration, pending the separately frozen final integration evidence/text review. This is not professional HR acceptance or production deployment authorization.

## Recovery authority and compatibility

recovery_v5.py now obtains shared cutover state inside each candidate transaction before _current/domain row locks; require_lane(legacy, continuing=True) executes for every noncancelled candidate before wrapper access, grant refresh or poll mutation. Catching CutoverRejected outside the transaction rolls it back and skips the candidate. It blocks both new grant issuance and return of a still-usable same-epoch existing command in cloud/draining_cloud, without altering original durable authority.

Cancellation skips only the lane rejection, not original _current identity/lease/worker/binding verification. Its grant-refresh condition remains false, and the stop command and recovery observation callback retain their existing protocol. record_recovery/events/acceptance routing was not modified. Cutover permission or SQL failures remain psycopg errors mapped to503, not an empty-success or inferred legacy state. The unchanged lock_state/require_lane helper preserves genuinely missing table/singleton legacy compatibility. This route was already HR-only; no general Relay or non-HR admission path is newly gated.

New signed tests use persisted authorized v6 scope and real grant issuance, explicit wrong-lane residual fault fixtures (not fake successful cutover), both cloud phases, refreshed and not-yet-refreshed cases, cancellation, and no payload/grant/poll side effects on rejection. The same-key race observes shared advisory-lock waiting before held domain locks, then rechecks the committed phase. True permission revocation produces503 with state unchanged.

Coverage limits: wrong-lane callback test submits missing=True observation, not a complete remote stop-proof or result-event replay. Unchanged callbacks retain authority by source inspection and existing tests; no new full cross-phase result acceptance proof should be inferred. Missing-table/singleton and v7 behavior share inspected code but have no separate new parameterized tests here. These are disclosed limits, not reasons to change unchanged policy or broaden into remote executor acceptance.

## Search resume

Only the stale fixture import changes for the original three search tests; their assertions remain. Three added real route/database tests use actual valid state-machine transitions and verify resume rejected503 in draining_legacy/cloud/draining_cloud with unchanged turns/messages/missions/attachment binding counts and no new request-id turn. Resume is a new turn/admission, so draining_legacy rejection is correct; it is not owner continuation. Test login identity and ScriptedRelay remain substitutes, correctly disclosed. No product search code or non-HR policy was changed.

## Preflight and migration deployment

Preflight expectations now come from the reviewed CUTOVER_MIGRATION_SHA256 constants. Ledger match remains distinct from image_match/image_sha256. A changed image cannot bless its own ledger, and missing/unreadable image files yield None and a blocker. Six cases independently cover102/103/104 with original or equally tampered ledgers. check_schema_ready still supplies its independent schema result; overall blockers now include the image mismatch. Normal report fields and previous permissions/gate checks remain.

SIGQUIT joins the existing signal handler, which records interrupted state and runs the established cleanup path; no cleanup or role scope was weakened. The real local SIGQUIT test retains exit128+signal, stop operation, zero memberships and cleanup_verified assertions, while adding owned process-group cleanup for a crashing RED. create receives cap-drop=ALL and no-new-privileges:true; a test inspects actual captured mock-Docker create arguments. These tests use genuine local PG/signals but a Docker substitute. The new assertion reads the final container state; source applies the same argument list to both environments. No real daemon/capability test or SIGKILL recovery guarantee is implied.

## Owner health and CandidateUnavailable

Detailed owner health gets hr_agent.api_ready from actual service ready/repository/access presence plus worker_checked=false. Public liveness is unchanged and optional HR does not cause shared API restarts. Tests execute the existing owner/audit route while substituting only HR assembly, and check public liveness remains ok. This is an assembly snapshot, not dynamic DB/worker/professional readiness. Automatic compose HR readiness remains explicitly deferred; deployment preflight/authenticated reads/canary remain separate.

Candidate repository maps CutoverRejected into a fixed info diagnostic, psycopg faults into fixed database+SQLSTATE diagnostics, and decode/value errors into data_contract. Existing external CandidateUnavailable, conflict and not-found behavior stays unchanged. No raw exception text, query, traceback or personal fields are logged by the new calls. Real paused-gate and renamed-table tests verify distinct categories/SQLSTATE, exact safe text and no traceback. SQLSTATE is the psycopg server error code, not SQL detail. Connection and malformed-data category variants are source-covered rather than separately injected here; claims should remain at that level.

## Evidence and next closure

root-focused-final log has22passed/10warnings, exit0; deployment-preflight-green has37passed, exit0. These are precommit source snapshots identified by their respective command metadata and saved source material, not a claim that those old command HEAD fields are6eaefc2. Cosmetic import ordering occurred before the reviewed commit. Root's ongoing fresh frozen-commit integration will supply final source validation; this review does not invent its result or aggregate overlapping counts.

The reviewed report correctly preserves missing old logs, intermediate source limitations, declared substitutes, deferred orchestration/old-chain restore and unchanged business/production gates. Final documentation/evidence alignment is deliberately deferred to the next exact final commit supplied by root; this code review remains immutable to its own commit.
