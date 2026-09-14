# Attachment erasure lease and bounded recovery report

## Outcome

The confirmed `erasure_jobs.state='running'` crash gap is closed in the reviewed runtime source. Migration 107 adds a five-minute renewable lease and an attempt token; only the current, unexpired token may renew or record a result. Expired attempts are reclaimed with a fresh token, and stale success or failure callbacks cannot update the replacement attempt. Automatic retries are bounded by `max_attempts` (default 5, allowed 1–10); exhausted jobs remain visible as `failed / erasure_attempts_exhausted` and retain all encrypted object references.

This report binds the runtime implementation to migration SHA-256 `d0405bdb767d2cc74efae79b566fab9380432d277c57d59589569a5e31687170`. The previously built `394c9ecd...` / `cbe282...` image does not contain migration 107 or this runtime and is not an eligible release image. Production migration, image build, service changes, SSH, S3 writes, owner/API acceptance, browser work, and model calls were not performed by this task.

## Root cause and implementation

Migration 064/106 could commit a claim as `running`, increment its attempt count, and then lose the worker process without a lease or generation token. The claim function selected only `queued` and `partial`, so the row could remain permanently stuck. The v64 result function keyed only by job ID and state, so merely requeueing the row would have allowed a late process to write over a newer attempt. `partial` also had no retry ceiling.

Migration 107 appends the following behavior without editing migrations 001–106:

- Existing running rows receive an unknown token and immediately expired lease. Other legacy rows receive normalized empty claim fields.
- A bounded batch of at most 100 due or expired rows is terminalized with `FOR UPDATE SKIP LOCKED` when `attempt_count >= max_attempts`. The next eligible row is claimed with a new token, incremented attempt count, and five-minute lease.
- Claim continues to lock and mark the attachment `deleted / erasure_pending` while keeping references. The physical object fence remains the protection against future application PUTs.
- Renewal requires the exact job/token, `running` state, and an unexpired lease. Recording has the same predicate. An expired token is rejected even before another worker reclaims the job.
- `partial` on the last permitted attempt becomes the visible exhausted state. Only exact-token `completed` reaches the existing result path that scrubs references.
- Runtime starts a 30-second heartbeat around object deletion, checks ownership before and after each key, joins the heartbeat for at most 15 seconds, permanently treats any renewal failure as loss, and performs a final synchronous renewal before recording.
- Claim commits before object-reference loading. Reference decode/load failure therefore consumes the claimed attempt through the same token-checked partial result. If that database result cannot be recorded, the committed lease expires and remains recoverable.
- The maintenance role loses `EXECUTE` on the v64 claim and result functions. New SECURITY DEFINER functions revoke `PUBLIC` and grant only the environment-matched maintenance role. The shared startup capability check now requires the exact 107 ledger checksum, the new columns and processing-job read grants, the v107 grants, and absence of the v64 grants.

Explicit operator recovery is limited to exhausted terminal rows. `erasure_recoveries` stores an immutable recovery UUID, job ID, and requested retry maximum. Replaying the same UUID and parameters is read-only and cannot reset the budget again after a later exhaustion; changing parameters under the same UUID is rejected. Recovery count comes from this durable table. A different recovery UUID can deliberately reopen an exhausted job, but cannot fabricate physical erasure or reopen arbitrary failures.

## Test evidence

The authoritative focused run is `green-core-3`. Its recorded command executes eight attachment capability, lease, erasure, S3 fence, migration, and real-PostgreSQL test modules. It completed with exit 0: **83 passed in 7.47 seconds**. The command receipt records the working directory, exact argv, test boundary, HEAD at execution, and all 11 source/test SHA-256 values. Current bytes were independently compared with every recorded hash.

The real PostgreSQL coverage includes:

- an owned child process that commits a claim and terminates through `os._exit(71)` before storage work;
- expiry and reclaim with a new token;
- rejection of an expired token before reclaim and of late completed and partial results after reclaim;
- a final crashed attempt becoming exhausted while its references remain;
- reference decryption failure consuming the bounded attempt;
- old v64 function privilege denial;
- explicit recovery, re-exhaustion, replay of the same UUID without a reset, changed-parameter rejection, and a second recovery count.

The child test uses bounded pipe polling and owned terminate/kill/join cleanup. It proves abrupt process exit after a committed claim; it is not a SIGKILL-during-S3 experiment. Lease expiry is advanced through the disposable database fixture instead of waiting five minutes.

Runtime coverage proves exact token propagation, a successful final renewal, and a renewal failure while object deletion is blocked. The latter prevents result recording and leaves the heartbeat thread stopped. Existing S3/fence and migration assertions remain in the focused run. The earlier real-MinIO fence tests validate the separate physical storage behavior but are not relabeled as 107 crash-recovery acceptance.

`green-core-2` is deliberately retained as a failed intermediate run: 82 passed and one PostgreSQL test failed because a previous test left a different, earlier claimable partial row in the shared disposable database. The fixture was corrected to make each intended job the oldest eligible row; production claim ordering was not changed. `red-unit-1` records an invalid Python 3.9 collection environment and is not product RED evidence. `red-unit-2` is the valid two-failure runtime RED, while `red-pg-1` records five real-PostgreSQL failures before migration 107 existed.

Ruff completed with exit 0 over the ten Python implementation/test files (`All checks passed!`). `git diff --check` also completed with exit 0 for the tracked runtime/test changes. An independent read-only review at `review/erasure-lease-runtime-final-core3/review.md` found no remaining important runtime issue and explicitly matched every final source hash.

The final attachment-family run deliberately excluded the eight modules already present in `green-core-3`, then ran the remaining `test_attachment*.py`, `test_conversation_attachment*.py`, and HR candidate-erasure coverage. The first run exposed one stale test double: `FailOnceS3.put_object` did not accept the writer's existing `IfNoneMatch="*"` argument, so Python rejected both calls before the intended fail-once behavior ran. This was an existing writer/fake contract gap discovered by the broader 107 regression, not a product change introduced by 107. The minimal test-only fix accepts and records the condition, asserts both attempts use `"*"`, and retains the original real-PostgreSQL assertions that the first distinct attempt becomes abandoned and the second distinct attempt becomes canonical. The targeted case then passed in 1.44 seconds. The authoritative second family run completed with exit 0: **355 passed, 15 warnings in 42.13 seconds**. The warnings are recorded Starlette/TestClient deprecations.

## Release and operational boundaries

Migration-helper manifests, production-only migration supervision, the 107 startup floor outside the runtime capability function, and the two root HR documents are owned and verified as a separate integration change. They must bind the same literal migration SHA before a new release image is built. The old 394/cbe image and the paused joint-release executor must not be reused for this implementation.

That integration is now frozen at helper SHA-256 `4a612094ac74ce689dfa945677ebd0e57b962ea451c56a08097c525acad9ceb2`. Its `final-1` receipt reports **44 passed in 87.72 seconds**, exit 0, over the two full helper files plus static migration inventory. It records all 106 prior migration files byte-identical to HEAD and migration 107 appended with the same `d0405bdb...` SHA. An independent read-only review in `review/erasure-107-host-docs/review.md` found no important helper, scope, authorization, or documentation issue. These are local helper/PostgreSQL/Docker-boundary tests; no production Docker or migration was run.

The maintenance snapshot in `readonly-counts.sql` is read-only and reports queued, running, partial, and exhausted counts plus lease state. The deployment window must capture these counts after old writers stop and again after migration/startup. A zero count observed earlier is not a substitute for the quiesced-window snapshot.

Terminal exhaustion intentionally keeps the erasure obligation and encrypted references visible. An operator must inspect and, when justified, invoke recovery with a fresh UUID. Automatic retries cannot loop forever. A renewal failure can occur after some physical keys were already erased; the next attempt safely repeats the idempotent fence-aware erase and only the current attempt may record completion.

The fixed runtime has not been run against production PostgreSQL or S3. Deployment still requires a fresh immutable image containing 107, exact migration supervision, quiescence of every old unconditional writer, the already designed MinIO/API/attachment-worker joint cutover, startup capability success, and separate authenticated API acceptance when an existing owner credential becomes available.

## Evidence index

- `design.md`: pre-implementation design and scope
- `readonly-counts.sql`: production-window read-only inventory query
- `red-unit-1/`: invalid Python 3.9 collection attempt, preserved for accuracy
- `red-unit-2/`: valid runtime RED, 2 failures
- `red-pg-1/`: valid pre-107 PostgreSQL RED, 5 failures
- `green-core-2/`: 82 passed / 1 failed shared-fixture ordering diagnosis
- `green-core-3/`: authoritative 83-pass command, logs, exit, and source hashes
- `green-family-1/`: broader regression RED, 354 passed / 1 stale-fake failure
- `green-family-targeted-1/`: corrected fail-once conditional-write test, 1 passed
- `green-family-2/`: authoritative remaining attachment family, 355 passed
- `lint-1/`: exact lint command and successful output
- `source-sha256.json`: frozen implementation and test hashes
- `source/`: byte-for-byte copies of the 11 reviewed implementation/test files
- `source.patch`: tracked and new-file patch for the reviewed runtime scope
