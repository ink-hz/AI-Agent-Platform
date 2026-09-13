# Attachment write-fence implementation report

Scope: local source, tests, and documentation only. No production mutation, model call, browser action, service start, or object-store write was performed by this task. The production bucket's read-only `Enabled` evidence remains the input in `../s3-versioning-readonly-1/stdout.json`.

## Root cause and resulting contract

The earlier version purge fixed delete-marker-only erasure but could not close a PUT that had already crossed the database boundary and completed after the final object listing. Migration 064 abandons an upload attempt without stopping its external S3 request; an attachment processing job can likewise finish its derivative PUT before its database result is rejected. A later write could therefore recreate personal payload after references were cleared.

The implementation closes legitimate application writers with these linked rules:

1. Original/artifact uploads and derivatives use `IfNoneMatch="*"`.
2. Erasure first writes an unconditional zero-byte ordinary object with exactly `platform-erasure-fence=v1` metadata.
3. Versioned and suspended buckets are enumerated with exact-key filtering, bounded pagination, entry and elapsed-call checks. Every ordinary version is HEADed by its exact `VersionId`; only a zero-byte object with exact fence metadata and a matching returned `VersionId` is retained. Payload versions and every delete marker are deleted by exact `VersionId`. An unbound delete response is unknown and fails the attempt.
4. An unversioned bucket retains the same zero-byte fence. No cleanup path issues a bare key delete.
5. A successful pass proves the current object is a fence, the bucket mode is unchanged, and no listed payload or marker remains. Storage, pagination, HEAD, delete, mode, or response uncertainty raises a sanitized error. Erasure then records `partial`; orphan cleanup does not acknowledge the object.
6. Two erasers retain each other's verified fence versions. A delayed conditional payload PUT receives 412 after a fence exists. A derivative 412 is idempotent only when version/ETag-bound HEAD+GET proves the complete existing bytes and empty payload metadata; an existing fence or different object is never removed.

The successful-response cleanup boundary is narrower than whole-key erasure. A failed upload with an exact returned `VersionId` deletes only that version. A transport exception with no response performs no deletion. A confirmed unversioned write followed by local size validation failure uses fence-preserving erasure.

## Database closure and capability floor

`backend/control_migrations/106_attachment_erasure_write_fence.sql` has final SHA-256:

`7c4ae854dccb96292d33ed66af2070d8f555fcdf5539150fd7a592fedcd0cabb`

106 replaces only `claim_attachment_erasure_job_v64`. The authorization predicate remains unchanged. In the claim transaction it locks the selected attachment, changes it to `deleted` / `erasure_pending`, preserves encrypted references, and only then marks the erasure job running. It neither locks nor updates processing jobs. The maintenance role receives SELECT on only `attachment_id`, `processing_job_id`, `job_kind`, and `derivative_kind` in `platform_attachments.processing_jobs`.

The repository reads all derive jobs for the attachment in that same claim transaction, regardless of processing state. It derives each possible object key through the same byte-stable `object_keys.derivative_object_key` function used by the processor, covering derivatives whose external PUT happened before their row was registered. Ref decoding and key closure also finish before the claim transaction commits; an error rolls the logical delete and running claim back.

`fence_capability.require_attachment_fence_capability` validates the exact DSN purpose/environment, live `session_user`, `current_user`, database identity, ledger version 106 and literal checksum, and all four maintenance column privileges. Migration 106 gives app, brain-worker, and maintenance roles the ledger columns needed for this check. The attachment-enabled API builder checks before constructing upload/artifact services. Processing and maintenance builders check before constructing a writer or claimer, and worker healthcheck uses the same gate. This does not gate an API configuration with conversation attachments disabled.

The PostgreSQL tests cover both production and preview identities, both scan-result/erasure lock orderings, an unregistered derived key, exact grants, missing/wrong ledger and identity, and a revoked runtime maintenance column. In the last case a repository claim fails and its transaction leaves the erasure queued and the attachment unchanged; it does not leave a running job or wipe references.

## TDD and verification evidence

The original RED source fingerprint is `source-sha-before.json`. The first contract runs failed as expected:

- `red/s3.stdout.log`: 12 failed (no conditional PUT/fence/shared derivative key and unsafe prior semantics).
- `red/database.stdout.log`: 4 failed (106 absent and claim did not close the key set).
- `red/capability.stdout.log`: 8 failed (capability module and builder gates absent).

Final local receipts are under `green/`, with exact post-recorded argv/cwd in `green/commands.json`:

- Core fence/capability/database selection: **32 passed in 2.01s**.
- Every `backend/tests/test_attachment*.py`: **316 passed**, 5 existing Starlette `TestClient(timeout=...)` deprecation warnings, in 15.96s.
- Ruff on the changed attachment modules and related tests: **passed**.
- Python compileall for `app/attachments` and `app/main.py`: **passed**.
- `git diff --check` on this task's files: **passed** before sealing.

The first broad-suite receipt attempted a quoted glob and correctly records pytest exit 4/no collection as `green/attachment-suite-attempt1.*`; the immediately following unquoted, expanded selection is the 316-test pass. It is retained rather than rewritten.

The broader `tests/test_control_plane_migration.py` is not green: **44 passed, 2 failed**. `known-failures/control-plane-migration.stdout.log` shows both exact failures. One old assertion requires root migration filenames to be contiguous despite the existing root-directory 089–099 gap; the other static `TABLES` set omits `hr_execution_cutover` and `hr_execution_cutover_operations` introduced independently by 102. Neither failure exercised 106's claim, grant, checksum, or runtime capability behavior. This report does not call the repository-wide control migration surface green.

## Release and remaining boundary

The fence guarantee applies only after every legitimate payload writer and cleanup client uses this generation. API and attachment worker must therefore be released as one cohort after 106, with old unconditional writers excluded for the entire write-quiescence window. Old root100-only API/worker images are not safe rollback targets after fences are active. The host migration supervisor and production release commands are outside this source commit.

The claim functions select only `queued` and `partial`. A process crash after a committed claim can leave an erasure or processing job in `running` without an automatic lease/reaper. Ordinary handled errors remain retryable, and pre-commit repository errors roll back, but crash recovery remains an explicit supervisor gap. No unproven automatic reaper was added here.

The prior real MinIO run proved the version-purge precursor and the later local driver produced a RED against the old non-fence generation. Independent real-fence run 1 against this candidate proved the 1001-old-version, cross-page purge and final fence, then stopped when its first duplicate `AttachmentObjectWriter.put_stream` raised a sanitized writer error; two-eraser and half-write cases did not run. Its owned process/thread cleanup completed and its evidence is outside this commit in `../s3-fence-real-local-1/`. The specific real boto client/body-signing incompatibility still requires diagnosis, so this report does not claim the real MinIO suite or production write acceptance passed.

Both root HR design documents now state the fence, 106 ordering, shared deterministic derivative key, capability floor, cohort requirement, fail-closed behavior, and running-job recovery limit.
