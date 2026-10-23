# Task 2: external DingTalk integration

Status: implemented locally; final targeted gate passed. No production action.
Base: `d5134e9c` (includes reviewed provider and canonical FAE launch change).

## Implemented

- Additive control migration **123**, after the complete top-level migration inventory
  through `122_sdk_native_requirements.sql`. No published migration was edited.
- Five-minute, one-use external attempts bind a keyed state digest to a keyed browser
  nonce. `fae-dt-v1.` callback dispatch precedes Platform cookie handling and cannot
  fall through to enterprise authentication. External callbacks accept `authCode` and
  reject duplicate parameters and `code` ambiguity. The nonce cookie is Secure,
  HttpOnly, SameSite=Lax, host-only, path `/`, and expires after five minutes.
- Existing trusted edge rate-limit buckets cover external starts/callbacks; the existing
  provider-exchange limiter covers code exchange.
- App-only security-definer atomic provisioning fixes provider to `dingtalk_external`,
  realm to a server UUID and agent to `ai-fae-agent`. It validates the consumed fresh
  attempt/nonce, holds the key-policy transaction lock and every sorted HMAC transition
  candidate lock, verifies any matching ciphertext in the repository, creates/reuses
  one subject/operator/identity/grant, refreshes encrypted nickname/verification time,
  and writes a fixed system audit. Existing suspended/revoked entities, denied grants
  or another realm fail closed. No owner identity is fabricated.
- Grant origin explicitly distinguishes owner grants from external OAuth grants.
  Existing owner mutations remain owner-only. System audit fields are fixed inside the
  narrow function; the existing append-audit dispatcher and all historical branches
  remain unchanged.
- Partner service/provider production factories load the control DSN, existing content
  keyring and two independent purpose-bound partner keyrings without Brain/relay.
  Registered external adapter remains subject to the unchanged real-account production
  release evidence gate. Factory injects durable `verified_at` lookup for the eight-hour
  lease; provider-owned HTTP resources close during application shutdown.
- Added owner-only CSRF-protected audited identity detail endpoint, with no-store unionId,
  registration digest, stable subject and nickname response. Ordinary callback/launch
  responses contain no unionId or provider secrets.
- Added private idempotent key preparation helper, disabled Compose secret references,
  and `docs/runbooks/2026-10-23-fae-external-dingtalk.md`. No release evidence was generated.

## TDD and verification

RED observations:

1. `pytest -q backend/tests/test_external_dingtalk_integration.py -x` failed with
   `automatic external provisioning is missing` before provisioning implementation.
2. `pytest ...test_external_dingtalk_integration.py -k factory` failed with
   `production partner service factory missing` before factory implementation.
3. Owner trace authorization test failed with `route_not_authorized` before adding
   the explicit owner route entry.
4. `pytest -q backend/tests/test_external_dingtalk_secret_preparation.py` failed twice
   with `partner key preparation helper missing` before writing the helper.

The dedicated integration/secret tests cover real disposable PostgreSQL, four concurrent
same-ID logins, encrypted stable IDs, FAE-only grants, no forged owner, exact TTL,
wrong-browser rejection before provider exchange, replay, one-time provisioning,
expired/stale attempts, identity/subject/operator/realm/grant revocation, wrong realm,
audit-failure rollback, both environments' function permissions and denied direct DML,
owner-only audited identity details, process-restart lease expiry, trusted-edge throttling,
factory wiring without Brain/relay, production evidence refusal, and private key files.
The HTTP flow uses a mocked httpx provider transport but the **real launch repository,
launch issuance, code exchange and partner binding**. A valid existing Platform cookie
cannot divert the external callback; no Platform cookie is issued.

Final command (using `PYTHONPATH=backend` and
`/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/pytest -q`):

```text
backend/tests/test_external_dingtalk_integration.py
backend/tests/test_external_dingtalk_secret_preparation.py
backend/tests/test_partner_provider.py
backend/tests/test_partner_service.py
backend/tests/test_partner_management_api.py
backend/tests/test_partner_release.py
backend/tests/test_dingtalk_auth_api.py
backend/tests/test_partner_operator_migration.py
backend/tests/test_dingtalk_external_provider.py
backend/tests/test_agent_launch.py
backend/tests/test_identity_rate_limits.py
backend/tests/test_control_plane_config.py
backend/tests/test_config.py
backend/tests/test_cloud_deployment.py
```

Final result: **904 passed, 0 failed, 0 skipped in 80.01 seconds**. The 125 warnings
are existing TestClient per-request-cookie deprecations. `git diff --check` passed.

Intermediate issues were resolved rather than hidden: the factory now preserves the
existing redacted startup error contract, generic partner-router test doubles remain
compatible, and missing worktree dependencies were linked from the existing workspace.
An owner-detail test uncovered that the old list-operators query lacks app-role access
to subject display columns; the new audited detail function returns only the selected
subject's encrypted nickname, without broadening direct table permissions or depending
on that existing list query. A test assertion assuming flat FastAPI routes was replaced
with a real HTTP boundary check.

## Self-review and limitations

Reviewed state dispatch, nonce/code ordering, single-use consumption, SQL grants/search
paths, ciphertext lookup equivalence, rotation candidate locking, actor-free system
creation, no automatic revival, ownership of the detail route, error redaction, callback
cookie handling, durable lease injection and the release gate. No other agent's code or
existing migration bytes were changed. Historical `.superpowers/sdd/task-2-report.md`
was not overwritten.

Local generated dependency links are not committed. These tests do not contact DingTalk,
exercise real accounts, deploy to production, inspect browser UI, or assert current
AppKey external-account entitlement. Production migration, release evidence from two
real external accounts, account isolation/revocation/replay acceptance, and production
enablement remain the root/human release work. Local 60-second validation caching and
the eight-hour recent-OAuth lease have the limitations documented in the design.
