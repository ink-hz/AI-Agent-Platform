# Observer adaptation for retained write fences

This is a new preparation revision. Previous observer commits and all previous logs remain unchanged. The invocation and protected credential inputs remain as documented in attachment-erasure-observer-preparation/README.md; trust_env=False from the separate proxy fix remains enforced. No production, model or browser execution occurred here.

## Updated physical-data acceptance contract

A valid fence is an ordinary object whose **explicit VersionId HEAD** returns ContentLength exactly integer zero and Metadata exactly `{'platform-erasure-fence': 'v1'}`. No extra metadata entries, nonzero content, or DeleteMarker may qualify. Multiple such concurrent fence versions are retained and accepted. Zero-byte unmarked business data is still payload.

After a canary DELETE, the observer:

1. HEADs every frozen before-payload VersionId and requires it absent. A still-readable frozen version fails even if it unexpectedly claims fence metadata.
2. Completely paginates the exact key again, rejecting delete markers and inspecting every listed ordinary version by its own VersionId. Every remaining ordinary version must be a verified fence; newly added payload fails. A listed version that disappears mid-observation is not accepted as a stable clean set.
3. Performs a separate current HEAD **without VersionId**. It must be an ordinary exact-metadata zero-byte fence whose VersionId was already independently verified. A current 404/delete marker or an unlisted concurrent current fence leaves the observation unprotected; another read-only observation may subsequently establish a stable result.
4. Retains the original real owner, same upload/attachment/content binding, real completed erasure job timestamp/owner, deleted DB state, zero artifact_versions, no active processing jobs, identical exact row-ID set, same source/DB/S3 identities, and encrypted before-snapshot checks.

`status=absent` now means **all payload versions absent and verified current write protection retained**, not no S3 objects at all. The output includes `verified_fence_versions` and hashed per-version/current-fence observations. A payload remains `remaining`; missing protection or delete markers are `unprotected`; ambiguous errors still become unknown/exit 1. Only the full contract plus real DB completion can set physical_erasure_verified. This is an observed service-writer-scope property, not protection against arbitrary outside actors or proof about storage-media backups.

## Derived keys cannot be omitted

Before now queries all derive processing-job identities/kinds/states for the owner-proven attachment under the existing app read-only transaction. It imports the existing `derivative_object_key` helper and independently checks each completed job against its deterministic derivative UUID and decrypted stored key. For this single ready synthetic text upload, a nonempty completed derive-job set and a one-to-one recorded derivative mapping are required. An unrecorded future key, missing job, unfinished job, extra derivative, or key mismatch is unknown: this observer intentionally does not accept incomplete/crashed derivative fixtures. It neither writes nor creates missing references.

The encrypted before snapshot therefore includes every deterministic derive key authorized by this narrow canary and all recorded original/upload-attempt keys. Processing-job ID plus derivative kind are added to the frozen row identity set; after rejects any new/changed job identity, preventing a newly discovered fence key from being silently skipped. The broader runtime 106 path that discovers/fences keys for unfinished jobs is another implementation's responsibility; this observer does not claim to test that path merely from a completed-derivative canary.

The existing bounds remain: 60-second whole observation supervisor, 32 bounded reference/job rows, 128 inspected ordinary versions total, eight bounded exact-key list pages; file cleanup/receipt persistence may follow interruption. HTTP, SQL and S3 operations remain read-only. Snapshots encrypted under older observer source bytes cannot be reused as if they proved the new contract; a new authorized canary before capture is required.

## Evidence

- `red/`: exact old observer/new test bytes and command receipt, 2 failed / 4 passed / 11 deselected. The two failures demonstrate that the previous observer rejected harmless multiple fences and did not distinguish latest delete markers under the new contract.
- `green-attempt-1.log`: interrupted at 57.80 seconds after the expected old empty-set assertion failed and the local real upload was stuck scanning. A narrow local diagnostic (`scan-diagnostic.log`) found `NameError: hashlib is not defined` in the concurrently edited runtime `_scan`. The runtime owner restored its import; this agent did not modify runtime. The local test now has a 15-second canary prepare budget to make a repeated fixture failure bounded. The temporary diagnostic trace was removed.
- `classification-green.log`: 16 classification tests passed before the real-PG rerun.
- `green-attempt-2.log`: real HTTP/PG path and then-current classification cases passed after the runtime fix.
- `green-final-1/`: **19 passed, 1 existing TestClient deprecation warning, 1.49 seconds, exit 0**. Exact observer/test source snapshots and SHA256 before/after for both plus four named runtime/helper dependencies are recorded; all six were unchanged during this run. Ruff passed.

The real local PG/HTTP test still issues/verifies actual owner sessions, uploads synthetic bytes through HTTP, runs actual processing and erasure repositories/services, and proves app-role READ ONLY refuses DELETE. Only external login, local object-store/provider metadata, and S3 responses are substituted. It now includes multiple fence versions on the S3 boundary and rejects both missing recorded derivatives and an additional unrecorded derive job. Other negatives prove exact metadata/size matching, old/new payload detection, current HEAD protection even after a marker-free list, and that the latest valid fence cannot classify an older unmarked zero-byte payload.

Root's earlier real MinIO test measured core version purge before the retained-fence adaptation. It must not be described as a test of this new write-fence concurrency contract. This preparation's S3 evidence is SDK classification only; actual fence/conditional-write behavior, migration 106 concurrency, and production physical acceptance require their separately recorded runs. No previous purge or H03 evidence was reclassified or rewritten.
