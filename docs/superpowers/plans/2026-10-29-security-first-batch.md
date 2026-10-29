# Security first batch implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore verifiable platform recovery points, address the deployed ZenTao PHP risk, and verify the expiring IP certificate without disrupting shared services.

**Architecture:** Resolve backup inputs from verified running container identities and immutable image IDs, recording the full ordered deployment evidence without recreating the application. Keep encryption and off-host recovery keys unchanged. Prepare PHP in an isolated candidate and apply one controlled production change at a time.

**Tech Stack:** Python 3, pytest, Bash, Docker, PostgreSQL 17, existing X25519/AES-GCM backup CLI, PHP/ZenTao, systemd and certbot.

## Global constraints

- User authorized this first remediation batch on 2026-10-29 after reviewing the Desktop report. Production changes necessary for these tasks are authorized; business messages and real model evaluations are not.
- Preserve all current valid fixes, every ordered Compose input, business data and unrelated worktrees. Actual running versions override local HEAD and the stale `current` link.
- Never test independent Nginx configuration with `nginx -t -c` or `nginx -T -c` on production. No production writable Nginx directories in candidate containers.
- FAE existing application fixes remain acceptance-only. No SSH/firewall changes before a verified recovery channel.
- No plaintext dump files, production recovery private keys on the server, secret values in arguments/logs, broad Docker cleanup, hidden retries, or automatic model fallback.
- Interface verification first; browser acceptance belongs to the user. Stop each affected batch at the first unexplained failure and retain evidence.

## Task 1: Runtime-bound encrypted platform backup

Files: `deploy/cloud/backup.sh`, new `deploy/cloud/backup_runtime.py`, `backend/tests/test_cloud_backup_runtime.py`; update affected deployment/retention tests and runbook.

Consumes: running `platform-api`, `platform-postgres` and `platform-minio` Docker metadata. Produces a complete encrypted backup set plus root-private manifest/last-success receipt, retaining the current backup CLI format and retention responsibility.

- [x] Capture the failing label contract: current API image uses a manufacturing tag; backup requires a bare 40-character SHA tag. Verify actual database/container/mount identities and all ordered Compose file references.
- [x] Write and run failing tests for runtime image ID selection despite non-SHA tags; unexpected project/service/mounts; ordered configuration identity; failed producer/encryptor; deployment change during backup; marker not advanced on incomplete set; safe diagnostic stages.
- [x] Implement an isolated standard-library resolver/orchestrator. Use immutable running IDs, never `compose up`; capture hashes for ordered input files, not their potentially secret content. Fail closed for unknown resources. Keep original encryption and no-plaintext pipelines; bind attachments to the observed MinIO data mount and explicit expected path.
- [x] Ensure each successful set records its scope and individual capture times. Do not claim a cross-database atomic snapshot. Retention must remain explicit and independently observable; do not silently remove it or equate retention failure with absent valid archives.
- [x] Run targeted tests, independent review, merge the verified changes to master, and install the exact reviewed helper/script with reversible systemd selection rather than rewriting stale release history.
- [x] Run the scheduled service under the corrected selection, verify artifact receipts/marker, download unchanged ciphertext, and restore off-host to isolated PG/MinIO. Verify table sets, report row aggregates and exact authenticated archive/extracted content; key remains local. First failed backup and first failed attachment baseline comparison remain failed.
- [ ] Original cross-time source directory equality was not achieved: 18/23,613 versus 42/42,277 internal metadata files/bytes. Preserve this failed criterion; later independent PG restoration and MinIO service/API checks do not turn it into a pass. Original role/IAM and full business DR remain unvalidated.

Commands: `backend/.venv/bin/python -m pytest backend/tests/test_cloud_backup_runtime.py backend/tests/test_cloud_backup.py backend/tests/test_cloud_retention.py backend/tests/test_cloud_deployment.py backend/tests/test_cloud_transport.py -q`; `git diff --check`.

### Task 1 execution-lifecycle review (2026-10-29, implementation gate)

Independent review of `e6082080` identified one common contract failure across
public-key copy, encryption, retention and `docker exec pg_dump`: the host
Docker CLI process was treated as the owner of daemon-side work. A CLI timeout
or helper SIGTERM does not establish that container/exec work stopped. Therefore
the old finally/flock boundary could release while retention still changed the
database, or while an encryptor or PG dump still ran. Adding another local
`docker rm` call to one branch would leave the same failure in adjacent stages.

The orchestrator owns each attempt's complete lifecycle while holding flock:
persist its unique operation/container identities before creation; create and
validate only this attempt's labelled containers; attach/start; on normal exit,
failure, timeout, SIGINT or SIGTERM stop/remove only these owned containers and
confirm their terminal absence before releasing the lock. Host processes and
their process groups are also reaped. If daemon reachability or cleanup cannot
be established, persist an unresolved-operation journal and deny a subsequent
backup rather than infer success or silently retry the business operation. A
hard SIGKILL cannot run Python cleanup; the durable journal is the admission
boundary for the next run and requires explicit operator reconciliation.

The parent approved PG producer adjustment uses an independently owned, named client
container from the running PostgreSQL immutable image, sharing the exact PG
container network namespace (`--network container:<PGID>`) and targeting its
verified internal IP. The parent verified `ip route get 172.30.0.2` in that
namespace routes locally via `dev lo`, preserving the old local-only dump
transport responsibility without a shared-bridge plaintext path. It mounts only the existing owner-password
file read-only, materializes a protected PGPASSFILE in its private tmpfs, and runs
pg_dump with a read-only transaction default. Values never enter host memory,
Docker environment arguments or logs. The shared PG container is never stopped
or used for an unowned exec. Actual secret-volume/file metadata and this change
were confirmed by the parent before implementation: named volume
`orbbec-agent-platform-postgres-secrets`, exact regular file
`/var/lib/docker/volumes/orbbec-agent-platform-postgres-secrets/_data/postgres-owner-password`,
uid/gid 999, mode 0400, 65 bytes; internal PG IP `172.30.0.2`. The host may
inspect metadata but must not open/read the password file. Real production
authentication/backup remains a separate parent-controlled validation. Loopback
127/::1 presently uses trust, so using 127 would not prove password authentication.
The self-IP matches the existing SCRAM host rule; `PGREQUIREAUTH=scram-sha-256`
requires that exchange and fails explicitly without changing HBA or network path.

The follow-up review of `a83d320b` accepted the execution ownership fix but found
one confidentiality blocker introduced by the independent PG client: attached
stdout still passes through the daemon logging driver. A pipe and subsequent
container removal do not prevent a default `json-file`/other driver from first
writing a plaintext dump on the host. The shared lifecycle executor therefore
creates **every** helper (public copy, PG producers, encryptors and retention)
with explicit `--log-driver none`. Before any start, and at subsequent owned-ID
checks, selective inspect must report `HostConfig.LogConfig.Type` exactly `none`;
CLI intent alone is insufficient. A missing/different value fails identity and
retains unresolved intent under the existing cleanup/admission contract. This
does not alter the dump, encryption, network or credential protocol.

- [x] RED/GREEN: the real-process simulated daemon defaults to `json-file` and
  records plaintext producer stdout unless disabled. Verify all seven helper
  roles explicitly select `none`, no simulated plaintext log is written, and a
  requested `none` with non-none/missing daemon metadata is rejected before start.
- [x] Repeat independent review of the logging boundary before production use;
  actual daemon none-driver inspection, SCRAM authentication and restoration also passed separately.

Already complete backup manifests/markers remain complete if retention is
interrupted; retention records failure or unknown completion separately, never
inventing rollback. If cleanup is unresolved, neither a failure receipt nor an
expired CLI deadline proves execution has ended.

- [x] RED: simulate daemon-owned workers with real child processes; show CLI
  timeout and helper SIGTERM leave no live owned workload before flock is
  reusable, while shared PG/unrelated workers survive. Cover copy, both pipeline
  roles and retention, cleanup failure journal, and next-run admission denial.
- [x] GREEN: unify lifecycle owner and signal handling; local process, PGPass
  escaping, metadata and exact-secret-file-scope tests validate the revised PG
  client contract without reading production values. Real Docker/PG authentication
  remains a separate gate before the full production backup.
- [x] Repeat the affected regression and independent review, then separately
  pass the parent-controlled real daemon/DB/scheduler and off-host restoration gates.

The readonly health contract distinguishes a live flock owner from orphaned
in-flight intent using O_RDONLY + nonblocking shared flock on the existing file.
An active owner does not suppress stale/corrupt backup or known retention failure.
After a new complete set commits, active pending retention is explicit
`status=in_progress, backup_status=fresh, retention_status=pending` (exit zero),
not a false completed/failed state. A journal with no owner is `unresolved_work`
(nonzero); a checker never creates/writes the lock or performs cleanup.

### Task 1 runtime-identity representation review (2026-10-29)

The first production attempt stopped at `runtime_changed` before any PG client
start or dump. Its public-copy helper and created-only PG client were confirmed
removed. A separately locked create-only diagnostic preserved raw before/after
resolves: IDs, image IDs, all 45 ordered Compose inputs and every mount field/value
were equal; only Docker inspect Mounts array order changed (API 15, MinIO 2).
The cause is comparing an unordered resource collection as an ordered list.
Resolve owns stable identity representation: sort **only** each container's
Mounts collection by a deterministic key over its complete item, retaining every
field/value, duplicate and count. Other identity fields remain exact, and Compose
inputs remain ordered. A permutation is equivalent; Source, RW, missing/extra
mounts and every other actual identity change must still stop the attempt.

The failure receipt also lacked its original resolve, preventing a retrospective
exact diff. The same attempt owner must atomically persist a private, unique
`backup-attempts/<attempt-id>.runtime.json` immediately after initial resolve and
before any helper. It records attempt ID, capture time, scope and the complete
metadata-only snapshot; failure receipts link that attempt and snapshot. An
evidence write failure stops before resource creation, never silently continuing.
No environment values, password contents or plaintext material are collected.

- [x] RED/GREEN: permuted complete mount items pass; Source/RW/missing/extra item
  changes fail while ordered Compose permutation remains a failure. Verify the
  private original snapshot precedes every helper and is linked by failure;
  snapshot persistence failure starts no helper.
- [x] Repeat targeted regression and independent review before a new full batch.
  The failed first attempt and create-only diagnostic are not successful dumps
  or repeat full backup attempts; the parent controls production validation.

## Task 2: ZenTao PHP security candidate and controlled cutover

Files: `deploy/sdk/` PHP-specific candidate/verification assets, `backend/tests/test_sdk_php_security.py`, `docs/operations/2026-10-29-sdk-php-security.md`.

- [x] Verify the actual Web interpreter, patch provenance, present image ID and IP/SDK SSO dependencies using read-only commands.
- [x] Choose the exact official 8.1 emergency source backport with recorded scope/provenance. Applicable native files: 10 pass; OOM compatibility diagnostics: one known failure, one unexecuted. This is not renewed upstream lifecycle support.
- [x] Build/test with no production writable mounts or public ports. Preserve existing ZenTao hardening and PHP extension/application compatibility. Record source/build and immutable candidate image identity.
- [x] Have the main agent review the candidate, create a current consistent ZenTao DB/data/config backup and verify off-host MariaDB restoration, then switch only ZenTao Web with captured rollback inputs. No shared API/Nginx or DB schema/data change.
- [x] Verify production CLI/Web PHP, final startup guard/maps/modules, anonymous rejection and existing service login/readonly story interface; compare the full runtime tree under four explicitly documented renames and HTTP JS/CSS bytes. Preserve original preinit-path comparison failure. SSO route/client engineering regression is separate from real employee SSO.
- [ ] Verify individual employee SSO, employee permission cases, real business attachment upload/download and user browser interactions. Synthetic multipart and service-identity GET are not these acceptance cases; full SDK business DR remains open.

## Task 3: IP certificate renewal and monitoring check

- [x] Inspect certbot timer, renewal lineage, hook, failure reason and current deployed certificate read-only.
- [x] Repair only the failed DNS dispatcher dependency; each DNS lineage staging passed once and normal service execution passed. The independent IP renewal already worked and was preserved, with no forced issuance or Nginx change.
- [x] Verify external platform/FAE/IP certificates with trusted hostname/IP validation; record fingerprints/expiration. Hourly sandbox health checks passed natural 11:00/12:00/13:00 runs.
- [ ] Bind an external alert destination and verify actual notification delivery; local JSON/journal/nonzero exit is not a notification channel.

## Task 4: Record outcomes and remaining security work

- [x] Record operational evidence without keys or personal records, pass/fail/unexecuted checks, precise source and production versions, backup scope and remaining gaps in `docs/operations/2026-10-29-security-first-batch-results.md` and linked runbooks.
- [x] Leave broader non-root/SSH/network/dependency work as separately versioned batches; no SSH/firewall/key changes. FAE application fixes remain a separate acceptance item. This batch cannot claim complete platform security acceptance.

Execution record: platform backup 1 pass/1 fail; original cross-time attachment comparison 1 fail retained; independent PG 1 pass; local MinIO 1 pass/1 fail; SDK cold backup 1 pass/3 fail; local SDK DB restoration 1 pass/1 image-create fail; PHP cutover 1 pass; original runtime-path comparison 1 fail retained, explicit runtime/access/content contract 1 pass; real SDK service auth 2 pass. Engineering and native diagnostics are listed separately in the results, not summed across overlapping suites. Employee/browser/full-business-DR and alert delivery remain open.
