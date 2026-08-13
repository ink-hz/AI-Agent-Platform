# Task 3 Report: Versioned Provider Identity Cryptography and Control Repository

## Status

Complete on `feat/agent-public-entry` in the required isolated worktree. No deployment, Docker, production, Keychain, or real provider identifiers were used. Existing migrations 001 and 002 were not edited. The later review fix adds migration 003 for the cross-version identity-key policy.

## Commit

- `9e2ca85 feat(identity): protect provider identity mappings`

## TDD Evidence

### Initial RED

Exact command:

```text
cd backend && .venv/bin/python -m pytest tests/test_identity_crypto.py tests/test_control_plane_repository.py -q
```

Result: exit 2, with two expected collection errors:

```text
ModuleNotFoundError: No module named 'app.control_plane.crypto'
ERROR tests/test_identity_crypto.py
ERROR tests/test_control_plane_repository.py
2 errors in 0.11s
```

### First GREEN iteration

The first implementation run produced `17 passed, 5 failed`; failures were limited to Session SQL parameter construction, malformed-ciphertext collision classification, and rotation test setup. After minimal corrections, the exact focused command produced:

```text
22 passed in 0.90s
```

### Security-boundary RED/GREEN

Two additional tests were added during self-review for strict 256-bit provider keys and rejecting a rotated lookup not derived from its authenticated ciphertext.

Exact RED command:

```text
cd backend && .venv/bin/python -m pytest tests/test_identity_crypto.py::test_provider_codec_rejects_non_256_bit_purpose_bound_keys tests/test_control_plane_repository.py::test_identity_rotation_rejects_a_lookup_not_derived_from_ciphertext -q
```

Result:

```text
2 failed in 0.76s
```

After minimal validation was added, the same command returned:

```text
2 passed in 0.75s
```

## Final Verification

Focused Task 3 suite, including real disposable PostgreSQL integration:

```text
cd backend && .venv/bin/python -m pytest tests/test_identity_crypto.py tests/test_control_plane_repository.py -q
24 passed in 0.82s
```

Control-plane integration set:

```text
cd backend && .venv/bin/python -m pytest tests/test_control_plane_config.py tests/test_control_plane_migration.py tests/test_control_credential_upgrade.py tests/test_identity_crypto.py tests/test_control_plane_repository.py -q
105 passed in 7.16s
```

Full backend suite, run fresh immediately before staging and commit:

```text
cd backend && .venv/bin/python -m pytest -q
700 passed, 1 skipped, 1 warning in 10.96s
```

The one warning is the pre-existing Starlette `httpx` deprecation warning from `fastapi.testclient`.

Static and policy checks:

```text
cd backend && .venv/bin/python -m compileall -q app/control_plane tests/test_identity_crypto.py tests/test_control_plane_repository.py
git diff --check
rg -n "unionid|userid|staffId|mobile" app/control_plane
rg -n -i "appsecret|access[_-]?token|BEGIN (RSA|OPENSSH|PRIVATE) KEY|postgresql://[^[:space:]]+:[^[:space:]@]+@" app/control_plane tests/test_identity_crypto.py tests/test_control_plane_repository.py
rg -n --pcre2 "agent_platform(?!_control)|platform_read|cloud_replica|replica" app/control_plane/repository.py
git diff --cached --check
```

Results: all commands exited cleanly; all sensitive-provider/secret/replica scans had no matches. Ruff is not installed in the backend virtual environment, so no Ruff result is claimed.

## Crypto Evidence

- `IdentityKeyring.from_file` requires an absolute, regular, current-user-owned, exact mode-0600 file; rejects malformed JSON/base64, absent active versions, wrong purpose, and wrong caller-specified key length without including key material in errors.
- Encryption and lookup keyrings have separate purpose labels (`provider-encryption` and `provider-lookup-hmac`); `ProviderIdentityCodec` also requires every provider key to be exactly 32 bytes.
- Provider values use AES-256-GCM with a fresh `secrets.token_bytes(12)` nonce. AAD authenticates `dingtalk:{subject_kind}:v{encryption_version}`.
- Lookup values use HMAC-SHA-256 over `dingtalk:{subject_kind}:{normalized_provider_id}` with explicit versions. Lookup candidates cover the exact database-backed transition window, including each active version and its configured previous version.
- `hmac.compare_digest` is used for protected lookup and decrypted identity comparisons where application-level comparison applies.
- `ProtectedProviderId`, `IdentityKeyring`, `ProviderIdentityCodec`, `ControlRepository`, and `IssuedWebSession` reprs redact keys, ciphertext, HMACs, DSNs, Cookie tokens, and CSRF tokens. Boundary errors are generic and value-free.
- Rotation decrypts the authenticated provider value, re-derives the active lookup, validates ciphertext/lookup consistency, checks collision, row-locks the mapping, and preserves `internal_user_id` while keeping active-plus-previous resolution possible.

## Repository and PostgreSQL Evidence

- Tests use the disposable PostgreSQL cluster fixture, migrate both exact control databases, connect the repository through `platform_control_app`, and inspect persisted values through the test admin connection.
- `ControlRepository` accepts only DSNs whose database is exactly `agent_platform_control` or `agent_platform_control_preview`; repository SQL references only `platform_control` and contains no replica imports or access.
- All values use psycopg parameters. Lookup SQL pairs key version and HMAC using typed `unnest(integer[], bytea[])`; it does not cross-match independent arrays.
- Atomic create-or-resolve establishes/checks the database transition policy, locks every versioned lookup candidate in deterministic order, re-queries with `SELECT ... FOR UPDATE`, authenticates collision rows by decrypting them, and never resolves by display name.
- Login attempts persist SHA-256 state/challenge hashes, use database time for expiry, use transaction row locks for consumption, and can be consumed once only.
- Web Sessions return raw random Cookie/CSRF values only in the redacted `IssuedWebSession`, persist SHA-256 hashes only, calculate idle/absolute expiry with PostgreSQL `now()`, and rotate under `SELECT ... FOR UPDATE` while preserving the original absolute deadline.
- Revocation uses database time and updates only unrevoked Sessions. Observation scopes select exact active `agent_id` values. Existing PostgreSQL constraints enforce at most one active owner and unique active grants.

## Files

- `backend/app/control_plane/crypto.py`
- `backend/app/control_plane/repository.py`
- `backend/control_migrations/003_identity_key_policy.sql`
- `backend/app/control_plane/models.py` (raw Session token repr redaction)
- `backend/tests/test_control_plane_migration.py`
- `backend/tests/test_identity_crypto.py`
- `backend/tests/test_control_plane_repository.py`

## Self-review

- Confirmed applied migrations 001 and 002 are byte-for-byte unchanged; additive migration 003 contains the new policy schema and grants.
- Confirmed all SQL data values are parameterized and all mutation sequences run within psycopg connection transactions.
- Confirmed create/rotation paths check the shared database policy and lock all derived versioned lookups, preventing old/new key-version races from creating distinct users.
- Confirmed lookup version and HMAC are paired exactly in SQL.
- Confirmed no name-based identity lookup API exists.
- Confirmed no raw provider ID, Cookie, CSRF value, key material, ciphertext, DSN, or production identifier appears in source logging/error paths or database plaintext columns.

## Concerns

- No known Task 3 correctness or security blocker.
- The environment does not provide Ruff, so verification used compileall, the full backend suite, diff checks, targeted scans, and manual source review.

## Review fix

### Status and scope

Fixed the Task 3 Critical and valid Important review findings without changing
the approved ownership invariant: the database permits at most one active
platform owner and permits zero. The state-token entropy finding remains out of
scope for Task 6.

### TDD RED evidence

The first review-fix focused run was:

```text
cd backend && .venv/bin/python -m pytest \
  tests/test_identity_crypto.py tests/test_control_plane_repository.py -q
```

It exited 1 with `18 failed, 7 passed, 10 errors`. The new explicit transition
metadata was rejected by the old keyring parser, proving the rollout contract
tests were exercising missing behavior.

After the keyring parser and codec contract were implemented, the isolated
repository RED run exited 1 with four expected failures: both tampered supplied
lookup paths reached the database, and Session rotation still required
`absolute_seconds`. An additional deterministic-lock RED test failed because
only one advisory lock was acquired instead of every transition candidate.

The database-policy RED sequence was:

```text
cd backend && .venv/bin/python -m pytest \
  tests/test_control_plane_migration.py::test_first_control_migration_exists -q
```

It failed because `003_identity_key_policy.sql` did not exist. After migration
003 was green, the first repository policy test failed during collection because
`IdentityKeyPolicyError` and the transactional database-policy guard did not yet
exist.

### Transition-key and concurrency contract

- HMAC keyrings now declare `transition_versions`. The codec requires the exact
  complete HMAC key set to be a strictly ascending contiguous window of one to
  three positive versions, containing the active version and its configured
  previous version. Missing, partial, duplicate, descending, non-contiguous, or
  oversized layouts fail closed.
- During an `N` to `N+1` rollout, all participants use the same database-backed
  union `(N-1, N, N+1)`, omitting `N-1` when absent. Thus both nodes preserve
  active-plus-previous resolution while deriving and querying identical lookup
  candidates.
- Additive migration `003_identity_key_policy.sql` creates one environment-local
  DingTalk policy row. Exact SQL checks enforce a one-to-three-element positive,
  unique, strictly ascending, contiguous integer array.
- The environment app role has only `SELECT` and `INSERT` on the policy table.
  It can establish an absent policy but cannot change or delete one. The matching
  maintenance role alone can execute the `SECURITY DEFINER` staged-rollout
  setter; PUBLIC, migrator, directory, Stream, audit, the app role, and opposite
  environment roles cannot execute it.
- Resolve, create-or-resolve, and rotation transactions take the shared policy
  advisory lock, insert-if-absent, read and compare the exact configured window,
  and fail with a generic policy mismatch before identity queries or mutations.
- Identity advisory-lock keys are SHA-256 domain-separated over the version and
  full HMAC. Every candidate lock is acquired in deterministic `(version, HMAC)`
  order, followed by a re-query under the locks.
- Real disposable PostgreSQL tests cover adjacent active v2/v3 nodes sharing
  `(1,2,3)` and mismatched `(1,2)`/`(2,3)` nodes. The matching race returns one
  user. In the mismatch race one policy wins, the other operation fails closed,
  and exactly one user/mapping remains.

### Other security fixes

- Repository boundaries authenticate-decrypt each `ProtectedProviderId`,
  re-derive the full configured candidates, and reject a supplied lookup HMAC or
  version not present in those candidates before opening a database connection.
- `ProviderIdentityCodec` rejects any byte-for-byte key reuse across any
  encryption and HMAC keyring versions.
- `rotate_web_session(cookie_token, idle_seconds)` no longer accepts
  `absolute_seconds`. Rotation copies the locked Session's exact
  `absolute_expires_at`; tests cover exact equality with smaller and larger
  original absolute lifetimes.
- Tests and the implementation plan now describe the ownership constraint as
  "at most one" and explicitly verify that zero active owners is permitted.

### Final verification

Focused crypto and repository suite, including both real PostgreSQL races:

```text
cd backend && .venv/bin/python -m pytest \
  tests/test_identity_crypto.py tests/test_control_plane_repository.py -q
48 passed in 1.23s
```

Migration integration:

```text
cd backend && .venv/bin/python -m pytest \
  tests/test_control_plane_migration.py -q
9 passed in 0.87s
```

Control-plane integration:

```text
cd backend && .venv/bin/python -m pytest \
  tests/test_control_plane_config.py tests/test_control_plane_migration.py \
  tests/test_control_credential_upgrade.py tests/test_identity_crypto.py \
  tests/test_control_plane_repository.py -q
132 passed in 7.65s
```

Full backend:

```text
cd backend && .venv/bin/python -m pytest -q
725 passed, 1 skipped, 1 warning in 10.71s
```

The warning is the same pre-existing Starlette `httpx` deprecation warning.

Static and policy verification:

```text
cd backend && .venv/bin/python -m compileall -q app/control_plane \
  tests/test_identity_crypto.py tests/test_control_plane_repository.py \
  tests/test_control_plane_migration.py
git diff --check
! rg -n "unionid|userid|staffId|mobile" \
  app/control_plane control_migrations/003_identity_key_policy.sql
! rg -n -i "appsecret|access[_-]?token|BEGIN (RSA|OPENSSH|PRIVATE) KEY|postgresql://[^[:space:]]+:[^[:space:]@]+@" \
  app/control_plane control_migrations/003_identity_key_policy.sql
! rg -n --pcre2 "agent_platform(?!_control)|platform_read|cloud_replica|replica" \
  app/control_plane/repository.py
! rg -n "exactly one active owner|exactly-one-owner|exactly one owner" \
  ../docs/superpowers/plans/2026-08-13-dingtalk-identity-release-1.md \
  tests/test_control_plane_repository.py ../.superpowers/sdd/task-3-report.md
test SHA-256 of migrations 001/002 equals their bytes at HEAD
```

All commands exited cleanly with no matches for provider values, secrets,
replica state, or forbidden owner wording. Migrations 001 and 002 are byte-for-
byte unchanged. A broader scan of migration tests finds only the two pre-existing
synthetic disposable-PostgreSQL URL constructors; no secret literal was added.

### Independent review

An independent reviewer first identified the locally-valid mismatched-window
gap and the versionless advisory-lock identity. After the database-backed policy
and versioned lock fixes, the reviewer reported no Critical or Important
findings in migration 003, privileges and environment isolation, maintenance
setter, repository checks, or matching/mismatched concurrency.

## Second review fix: pre-connection rotation validation and migration 004

### Status and scope

Fixed the two remaining Important Task 3 findings. No deployment, real secrets,
provider production data, or unrelated Task 6 Minor finding was touched.
Migrations 001, 002, and 003 remain byte-for-byte unchanged.

### TDD RED evidence

The strict rotation regression supplied a `ControlRepository` connection
factory that raises `AssertionError` whenever called. Its first run was:

```text
cd backend && .venv/bin/python -m pytest \
  tests/test_control_plane_repository.py::test_identity_rotation_rejects_previous_lookup_before_database_connection -q
1 failed in 0.10s
```

The failure stack reached `ControlRepository._connection`, proving a tampered
`previous.lookup_key_version/lookup_hmac` crossed the database-connection
boundary.

Before migration 004 existed, the real-PostgreSQL RED command was:

```text
cd backend && .venv/bin/python -m pytest \
  tests/test_control_plane_migration.py::test_first_control_migration_exists \
  tests/test_control_plane_migration.py::test_app_role_insert_rejects_invalid_identity_key_version_arrays \
  tests/test_control_plane_migration.py::test_identity_key_policy_hardening_fails_closed_on_poisoned_row_then_applies -q
3 failed in 0.76s
```

The expected failures were the missing 004 file, direct app-role acceptance of
`ARRAY[NULL]`, and migration success despite a poisoned pre-004 row.

### Implementation and database safety

- `rotate_provider_identity` now calls `_lookup_candidates(previous)` before
  opening a database connection. This authenticates the previous ciphertext,
  re-derives the configured lookup candidates, and rejects a supplied previous
  version/HMAC that does not match any derived pair.
- Additive migration `004_reject_null_identity_key_versions.sql` first locks the
  policy table against concurrent app inserts, then scans every array element.
  Any NULL or non-positive element raises the generic PostgreSQL check-violation
  message `provider identity key policy data invalid`. The transaction abort
  leaves the poisoned row intact and does not record migration version 004.
- On clean data, migration 004 adds a named `NOT VALID` CHECK that explicitly
  requires `array_position(..., NULL) IS NULL` and every element to be positive,
  then validates it. The numbered migration runner makes clean reruns
  idempotent.
- Migration 004 contains no ownership, grant, revoke, role, or environment-role
  statements. Existing tests confirm the policy table remains owned by the
  selected environment owner and the app/maintenance privilege split remains
  unchanged.
- Direct app-role tests reject NULL in positions 1, 2, and 3, zero and negative
  values in each applicable position, and accept valid one-, two-, and
  three-version windows. The poisoned-row test verifies failure without repair
  or deletion, followed by a clean successful and idempotent application.

### GREEN and final verification

Strict rotation GREEN, including the existing tampered-rotated and real
PostgreSQL preservation cases:

```text
3 passed in 0.84s
```

The same migration command used for RED:

```text
3 passed in 0.81s
```

Focused crypto/repository, migration integration, control-plane integration,
and full backend verification:

```text
49 passed in 1.28s
11 passed in 1.02s
135 passed in 7.64s
728 passed, 1 skipped, 1 warning in 10.91s
```

The warning is the same pre-existing Starlette `httpx` deprecation warning.
`compileall`, `git diff --check`, the replica-boundary scan, production
provider/secret scans, changed-addition scans, and migration-004 privilege-drift
scan exited cleanly. Ruff remains unavailable in the backend virtual
environment, so no Ruff result is claimed. Broader test scans only found the
pre-existing schema-column assertions and synthetic disposable-PostgreSQL URL
constructors.

The final SHA-256 values, equal to the recorded pre-change baseline, are:

```text
309cf6aebdb37d984823faebb859e58f44d387cda4c2fb4bdf61af06868541cd  001_identity_security.sql
837bb27aa7ee09ff52e424c978c2362cad4e40a25d1c02a4ced0183c61dcbd2f  002_isolate_environment_roles.sql
4bef30a941e95f0e7508b5ad07c27fd1cf2673effad52738aac8b1fcf6c217f4  003_identity_key_policy.sql
```

### Concerns

- No known Task 3 correctness, security, migration, ownership, or privilege
  blocker.
- A poisoned deployment intentionally requires maintenance-incident handling;
  migration 004 does not silently delete or infer replacement policy data.
