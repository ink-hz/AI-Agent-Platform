# Independent Task 1 review of frozen commit 3504c20

Review target: 3504c20cb2266887759c00759b8675e2894521fa, base 5978eaa0bf657721d1ef7ab64d5b4a7ada5ca43a. Exactly three changed files: the runbook, test_hr_cutover_runbook.py and new test_hr_cutover_operations.py. Reviewed commit contents and supplied full saved patch/sources, Task 1 plan and operations report. No root uncommitted Task 2 implementation is included in this review. No source/index edits, test reruns, production, model, browser, Docker or PM2 execution.

## Findings and disposition

Critical: none. Important: none. Minor: none requiring changes within this scoped implementation.

Task 1 meets its stated local engineering requirements, with the explicitly retained operational/evidence limitations below. Acceptable for integration; not deployment approval or production stop/restore/erasure acceptance. The frozen runbook references root's Task 2 SIGQUIT/readiness implementation; those behaviors still require the separate final Task 2 source/evidence review and are not certified by this three-file review.

## Actual held-lock timeout coverage

runbook initialize/count/transition use autocommit=True plus explicit transaction(), SET LOCAL lock_timeout=2s and statement_timeout=3s. Count remains read-only repeatable-read. The mutation returns/prints a receipt only after commit. Existing contention and idempotent receipt assertions remain.

The new slow-count test (test_hr_cutover_runbook.py:136 onward) renames only the disposable database function, wraps the original real count followed by pg_sleep(6), preserves owner and maintenance-only access, and runs the actual documented transition. It observes maintenance holding granted ExclusiveLock while waiting on PgSleep, then observes the application role waiting for ShareLock on the cutover key during real non-HR repository append. QueryCanceled occurs and the append succeeds in under five seconds; phase/epoch/row_version and operation-row count remain unchanged. The retained actual receipt records 2.9949393337592483 seconds, draining_legacy/2/2 unchanged and zero new receipts. This reproduces holding the lock during slow count, rather than merely waiting to acquire it. It uses synthetic latency, not a pathological production query/production load. The runbook explicitly excludes host/network/Docker wall-clock guarantees.

## Four-edge state-machine/rollback claims

Runbook section 6.2 now defers old executor restoration entirely: no executable restore-one, old Worker/API restart or transition-to-legacy command remains. The documented transition validates targets before opening a database connection and rejects legacy. The only current recovery commitment is draining_cloud repair; failure in a future restore must receive a separately designed reachable endpoint. No SQL migration or state-machine edge changed. Shared API initial deployment force-recreate impact is expressly disclosed; recovery no longer purports to restart it without wider effects. Failed drafts still require explicit disposition and no draining_legacy direct reversal is invented.

## Attachment and PM2 causal controls

The attachment block validates approved runbook/migration/image/source identity and resolved compose image before stop, removes old restart policy, stops and inspects the original container, invokes the existing bootstrap, verifies migration100 plus six maintenance column privileges, and starts only the attachment service with no dependencies and the approved immutable image. Its cleanup after the stopped boundary attempts to stop precise old/current service containers on failures and withholds success receipt. Negative scenarios cover wrong configuration/source image, ineffective/failed stop, migration/ledger/start failure and wrong started image. The mock bootstrap checks old_running=false; mock up checks stopped+migrated; running state changes only through executed commands. Original shared bootstrap was read narrowly to confirm its four-argument interface; its actual authorization lifecycle/container execution is not covered by these mocks.

PM2 approval validates hostname, wrapper/ecosystem/runbook hashes, expected online/stopped state and unused evidence paths. It reads actual before state and peer snapshot, fsyncs copied wrapper/ecosystem and before identity before delete, then requires absent and equal peers before save/success receipt. Mock state becomes absent only on effective delete-one; delete_no_effect remains online and cannot save. Missing/wrong approval, host, wrapper/runbook, state, reused evidence, failed delete, changed peers and failed save are covered. These tests do not prove an actual host identity, watchdog absence, filesystem durability under a machine crash, or production PM2 behavior. No restore test is claimed because restoration is deferred.

## Exact evidence identity and limits

Saved full source bytes and source manifest match all three files in the reviewed commit, and 102/103/104 match base bytes. Each of the 20 final mock result records has the commit's runbook SHA, a matching saved original fence SHA, and a saved executed script matching its hash. Real PG receipt uses the same runbook SHA. Exact comparison results and artifact fingerprints are retained in commit-fingerprints.json.

Final frozen-operations log records 29 passed, exit0 in19.24s. That comprises nine PostgreSQL/handbook tests (some source-only assertions) and 20 external-command mocks; it is not 29 independent real infrastructure tests. Recorded static checks have exit0 for all 11 bash fences, Ruff on two test files and scoped diff check. No reruns were needed for this review.

Covered fences are1 attachment and11 PM2 via causal external mocks;6/7/8 embedded maintenance Python via real disposable PostgreSQL. Other fences have syntax checks only; outside Docker wrappers, bootstrap authorization, real containers, actual erasure and production process state remain unverified. Full runbook/fence identity prevents an earlier SHA from certifying newly edited blocks.

The Task 1 report expressly discloses that early RED/intermediate GREEN command/output/exit records did not contemporaneously retain exact source bytes or authenticated source hashes. The055613 runbook SHA alone does not reconstruct its full intermediate text. This review does not restore or infer those missing intermediate bytes, and does not apply final source to earlier RED. Final055820 receipt plus055907 full byte/patch snapshot is now bound to this actual Git commit. The report's historical precommit wording is treated as its generation-time statement; this independent receipt supplies the frozen commit identity.
