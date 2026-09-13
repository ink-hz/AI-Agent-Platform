# Suspended `null` fence compatibility

## Root cause and scope

The real disposable MinIO diagnostic in `../s3-fence-real-local-diagnostic-3/` showed the precise failing shape after bucket versioning was suspended: `ListObjectVersions` returned the latest fence as `VersionId="null"`; an explicit `HeadObject?versionId=null` returned HTTP 200, `ContentLength=0`, and exactly `platform-erasure-fence=v1`, but no parsed `VersionId`. The production read-only MinIO identity in `../minio-version-readonly-1/stdout.log` and the local source used by the diagnostic are the same MinIO commit, `7ced9663e6a791fef9dc6be798ff24cda9c730ac`.

The matching MinIO source is explicit: `cmd/api-headers.go:setObjectHeaders` emits `x-amz-version-id` only when the internal version is neither empty nor `null`. Its captured SHA-256 is `81b485e3db8b14df32a0654642fe9d151210ce8ef44da9c3b2f599e52e5d67ac`.

The runtime change is deliberately narrow. `_is_fence` accepts an omitted response version only when its private caller passed the exact listed string `version_id == "null"`, which means the request itself carried `VersionId="null"`. It still requires a dictionary response, integer zero content length, and the exact dedicated fence metadata. An explicitly present null value or conflicting response version remains an error. Missing or conflicting echoes for every non-`null` version remain errors. Current-object HEAD behavior and deletion response binding did not change.

The observer remains Enabled-only and was not widened to Suspended buckets. Root's real driver applied the same null-response protocol only to its dedicated Suspended case.

## TDD evidence

The frozen RED source and result are in `red/`: the real response shape failed under the base `b80ea68890d8a16bb959cb171e2709cc2bd61cbc` behavior (`1 failed, 2 passed`). A second RED (`explicit-none.*`) proved that treating an explicitly present `VersionId: None` as an omitted field was too broad (`1 failed, 1 passed`); the final implementation requires the response key itself to be absent.

Final local checks in `green/`:

- Focused compatibility and strictness checks: `5 passed in 0.12s`.
- All attachment tests: `321 passed, 5 existing Starlette deprecation warnings in 16.53s`.
- Ruff on the changed runtime and test: passed. The first lint attempt identified the test file's pre-existing import grouping once that file entered this change scope; the final source fixes that ordering and `ruff-final.*` is authoritative.

Root's independent real MinIO run is recorded in `../s3-fence-real-local-4/receipt.json` with `status=passed`, final adapter SHA-256 `42c230ff413fd53402176dd988ff78b511a79a28489130890808d7331cc44933`, and owned process/thread/socket cleanup complete. It covers 1,001 old versions over two pages, duplicate canonical and derivative behavior, two erasers retaining two fences, the bounded in-flight PUT ordering, Suspended null-fence retention with old-payload deletion, and the unversioned fence. This was local disposable MinIO with real signed boto3 requests; it did not write production or access a production database.

## Documentation and remaining boundary

Both root HR design documents now state the exact Suspended compatibility rule and preserve the fail-closed rule for every other missing or mismatched version identity. The broader release requirements already recorded there remain unchanged: migration 106, exclusion of old unconditional writers in the same cutover window, capability checks before PUT/claim, and explicit supervision of unreaped running erasure jobs.

`change.patch`, `source-fingerprints.json`, and `commands.json` bind the review to the exact source and commands. No production mutation was performed by this task.
