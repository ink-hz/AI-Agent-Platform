# Independent review: Task 2 / Task 3

Reviewed the supplied uncommitted patch against base 192a448, Task 2 report, operations source and recorded evidence. Source identities are in fingerprints.json. No source/index changes, suite reruns, production/model/browser calls, or host process actions were performed.

## Findings

Critical: none. Important: none.

Minor — Initialization receipt is silently discarded in the supplied patch: docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md:119 executes initialize and fetches one row without storing/printing it. Section 4 line 181 asks for successful command receipts to be retained, and initialization is the first gate operation with epoch/request identity. Count and transition already print outputs. Capture and print initialization result (prefer the same dict_row/JSON pattern as transition), and verify that the documented successful initialize emits its receipt. SQL transaction safety is unaffected. Root was informed separately; this review records the snapshot finding even if subsequently fixed.

## Spec compliance and evidence

Task 2 meets its scoped implementation requirements. 104 is additive; baseline byte comparisons confirm 102/103 unchanged. Existing 028 status/terminal_at CHECK makes the removed interrupted-with-NULL clause unreachable. 104 preserves signature, SECURITY DEFINER/search_path, and replacement-function grants/owner. The actual unresolved-stop, live association, terminal exact-v5-lineage protections remain. Tests extend real signed result completion with residual provenance faults; immutable trigger checks are correctly distinguished from independently executable query fault cases. Thirteen dependency-table renames exercise count/transition fail-closed, including conversation_turns handled by the exception path.

Readiness checks require exact 102/103/104 receipts, while preflight and migration helper include 104 release-file identity. Named compatibility inspection of app/main.py:1540,1571 and hr_agent/worker.py:105,115 confirms runtime strictness belongs to enabled cloud HR setup/startup; it does not alter the legacy absent-singleton compatibility path. No newly reachable unsafe count state was identified.

Counts evidence accurately discloses characterization on 103 (34 passed), transient generated-SQL and assertion mistakes, corrected run (115 passed / 2 failed), and helper-only correction (16 passed). These are not a single 117-pass execution; final integrated green evidence remains root's responsibility. No log was reconstructed or existing artifact altered.

Task 3 substantially meets scoped requirements. All three executable maintenance Python snippets set transaction-local 2s lock_timeout and 30s statement_timeout. Count uses read-only repeatable-read; mutations commit via connection context and exceptions roll back. Existing recorded operations-final-1.log and its command JSON record 12 passed, exit 0: real maintenance-role PostgreSQL lock contention and state/operation rollback, count timeout not zero, successful transition replay, three application-role failed-draft repository tests, and four mocked restore shell scenarios. Tests execute extracted documented Python, not duplicate SQL. Secret-file resolution is replaced locally; Docker and actual production PM2 are not exercised. The 30s setting is observed on count and present in mutation source; no 30s statement cancellation was exercised, so do not claim such a timed failure test.

Operational contradiction is resolved: persistent executor removal excludes normal deployment/automatic rollback; section 6.2 is an explicit approved restoration exception with gate kept draining_cloud, current safety fixes retained, exact instance/config/readiness/peer checks, then transition. Database phase does not restart PM2 or change stopped configuration. Failed drafts are technical terminal rows, not completed business outcomes: legacy retry must finish before drain; failed rows during drain require explicit dismiss/read-only disposition or pause, and the documented lack of draining_legacy->legacy matches 102's four-edge transition state machine. SQL does not claim to enforce the human disposition list.

The document correctly limits PM2 evidence to checked-in local repository configuration/scripts, not verified host deployment. Production exact counts, topology restore/readiness, production migration receipts and rollout remain unverified. Failed-draft tests verify repository/database authorization, not HTTP routing. No new browser or real model quality evidence exists.

## Disposition

Task 2 acceptable for local engineering integration. Task 3 acceptable with the minor initialization receipt follow-up. No approval for production cutover/deployment is implied. Root must attach its integration result and preserve historical evidence caveats.
