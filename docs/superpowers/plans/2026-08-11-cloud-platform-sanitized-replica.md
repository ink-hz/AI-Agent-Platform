# Cloud Platform Sanitized Replica Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the complete read-only AI Agent Platform on the existing Alibaba Cloud host from an irreversible, encrypted, one-way sanitized replica while leaving FAE and all local source systems unchanged.

**Architecture:** A local exporter reads exact allowlisted `platform_read` fields, sanitizes content before persistence, replaces provider identities with HMAC IDs, and signs canonical batches. A restricted SSH forced command streams each batch to a transactional cloud importer; the cloud stores encrypted replica payloads in a dedicated PostgreSQL database and the existing Platform UI reads them through cloud-mode repositories on `127.0.0.1:8080`.

**Tech Stack:** Python 3.11, FastAPI, psycopg 3, Pydantic, cryptography 50, PostgreSQL 17, React/Vite, Docker Compose, Bash, OpenSSH, macOS launchd.

## Global Constraints

- Local Flywheel and the local Mac remain the only source of truth.
- Normal synchronization is every five minutes and only the local Mac initiates network connections.
- No raw provider IDs, customer/candidate/project identities, credentials, paths, attachment bytes, object coordinates, system prompts, raw tool payloads or full traces reach the cloud.
- Unresolved sensitive content is omitted as `内容因敏感性未同步`; no classifier may override a deterministic sensitive match.
- Employee display names are allowed; their provider IDs are replaced with local-keyed HMAC IDs.
- Cloud Session detail is retained for one year; irreversible non-personal aggregates may remain.
- Cloud display payloads are encrypted with AES-256-GCM and a mode-0600 key outside Git and Compose.
- The first release binds only `127.0.0.1:8080`, adds no public port, does not edit Nginx and is accessed through an SSH tunnel.
- Cloud mode is read-only: Review mutation, replay, Agent control, attachment tickets/downloads, remote pull-sync and source database fallback remain unavailable.
- Existing FAE container, image, start time, health payload, Nginx configuration and public listeners must remain unchanged.
- Deployment and synchronization are non-interactive and never invoke password, Keychain or credential UI.

---

## File map

New backend package `backend/app/cloud_replica/` owns only the cloud replica boundary:

- `models.py`: sanitized records, batch headers and import results.
- `sanitize.py`: deterministic redaction, private dictionary matching and fail-closed post-scan.
- `crypto.py`: HMAC identifiers, AES-GCM field encryption and Ed25519 signatures.
- `protocol.py`: canonical batch serialization and envelope validation.
- `source.py`: exact read-only Flywheel queries and source watermarks.
- `exporter.py`: source rows to sanitized signed records.
- `store.py`: encrypted PostgreSQL replica storage and transactional import.
- `repository.py`: replica-backed Platform read interfaces.
- `cli.py`: `export`, `import`, `retention`, `backup` and `health` commands.

Deployment files live under `deploy/cloud/` and cannot modify existing FAE or
Nginx assets. Local scheduling uses a separate launchd template and entrypoint.

---

### Task 1: Cloud-mode configuration and fail-closed application gates

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Create: `backend/app/cloud_replica/__init__.py`
- Test: `backend/tests/test_cloud_config.py`
- Test: `backend/tests/test_cloud_mode.py`

**Interfaces:**
- Consumes: current `Config`, `create_app`, unavailable Review and disabled attachment patterns.
- Produces: `Config.deployment_mode`, `Config.replica_database_url_file`, `Config.replica_encryption_key_file`, `is_cloud_mode(config)`, and a cloud-mode app that starts no local/remote pollers or mutating services.

- [ ] **Step 1: Write failing configuration tests**

Add tests that clear all Platform variables, assert the default mode is
`local`, then set the exact cloud configuration:

```python
monkeypatch.setenv("PLATFORM_DEPLOYMENT_MODE", "cloud-replica")
monkeypatch.setenv("PLATFORM_HOST", "127.0.0.1")
monkeypatch.setenv("PLATFORM_PORT", "8080")
monkeypatch.setenv("PLATFORM_REPLICA_DATABASE_URL_FILE", str(db_secret))
monkeypatch.setenv("PLATFORM_REPLICA_ENCRYPTION_KEY_FILE", str(key_secret))
config = load_config()
assert config.deployment_mode == "cloud-replica"
assert config.host == "127.0.0.1"
assert config.port == 8080
```

Reject cloud mode when the host is not loopback, either secret file is not an
absolute regular mode-0600 file, attachments are enabled, or Review is enabled.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_cloud_config.py tests/test_cloud_mode.py -q
```

Expected: collection or assertions fail because cloud fields and mode gates do
not exist.

- [ ] **Step 3: Add exact cloud configuration**

Pin `cryptography==50.0.0`. Add these `Config` fields:

```python
deployment_mode: Literal["local", "cloud-replica"]
replica_database_url_file: str
replica_encryption_key_file: str
replica_signing_public_key_file: str
replica_stale_seconds: int
```

Implement `_validate_cloud_config(config)` with exact mode, loopback, file mode,
attachment/review-disabled and `replica_stale_seconds == 900` checks. Cloud
mode must never read a local Flywheel or Review DSN.

- [ ] **Step 4: Gate application startup**

Add:

```python
def is_cloud_mode(config: Config) -> bool:
    return config.deployment_mode == "cloud-replica"
```

In `create_app`, cloud mode must skip `ClusterMonitor` polling,
`RemoteHealthMonitor` polling, local Flywheel repository construction,
operations schedulers, Review construction and attachment construction. For
this task inject unavailable services; Task 6 replaces read services with
replica-backed implementations. Assert `/api/health` remains `200` and Review,
attachment ticket and attachment content routes are `404` or `503`.

- [ ] **Step 5: Run tests and commit**

Run the two focused tests and the existing `test_main.py`, `test_config.py` and
`test_attachment_api.py`; expect all pass. Commit:

```bash
git add backend/requirements.txt backend/app/config.py backend/app/main.py \
  backend/app/cloud_replica backend/tests/test_cloud_config.py \
  backend/tests/test_cloud_mode.py
git commit -m "feat(platform): add fail-closed cloud replica mode"
```

---

### Task 2: Deterministic irreversible sanitizer

**Files:**
- Create: `backend/app/cloud_replica/models.py`
- Create: `backend/app/cloud_replica/sanitize.py`
- Test: `backend/tests/test_cloud_sanitizer.py`

**Interfaces:**
- Consumes: raw allowlisted Session/Turn dictionaries and a private UTF-8 sensitive dictionary file.
- Produces: `RawAttachment`, `RawTurn`, `RawSession`, `SanitizationPolicy`, `SanitizedText`, `SanitizedSessionRecord`, `sanitize_text(text: str, policy: SanitizationPolicy, scope: str) -> SanitizedText`, and `sanitize_session(raw: RawSession, policy: SanitizationPolicy) -> SanitizedSessionRecord`.

- [ ] **Step 1: Write parameterized failing canary tests**

Use literal synthetic values for phone, email, identity number, address, bearer
token, AWS-like access key, macOS/Linux paths, signed URL, raw provider ID,
customer alias, candidate alias, project code and unreleased model. For every
case assert the raw canary is absent and the expected placeholder is present.

Also assert:

```python
result = sanitize_text("客户甲询问项目鹰，客户甲要求附件报价", policy, "s1")
assert result.text == "[客户1]询问[项目1]，[客户1]要求[附件1]报价"
assert result.safe is True
```

Add a post-detector canary that cannot be classified and assert the output is
exactly `内容因敏感性未同步` with `safe=False`.

- [ ] **Step 2: Verify RED**

Run `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_cloud_sanitizer.py -q`.
Expected: import failure for `app.cloud_replica.sanitize`.

- [ ] **Step 3: Implement sanitizer units**

Implement immutable policy data, ordered regex rules, URL/Markdown
normalization, longest-alias-first dictionary replacement and per-Session
placeholder maps. `SanitizationPolicy.from_private_file(path)` must require an
absolute, same-user, regular non-symlink mode-0600 file and accept YAML groups
`customers`, `candidates`, `projects`, `products`, `addresses`.

The post-detector returns a positive match for unresolved credential forms,
provider IDs, paths, query strings and dictionary aliases. It may only turn a
safe result into an omitted result.

- [ ] **Step 4: Sanitize complete Session records**

Allow only exact Session, Turn and attachment fields. Generate HMAC-ready raw
identity inputs but do not retain them in `SanitizedSessionRecord`. Replace
attachment display names with `附件 N`, discard `sources`, evidence references,
Review text, feedback comments, trace details and arbitrary `details` keys.

- [ ] **Step 5: Run tests and commit**

Run the sanitizer tests plus `tests/test_observability_api.py`; expect all
pass. Commit:

```bash
git add backend/app/cloud_replica/models.py \
  backend/app/cloud_replica/sanitize.py \
  backend/tests/test_cloud_sanitizer.py
git commit -m "feat(platform): sanitize cloud replica sessions"
```

---

### Task 3: Cryptographic identity, payload and batch protocol

**Files:**
- Create: `backend/app/cloud_replica/crypto.py`
- Create: `backend/app/cloud_replica/protocol.py`
- Test: `backend/tests/test_cloud_crypto.py`
- Test: `backend/tests/test_cloud_protocol.py`

**Interfaces:**
- Consumes: 32-byte HMAC/AES keys, Ed25519 private/public key files and sanitized record dictionaries.
- Produces: `stable_id(scope: str, value: str, key: bytes) -> str`, `FieldCipher.encrypt/decrypt`, `BatchSigner`, `BatchVerifier`, `BatchState`, `BatchLimits`, `BatchHeader`, `SignedBatch`, `encode_batch(records: tuple[dict, ...], state: BatchState, signer: BatchSigner) -> bytes`, and `decode_and_verify_batch(stream: BinaryIO, verifier: BatchVerifier, limits: BatchLimits) -> SignedBatch`.

- [ ] **Step 1: Write failing crypto tests**

Assert stable IDs are deterministic, scope-separated and never contain input.
Assert AES-GCM round trips only with matching associated data
`schema_version:record_kind:record_key`; wrong key, nonce or associated data
must fail with `ReplicaCryptoError` and no plaintext in the error.

Assert mode-0600 regular file checks reject symlink, directory, permissive,
empty and wrong-size key files.

- [ ] **Step 2: Write failing protocol tests**

Build a two-record batch and assert deterministic canonical bytes, SHA-256 and
valid signature. Mutate content, signature, sequence, predecessor, timestamps,
schema version, record count and byte count; each must raise one stable class
without echoing data. Assert limits of 10 MiB per batch, 1 MiB per record and
10,000 records.

- [ ] **Step 3: Implement cryptographic primitives**

Use `hmac.digest(key, f"{scope}\\0{value}".encode(), "sha256")`, URL-safe
lowercase stable IDs, `AESGCM` with a
fresh 96-bit nonce and Ed25519 from `cryptography`. Exceptions are
`ReplicaCryptoError("identity_failed"|"decrypt_failed"|"signature_failed")`.

- [ ] **Step 4: Implement canonical signed batches**

Use UTF-8 JSON Lines with sorted keys, compact separators and RFC3339 UTC
timestamps. The first line is a header, following lines are records, and the
last line contains only digest and signature. Verification finishes before any
record reaches storage.

- [ ] **Step 5: Run tests and commit**

Run both focused modules; expect all pass. Commit:

```bash
git add backend/app/cloud_replica/crypto.py \
  backend/app/cloud_replica/protocol.py \
  backend/tests/test_cloud_crypto.py backend/tests/test_cloud_protocol.py
git commit -m "feat(platform): sign and encrypt replica data"
```

---

### Task 4: Exact local source reader and exporter

**Files:**
- Create: `backend/app/cloud_replica/source.py`
- Create: `backend/app/cloud_replica/exporter.py`
- Modify: `backend/app/cloud_replica/cli.py` or create it if absent
- Test: `backend/tests/test_cloud_source.py`
- Test: `backend/tests/test_cloud_exporter.py`

**Interfaces:**
- Consumes: current managed analyst DSN file, sanitizer policy, HMAC key, signing key, previous export state and exact `platform_read` fields.
- Produces: `ReplicaSource.fetch_sessions(after: datetime, through: datetime, limit: int) -> tuple[RawSession, ...]`, `ReplicaExporter.export_batch(after: datetime, through: datetime, limit: int) -> ExportResult`, and `python -m app.cloud_replica.cli export`.

- [ ] **Step 1: Write source-query tests**

Assert source connections use `default_transaction_read_only=on`, repeatable
read, a 10-second statement timeout and exact explicit columns. The query must
exclude rows older than one year, order by `(last_active_at, session_key)`, and
use the same upper watermark for Sessions, Turns and attachments.

No SQL may use `select *`, raw identity views, provider tables, attachment
storage tables or Review writer schemas.

- [ ] **Step 2: Write exporter safety tests**

Use fake source rows containing every canary. Assert the encoded batch has none
of them, uses HMAC IDs for session, turn and user identity, labels attachments,
and contains no raw `native_id`, `user_identity`, `details`, `sources`,
filename or attachment UUID.

Assert an exporter failure leaves the prior mode-0600 state file and queued
batch unchanged. Exact replay from the same watermark produces identical
logical records but a new creation time and signature.

- [ ] **Step 3: Implement exact source reader**

Read only these relations: `platform_read.sessions`, `platform_read.turns`,
`platform_read.attachments`, `platform_read.traces` and
`platform_read.trace_steps`. Export trace/runtime aggregates only; never export
step summaries, metadata or errors. Read the DSN from a private file inside the
process and never put it in argv or logs.

- [ ] **Step 4: Implement exporter and atomic state**

State contains only source instance ID, next sequence, previous digest and
upper watermark. Write batch and state through mode-0600 sibling temporary
files and `os.replace`. A batch is queued before state advances. Emit aggregate
JSON only: sequence, safe record counts, watermarks and digest.

- [ ] **Step 5: Run tests and commit**

Run source/exporter/sanitizer/protocol tests; expect all pass. Commit:

```bash
git add backend/app/cloud_replica/source.py \
  backend/app/cloud_replica/exporter.py backend/app/cloud_replica/cli.py \
  backend/tests/test_cloud_source.py backend/tests/test_cloud_exporter.py
git commit -m "feat(platform): export sanitized replica batches"
```

---

### Task 5: Transactional encrypted cloud store and importer

**Files:**
- Create: `backend/migrations/008_cloud_replica.sql`
- Create: `backend/app/cloud_replica/store.py`
- Modify: `backend/app/cloud_replica/cli.py`
- Test: `backend/tests/test_cloud_replica_migration.py`
- Test: `backend/tests/test_cloud_store.py`
- Test: `backend/tests/test_cloud_importer.py`

**Interfaces:**
- Consumes: verified `SignedBatch`, replica PostgreSQL DSN, `FieldCipher` and the last committed sequence/digest.
- Produces: `ReplicaStore.migrate()`, `ReplicaStore.import_batch()`, `ReplicaStore.expire()`, `ReplicaImportResult`, and CLI `import`/`retention`.

- [ ] **Step 1: Write failing migration contract tests**

Require schema `platform_replica` with tables `generations`, `agents`,
`sessions`, `runtime_snapshots`, `aggregate_snapshots`, `import_audit` and
`retention_audit`. Display payload columns are `bytea`; no raw identity,
question, answer, filename or credential column may exist. The import role can
insert/update, the read role can select, and neither can read another
application schema.

- [ ] **Step 2: Write transactional importer tests**

With a fake connection, assert signature verification precedes `begin`, all
upserts and watermark commit occur once, exact replay is a no-op, and sequence
gap, predecessor mismatch, same ID/different hash or injected upsert failure
rolls back everything. Errors contain only stable classes.

- [ ] **Step 3: Implement migration and encrypted store**

Store safe indexing columns such as HMAC key, agent ID and event times in
plaintext. Encrypt complete sanitized Pydantic JSON with AES-GCM and associated
data per row. Store plaintext SHA-256 beside ciphertext for comparison without
decryption. `import_audit` contains counts/digests only.

- [ ] **Step 4: Implement expiry**

Under one transaction, delete Session payloads older than one calendar year,
delete orphan Agent identity links, and append aggregate-only retention audit.
Dry-run returns counts without mutation.

- [ ] **Step 5: Run tests and commit**

Run all Task 5 tests and protocol tests; expect pass. Commit:

```bash
git add backend/migrations/008_cloud_replica.sql \
  backend/app/cloud_replica/store.py backend/app/cloud_replica/cli.py \
  backend/tests/test_cloud_replica_migration.py \
  backend/tests/test_cloud_store.py backend/tests/test_cloud_importer.py
git commit -m "feat(platform): import encrypted replica batches"
```

---

### Task 6: Replica-backed read services and cloud UI behavior

**Files:**
- Create: `backend/app/cloud_replica/repository.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/observability/models.py`
- Modify: `backend/app/observability/routes.py`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/components/AttachmentList.tsx`
- Modify: `webui/src/router.ts`
- Test: `backend/tests/test_cloud_repository.py`
- Test: `backend/tests/test_cloud_api.py`
- Test: `webui/src/cloudMode.test.tsx`

**Interfaces:**
- Consumes: encrypted replica rows and existing Platform response models.
- Produces: `ReplicaObservabilityRepository`, `ReplicaFleetRepository`, deployment/freshness API state and a read-only cloud UI.

- [ ] **Step 1: Write failing repository/API tests**

Assert list/search/pagination, Agent detail, Session order, sanitized question and
answer, safe attachment metadata and stale state match existing response
shapes. Wrong encryption key and corrupt ciphertext return `503` without
partial data. Trace detail is `unavailable` and contains aggregate runtime only.

Assert cloud routes for Review mutation, replay, attachment ticket/content and
control actions are absent or forbidden.

- [ ] **Step 2: Implement replica repositories**

Decrypt only rows needed for the request. Enforce `limit <= 100`, stable newest
ordering and in-memory text filtering over sanitized content. Construct
existing Pydantic models so ordinary UI components do not gain a second data
contract. Fleet usage derives from committed encrypted Sessions and safe
aggregate snapshots.

- [ ] **Step 3: Wire cloud mode into `create_app`**

Build replica repositories only from the mode-0600 replica DSN/key files.
Cloud startup fails if either secret or the replica schema is unavailable. An
empty schema starts successfully with `freshness=unavailable`, which permits a
first signed import without weakening any read route. Add `GET /api/deployment`
returning only:

```json
{"mode":"cloud-replica","read_only":true,"auth":"ssh-tunnel","freshness":"current","last_success_at":"2026-08-11T04:00:00Z"}
```

- [ ] **Step 4: Add cloud UI safeguards**

Show a compact `云端脱敏只读副本` banner with freshness and last sync. Hide
Review navigation and attachment open/download controls when `read_only=true`;
keep attachment type/state labels. The banner must visibly change for stale and
unavailable states.

- [ ] **Step 5: Run tests and commit**

Run focused backend tests, `npm test`, and `npm run build`; expect pass. Commit:

```bash
git add backend/app/cloud_replica/repository.py backend/app/main.py \
  backend/app/observability webui/src backend/tests/test_cloud_repository.py \
  backend/tests/test_cloud_api.py webui/src/cloudMode.test.tsx
git commit -m "feat(platform): serve cloud replica read views"
```

---

### Task 7: Restricted transport and five-minute local scheduler

**Files:**
- Create: `deploy/cloud/push-replica.sh`
- Create: `deploy/cloud/forced-import.sh`
- Create: `deploy/cloud/bootstrap-keys.sh`
- Create: `deploy/com.orbbec.ai-agent-platform-cloud-sync.plist.template`
- Create: `deploy/install-cloud-sync-launchagent.sh`
- Test: `backend/tests/test_cloud_transport.py`
- Test: `backend/tests/test_cloud_launchagent.py`

**Interfaces:**
- Consumes: queued signed batch, restricted SSH key and fixed host.
- Produces: non-interactive push, forced importer contract and a 300-second launchd schedule.

- [ ] **Step 1: Write static and command tests**

Require SSH options `BatchMode=yes`, `IdentitiesOnly=yes`, `ConnectTimeout=8`,
fixed key/host from private config, stdin batch transport and no payload argv.
Require `authorized_keys` options `restrict`, `command=`, `no-pty`,
`no-agent-forwarding`, `no-port-forwarding` and `no-X11-forwarding`.

The LaunchAgent must use `StartInterval=300`, absolute paths, separate logs and
no secrets/environment credential values.

- [ ] **Step 2: Implement push acknowledgement**

The forced importer prints one line:

```text
REPLICA_IMPORT_OK sequence=$SEQUENCE digest=$BATCH_DIGEST replay=$REPLAYED
```

Only after an exact acknowledgement does the local push remove its queued
batch. Network/remote/parse failures retain the queue and emit one stable local
error.

- [ ] **Step 3: Implement installer**

The installer validates private directories/files, renders the template,
`plutil -lint`s it, atomically installs it and bootstraps only the cloud sync
LaunchAgent. It never restarts Platform, MetaBot or FAE.

`bootstrap-keys.sh` creates, validates and never rotates an existing HMAC key,
Ed25519 batch-signing key, restricted SSH key and X25519 backup-recovery key in
the local mode-0700 private root. It emits only public keys and fingerprints;
private values never reach stdout, argv or Git. The remote AES-GCM storage key
is generated independently on the cloud as a root-owned mode-0600 file.

- [ ] **Step 4: Run tests and commit**

Run both tests plus `bash -n` on both scripts; expect pass. Commit:

```bash
git add deploy/cloud/push-replica.sh deploy/cloud/forced-import.sh \
  deploy/cloud/bootstrap-keys.sh \
  deploy/com.orbbec.ai-agent-platform-cloud-sync.plist.template \
  deploy/install-cloud-sync-launchagent.sh \
  backend/tests/test_cloud_transport.py backend/tests/test_cloud_launchagent.py
git commit -m "ops(platform): add restricted replica transport"
```

---

### Task 8: Immutable cloud container deployment and rollback

**Files:**
- Create: `deploy/cloud/Dockerfile`
- Create: `deploy/cloud/compose.yaml`
- Create: `deploy/cloud/registry.yaml`
- Create: `deploy/cloud/deploy.sh`
- Create: `deploy/cloud/remote-stage.sh`
- Test: `backend/tests/test_cloud_deployment.py`

**Interfaces:**
- Consumes: clean reviewed source commit, cloud baseline facts and private config files.
- Produces: immutable release under `/opt/orbbec-agent-platform/releases/$RELEASE_SHA`, dedicated PostgreSQL, loopback Platform `127.0.0.1:8080`, exact rollback and stable deployment result. `RELEASE_SHA` is assigned from `git rev-parse HEAD` after clean-source validation.

- [ ] **Step 1: Write deployment policy tests**

Assert Compose has only `platform-api` and `platform-postgres`, database has no
published port, API publishes exactly `127.0.0.1:8080:8080`, image runs nonroot,
root filesystem is read-only, capabilities are dropped, secrets are read-only
files and FAE/Langfuse/Nginx names are absent.

Assert deploy preflight records FAE container ID/image/start time/health digest,
Nginx digest and listeners. Reject dirty source, wrong branch, missing mode-0600
files, insufficient 10 GiB reserve and occupied 8080.

- [ ] **Step 2: Implement multi-stage image**

Build Web UI with Node, install pinned Python requirements into a Python 3.11
runtime stage, copy built static assets and backend, create nonroot UID/GID and
healthcheck `/api/health`. Do not include `.git`, tests, local state, private
dictionary or secrets.

- [ ] **Step 3: Implement immutable deploy and rollback**

Package a manifest-bound artifact, stream it to a private staging directory,
verify every path/mode/size/digest, build `orbbec-agent-platform:$RELEASE_SHA`,
run migration, start candidate, require database/import/cloud API health, then
switch `current`. On failure restore the previous release and database snapshot
when required. Never call Nginx, FAE or unrelated Compose projects.

- [ ] **Step 4: Add post-deploy invariants**

Require exact equality for recorded FAE/Nginx facts and public listener set.
Require `127.0.0.1:8080` healthy and reject `0.0.0.0:8080`/`[::]:8080`.
Print only:

```text
CLOUD_PLATFORM_DEPLOY_OK release=$RELEASE_SHA mode=ssh-tunnel
```

- [ ] **Step 5: Run tests and commit**

Run deployment policy tests, `bash -n`, Docker Compose config validation and
all backend tests; expect pass. Commit:

```bash
git add deploy/cloud backend/tests/test_cloud_deployment.py
git commit -m "ops(platform): deploy isolated cloud replica"
```

---

### Task 9: Encrypted backup, retention and restore drill

**Files:**
- Create: `backend/app/cloud_replica/backup.py`
- Modify: `backend/app/cloud_replica/cli.py`
- Create: `deploy/cloud/backup.sh`
- Create: `deploy/cloud/restore-drill.sh`
- Test: `backend/tests/test_cloud_backup.py`
- Test: `backend/tests/test_cloud_retention.py`

**Interfaces:**
- Consumes: PostgreSQL dump stream, dedicated X25519 recovery public key, one-year cutoff and local-only recovery private key.
- Produces: encrypted backup envelope, retention audit and restore drill aggregate result.

- [ ] **Step 1: Write backup crypto and no-plaintext tests**

Use X25519 ephemeral agreement, HKDF-SHA256 and AES-GCM to encrypt the dump.
Assert backup bytes contain no synthetic question, employee name, DSN or key;
only the matching recovery private key decrypts it. Header contains version,
creation time, encrypted size, plaintext SHA-256 and ephemeral public key.

- [ ] **Step 2: Write retention tests**

At exactly one calendar year, delete encrypted Session/Turn/attachment payloads
and preserve aggregate counts. Dry-run is mutation-free. A retention or backup
failure must not advance its audit success marker.

- [ ] **Step 3: Implement backup and restore drill**

Stream `pg_dump --format=custom` directly into encryption without a plaintext
file. Restore drill decrypts into a private temporary directory, restores into
a temporary PostgreSQL database, verifies schema, row counts and stored
sanitized hashes, then removes the temporary database/files.

- [ ] **Step 4: Run tests and commit**

Run backup/retention tests and shell syntax checks; expect pass. Commit:

```bash
git add backend/app/cloud_replica/backup.py \
  backend/app/cloud_replica/cli.py deploy/cloud/backup.sh \
  deploy/cloud/restore-drill.sh backend/tests/test_cloud_backup.py \
  backend/tests/test_cloud_retention.py
git commit -m "ops(platform): back up and expire cloud replica"
```

---

### Task 10: Runbook, end-to-end gate and production rollout

**Files:**
- Modify: `README.md`
- Create: `docs/runbooks/cloud-platform.md`
- Create: `deploy/cloud/acceptance.sh`
- Test: `backend/tests/test_cloud_acceptance_policy.py`

**Interfaces:**
- Consumes: Tasks 1-9, existing SSH administrator key and current managed local analyst DSN.
- Produces: verified cloud Platform, one-year sanitized backfill, enabled five-minute sync, encrypted backup/restore evidence and unchanged FAE/Nginx facts.

- [ ] **Step 1: Write acceptance policy test and runbook**

The acceptance script must cover all 18 design criteria, print no content, IDs,
paths, credentials or attachment coordinates, and stop before deployment on any
failed local test or preflight. Document exact tunnel, sync, freshness, backup,
restore, rollback and later domain/identity operations.

- [ ] **Step 2: Run complete local verification**

Run:

```bash
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
cd ../webui && npm test && npm run build
cd .. && bash -n deploy/cloud/*.sh deploy/install-cloud-sync-launchagent.sh
git diff --check
```

Expected: backend and frontend pass, build succeeds, shell syntax is valid and
the worktree is clean after commits.

- [ ] **Step 3: Capture production baseline and deploy**

Run the checked-in preflight and deploy. Expected: no password prompt and exact
`CLOUD_PLATFORM_DEPLOY_OK`. Re-query FAE health/container, Nginx digest and
listeners and require exact equality.

- [ ] **Step 4: Import canaries and initial backfill**

Import a signed synthetic batch first and prove every forbidden canary is absent
from database/API/HTML/logs/backup. Before real data, run the guarded
`reset-test-generation` operation, which succeeds only when every committed
generation has `source_instance_id=synthetic-acceptance`; prove the database is
empty, then run the bounded one-year backfill. Reconcile safe counts/hashes and
run a second export/import requiring no conflicting or duplicate rows.

- [ ] **Step 5: Verify tunnel, backup, restore and scheduler**

Open `ssh -L 8080:127.0.0.1:8080`, verify Overview, Agents, Sessions and Session
detail, then verify stale-state behavior. Run encrypted backup and restore drill,
install the five-minute LaunchAgent, wait for one successful incremental batch
and confirm source watermark advances.

- [ ] **Step 6: Final invariant gate and commit documentation**

Run `deploy/cloud/acceptance.sh`; expected:

```text
CLOUD_PLATFORM_ACCEPTANCE_OK release=$RELEASE_SHA criteria=18
```

Commit:

```bash
git add README.md docs/runbooks/cloud-platform.md \
  deploy/cloud/acceptance.sh backend/tests/test_cloud_acceptance_policy.py
git commit -m "docs(platform): record cloud replica operations"
```

- [ ] **Step 7: Push reviewed branch and fast-forward master**

Fetch, require `origin/master` is an ancestor, push the feature branch and
master without force, then verify both remote refs equal the tested commit.
