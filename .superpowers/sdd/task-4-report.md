# Task 4 report: fail-closed audit and role administration

## Status

Complete and committed at current Task 4 HEAD. No deployment, production
access, real DingTalk identity, Keychain access, or external mutation was
performed.

Commit:

```text
07dd217536ff14cc19c83b257df4faa22d58ad9c
07dd217 feat(identity): add audited role administration
```

## Design and failure semantics

Audit append and control mutation use separate control-database connections.
This implementation does not claim a distributed atomic transaction.

The protocol is:

1. deterministically append the immutable `*_requested` event through the
   audit DSN;
2. commit the control mutation through the app/migrator DSN while storing the
   requested audit event UUID on the affected role/grant row;
3. append a separate deterministic `*_completed` or `*_failed` event;
4. if step 1 fails, do not call or commit the mutation;
5. if step 3 fails after the mutation, return explicit
   `management_mutation_indeterminate`/503 with the safe request UUID;
6. retry using the same request UUID. Audit insertion collision checks and
   linked mutation IDs make reconciliation idempotent. The requested audit row
   is never updated.

The Web reason body has an optional `request_id` specifically for retrying an
indeterminate operation. Authentication, CSRF, and freshness do not trust
headers: their explicit dependencies fail closed until Tasks 6/8/12 install
the production implementations.

## TDD evidence

### Required RED

Command:

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_control_plane_audit.py \
  tests/test_identity_admin_cli.py \
  tests/test_control_maintenance_cli.py \
  tests/test_governance_audit_api.py -q
```

Exact summary/output cause:

```text
ERROR tests/test_control_plane_audit.py
  ModuleNotFoundError: No module named 'app.control_plane.audit'
ERROR tests/test_identity_admin_cli.py
  ModuleNotFoundError: No module named 'app.control_plane.admin_cli'
ERROR tests/test_control_maintenance_cli.py
  ModuleNotFoundError: No module named 'app.control_plane.maintenance_cli'
ERROR tests/test_governance_audit_api.py
  ModuleNotFoundError: No module named 'app.control_plane.audit'
!!!!!!!!!!!!!!!!!!! Interrupted: 4 errors during collection !!!!!!!!!!!!!!!!!!!!
1 warning, 4 errors in 0.18s
```

Exit status: `2`. This was the correct RED: only the not-yet-created Task 4
modules prevented collection.

### First implementation run

```text
4 failed, 31 passed, 1 warning in 2.37s
```

Root causes were isolated before changes:

- fake audit IDs were random despite the deterministic production contract;
- an argparse assertion counted both usage and option-list rendering;
- the installed TestClient requires `request("DELETE", json=...)`;
- maintenance correctly lacked broad table DELETE, requiring a narrow
  security-definer cleanup function rather than a broader grant.

After those fixes:

```text
35 passed, 1 warning in 2.43s
```

Additional RED/GREEN cycles:

```text
# Offline operation/audit mismatch negative test
1 failed, 1 passed, 1 warning in 1.52s
# Exact operation/event/result/generation linkage added
2 passed, 1 warning in 1.51s

# CLI machine-readable indeterminate error
ImportError: cannot import name 'render_error'
# Safe renderer added
1 passed in 0.05s

# Web caller request ID and real-PG reconciliation
2 failed, 1 warning in 0.83s
# request_id body/service hook added
2 passed, 1 warning in 0.82s
```

### Final focused GREEN

Exact required command:

```text
.......................................                                  [100%]
39 passed, 1 warning in 3.19s
```

The warning is the existing FastAPI TestClient deprecation warning:

```text
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is
deprecated; install `httpx2` instead.
```

## Real PostgreSQL evidence

All PostgreSQL checks use the disposable clusters created by
`test_control_plane_migration.control_database` for both production and
preview role sets.

Audit and failure states:

- the audit append role appends one deterministic row when the same command is
  submitted twice; event UUID, request UUID, result, reason, and allowlisted
  metadata are preserved;
- app, audit-append, directory-worker, and stream-ingest roles each receive
  `InsufficientPrivilege` on direct audit DELETE;
- required initial audit failure returns 503 and leaves a real target at role
  `member`;
- forced completed-outcome failure leaves the real mutation committed and
  linked to its immutable requested event, returns an explicit indeterminate
  response, and a same-request retry produces exactly one requested and one
  completed event;
- an offline `bind` cannot consume a `replace` audit intent or an intent for a
  different generation/result;
- no test or implementation updates the requested audit row.

Role/session transaction:

- real viewer revocation changes the role to `member`, stores
  `role_audit_event_id`, revokes the active Web Session in the same control
  transaction, and preserves requested/completed audit rows;
- real offline owner bind/replacement selects two same-display-name users only
  by protected stable provider lookup plus explicit complete generation;
- replacement demotes the old owner, promotes the selected target, links both
  changes to the audit intent, revokes Sessions, and leaves exactly one active
  owner;
- targets absent from the selected complete generation are refused.

Retention/grants:

- maintenance uses only `purge_expired_control_state()`, whose audit cutoff is
  database `clock_timestamp() - interval '365 days'` and cannot be overridden;
- a 366-day audit row is deleted and a 364-day row remains;
- a cutoff only one second newer than 365 days is rejected with
  `CheckViolation`;
- expired login attempts, absolute-expired Web Sessions, and old rate buckets
  are cleaned by the narrow maintenance-only function;
- unknown/breached time or WAL health is rejected before connecting;
- runtime roles retain no audit DELETE privilege.

## API and CLI evidence

API tests prove:

- explicit server `AuthContext`; spoofed role/user headers do not authenticate;
- owner-only internal user listing with only internal UUID, display name, local
  status, role, and exact scopes;
- owner + CSRF + fresh-directory requirements for role/scope mutations;
- viewer/member/hard-stale mutation denial;
- exact Agent ID observation assignment/revocation with reason and audit;
- immediate viewer Session revocation;
- owner/viewer governance-audit reads, member denial, and audit-of-audit-read;
- allowlisted immutable metadata without provider, Session, message, filename,
  file, or Evidence content;
- no bind/replace-owner Web endpoint.

CLI verification:

```text
.venv/bin/python -m app.control_plane.admin_cli --help       # exit 0, 19 lines
.venv/bin/python -m app.control_plane.maintenance_cli --help # exit 0, 12 lines
```

Admin CLI exposes only `bind-owner`, `replace-owner`, and
`show-directory-generation`. Target input is a mode-0600 provider-ID file and
an explicit generation UUID; there is no name/mobile/email selector.
Replacement requires two distinct named approvers, backup confirmation, a dry
run, and separate `--confirm`. Success/error output is JSON containing no raw
provider identity. Normal Task 4 replacement only is implemented; expanded
hard-stale continuity remains Task 10.

The runbook documents exact commands, backup and fresh-directory prerequisites,
two-person approval, rollback through another audited replacement, control-only
PITR escalation, post-recovery audit reconciliation, and fixed retention.

## Final verification

Control integration:

```bash
.venv/bin/python -m pytest \
  tests/test_control_plane_migration.py \
  tests/test_control_plane_repository.py \
  tests/test_identity_crypto.py -q
```

```text
60 passed in 2.25s
```

Full backend:

```text
767 passed, 1 skipped, 1 warning in 13.89s
```

Other final gates:

```text
compileall: exit 0
admin CLI --help: exit 0
maintenance CLI --help: exit 0
test_no_keychain_runtime.py: 2 passed in 0.01s
git diff --cached --check: exit 0
secret literal scan: clean
Keychain implementation scan: clean
provider/profile field scan: clean
placeholder scan: clean
```

Ruff was not installed in the backend virtual environment. Compileall, focused,
control integration, full backend, diff, credential, provider-field, and
Keychain checks were used instead.

## Files

- `backend/control_migrations/005_audited_role_administration.sql`
- `backend/app/control_plane/audit.py`
- `backend/app/control_plane/admin_cli.py`
- `backend/app/control_plane/maintenance_cli.py`
- `backend/app/control_plane/routes_manage.py`
- `backend/tests/test_control_plane_migration.py`
- `backend/tests/test_control_plane_audit.py`
- `backend/tests/test_identity_admin_cli.py`
- `backend/tests/test_control_maintenance_cli.py`
- `backend/tests/test_governance_audit_api.py`
- `docs/runbooks/platform-identity-break-glass.md`

## Self-review and concerns

- Migration 005 is additive; migrations 001-004 were not edited.
- `routes_manage.router` is intentionally not mounted in `main.py`. Task 6 must
  supply directory freshness, Task 8 must supply Session/CSRF dependencies,
  and Task 12 must install global route authorization, inject
  `ManagementService`, and mount the router. Until then all production hooks
  fail closed. Independent Task 4 FastAPI tests exercise the route contract.
- Phase one has no independent external audit sink. The owner may revoke a
  viewer; the immutable revocation remains for the owner/offline operators but
  the former viewer immediately loses audit access. The runbook documents this
  governance limit.
- The existing TestClient deprecation warning remains; it is unrelated to Task
  4 behavior.

## Review fix

All Critical and Important Task 4 review findings are corrected by additive
control migration `006_audited_mutation_boundary.sql`; migrations 001–005 retain
their recorded SHA-256 bytes.

### RED evidence

- Missing migration 006: `1 failed, 1 passed`; failure was the exact missing
  `006_audited_mutation_boundary.sql` assertion.
- Database bypass/functions: `2 failed`; app direct `internal_users` INSERT did
  not raise `InsufficientPrivilege`, and all required audited functions were
  absent.
- Ledger/owner/retention: `3 failed`; audited functions were absent before 006.
  Two incidental shared-fixture collisions were corrected before implementation
  so RED remained architectural.
- Repository boundary: `19 failed, 8 passed`; every cascade began at revoked
  direct user/role/grant SQL or the superseded 005 owner function.
- Audit contract: collection failed because `AppliedMutation` and
  `ControlCommitIndeterminateError` did not exist; the DB append-boundary test
  then failed four unsafe vocabulary/schema cases before SQL validation.
- DSN contract: collection failed because `app.control_plane.dsn` did not
  exist.
- Offline receipt contract: collection failed because receipt write/consume
  functions did not exist.

### GREEN evidence

- Disposable PostgreSQL boundary checkpoint: `7 passed in 1.55s`. It proves
  006 applies twice in production and preview; app user INSERT, owner promotion
  and demotion, audit-link manipulation, and observation-grant INSERT/UPDATE/
  DELETE raise `InsufficientPrivilege`; function grants are exact; operation
  ledger replay is idempotent and causally stale-safe; inactive-owner replace,
  old-replace replay, and referenced-audit retention are correct.
- Audit/coordinator: `28 passed in 0.78s`; exact Python and SQL vocabulary,
  typed metadata, unsafe-string rejection, actual-result outcome metadata,
  failed-outcome ambiguity, and commit ambiguity are covered.
- Identity/control repository: `25 passed in 1.25s`; internal user creation now
  crosses a narrow function boundary and later Session/identity flows retain
  required column access.
- Governance API: `17 passed in 0.93s`; same-operation retry reconciles through
  the ledger and typed outcomes contain actual roles/scopes/Session count.
- Offline administration: `9 passed in 0.83s`; root-owned mode-0600 HMAC receipt
  creation, exact payload/state binding, expiry, tamper rejection, atomic
  single-use consumption, stable approvers, owner v2 bind/replace, and
  provider-free output are covered.
- The combined Task 4/control suite reached `114 passed, 1 failed`; the sole
  failure was an obsolete test-string expectation for the old active-only owner
  predicate. It was corrected to assert the new status-independent owner-role
  uniqueness and is included in final verification below.

### Security semantics after the fix

The app has no direct DML on `internal_users`, `observation_grants`, or the
mutation ledger. Role/scope/owner functions require an exact persisted requested
audit event and deterministic operation UUID, enforce expected row versions,
and store immutable applied snapshots. Replaying an old operation returns that
snapshot without reapplying state. Audit retention nulls historical row links
while the ledger retains the non-sensitive requested audit UUID copy.

The append boundary rejects arbitrary event/action/result/reason values and
non-exact JSON schemas in both Python and PostgreSQL. Governance reads validate
and project only typed allowlisted metadata. A requested-audit failure prevents
mutation; business rejection is determinate only when its failed outcome is
durable; failed failure-audit, control commit ambiguity, and completed-outcome
failure return `IndeterminateMutationError`/503.

Offline confirmation is a root-operated HMAC receipt with two recorded stable
approver IDs. This does not claim independent approver signatures or an
external audit sink; those remain explicit phase-one governance limitations.
The management router remains unmounted and its auth/CSRF/freshness dependencies
remain fail-closed until Tasks 6/8/12.

### Independent security review closure

The requested independent review found one Critical and three Important gaps.
All four were reproduced together as `8 failed, 12 passed` before correction:

- Scope causality now uses a target/agent transaction lock and a monotonic
  version across every revoked/regranted incarnation. A delayed revoke from a
  prior incarnation is rejected and cannot revoke a later grant.
- The PostgreSQL append boundary now rejects required JSON nulls and incorrect
  JSON types. Scope arrays are capped at 256 entries and must be sorted and
  unique, matching the Python projection contract.
- Online management and offline owner administration now reject mismatched
  control/audit database environments before recording an intent or mutation.
- Governance projection now includes requested/completed/failed privileged
  management-directory reads.

The four review fixes reached `124 passed, 1 warning in 6.32s` in the focused
Task 4/control suite. The final fresh backend suite reached `816 passed, 1
skipped, 1 warning in 15.28s`. The no-Keychain runtime suite reached `2 passed
in 0.01s`; application byte compilation and both administration CLI help paths
also passed. `git diff --check` and secret/provider/router/direct-DML scans were
clean. Migrations 001–005 retained their recorded SHA-256 values.

The reviewer also noted a Minor limitation: a signed receipt is consumed by
atomic rename at its original path, while a copied signed receipt can be
presented again. It carries the same operation UUID and exact state/payload, so
the database mutation ledger makes such presentation idempotent rather than a
second state transition. Durable cross-host receipt redemption state is outside
this phase-one local receipt design and remains a runbook/governance concern.

## Second review fix

The final reviewer reported three Important findings and one Minor hardening
gap. The first real-PostgreSQL RED run was `2 failed`: the 257th scope produced
an indeterminate outcome because the failed/completed contracts could not
represent the existing list, and a legacy 257-scope viewer revocation committed
but could not persist its completed event. Offline reconciliation and legacy
projection initially failed at collection because their supported APIs did not
exist. Receipt/key symlink tests also established the missing descriptor-level
protection.

Additive migration `007_reconcilable_audit_boundary.sql` now enforces at most
256 active scopes before insert/update. Existing oversized legacy viewers can
still be revoked: their ledger/outcome uses canonical scope count plus SHA-256
summary instead of an impossible array. Same-request retry uses that immutable
snapshot. The offline `reconcile-owner` command verifies the consumed signed
pre-state journal and queries only the exact ledger identity; it appends a
completed or failed outcome without calling the mutation function. Receipt and
key files use no-follow descriptors, `fstat` owner/mode checks, inode/path
identity checks, and a non-group/world-writable owner directory.

Governance projection now marks strict rows `current`, projects only the 005
metadata allowlist as `legacy_005`, and emits visible empty-metadata
`unsupported_redacted` records for malformed/unsupported rows rather than
silently dropping them. No arbitrary legacy metadata is exposed.

The review-fix checkpoint reached `10 passed, 1 warning in 2.56s`; the complete
Task 4/control suite then reached `130 passed, 1 warning in 6.32s`. Migrations
001–006 are byte-immutable, including 006 SHA-256
`7d1886ee0d162ee7303020369a394227b5f6aa958986633e1e763d721b0911a8`.

Final self-review added a real concurrent PostgreSQL regression: without a
per-viewer lock, two simultaneous grants at 255 both committed. It failed with
two `committed` outcomes, then passed after the additive trigger acquired a
per-viewer transaction advisory lock. The final Task 4/control suite reached
`131 passed, 1 warning in 6.35s`; final fresh backend verification reached `823
passed, 1 skipped, 1 warning in 14.99s`.
