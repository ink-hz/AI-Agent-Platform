# Exact synthetic attachment erasure observation — prepared, not production executed

The standalone `../attachment_erasure_observe.py` is a read-only companion to the existing HTTP `attachment_erasure_canary.py`. It never uploads, deletes, claims/records a job, issues credentials, or controls services. The real owner session is still unavailable. This preparation is not physical production erasure acceptance.

## Existing inputs and sequence

Use the reviewed application image with these **existing protected files mounted read-only**, not new copies of secret values in a report:

- `PLATFORM_CONTROL_DATABASE_URL_FILE`: existing app-role control DSN. The maintenance role lacks some SELECT columns required for this complete observation; no new SQL grant is introduced.
- `PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE`: existing platform-content-encryption keyring.
- Existing `PLATFORM_ATTACHMENT_S3_ENDPOINT`, `PLATFORM_ATTACHMENT_S3_BUCKET`, `PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE`, `PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE`.
- `--config`: the existing canary's absolute regular 0600 owner JSON containing owner_id/session_cookie/csrf/public_origin/api_base_url. No session is minted. Each observation verifies actual `/api/v1/account` and the exact attachment with HTTP GET, using the existing cookie, Origin and CSRF headers.
- `--ledger`: the canary's absolute 0600 ledger. It contains synthetic upload metadata and sent-content SHA, **no S3 references**. The observer checks the prepared metadata digest, synthetic bytes digest/size, upload response IDs and owner account evidence. It never modifies this ledger.

The following is the application-level command recipe, **not an instruction to start a container or execute production**. `/app/backend` and `/observer` stand for reviewed read-only application/script mounts; private file locations are supplied by the root operator. Both `attachment_erasure_observe.py` and its existing `api_canary.py` dependency must be importable.

```sh
PYTHONPATH=/app/backend python /observer/attachment_erasure_observe.py before --config /private/owner.json --ledger /private/canary/ledger.json --snapshot /private/observer-before.json --receipt /private/observer-before-receipt.json
# Root reviews captured receipt, then runs the existing HTTP erase command on
# that same canary ledger/attachment within its original mutation deadline.
PYTHONPATH=/app/backend python /observer/attachment_erasure_observe.py after --config /private/owner.json --ledger /private/canary/ledger.json --snapshot /private/observer-before.json --receipt /private/observer-after-receipt.json
```

Use one operator serially. The before snapshot and every receipt must be new paths in an already protected directory; no existing artifact is overwritten. After may be repeated with a new receipt path for read-only observation; it never extends the HTTP canary's 300-second mutation authority. No automatic wait, poll loop, DELETE retry, or restart occurs.

## Evidence contract

A single PostgreSQL REPEATABLE READ, READ ONLY transaction under the actual app role checks exact owner/attachment/upload identity. It reads only object-reference ciphertext, immutable locator, size/hash, row IDs and states from `attachments`, `uploads`, `upload_write_attempts`, `derivatives`, `artifact_versions`, `processing_jobs`, and `erasure_jobs`. It never reads original names, user question bodies, erasure reasons or content bytes. Related tables are scoped to the owner-proven attachment in the same snapshot. The upload-only scope requires **zero artifact_versions rows** and no queued/running processing jobs; either condition failing is unknown, not silently omitted coverage.

Before requires actual ready state, matching DB content SHA/size, no existing erasure job or erase attempt, decryptable original/upload/attempt/derivative references, and a present data version for every referenced key. It reuses `attachment_object_subject` and `ContentCodec`, decrypting only object references in memory. Exact object keys/version IDs are sealed using the existing content keyring into a 0600 snapshot. The snapshot binds owner/run/upload/attachment, synthetic content hash, DB DSN identity, S3 endpoint/bucket identity, exact reference row IDs and observer source SHA. A credential/endpoint/source change therefore requires explicit re-evaluation rather than silently observing another target.

S3 must report Enabled versioning. The client uses only GetBucketVersioning, ListObjectVersions with Prefix equal to the exact decrypted key, and HeadObject with an explicit VersionId. It filters Key equality (nearby prefix matches never become targets), follows complete key+version cursors, and refuses incomplete/repeating/over-limit pagination. SDK credentials need read permissions corresponding to `s3:GetBucketVersioning`, `s3:ListBucketVersions`, and `s3:GetObjectVersion`; no observer write grant is required or added. The existing credential may have broader worker privileges, but the observer issues no write operations.

After requires the same encrypted snapshot and binding. Every frozen data version is HEADed explicitly; the same exact keys are also fully re-enumerated to catch newly added versions. A 404 HEAD alone is insufficient: a successful, complete list must contain no data versions. A listed version—even if a racing HEAD returns 404—prevents green. A readable old/new data version is `remaining`; HEAD 403, transport error, missing reference, wrong scope, missing permissions, corrupt ciphertext, malformed list or exceeded limit yields `unknown`/exit 1. A single actual owner-requested erasure job must have completed after the before snapshot, the attachment must be deleted, and processing must be idle. A job still queued/partial/failed never passes.

The physical claim covers **payload/data versions at these exact keys**, not deletion-marker metadata, inaccessible backup/media replicas, or storage-provider internal retention. Delete markers do not contain the uploaded payload and are not counted as data versions. No GET reads object bodies. Observation proves the checks at observation time, not absence of future writes; existing service lifecycle controls and owner-only synthetic scope remain necessary.

The CLI wraps the entire invocation in the previously reviewed POSIX main-thread wall-clock supervisor: 60 seconds for HTTP, DB, S3, crypto and capture together. It rejects an existing alarm instead of replacing it, cancels its timer and restores the handler in finally. Receipt persistence and process unwinding occur after interruption, so 60 seconds is not an absolute guarantee for filesystem/OS teardown. Limits: 32 reference rows/keys, 128 checked data versions total, eight list pages per exact key, 128 entries per page, DB 3-second statements/1-second locks, finite SDK retries. Failure keeps a safe unknown receipt and never repeats a write (there are no writes to API/DB/S3).

Stdout and receipts contain hashes, presence flags, counts and job states only. Private snapshots contain encrypted references, not plaintext keys/version IDs or credentials. Raw SDK/SQL/HTTP exception strings are never serialized.

## Verification and limits

`red-command.json`, `red-tests.py`, and `red.log` preserve six initial failures because the observer did not yet exist. Intermediate `green-attempt-*` logs are retained. Attempts 1–2 exposed the reused local MemoryStore's missing derivative writer, leaving a real queued processing job; attempt 3 reached the expected provider-locator mismatch (MemoryStore supplies an etag, production uses versions). The test supplies the missing local derivative-store boundary and explicitly translates only returned provider locator metadata to a synthetic VersionId. It does not insert a fake completed processing/erasure record.

`green-final-1/command.json` and `output.log`: **11 passed, 1 TestClient timeout deprecation warning**, exit 0; exact source/test bytes and SHA256 before/after unchanged. Real local PostgreSQL, real issued/verified DingTalk session, real authorization/HTTP upload and DELETE, real processing and erasure repository/service paths are retained. Wrong owner fails; the actual app-role connection reports READ ONLY and rejects DELETE. S3 SDK responses are substituted; the positive S3 absence case is classification engineering, **not actual MinIO erasure**. The real SQL erasure service reaches completed through its normal APIs; the observer still rejects a simulated readable old version afterward.

Other negatives cover HEAD 403/network errors, missing reference sets, new versions, paginated exact-key filtering, missing cursors, page limits, corrupted encrypted snapshots and changed bucket identity. A real main-thread timer interrupts a deliberately blocking observation at a shortened 0.15-second test budget (recorded elapsed approximately 0.157 seconds), leaves an unknown private receipt without a before snapshot, and restores the signal handler/timer. No model, browser or production execution occurred.

The root's separate `production/s3-versioning-readonly-1/stdout.json` reports Enabled production versioning. Existing key-only deletion plus completed-job reference wiping explains why this observer was necessary; the version-erasure implementation is another agent's scope and is not certified by these tests. Frozen ce6/4f08431 deployment bundles are not executed by this work.
