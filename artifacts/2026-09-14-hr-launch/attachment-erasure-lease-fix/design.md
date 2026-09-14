# Attachment erasure lease recovery — implementation design

## Confirmed failure

Migration 064/106 moves a claimed erasure job to `running` and increments `attempt_count`, but stores no lease or attempt token. The claim query only selects `queued`/`partial`; a worker process killed after commit therefore leaves a permanently running row. The existing four-argument result function accepts any caller holding the maintenance role for any running job ID, so blindly requeueing would also let the old process submit success or failure against a newer attempt. `partial` has no max attempt bound.

## Migration 107

Add `max_attempts` (default 5, 1–10), a non-null `attempt_token`, and `lease_expires_at` to `erasure_jobs`. Normalize non-running legacy claim fields; legacy running rows receive a token and an already-expired lease so the new worker can recover them. Add a claim index for state/time/lease. Do not alter migrations 001–106.

`claim_attachment_erasure_job_v107` first marks due queued/partial or expired-running rows at their max as terminal `failed` with reason `erasure_attempts_exhausted`, preserves all encrypted references/reason evidence, and records bounded-attempt metadata. It then claims exactly one queued/partial due job or expired running job below max under `FOR UPDATE SKIP LOCKED`, generates a new token, increments the count, and grants a five-minute lease. An expired running row is never reset without changing its token.

`renew_attachment_erasure_lease_v107` extends the lease only for the exact current job/token while it is running and unexpired. The service uses a 30-second heartbeat during each S3 operation because one exact key may require many version HEAD/delete requests. A renewal failure prevents any later key or result commit. Physical deletion already completed before a lost renewal remains safe and idempotent, while database references remain for the next attempt.

`record_attachment_erasure_result_v107` requires the exact current token and an unexpired lease. Expiry rejects a late result even if no worker has reclaimed yet; after reclaim, both late success and late partial fail the token check. A partial result at max becomes terminal `failed/erasure_attempts_exhausted`; only a current completed result scrubs references. Explicit failed and exhausted results preserve references.

The old v64 claim/result functions are explicitly revoked from maintenance roles so an old attachment worker cannot claim or commit after 107. A maintenance-only `recover_attachment_erasure_job_v107(job,recovery_id,max_attempts)` allows a deliberate retry of only an exhausted terminal row, records recovery identity/count in status, resets the bounded counter, and creates a new attempt generation on the next claim. It never marks erasure complete.

Claim commits before reference enumeration/decryption. If reference loading fails, the repository records a partial result with the current token, so malformed or unavailable references consume the same finite budget. If that record itself cannot reach the database, the committed lease remains and becomes recoverable after expiry.

## Runtime and release floor

`ErasureJob` carries the attempt token. Repository claim, renewal and record use only v107 functions. The service starts a bounded heartbeat, stops it before final record, performs one synchronous final renewal, and refuses completion after any lost lease. Worker startup and health retain the existing capability check, upgraded to the exact 107 ledger checksum, v107 columns, and maintenance function privileges. Root migration manifest/supervisor adds exact 107 after 106; old images fail the new floor.

The maintenance read-only query reports queued/running/partial/failed-exhausted counts plus job ID, attachment ID, attempt count/max, claim age and lease state. The release executor must capture running counts after old writers stop and again after migration/startup. This is an observation gate, not a mutation or automatic reset.

## RED coverage

- Real PostgreSQL: process exits immediately after a committed claim; expiry permits a new token, while the old token cannot submit success or partial.
- Real PostgreSQL: an expired token is rejected before reclaim; the last claimed attempt that crashes becomes terminal exhausted after expiry, retains refs, and blocks ordinary duplicate requests.
- Real PostgreSQL: explicit recovery of exhausted work requires a new recovery UUID and yields a fresh token; arbitrary failed jobs cannot be reset.
- Real PostgreSQL: reference decode/load failure consumes attempts and eventually becomes visible exhausted without scrubbing references.
- Runtime unit: renewal loss during storage work prohibits result commit; heartbeat stops; current-token partial/completed propagation remains exact.
- Capability/helper/static migration tests: exact 107 checksum, columns, grants, continuous migration manifest and old v64 denial.
- Process-failure test uses a real child process against test PostgreSQL, not a fake ticker. No production, S3, model, browser or SSH action is part of this implementation.
