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
- [ ] Write and run failing tests for runtime image ID selection despite non-SHA tags; unexpected project/service/mounts; ordered configuration identity; failed producer/encryptor; deployment change during backup; marker not advanced on incomplete set; safe diagnostic stages.
- [ ] Implement an isolated standard-library resolver/orchestrator. Use immutable running IDs, never `compose up`; capture hashes for ordered input files, not their potentially secret content. Fail closed for unknown resources. Keep original encryption and no-plaintext pipelines; bind attachments to the observed MinIO data mount and explicit expected path.
- [ ] Ensure each successful set records its scope and individual capture times. Do not claim a cross-database atomic snapshot. Retention must remain explicit and independently observable; do not silently remove it or equate retention failure with absent valid archives.
- [ ] Run targeted tests, independent review, merge the verified changes to master, and install the exact reviewed helper/script with reversible systemd selection rather than rewriting stale release history.
- [ ] Run the existing scheduled job once under the corrected selection, verify all artifact receipts and the marker, then decrypt and restore off-host into isolated PostgreSQL with no production mounts or published ports. Compare schema/table/row aggregates and attachment content identity; key remains on the local machine.

Commands: `backend/.venv/bin/python -m pytest backend/tests/test_cloud_backup_runtime.py backend/tests/test_cloud_backup.py backend/tests/test_cloud_retention.py backend/tests/test_cloud_deployment.py backend/tests/test_cloud_transport.py -q`; `git diff --check`.

## Task 2: ZenTao PHP security candidate and controlled cutover

Files: `deploy/sdk/` PHP-specific candidate/verification assets, `backend/tests/test_sdk_php_security.py`, `docs/operations/2026-10-29-sdk-php-security.md`.

- [ ] Verify the actual Web interpreter, patch provenance, present image ID and IP/SDK SSO dependencies using read-only commands.
- [ ] Choose a vendor-supported patched runtime, or an exact official source backport with recorded scope and provenance if compatibility requires it. Test expected patch coverage before marking the candidate fixed.
- [ ] Build/test with no production writable mounts or public ports. Preserve existing ZenTao hardening and PHP extension/application compatibility. Record source/build and immutable candidate image identity.
- [ ] Have the main agent review the candidate, create a current consistent ZenTao DB/data backup, and switch only ZenTao with a captured rollback combination. Do not change the shared API or Nginx as a shortcut.
- [ ] Verify Web PHP and all workers, startup guard, SDK SSO contract, authentication/permission interfaces and affected attachment behavior. User browser acceptance and absent signed-in coverage remain explicit.

## Task 3: IP certificate renewal and monitoring check

- [ ] Inspect certbot timer, renewal lineage, hook, failure reason and current deployed certificate read-only.
- [ ] Repair only the verified failed renewal dependency, validate in the provider's safe test mode where available, then renew/deploy under the existing lineage. Preserve IP/SDK SSO and shared Nginx rules.
- [ ] Verify external certificate identity/expiration and unrelated HTTPS sites; record exact failure and repair evidence.

## Task 4: Record outcomes and remaining security work

- [ ] Commit operational evidence without keys or personal records. Record pass/fail/unexecuted checks, precise source and production versions, backup scope, remaining PHP/IP/whole-platform gaps.
- [ ] Leave broader non-root/SSH/network/dependency work as separately versioned batches; this batch cannot claim complete platform security acceptance.
