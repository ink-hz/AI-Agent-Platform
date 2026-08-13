# Task 2 Report: Bootstrap and migrate the isolated control databases

## Status

COMPLETE. Implemented and committed as:

```text
4a577d6 feat(identity): add isolated control databases
```

No production deployment was performed. The existing `agent_platform` replica database, replica roles/DSNs, and FAE lifecycle remain unchanged; `remote-stage.sh` received only the reviewed bootstrap-helper invocation.

## TDD evidence

### Clean baseline

Command:

```bash
cd backend && .venv/bin/python -m pytest -q
```

Output:

```text
656 passed, 1 skipped, 1 warning in 3.22s
```

The warning was the pre-existing Starlette/httpx deprecation warning.

### RED

The initial RED exposed a disposable-cluster fixture error because the macOS pytest temp directory exceeded PostgreSQL's Unix-socket path limit. I corrected only the fixture to use a short `/tmp/control-pg-*` path, then ran the required command again.

Exact command:

```bash
cd backend && .venv/bin/python -m pytest tests/test_control_plane_migration.py tests/test_cloud_deployment.py -q
```

Correct RED output:

```text
FFFFFF......FF                                                           [100%]
FAILED tests/test_control_plane_migration.py::test_first_control_migration_exists
FAILED tests/test_control_plane_migration.py::test_migration_is_idempotent_and_checksum_guarded
FAILED tests/test_control_plane_migration.py::test_migration_creates_complete_constrained_control_model
FAILED tests/test_control_plane_migration.py::test_partial_owner_and_active_grant_uniqueness
FAILED tests/test_control_plane_migration.py::test_runtime_roles_cannot_cross_grant_boundaries
FAILED tests/test_control_plane_migration.py::test_audit_is_append_only_and_retention_is_fixed_cutoff
FAILED tests/test_cloud_deployment.py::test_control_database_bootstrap_is_isolated_and_least_privilege
FAILED tests/test_cloud_deployment.py::test_remote_stage_calls_control_bootstrap_without_replacing_replica
8 failed, 6 passed in 0.77s
```

The required first failure was:

```text
AssertionError: missing migration: /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/agent-public-entry/backend/control_migrations/001_identity_security.sql
```

The other failures were the intended absent `app.control_plane.migrate` module/schema, bootstrap helper, and remote-stage helper invocation.

### GREEN

Exact focused command:

```bash
cd backend && .venv/bin/python -m pytest tests/test_control_plane_migration.py tests/test_cloud_deployment.py -q
```

Output:

```text
..............                                                           [100%]
14 passed in 0.74s
```

## Disposable PostgreSQL migration/grant integration evidence

The host has PostgreSQL 17.10 `initdb`, `pg_ctl`, and `psql`. The integration fixture starts a real isolated cluster under `/tmp`, creates dedicated test roles and a database owned by `platform_control_migrator`, migrates it through psycopg, and destroys the cluster afterward. No database behavior was mocked or weakened.

Fresh post-commit evidence command:

```bash
cd backend && .venv/bin/python -m pytest tests/test_control_plane_migration.py -vv
```

Output:

```text
collected 6 items
test_first_control_migration_exists PASSED
test_migration_is_idempotent_and_checksum_guarded PASSED
test_migration_creates_complete_constrained_control_model PASSED
test_partial_owner_and_active_grant_uniqueness PASSED
test_runtime_roles_cannot_cross_grant_boundaries PASSED
test_audit_is_append_only_and_retention_is_fixed_cutoff PASSED
6 passed in 0.70s
```

This proves:

- advisory-locked migration application, identical second application, one version/checksum row, and changed-checksum refusal before changed SQL execution;
- all 16 required control tables, the exact role enum, protected provider lookup fields, absence of raw provider-ID columns, and hash-only Web Session secret storage;
- partial uniqueness for the active platform owner and active observation grants, plus unique Stream event keys;
- revoked `PUBLIC` schema usage and distinct app/directory/Stream/audit/maintenance grant boundaries;
- no runtime table-grant path for audit update/delete;
- `SECURITY DEFINER` retention executable only by maintenance and rejection of cutoffs newer than database time minus 365 days.

Docker is not installed in this environment, so the Docker-orchestrated bootstrap helper could not be executed locally. Its deployment contract is covered by static policy tests and shell syntax; the underlying migration and grants were exercised against real PostgreSQL.

## Full backend and static verification

Full backend command:

```bash
cd backend && .venv/bin/python -m pytest -q
```

Output:

```text
664 passed, 1 skipped, 1 warning in 3.93s
```

The warning is the same pre-existing Starlette/httpx deprecation warning.

Commands:

```bash
bash -n deploy/cloud/bootstrap-control-db.sh deploy/cloud/remote-stage.sh
git diff --cached --check
```

Output: both exited 0 with no output.

Credential scan covered all seven task paths for private-key markers, AWS-style access keys, Slack-style tokens, literal PostgreSQL credentials, FDW, and dblink. It found only parameterized DSN format strings and disposable local trust URLs; no credential material or forbidden extension was found.

## Implementation summary

- `database.py` reads only a private migrator DSN secret file through the existing hardened secret reader.
- `migrate.py` loads strictly numbered SQL files, hashes raw bytes, obtains advisory transaction lock `0x41504331`, creates the migration ledger, skips identical versions, and rejects checksum changes.
- `001_identity_security.sql` creates the isolated R1 schema, constraints/indexes, role-specific grants, append-only audit function, and fixed 365-day retention function.
- `bootstrap-control-db.sh` idempotently creates six login roles with separately persisted generated passwords, both template0 databases, production/preview role DSNs at mode 0600, revokes broad database/schema access, and migrates each database using only the migrator secret.
- `remote-stage.sh` calls the reviewed helper after PostgreSQL health and before the existing replica role grant block. It does not restart FAE, publish a domain, or replace replica DSNs.

## Files changed

```text
backend/app/control_plane/database.py
backend/app/control_plane/migrate.py
backend/control_migrations/001_identity_security.sql
backend/tests/test_cloud_deployment.py
backend/tests/test_control_plane_migration.py
deploy/cloud/bootstrap-control-db.sh
deploy/cloud/remote-stage.sh
```

Commit stat: 7 files changed, 1003 insertions; bootstrap helper mode is 100755.

## Self-review

- Re-read the Task 2 brief and checked every named table, role, database, constraint, index, secret file, revocation, runner contract, and deployment prohibition.
- Reduced the directory worker's `internal_users` update grant to display/status/freshness columns so it cannot mutate role values.
- Confirmed audit append and retention are function-only for their dedicated runtime roles; neither receives direct audit INSERT/UPDATE/DELETE grants.
- Confirmed Stream ingest can insert only inbox rows and cannot read identities; the app cannot insert Stream inbox rows; the audit role cannot read audit rows.
- Confirmed the helper contains no replica role/DSN references and the only `remote-stage.sh` diff is its two-line helper invocation.
- Confirmed the migration image need not be expanded: the helper mounts the release's reviewed `backend/control_migrations` directory read-only into the one-shot migration container.

## Concerns

- Docker was unavailable locally, so the helper's Docker orchestration was not end-to-end executed. Real PostgreSQL migration/grant behavior, shell syntax, deployment policy, and the full backend suite are verified.
- The helper generates six role passwords and writes both production and preview DSN variants for each role so preview cannot connect to production control data. Both variants reuse the corresponding role's cluster password, while database/schema privileges remain isolated.

## Review fix

The Critical/Important review findings were resolved without changing the
already-applied `001_identity_security.sql` bytes. The fix adds an additive
`002_isolate_environment_roles.sql` migration, twelve distinct login roles and
password/DSN files (six canonical production names plus six `_preview` names),
and separate NOLOGIN owner roles for production and preview. The migration
runner now accepts only the exact owner roles `platform_control_owner` and
`platform_control_owner_preview`, enters the selected role with `SET LOCAL
ROLE`, and rejects every other identifier before opening a connection.

Bootstrap hardens every login role with `NOSUPERUSER NOCREATEDB NOCREATEROLE
NOREPLICATION NOBYPASSRLS`; migrators are `NOINHERIT` and runtime roles are
`INHERIT`. It revokes all cross-environment CONNECT grants, assigns each
database and its objects to its environment's NOLOGIN owner, grants owner
membership only for the bounded migration window, and installs an EXIT trap
before either membership grant. Success and failure paths revoke both
memberships. Before granting, it also removes any prior membership held by the
twelve dedicated login roles and any pre-existing member of either owner role;
a final catalog query requires both owner roles to have zero members. No
deployment or real credential access was performed.

### RED

Exact command:

```bash
cd backend && .venv/bin/python -m pytest tests/test_control_plane_migration.py tests/test_cloud_deployment.py -q
```

Output summary:

```text
4 failed, 7 passed, 6 errors in 0.75s
```

The intended failures were the missing
`002_isolate_environment_roles.sql`, the absent `owner_role` migration
interface/validation, missing preview login roles and owner roles, absence of
the bounded privilege window, and absence of `SET LOCAL ROLE`. Fixture-backed
tests errored at the same missing `owner_role` interface before any production
change was made.

### GREEN and disposable PostgreSQL

Focused command:

```bash
cd backend && .venv/bin/python -m pytest tests/test_control_plane_migration.py tests/test_cloud_deployment.py -q
```

Output:

```text
17 passed in 0.96s
```

Fresh real disposable PostgreSQL command:

```bash
cd backend && .venv/bin/python -m pytest tests/test_control_plane_migration.py -vv
```

Output:

```text
collected 8 items
8 passed in 0.83s
```

The PostgreSQL 17.10 integration starts one isolated cluster with both control
databases and proves: production/preview CONNECT denial in both directions;
all twelve distinct logins and both NOLOGIN owners have the required hardened
attributes; no login retains owner membership; databases, schemas, tables,
and functions are owned only by the environment owner; opposite-environment
roles have no schema/table grants; both migrations remain idempotent and
checksum guarded; stored migrators cannot `SET ROLE`, update/delete audit,
or execute either audit function; and only the matching maintenance login can
purge rows through the fixed-cutoff retention function.

### Full verification

Full backend command:

```bash
cd backend && .venv/bin/python -m pytest -q
```

Output:

```text
667 passed, 1 skipped, 1 warning in 4.23s
```

The warning is the pre-existing Starlette/httpx deprecation warning.

Commands:

```bash
bash -n deploy/cloud/bootstrap-control-db.sh deploy/cloud/remote-stage.sh
git diff --check
```

Both exited 0 with no output. Credential scanning across the changed control
runner, migrations, deployment scripts, and tests found no private-key
markers, AWS/GitHub/Slack token forms, literal PostgreSQL credentials, FDW,
or dblink. The two `rg` no-match scans exited 1 as expected.

### Concerns

- Docker is not installed in this environment, so the Docker-orchestrated
  helper and its EXIT trap could not be failure-injected end to end. The trap
  ordering and exact grant/revoke window are enforced by static deployment
  tests; the resulting ownership, role attributes, memberships, grants,
  cross-database denial, and audit behavior are exercised against real
  PostgreSQL.
- Existing installations must run the reviewed bootstrap helper to reassign
  legacy migrator-owned objects before the additive migration runs. The helper
  performs that reassignment idempotently; the immutable `001` checksum and
  checksum-mismatch behavior are preserved.

### Legacy credential rotation follow-up

The remaining Important upgrade-path finding is fixed without changing either
applied migration. The deployment now owns an exact version-2 credential state
machine under the existing private root:

```text
.control-database-credentials-v2.state
.control-database-credentials-v2/
```

The state file is root-owned mode 0600 on deployment. It contains exactly
`version=2`, `status=rotating`, and an origin while work is retryable, then
exactly `version=2` and `status=complete` after successful verification. The
private mode-0700 work directory preserves twelve candidate password/DSN pairs
across retries. Detection accepts only an empty fresh layout, the exact legacy
six-role shared-password layout, the exact twelve-role unmarked layout produced
by the preceding fix, a valid rotating record, or a valid completed record. It
also checks the cluster catalog signature, so marker absence alone cannot
classify an installation.

For every unmarked accepted layout, bootstrap generates twelve unique new
passwords, rotates exactly the six canonical production roles, assigns the six
preview passwords only to `_preview` roles, migrates both databases through the
bounded NOLOGIN-owner membership window, restores grants/revocations, and
requires zero owner-role memberships. It then atomically replaces every
password/DSN file, writes the completion marker, and emits exactly:

```text
CONTROL_DATABASE_CREDENTIALS_READY version=2
```

Remote staging requires that exact result and force-recreates every present
control-secret API/directory/Stream consumer before deployment acceptance.
Future Task-14 services are optional at this stage and are skipped when absent.
Audit credentials reload with the API; maintenance and migration are one-shot
consumers that read their secret file on each invocation. No FAE operation,
domain publication, replica credential/database change, or cutover was added.

#### RED

Initial exact command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_control_credential_upgrade.py -q
```

Output:

```text
FFFFFFFF                                                                 [100%]
8 failed in 0.73s
```

The six executable-helper failures reported the missing
`deploy/cloud/control-db-credential-state.sh`; the remaining failures proved
that bootstrap lacked ordered rotation/verification/completion and remote
staging lacked the exact acceptance marker plus consumer recreation contract.

Failure-window review then added a state-only interruption test. Exact command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_control_credential_upgrade.py::test_state_only_partial_prepare_resumes_without_a_second_rotation -q
```

Output:

```text
F                                                                        [100%]
1 failed in 0.05s
```

It failed because classification required the candidate directory to exist.
The transition now persists the rotating state before creating candidates;
state-only and partially generated candidate work both resume safely.

#### GREEN and disposable PostgreSQL

Focused helper/deployment command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_control_credential_upgrade.py backend/tests/test_cloud_deployment.py -q
```

Output:

```text
..................                                                       [100%]
18 passed in 5.98s
```

These tests execute the shell state helper against temporary private roots and
prove exact legacy detection, fresh-install classification, malformed/partial
layout refusal, state-only and candidate retry behavior, twelve unique values,
all six production values changing, disjoint preview values, exact role/DSN
content, mode 0600 atomic replacement, exact completion contents, no second
rotation after completion, ordered database verification, exact acceptance,
optional consumer handling, and no FAE/replica bootstrap impact.

Fresh real PostgreSQL command:

```bash
cd backend && .venv/bin/python -m pytest tests/test_control_plane_migration.py -vv
```

Output:

```text
collected 8 items
8 passed in 0.86s
```

This re-proves the unchanged advisory/checksum migration behavior, two-database
ownership/grants, cross-environment denial, NOLOGIN owners, zero retained owner
membership, append-only audit, and maintenance-only fixed-cutoff retention on a
disposable PostgreSQL 17.10 cluster.

#### Full verification

Full backend command:

```bash
cd backend && .venv/bin/python -m pytest -q
```

Output:

```text
676 passed, 1 skipped, 1 warning in 9.75s
```

The warning is the pre-existing Starlette/httpx deprecation warning.

Final static checks cover all three affected shell scripts with `bash -n`,
`git diff --check`, and scans for private-key material, common cloud/chat token
forms, literal PostgreSQL credentials, FDW, and dblink. No Docker deployment,
publication, cutover, FAE restart, or real credential access was performed.

#### Concerns

- Docker remains unavailable locally, so the orchestration path could not be
  failure-injected end to end. The state machine and retry transitions are
  executable shell tests; migration/ownership/grants are real PostgreSQL tests;
  Docker ordering and consumer recreation remain strict static policy tests.
- Task 14 still owns actual preview service/secret-volume packaging. This fix
  supplies its required service-name/recreation and exact acceptance contract;
  absent future services do not fail the current deployment.
