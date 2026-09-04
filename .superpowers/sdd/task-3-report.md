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

## Formal review follow-up: immutable migration upgrade path

Formal review identified that Task 3 had appended rejection SQL to already-committed migration 054. The control-plane runner records each migration's SHA-256 and rejects changed bytes, so a database that had already applied Task 2's 054 could not upgrade to the Task 3 state.

The correction restores `054_partner_operator_identity.sql` byte-for-byte to the Task 2 version and moves every Task 3 SQL addition into additive `055_partner_management_rejection.sql`: the audit-validator replacement, rejection function, public and per-role revokes, environment/owner validation, and app-only grant. The callable rejection function keeps its existing `_v54` name so the shipped repository contract does not change; only its defining migration moves forward.

The immutable Task 2 checksum is:

```text
d1d89d5ca37d6c65c58e0362766173805d0262f9c9a5e02d790bf6ef03a421fc  backend/control_migrations/054_partner_operator_identity.sql
```

TDD RED was captured before the production correction. The byte regression reported the modified checksum `7ec802a26c33702957c15a1f7ceafc7d30908d392894935e5432c6c8a6e605bd` instead of the Task 2 checksum. The disposable-PostgreSQL upgrade regression independently recorded that same wrong 054 checksum after applying migrations through 054.

After restoring 054 and adding 055, the exact regression pair passed:

```bash
cd backend
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q \
  tests/test_partner_operator_migration.py::test_v54_bytes_remain_task2_immutable \
  tests/test_partner_operator_migration.py::test_already_applied_v54_upgrades_additively_to_v55
```

```text
2 passed in 0.88s
```

The PostgreSQL regression creates a disposable cluster with the real production control-database and role names, applies only migrations 001 through unchanged 054, verifies the persisted 054 checksum, then runs the current migration directory and verifies that 055 applies without changing the 054 ledger entry. It also confirms the rejection function exists after the upgrade.

Focused migration, API, and service verification:

```bash
cd backend
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q \
  tests/test_partner_operator_migration.py \
  tests/test_partner_management_api.py \
  tests/test_partner_service.py \
  tests/test_control_plane_migration.py::test_control_migration_versions_are_unique_and_contiguous \
  tests/test_control_plane_migration.py::test_migration_is_idempotent_and_checksum_guarded
```

```text
79 passed in 3.32s
```

Fresh full backend regression after the correction:

```bash
cd backend
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q
```

```text
3347 passed, 2 skipped, 179 warnings in 287.00s (0:04:46)
```

No UI code changed in this follow-up, so the UI suite and build were not rerun. The prior Task 3 UI verification remains applicable.

Formal-review follow-up files:

```text
.superpowers/sdd/task-3-report.md
backend/control_migrations/054_partner_operator_identity.sql
backend/control_migrations/055_partner_management_rejection.sql
backend/tests/test_control_plane_migration.py
backend/tests/test_partner_operator_migration.py
```

An independent read-only follow-up review verified the base hash, additive upgrade, validator and rejection behavior, least-privilege grants, PostgreSQL regression, migration expectation, and report. It approved the correction with no Critical, Important, or Minor findings.

---

# Task 3 report: FAE-style answer actions

## Status

Completed and committed as `f0dc95c feat(hr): align answer actions with FAE`.

## Files changed

- `webui/package.json` and `webui/package-lock.json`: added `lucide-react@^0.562.0`.
- `webui/src/components/conversation/MessageActions.tsx`: replaced copy/helpful/unhelpful text controls with accessible Lucide icon buttons; retained retry, feedback state, the downvote reason panel, and the existing 1,000-code-point optional-comment limit.
- `webui/src/styles.css`: added the FAE-like 28 px transparent icon-button treatment, including hover, copied, error, and selected states.
- `webui/src/components/conversation/MessageActions.test.tsx` and `webui/src/pages/HrWorkspace.acceptance.test.tsx`: added assertions for accessible labels, SVG icons, copied state, and the existing downvote-detail workflow.
- `webui/src/pages/ConversationPage.test.tsx`: updated existing consumers from the retired labels to `有用` and `不达标`.

## TDD evidence

Red command:

```sh
npm test -- --run src/components/conversation/MessageActions.test.tsx src/pages/HrWorkspace.acceptance.test.tsx
```

Result: expected failure — 2 failures across the 2 requested tests (new `复制回答` / `有用` / `不达标` controls did not exist in the old text-button UI).

Green command:

```sh
npm test -- --run src/components/conversation/MessageActions.test.tsx src/pages/HrWorkspace.acceptance.test.tsx src/pages/ConversationPage.test.tsx
```

Result: 3 test files passed, 25 tests passed.

Additional verification:

```sh
npm run build
```

Result: passed (`tsc -b && vite build`).
## Self-review and concerns

- `onFeedback` payloads remain unchanged: helpful submits `("helpful", null, null)`, and a downvote still submits `("unhelpful", reason, trimmedCommentOrNull)`.
- The old selected/disabled behavior and retry button remain intact; only the presentation and accessible control labels changed.
- The build reports Vite's existing large-chunk advisory, and the test run reports Node's localStorage experimental warning; neither caused a failure.
- `.superpowers/sdd/task-2-report.md` was already modified and was intentionally left unstaged and uncommitted.

## Review fixes

### Scope and selected-state semantics

- Added the explicit `MessageActionsPresentation` variant. `legacy` is the default; it keeps the prior visible copy, helpful, and improvement controls and their styling.
- Threaded the variant through `ConversationMessages` and `ConversationPage`. `DirectAgentWorkspace` selects `icon` only when `agentId === "hr-bot"`; every other direct Agent selects `legacy`.
- Added `aria-pressed` to both helpful and unhelpful controls in either presentation, reflecting the server-projected feedback state without changing feedback submission payloads.

### TDD evidence

Red command:

```sh
npm test -- --run src/components/conversation/MessageActions.test.tsx src/pages/ConversationPage.test.tsx src/pages/AgentUsePage.test.tsx src/pages/HrWorkspace.acceptance.test.tsx
```

Result: expected failure — 8 assertions failed. The old implementation defaulted to icon controls, did not expose `aria-pressed`, and did not pass a presentation variant through the HR/non-HR path.

Green command:

```sh
npm test -- --run src/components/conversation/MessageActions.test.tsx src/pages/ConversationPage.test.tsx src/pages/AgentUsePage.test.tsx src/pages/HrWorkspace.acceptance.test.tsx
```

Result: 4 test files passed, 42 tests passed. The tests cover legacy defaults, HR icon rendering, direct-Workspace HR/non-HR presentation routing, copied state, selected `aria-pressed`, retry-compatible actions, required downvote reasons, and the 1,000-code-point comment cap.

Additional verification:

```sh
npm run build
```

Result: passed (`tsc -b && vite build`).
