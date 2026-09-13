# Attachment fence: bounded release-window review

**Use one shared API + attachment-worker write-quiescence window. Do not start a new fence eraser while any old unconditional payload writer or bare-delete cleanup can run.** This supersedes the old deployment plan's root100-only attachment-first startup and its permission to restore an old API while retaining the new attachment worker. It is an execution design for root review, not executed commands or production acceptance.

## Evidence and identity scope

The supplied 24-container snapshot is dated **2026-09-13 04:01:03 UTC**, not current live state. Its current symlink was release 65e7fbd14a3cbe99883b0b31e31b5705d183d1f9. Exact selected identities and images are in selected-historical-identities.json. Original snapshot, earlier mount receipt, old plan and inspected current source are copied exactly under source/ and hashed. No SSH, credentials, model calls or operational script edits were performed. Root must refresh sanitized exact IDs/images/PIDs/restart policies/mounts before executing because subsequent provisioning/hotfix operations can change them.

| Process/service | Actual attachment role | Fence-window treatment |
|---|---|---|
| platform-api; historical ID 3347e61e… | main.py builds AttachmentObjectWriter for user upload and ArtifactOutputService; original/artifact content routes perform S3 PUT and cleanup | Stop exact old instance and disable its automatic resurrection. Replace with conditional-PUT/fence-aware generation before admitting any upload. |
| platform-attachments; historical ID 908c2f63… | worker_runtime all runs derive PUT, erasure and cleanup | Stop every old/candidate instance capable of writing or deleting; restart only compatible generation. |
| platform-minio; historical ID de10f2bd… | server completing requests accepted from those clients | Establish server-side quiescence below; retain exact image and /data storage. |
| platform-hr-web-worker; historical ID ce6d1b1f… | direct_worker; ArtifactRecovery retries database result/file association, not the S3 object writer | Not a direct S3 payload writer in reviewed call graph. Do not fold HR execution cutover into this attachment stop list. It shares API-secret mounts, which alone does not prove active S3 PUT. |
| signed local execution worker | Output grant URL points to platform API artifact routes; local authenticated client submits bytes through that API | API stop closes the actual S3 gateway. Reviewed local ecosystem config has signing/cloud/DB/MetaBot paths, no S3 key/endpoint. This is a code/config conclusion, not a new live local-process inspection. Retried API uploads must reach only the new API. |
| new platform-hr-agent-worker | materials uses S3ImmutableAttachmentStore for reads | No direct payload PUT found; keep new admission gated under the separate HR rollout. S3 restart may interrupt reads. |
| attachment-storage-init; historical stopped, restart=no | mc bucket creation/privacy/versioning setup | Leave stopped; do not rerun implicitly through compose dependencies or change bucket mode. |

The repository-wide backend payload PUT search identifies two adapter calls: attachments/object_writer.py and attachments/worker_runtime.py. No additional legitimate direct S3 writer was found in inspected local execution/HR paths. This does not certify arbitrary uninspected container code. Refresh the running-container inventory and reject unexpected attachment writer/cleanup candidates. Existing snapshot separates **platform-minio** from **langfuse-minio**; other VOC/FAE/Langfuse services are not targets.

## Establishing the server boundary

Neither provided production receipt contains request-inflight measurements. Docker client exit proves no further code runs there, not that MinIO has finished already accepted PUTs. A one-off empty object list, a TCP disconnect or arbitrary sleep is also insufficient.

MinIO documents in-flight API counters (v2 minio_s3_requests_inflight_total; current v3 references also describe API/bucket metrics). Their presence, access method and precise deployed-version semantics are **not observed here**. A future metric path must first bind server version, enumerate all relevant request classes and exclude new arrivals; absent series/403 is unknown, never zero. [MinIO metric definitions](https://github.com/minio/minio/blob/master/docs/metrics/prometheus/list.md), [MinIO v3 reference](https://docs.min.io/aistor/operations/monitoring/metrics-and-alerts/metrics-v3/).

For this one local-data-directory server, a more concrete small boundary is a supervised stop/restart of **the exact platform-minio container**, while every old legitimate S3 writer and cleanup remains stopped and restart-disabled. Capture pre-stop container/image/host PID/StartedAt/mount and restart policy. Stop boundedly, escalate through the host supervisor if necessary, and prove the old server process exited before starting the same image with the same data and secrets mounts. A mere restart command return is not the receipt: prove old PID exit, new process identity/start and ready object-store response. No old in-memory server request handler can survive that process boundary. Operations completed before exit may persist and must be included by subsequent version enumeration; interrupted clients remain unknown and retain their ledgers. Do not claim every old PUT failed.

This design assumes the captured server is the sole server over its local /data directory, with no other process sharing that data mount and no external write queue/replication path. Confirm those concrete deployment facts before execution; the current Compose is a single `minio server /data`, internal network, no published S3 port. The older production mount receipt does not independently enumerate MinIO mounts, so fresh exact mount/process verification is an execution precondition. Do not restart Docker, PostgreSQL, the host, storage volumes, or langfuse-minio. Do not delete unfinished storage internals. If server stop/restart cannot be proved or storage recovery is unhealthy, keep the write window closed.

## Concrete sequence for the host supervisor

1. Prepare immutable compatible API/attachment images, exact 106/checksum, reviewed helper, source/binary fingerprints and backups before the outage. Confirm 106-required local tests, real delayed-PUT/two-eraser tests and root100-missing negative tests. Resolve the exact current container inventory, no-drift inputs and target data mount. Keep all staging candidates restart=no.
2. Enter a durable shared API maintenance window. Reject new public and internal API work through the existing ingress mechanism, if available; stopping the exact API is the fallback and must be reported as whole-API downtime. Record unknown in-flight upload/task requests, not invented completion. Stop and prevent restart of old API and attachment worker/cleanup instances. Do not broadly compose up or stop unrelated workers.
3. Establish the platform-MinIO old-server-process boundary above. Restart its same storage generation while API/attachment writers stay stopped. Capture versioning status, readiness and unchanged mounts/image. This is the extra data-plane interruption required by the unobserved inflight gap.
4. Under the existing bounded migration supervisor, verify exact existing ledger/checksums and apply appended 106 production-only. Assert claimed-erasure logical-deletion behavior and scoped SELECT rights with the correct identities; retain grant/session-zero receipts. Do not edit 064/100 or use a general owner credential in services. A migration failure leaves both writers stopped.
5. Create compatible API and attachment containers with durable new IDs before start, restart=no initially, fixed images and reviewed mounts. Require startup capability checks below before enabling attachment operations. Start the new API and new attachment worker as one compatible cohort; ingress can remain maintenance while internal checks run. Multiple new erasers are semantically safe only if all use the verified fence-preserving implementation.
6. Validate real owner/CSRF/signed-worker boundaries, upload→erase→version-aware canary (zero payload versions, verified fence versions), duplicate/unknown outcome behavior and non-HR shared API checks. Only then reopen ingress and persist the new cohort's intended restart configuration. Preserve old IDs stopped/restart=no. Keep HR 102–105 admission/cutover separate; an attachment pass does not grant HR cloud readiness.
7. Record per-stage exact timestamps, exits, container identities and uncertainty. A failed stage keeps affected writers closed and retains refs/recovery evidence. No step silently restores an old bare-PUT/bare-delete generation.

## Required 106 readiness binding

106 is a hard attachment write/erase capability floor for this cohort, not an HR-only health hint. The API attachment/upload/artifact service construction and attachment worker startup must verify the expected 106 ledger **and exact checksum**, plus the required maintenance processing-job column access on its own startup boundary. Missing/checksum mismatch/unreadable permission fails closed before write/claim/delete; no “best effort old100” fallback. A runtime check or unchanged-schema assumption must cover any supported reconfiguration; an administrator dropping DB capabilities later cannot be treated as success.

Generic shared /api/health liveness cannot stand in for this check. If implementation can disable only attachment endpoints safely, unrelated API may remain live while attachment admission is rejected; otherwise report API startup failure plainly. HR preflight/readiness with attachments enabled should consume the same capability proof but must not be the only enforcement, because other Bots and signed output upload also share these routes. This is a proposed contract to implement/test, not a claim current source already enforces 106.

## Impact and rollback

Shared API stop interrupts all Bot web/API requests, signed worker callbacks/artifact uploads and account/management routes using it; clients may retain/retry durable work through normal authenticated idempotent paths. MinIO restart interrupts all reads in the platform attachment bucket, including HR material reads. Other process identities can remain unchanged but this does not imply their requests were unaffected. Communicate the joint window as a platform API/attachment outage, not HR-only downtime. Feishu's independent process remains excluded.

Once fences/106 are active, rollback can only use another verified conditional-PUT/fence-aware API **and** attachment worker with the same schema contract. Old root100 API/worker images are not safe rollback targets. If no compatible prior generation exists, keep writes stopped and repair forward. Preserve data/versions, ledger, fences and references; never undo106 or delete fences to recover old code. HR state-machine rollback rules remain independent.

## Uncommitted audit directories for root sealing

At the status check for this review, all three prior directories were untracked; no files were added/committed by this reviewer:

- artifacts/2026-09-13-hr-launch/history-h03-real-review-3/ — report SHA 999428254f78943f639f68d7fbd0179268e1accc5e7a1cdda0779195be753720
- artifacts/2026-09-13-hr-launch/production/attachment-write-fence-design-review/ — report SHA 4d00ba07a1f46431fd10d1e158ddc69d244d105beaa44d3656da749558a4bfb8
- artifacts/2026-09-13-hr-launch/production/attachment-write-fence-design-review-2/ — report SHA 5b067d066245fb4b6e03c091568d1622d7e23d1a0b9720a2c28ee5dfa4d6a8b3
- artifacts/2026-09-13-hr-launch/production/attachment-fence-release-plan-review/ — this new report, exact sources and fingerprints.
