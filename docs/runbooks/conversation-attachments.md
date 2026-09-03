# Conversation attachments operations

This runbook covers the private attachment path used by the HR web workspace. It does not enable the legacy Flywheel `/api/attachments` API.

## Storage and access boundaries

- PostgreSQL metadata is stored under `/data/orbbec-agent-platform/postgres` and object bytes under `/data/orbbec-agent-platform/attachments`.
- MinIO, ClamAV, and the attachment worker are attached only to `orbbec-agent-platform-internal`; none publishes a host port.
- The API uses the app-role DSN. The processing worker uses the brain-role DSN. Retention and erasure use the maintenance-role DSN.
- S3 credentials live in root-owned files under `/opt/orbbec-agent-platform/private` and are copied into service-specific Docker secret volumes. Never put them in Compose environment values or a release.
- The bucket must remain private and versioning enabled. Do not configure an object lifecycle shorter than the database `retained_until` value (365 days for normal conversation material).

## Provisioning and rotation

`remote-stage.sh` creates the S3 access and secret keys once, validates mode `0600`, and installs per-service copies. For rotation, stop only `platform-api` and `platform-attachments`, rotate MinIO credentials, replace the two service secret volumes, start those services, and run the acceptance checks. Keep the previous credentials only for the bounded rollback window, then revoke them.

The approved immutable runtime images are pinned in `compose.yaml`. Change a pin only in a reviewed release and verify its manifest before production pull.

## Health and capacity

The worker health command is:

```bash
python -m app.attachments.worker_runtime healthcheck
```

It fails closed unless both database roles connect, the private bucket responds, and the ClamAV signature database is fresh. Monitor upload completion, scan latency, processing queue age, erasure `partial` jobs, `/data` free bytes, and object/metadata count drift. Update ClamAV signatures continuously; a database older than 48 hours blocks scans.

Files are streamed with a 50 MiB per-file ceiling. Do not copy uploads to `/tmp`, a release, or the root disk. The worker's bounded tmpfs is for derivative scratch data only.

## Retention, deletion, and reconciliation

- Normal attachments retain for exactly 365 days. Archiving a conversation does not shorten this period.
- An incomplete upload expires after 24 hours.
- User deletion immediately revokes browser tickets and task grants, then queues physical erasure.
- Erasure covers the original object, every upload write attempt, all artifact versions, and every derivative. A failed object delete records `partial` and retries only that attachment job.
- After successful erasure, encrypted names and object coordinates are scrubbed. Only the minimum audit metadata and content hash remain.

Daily reconciliation must compare ready database objects, MinIO versions, processing jobs, and erasure jobs. Investigate missing objects or unreferenced versions; never repair drift by broad bucket deletion.

For emergency erasure, authorize the exact attachment ID, queue deletion through the database function, watch the job to `completed`, verify every version is absent, and retain the audit event. Do not use recursive `mc rm`.

## Backup, restore, and rollback

Back up PostgreSQL metadata and the MinIO data directory together to `/data`-backed encrypted storage. A restore drill must prove that metadata, object versions, hashes, tickets, and retention deadlines remain consistent.

Feature rollback sets `PLATFORM_CONVERSATION_ATTACHMENT_ENABLED=0` and recreates only the Platform API and attachment worker. It stops new upload/download actions but must not delete schema, object data, queued jobs, or secrets. The legacy `PLATFORM_ATTACHMENT_ENABLED` remains `0` throughout.

Before every release, record `df -B1 / /data`. The deployment uses `/data/staging/orbbec-agent-platform/<deployment_id>`, cleans only that directory through a trap, retains current plus two rollback releases and images on root, and archives at most ten releases younger than 30 days under `/data/archive/orbbec-agent-platform/releases`.
