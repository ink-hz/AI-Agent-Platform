# Task 3 Report: Add owner-only partner access management

## Status

COMPLETE. The partner management API, safe service projections, audited rejection boundary, and embedded owner UI are implemented. No push, deployment, production credential access, or external side effect was performed.

The application accepts a deliberately injected `PartnerService` and fails closed with `503 partner management unavailable` when it is absent. This avoids reusing enterprise identity encryption material for partner identity data; provider/config construction remains outside this task.

## TDD evidence

### Clean baseline

Backend focused baseline:

```bash
cd backend
.venv/bin/python -m pytest tests/test_partner_service.py tests/test_r1_authorization.py -q
```

```text
67 passed
```

UI identity baseline:

```bash
cd webui
npm test -- IdentityManagementPage.test.tsx
```

```text
1 file passed, 34 tests passed
```

### RED

The API RED command was:

```bash
cd backend
.venv/bin/python -m pytest tests/test_partner_management_api.py -q
```

It failed during collection because `app.control_plane.routes_partner` did not exist. The UI RED command failed because `PartnerAccessPanel` did not exist:

```bash
cd webui
npm test -- PartnerAccessPanel.test.tsx
```

The service/migration RED added before implementation produced four intended failures: missing operator and binding-request read projections, missing fail-closed projection decryption, missing service/repository rejection, and absent `reject_partner_binding_request_v54`.

Independent review drove additional RED/GREEN cases for owner-before-service authorization, route-level CSRF, canonical DELETE UUIDs, missing mutation result projections, refresh/result mismatch, corrupt and reloaded pending state, inactive-operator FAE revocation, noncanonical stored Provider kind, complete binding-request metadata, explicit single-operator binding, and stale organization selection. Each new test failed for the stated reason before its implementation change and passed afterward.

### GREEN

Focused API verification:

```bash
cd backend
.venv/bin/python -m pytest tests/test_partner_management_api.py -q
```

```text
52 passed
```

Focused partner service, migration, authorization, application wiring, and existing control-plane regression:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_partner_management_api.py \
  tests/test_partner_operator_migration.py \
  tests/test_partner_identity_crypto.py \
  tests/test_partner_service.py \
  tests/test_control_plane_migration.py \
  tests/test_r1_authorization.py \
  tests/test_main.py -q
```

```text
179 passed in 6.43s
```

Focused UI verification:

```bash
cd webui
npm test -- PartnerAccessPanel.test.tsx IdentityManagementPage.test.tsx
```

```text
2 files passed, 48 tests passed
```

## Security and integrity behavior

- All eleven management routes use the exact `/api/v1/manage/partners/...` templates and require `platform_owner` at both route and central authorization boundaries. `member`, `management_viewer`, and `platform_admin` receive `403`.
- Owner authorization resolves before partner-service availability, so a non-owner always receives `403` without learning whether the service is configured. Unsafe routes also require the shared route-level CSRF verifier in addition to the global exact-Origin/CSRF middleware.
- Pydantic mutation bodies are strict and reject extra fields, noncanonical/non-string UUIDs, invalid enum values, empty display names, NULs, and reasons outside the trimmed 3-500 character bound.
- Repository reads retain encrypted fields internally. `PartnerService` decrypts only display-name payloads with entity-specific AAD, accepts the exact `{display_name}` plaintext shape, and fails closed as `partner_identity_unavailable` on malformed ciphertext or payloads.
- Route responses are explicit safe allowlists. They do not expose provider subjects, lookup HMACs, ciphertext, key versions, tokens, or secrets.
- `reject_partner_binding_request_v54` is an app-only `SECURITY DEFINER` function. It validates the session user and owner, locks the pending request, rejects it, records its resolution, and appends a sanitized `partner_identity_rejected` audit event in one transaction.
- Real PostgreSQL tests prove direct/admin misuse is denied and a forced audit insertion failure rolls the rejection back. The deliberately rolled-back fixture row is subsequently resolved through the same audited app-only boundary so later migration tests remain isolated.
- Every client mutation creates one request ID, persists its pending state before the request, requires the echoed ID and exact safe result projection, refreshes server projections before showing success, never replays automatically, and leaves network/5xx/response-integrity outcomes explicitly blocked for manual audit. Confirmed results persist their expected safe projection and clear only when immediate, reload, or manual refresh returns an exact match. Corrupt persisted records canonicalize to `integrity_failure` instead of silently unblocking.
- The UI is embedded below the existing enterprise identity controls only for the owner. It adds no route or navigation entry and never renders provider raw identity.

## Verification

Targeted Ruff verification for the new backend surface:

```bash
cd backend
.venv/bin/ruff format --check \
  app/control_plane/routes_partner.py \
  app/control_plane/partner_models.py \
  app/control_plane/partner_repository.py \
  app/control_plane/partner_service.py \
  tests/test_partner_management_api.py \
  tests/test_partner_service.py \
  tests/test_partner_operator_migration.py
.venv/bin/ruff check <same paths>
```

```text
7 files already formatted
All checks passed!
```

Full backend regression:

```bash
cd backend
.venv/bin/python -m pytest -q
```

```text
3345 passed, 2 skipped, 179 warnings in 284.19s (0:04:44)
```

The warnings are the existing Starlette per-request cookie deprecation warnings.

Full UI regression:

```bash
cd webui
npm test
```

```text
62 test files passed, 496 tests passed
```

Production UI build:

```bash
cd webui
npm run build
```

```text
TypeScript build passed; Vite transformed 3501 modules and completed successfully.
```

Vite emitted its existing advisory for chunks over 500 kB; it did not fail the build. `git diff --check` also exited 0 with no output.

## Independent review

The mandatory read-only reviewer checked the complete working tree against the Task 3 plan and design. All Critical, Important, and Minor findings were resolved with regression tests before commit, including authorization/dependency ordering, strict response and persisted-state integrity, exact projection reconciliation, fail-closed stored-data validation, revocation lifecycle behavior, enterprise test-storage isolation, and selector state transitions.

## Files changed

```text
.superpowers/sdd/task-3-report.md
backend/app/control_plane/authorization.py
backend/app/control_plane/partner_models.py
backend/app/control_plane/partner_repository.py
backend/app/control_plane/partner_service.py
backend/app/control_plane/routes_partner.py
backend/app/main.py
backend/control_migrations/054_partner_operator_identity.sql
backend/tests/test_partner_management_api.py
backend/tests/test_partner_operator_migration.py
backend/tests/test_partner_service.py
webui/src/pages/IdentityManagementPage.test.tsx
webui/src/pages/IdentityManagementPage.tsx
webui/src/pages/PartnerAccessPanel.test.tsx
webui/src/pages/PartnerAccessPanel.tsx
webui/src/partnerApi.ts
```

The pre-existing modified `.superpowers/sdd/task-2-report.md` is intentionally preserved and excluded from this task's commit.

## Concerns

- There are no blocking Task 3 concerns.
- Production construction of the independent partner identity/content keyrings is intentionally not inferred here. Until the caller injects a correctly configured `PartnerService`, the management routes fail closed with 503 rather than borrowing incompatible enterprise keys.
- The pre-existing Vite chunk-size advisory remains unchanged.
