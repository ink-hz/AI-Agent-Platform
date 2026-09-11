# HR WEB production release — 2026-09-08 15:02 CST

Status: published. HR page HTTP 200; cloud API healthy; independent HR worker
running; the authenticated MetaBot probe is healthy and the cloud's signed
worker observation is `ready=true, reason=ready` (07:02:26 UTC). No real model
business task was submitted during deployment; the user will perform business
acceptance. Refresh the HR page and create a new conversation to exercise the
new execution owner. Existing historical Turns were not rewritten or replayed.

## Versions and scope

- Platform API, attachment processor and new HR WEB coordinator:
  `03e60f83cddcfffe704cce627c1ca62d29da0327`, merged and pushed to master.
- MetaBot HR only: `6ddbdefffede245d48ec20b43ee487c7ff2c732c`.
  The final one-line compatibility-profile ID serialization fix closes the
  real production readiness handshake failure; its focused reproducer failed
  first and passed after the correction. The model remains `claude-opus-5`.
- Signed local Relay uses Platform code at the same release with v5 enabled.
  Its previous code/config are retained under the HR private rollback directory.
- Cloud root migrations through 088 and explicitly opted-in HR migrations
  089–093 applied; migrator owner membership revoked afterward. HR migration
  numbers are reserved in `backend/control_migrations/hr_web/README.md`.
- Local HR runtime has its own database/least-privilege roles and private input/
  output directories. The existing local PostgreSQL hba gained only the exact
  HR database/role/127.0.0.1 rule and was reloaded, not restarted. The local Relay
  received its additive v5 tables. No other application's database was changed.
- No Nginx changes (before/after configuration hash identical), Office route
  changes, other Bot restarts, Feishu migration, or business messages. Other
  cloud containers retained their IDs; non-HR Bot restart counts remained zero.
  Existing Platform loopback, directory, DingTalk, Brain, PostgreSQL and MinIO
  services were not restarted.

## Required disk / retention report

`df -B1 / /data`, initial preflight → final:

| Mount | Total bytes | Used before | Available before | Used after | Available after | Use |
|---|---:|---:|---:|---:|---:|---|
| / | 105286258688 | 59660140544 | 41113464832 | 60581076992 | 40192528384 | 60% → 61% |
| /data | 105088192512 | 27390832640 | 72311930880 | 27627474944 | 72075288576 | 28% → 28% |

Root net growth: 920,936,448 bytes, below 1 GiB. Preflight reserved 2 GiB for
build/image work and still exceeded the 20 GiB projected-free threshold. Final
root utilization is below 75%.

- New code release: 18 MiB, without data/uploads/logs/databases/dependencies.
- New Platform image: 482 MB; build cache accounts for additional root growth.
- New control database backup: 225 MiB, under
  `/data/orbbec-agent-platform/backups/hr-web-20260908/`.
- New local MetaBot artifacts: 2.4 MiB each for initial and corrected candidates;
  existing dependencies are referenced, not copied. Runtime data lives outside
  releases under the HR instance directory on the local execution host.
- Current Platform release/image: `03e60f8` / `ee24975e38f8`.
- Two retained rollback release/images: `56af396` / `35b0b211ccde` and
  `3d1769d` / `c75ce8d34c8b`.
- Archived `ed97c8e` to `/data/archive/orbbec-agent-platform/releases/` and
  removed only its verified unreferenced Docker tag/image.
- Archive retention restored to ten releases by removing oldest `8d9287c`.
  That release directory is no longer available, but source is recoverable
  from its Git commit. No business data was deleted. All retained archives
  are under 30 days old.
- `/data/staging/orbbec-agent-platform/` is empty after trap cleanup. The first
  cutover's cross-filesystem atomic-config rename was rejected; it changed no
  running cloud service, its staging was cleaned, and the subsequent same-disk
  config transaction completed. No tar, part or half-built staging remains.

## Rollback caution

Retained builds are not permission to switch a worker-owned live Turn back to
legacy execution. Stop new intake and reconcile original native execution before
rollback. Preserve additive ledgers, Results and private files; never rerun a
prompt to repair delivery.

Release metadata, pre-cutover environment backup, container inventory and Nginx
hash are stored on `/data/orbbec-agent-platform/release-metadata/03e60f83cddcfffe704cce627c1ca62d29da0327/`.
