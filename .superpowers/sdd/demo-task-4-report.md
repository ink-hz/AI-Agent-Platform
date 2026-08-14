# Minimal DingTalk Demo — Task 4 Implementation Report

Date: 2026-08-14

## Scope

Implemented the code-only release, first-run prerequisite bootstrap, automated
acceptance and operator runbook for the isolated DingTalk preview. No command in
this work deployed to, mutated, restarted or authenticated against the target.

The reviewed scope expanded by one release-owned helper after review proved that
the target has no preview database, preview roles or completed 12-file secret
directory. Static packaging alone could never complete a first deployment.

## Delivered files

- `deploy/cloud/deploy-demo-preview.sh`
- `deploy/cloud/bootstrap-demo-preview-prerequisites.sh`
- `deploy/cloud/accept-demo-preview.sh`
- `docs/runbooks/minimal-dingtalk-demo.md`
- `backend/tests/test_demo_preview_release.py`

The release consumes the already-reviewed Task 1–3 implementation plus the
separately committed outbound-network repair. It does not modify Task 3.

## TDD evidence

Initial RED:

```text
11 failed
```

The failures were the intentionally missing deployment script, acceptance script
and runbook.

Review-driven RED after the database/network feasibility audit:

```text
3 failed, 11 passed
```

The three failures proved that first-run prerequisite generation, preview-only
database provisioning and Compose-runner execution did not yet exist.

Final focused GREEN:

```text
backend/.venv/bin/pytest -q backend/tests/test_demo_preview_release.py
20 passed
```

The final review RED added six independent regressions for the merged Compose
contract, resumable prerequisite publication, duplicate-free runner view, safe
`current` transaction, fail-closed rollback and the signal window between the
atomic `current` move and its bookkeeping flag. All six are GREEN.

## Implementation boundary

The release is an explicit `prepare → verify → activate` transaction:

- `prepare` rejects tracked or untracked dirt, requires the exact HEAD commit,
  builds `git archive`, and verifies the operator-supplied archive SHA-256.
- `verify` checks existing root/ADMIN/FAE/container/listener invariants before
  mutation, generates only preview prerequisites, builds an immutable image,
  verifies the fully merged Compose JSON, migrates and bootstraps through the
  dual-network `platform-demo-preview-runner`, starts only the two preview
  services, and proves 8081 is loopback-only. Nginx is not touched.
- `activate` first proves that `current` resolves to an exact 40-hex release
  directory, arms cleanup before creating a per-process temporary link, and
  switches it atomically. The failure handler ignores secondary termination
  signals, resolves the live link rather than trusting a late flag, requires the
  exact Task 3 rollback result, proves the Nginx include/snippet and 8081 listener
  are absent, and only then restores the prior link. A rollback failure keeps the
  new release/current and backend state, records `rollback-retry`, and hard-fails.

The first-run prerequisite boundary accepts exactly five root-owned `0600`
operator files. It generates three independent purpose-bound 32-byte keyrings and
four independent preview-role passwords/DSNs in a resumable root-only sibling
state. It creates only `agent_platform_control_preview` and the seven roles
required by migration 001. The owner and two unused roles are NOLOGIN/NOINHERIT;
the four real credential roles are LOGIN/NOINHERIT; the only role membership is
preview owner to preview migrator so the offline migrator can explicitly
`SET ROLE`. A completed rerun reuses the same credentials and does not rotate
them.

The deployment preflight now accepts a 6–11-file crash state only when the
sibling state is a root-owned non-symlink `0700` directory and its root-owned
`0600` files are the exact disjoint complement of already published generated
files. The migration and member bootstrap consume only the new duplicate-free
runner projection; the loopback Compose gate validates its exact networks,
image and sole loopback port without imposing the API/runner egress priority.

PostgreSQL gives ordinary databases PUBLIC CONNECT and has no per-role DENY. The
helper deliberately does not revoke production PUBLIC access. It removes direct
CONNECT grants for preview roles, verifies no direct preview-role schema/relation/
routine/type grants exist in any other database, and relies on four DSNs plus the
application DSN validator being pinned to the preview database. The runbook names
this residual rather than claiming complete same-cluster connection isolation.

## Acceptance boundary

The automated acceptance checks:

- exact built image ID and healthy preview container identities;
- loopback-only 8081 and unchanged public listeners;
- unchanged non-preview container image, ID, StartedAt and RestartCount;
- unchanged root, ADMIN, FAE domain and FAE IP behavior;
- trusted preview HTTPS, minimal JSON health, login HTML and a hashed build asset;
- login challenge Cookie `Secure`, `HttpOnly`, `SameSite=Lax` and preview Path;
- protected-account 401 without provider invocation;
- invalid state 401, one deliberately failed provider exchange, and consumed-state
  replay 401;
- exact 1–3 member allowlist count matching the safe bootstrap result.

It writes response bodies, OAuth state and Cookies only to a root-private temporary
directory, deletes them on exit, and emits only fixed PASS/FAIL labels.

## Verification run

```text
Related Task 2/4 deployment regression: 38 passed, 1 skipped
Full backend: 1176 passed, 2 skipped, 31 warnings
Frontend: 29 files / 169 tests passed
Frontend production build: passed
Python compileall: passed
Bash syntax (prerequisite/deploy/accept plus Task 2–3 scripts): passed
git diff --check: passed
No-Keychain / obvious secret / destructive-command scan: passed
```

The two skipped tests are environment-gated Docker checks. Docker is not installed
in the local execution environment, so local `docker compose config` and image
build were not claimed. The deployment script makes merged Compose JSON validation,
image build, exact image ID, dual-network `gw_priority`, migration, bootstrap and
health mandatory on the target before Nginx activation.

## Not completed in this code-only task

- No target mutation or release deployment.
- No real secret creation or copying.
- No real QR login, logout or unapproved-account scan.
- No rollback rehearsal.
- No production identity cutover. Tasks 8–16 remain the production gate.

The runbook evidence block therefore remains explicitly `未部署` / `未执行` and
does not represent production acceptance.
