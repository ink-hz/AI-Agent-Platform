# Agent Execution Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the secure, durable outbound execution channel that lets the cloud Agent Platform dispatch real work to the seven MetaBot Agents kept on the local `agentops` host and receive their ordered events without exposing a local inbound port.

**Architecture:** The cloud control database owns encrypted relay jobs and events. One local Python worker long-polls authenticated HTTPS endpoints, persists every lease and callback event in local SQLite, calls the existing loopback MetaBot `/api/core-chat/runs` API, and uploads ordered events. The cloud never connects to the Mac and never stores the MetaBot API secret; unknown dispatch state is interrupted, never automatically replayed.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, psycopg 3, PostgreSQL 17, httpx, cryptography/Ed25519/AES-GCM, SQLite, pytest, LaunchAgent, existing MetaBot Core Chat HTTP protocol.

## Global Constraints

- MetaBot and the seven professional Agents remain on the local `agentops` host for this release.
- Run exactly one local execution worker for `hr-bot`, `fae-bot`, and the five `marketing-*-bot` Agents.
- The worker initiates every network connection; do not publish ports 9101–9108 or add SSH/reverse-tunnel access from cloud to local.
- The cloud must not receive or store the local MetaBot API secret.
- Every cloud-stored task payload and event payload is AES-256-GCM encrypted with an explicit `key_version`.
- Worker authentication uses a dedicated, revocable Ed25519 device key; it must not reuse DingTalk, SSH, MetaBot, replica-signing, or content-encryption keys.
- A task that may already have reached MetaBot is never automatically dispatched again.
- Worker or network failure is explicit; do not switch Agent, model, provider, or execution host as fallback.
- Local callback and outbox state survive worker restarts in SQLite.
- The existing sanitized management-replica sync remains unchanged.
- All new production secrets are owner-only regular files with mode `0600`; parent directories use `0700`.
- No browser UI or Agent Brain behavior is implemented in this prerequisite increment.

---

## File Structure

Create one focused backend package:

```text
backend/app/execution_relay/
├── __init__.py          package marker
├── models.py            relay enums and typed request/event models
├── content_crypto.py    purpose-bound envelope codec for job/event JSON
├── worker_auth.py       Ed25519 canonical request signing and verification
├── repository.py        PostgreSQL queue, lease, transition and event operations
├── routes.py            machine-authenticated cloud relay endpoints
├── worker_store.py      local SQLite run/outbox durability
├── metabot_client.py    loopback MetaBot Core Chat client
└── worker.py            long-poll, callback receiver, upload and heartbeat runtime
```

Deployment files live under `deploy/local-execution-worker/`; production cloud wiring remains in the existing Compose and Nginx files.

---

### Task 1: Add the control-plane relay schema and least-privilege grants

**Files:**
- Create: `backend/control_migrations/027_execution_relay.sql`
- Modify: `backend/tests/test_control_plane_migration.py`
- Create: `backend/tests/test_execution_relay_migration.py`

**Interfaces:**
- Consumes: numbered immutable control migrations and the existing `platform_control_app` role.
- Produces: `platform_control.execution_workers`, `execution_worker_keys`, `execution_jobs`, `execution_events`, and `execution_worker_nonces` with no business-record `DELETE` grant to the application role.

- [ ] **Step 1: Write the failing migration tests**

Add `027_execution_relay.sql` to the migration existence assertions and add a PostgreSQL test that expects these exact columns and constraints:

```python
RELAY_TABLES = {
    "execution_workers",
    "execution_worker_keys",
    "execution_jobs",
    "execution_events",
    "execution_worker_nonces",
}

def test_execution_relay_schema_is_versioned_encrypted_and_append_only(control_database):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        tables = {row[0] for row in connection.execute(
            "select table_name from information_schema.tables "
            "where table_schema='platform_control'"
        )}
        grants = connection.execute(
            "select table_name,privilege_type from information_schema.role_table_grants "
            "where grantee='platform_control_app' and table_name like 'execution_%'"
        ).fetchall()
    assert RELAY_TABLES <= tables
    assert all(privilege != "DELETE" for _, privilege in grants)
```

Also assert `payload_ciphertext` is `bytea`, `encryption_key_version` is non-null and positive, `(run_id, seq)` is unique, worker public keys are exactly 32 bytes, `(worker_id, key_id)` is unique, and job status is constrained to `queued|leased|dispatched|running|completed|failed|cancelled|interrupted`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_control_plane_migration.py tests/test_execution_relay_migration.py -q
```

Expected: FAIL because migration 027 and the four tables do not exist.

- [ ] **Step 3: Implement migration 027**

Use this schema contract:

```sql
create table platform_control.execution_workers (
  worker_id text primary key check (worker_id ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
  allowed_agent_ids text[] not null check (cardinality(allowed_agent_ids)>0),
  status text not null check (status in ('active','revoked')),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  last_seen_at timestamptz,
  check ((status='active' and revoked_at is null) or status='revoked')
);

create table platform_control.execution_worker_keys (
  worker_id text not null references platform_control.execution_workers(worker_id),
  key_id text not null check (key_id ~ '^worker-v[1-9][0-9]*$'),
  public_key bytea not null check (octet_length(public_key)=32),
  status text not null check (status in ('active','revoked')),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  primary key(worker_id,key_id),
  unique(public_key),
  check ((status='active' and revoked_at is null) or status='revoked')
);

create table platform_control.execution_jobs (
  job_id uuid primary key,
  run_id uuid not null unique,
  agent_id text not null check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  payload_ciphertext bytea not null,
  encryption_key_version integer not null check (encryption_key_version>0),
  status text not null check (status in (
    'queued','leased','dispatched','running','completed','failed','cancelled','interrupted'
  )),
  lease_worker_id text references platform_control.execution_workers(worker_id),
  lease_expires_at timestamptz,
  cancel_requested boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  terminal_at timestamptz,
  check ((status in ('queued') and lease_worker_id is null and lease_expires_at is null)
      or (status <> 'queued' and lease_worker_id is not null)),
  check ((status in ('completed','failed','cancelled','interrupted')) = (terminal_at is not null))
);

create table platform_control.execution_events (
  run_id uuid not null references platform_control.execution_jobs(run_id),
  seq integer not null check (seq>0),
  event_type text not null check (event_type ~ '^[a-z][a-z0-9_.-]{0,63}$'),
  payload_ciphertext bytea not null,
  encryption_key_version integer not null check (encryption_key_version>0),
  created_at timestamptz not null,
  received_at timestamptz not null default now(),
  primary key (run_id,seq)
);

create table platform_control.execution_worker_nonces (
  worker_id text not null references platform_control.execution_workers(worker_id),
  nonce bytea not null check (octet_length(nonce)=32),
  expires_at timestamptz not null,
  primary key (worker_id,nonce)
);
```

Add indexes on `(status, created_at)`, `(lease_worker_id, status)`, and nonce expiry. Grant the application role only the required `SELECT, INSERT, UPDATE` on worker/key/job/event tables and `SELECT, INSERT, DELETE` only on `execution_worker_nonces`; nonce deletion is bounded authentication housekeeping, not business-record deletion. Revoke all from `public` and every other runtime role.

- [ ] **Step 4: Run the migration tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/control_migrations/027_execution_relay.sql backend/tests/test_control_plane_migration.py backend/tests/test_execution_relay_migration.py
git commit -m "feat(relay): add durable execution queue schema"
```

---

### Task 2: Implement encrypted relay models and repository transitions

**Files:**
- Create: `backend/app/execution_relay/__init__.py`
- Create: `backend/app/execution_relay/models.py`
- Create: `backend/app/execution_relay/content_crypto.py`
- Create: `backend/app/execution_relay/repository.py`
- Create: `backend/tests/test_execution_relay_crypto.py`
- Create: `backend/tests/test_execution_relay_repository.py`

**Interfaces:**
- Consumes: `IdentityKeyring.from_file(keyring_path, expected_purpose="platform-content-encryption", expected_key_length=32)` and migration 027.
- Produces: `RelayJobPayload`, `RelayEvent`, `RelayLease`, `ContentCodec`, and `ExecutionRelayRepository` methods used by routes and future Chat Gateway.

- [ ] **Step 1: Write failing model and crypto tests**

Test these exact public types:

```python
class RelayJobPayload(BaseModel):
    run_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    agent_id: str
    prompt: str
    max_turns: int = Field(ge=1, le=24)

class RelayEvent(BaseModel):
    run_id: UUID
    seq: int = Field(gt=0)
    event_type: str
    created_at: datetime
    payload: dict[str, object]

class RelayLease(BaseModel):
    job_id: UUID
    payload: RelayJobPayload
    lease_expires_at: datetime
    cancel_requested: bool
```

Assert that `ContentCodec.seal_json(subject, value)` returns ciphertext without plaintext, records the active version, authenticates `subject` as AAD, and fails with `ContentCryptoError("content decrypt failed")` for a wrong subject, wrong version, or modified ciphertext.

- [ ] **Step 2: Run the unit tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_execution_relay_crypto.py tests/test_execution_relay_repository.py -q
```

Expected: collection fails because `app.execution_relay` does not exist.

- [ ] **Step 3: Implement the content codec**

Implement AES-GCM with 12 random nonce bytes prefixed to ciphertext and AAD `orbbec-platform:{subject}:v{version}`. Expose:

The public signatures are `ContentCodec.seal_json(subject: str, value: dict[str, object]) -> SealedContent` and `ContentCodec.unseal_json(subject: str, sealed: SealedContent) -> dict[str, object]`. `SealedContent` is a frozen dataclass with redacted `ciphertext: bytes` and `key_version: int` fields.

Serialize JSON with UTF-8, sorted keys and compact separators. Reject non-object top-level values, NUL-containing subjects, ciphertext shorter than 28 bytes, unknown key versions and invalid tags without logging protected values.

- [ ] **Step 4: Implement repository state transitions**

Expose these exact methods:

Expose `ExecutionRelayRepository.enqueue(payload: RelayJobPayload) -> UUID`, `lease(worker_id: str, allowed_agents: tuple[str, ...], lease_seconds: int) -> RelayLease | None`, `mark_dispatched(worker_id: str, run_id: UUID) -> None`, `append_events(worker_id: str, events: tuple[RelayEvent, ...]) -> int`, `request_cancel(run_id: UUID) -> bool`, `finish(worker_id: str, run_id: UUID, status: Literal["completed","failed","cancelled","interrupted"]) -> None`, and `heartbeat(worker_id: str) -> tuple[UUID, ...]`.

`lease()` uses one transaction with `FOR UPDATE SKIP LOCKED`, requires an active worker, intersects the worker database allowlist with the request allowlist, and leases only `queued` jobs. It must never lease `dispatched`, `running`, or terminal jobs. `append_events()` uses `ON CONFLICT (run_id,seq) DO NOTHING`, verifies the existing encrypted row has the same event type on duplicates, advances `leased|dispatched` to `running`, and never changes a terminal job back to running.

- [ ] **Step 5: Run unit and PostgreSQL tests and verify GREEN**

```bash
cd backend
.venv/bin/pytest tests/test_execution_relay_crypto.py tests/test_execution_relay_repository.py -q
```

Expected: PASS, including concurrent lease, duplicate event, revoked worker, disallowed Agent, cancellation and terminal-state cases.

- [ ] **Step 6: Commit**

```bash
git add backend/app/execution_relay backend/tests/test_execution_relay_crypto.py backend/tests/test_execution_relay_repository.py
git commit -m "feat(relay): encrypt jobs and enforce transitions"
```

---

### Task 3: Add replay-safe Ed25519 worker authentication

**Files:**
- Create: `backend/app/execution_relay/worker_auth.py`
- Create: `backend/tests/test_execution_worker_auth.py`

**Interfaces:**
- Consumes: worker public key and status from `execution_workers`; nonce persistence from `execution_worker_nonces`.
- Produces: `WorkerRequestSigner.sign(method, path, body)` and `WorkerRequestVerifier.verify(request, body) -> WorkerIdentity`.

- [ ] **Step 1: Write failing authentication tests**

Use this canonical request contract:

```text
orbbec-agent-worker-v1\n
{METHOD}\n
{PATH_WITH_QUERY}\n
{UNIX_TIMESTAMP}\n
{BASE64URL_32_BYTE_NONCE}\n
{LOWERCASE_SHA256_HEX_OF_BODY}
```

Required headers are:

```text
X-Orbbec-Worker-Id
X-Orbbec-Worker-Key-Id
X-Orbbec-Worker-Timestamp
X-Orbbec-Worker-Nonce
X-Orbbec-Worker-Signature
```

Tests must prove valid signatures pass; body/path/method changes fail; timestamps outside ±60 seconds fail; reused nonces fail; revoked workers fail; a key ID mismatch fails; malformed base64 and oversized bodies fail without exposing a key or body in the error.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_execution_worker_auth.py -q
```

Expected: FAIL because signer and verifier are missing.

- [ ] **Step 3: Implement signer and verifier**

Expose:

`WorkerIdentity` is a frozen dataclass with `worker_id: str`, `key_id: str`, and `allowed_agent_ids: tuple[str, ...]`. Expose `WorkerRequestSigner.sign(method: str, path_with_query: str, body: bytes, *, now: datetime | None = None) -> dict[str, str]` and `WorkerRequestVerifier.verify(method: str, path_with_query: str, body: bytes, headers: Mapping[str, str], *, now: datetime | None = None) -> WorkerIdentity`.

Use raw Ed25519 keys from `cryptography`, URL-safe base64 without padding, `secrets.token_bytes(32)`, `hmac.compare_digest` for stable text comparisons, and an atomic nonce insert before returning success. Delete only expired nonces for that worker inside the same transaction.

- [ ] **Step 4: Run tests and verify GREEN**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/execution_relay/worker_auth.py backend/tests/test_execution_worker_auth.py
git commit -m "feat(relay): authenticate execution workers"
```

---

### Task 4: Expose exact machine-authenticated relay endpoints

**Files:**
- Create: `backend/app/execution_relay/routes.py`
- Modify: `backend/app/control_plane/middleware.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/config.py`
- Create: `backend/tests/test_execution_relay_api.py`
- Modify: `backend/tests/test_config.py`
- Modify: `backend/tests/test_r1_authorization.py`

**Interfaces:**
- Consumes: `ExecutionRelayRepository`, `WorkerRequestVerifier`, and `RelayEvent`.
- Produces: signed worker endpoints and `app.state.execution_relay_repository` for later Chat Gateway use.

- [ ] **Step 1: Write failing API and middleware tests**

The only machine-public routes are:

```text
POST /api/v1/execution-worker/lease
POST /api/v1/execution-worker/heartbeat
POST /api/v1/execution-worker/runs/{run_id}/dispatched
POST /api/v1/execution-worker/runs/{run_id}/events
POST /api/v1/execution-worker/runs/{run_id}/terminal
```

Assert these routes do not require a DingTalk cookie or browser `Origin`, but reject a missing/invalid worker signature with `401` before repository access. Assert every other method or path under `/api/v1/execution-worker` remains `403` or `404`. Browser cookies must not authorize machine routes. A valid worker can lease only its allowed Agents. Event batches are capped at 100 events and 1 MiB encoded JSON.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_execution_relay_api.py tests/test_config.py tests/test_r1_authorization.py -q
```

Expected: FAIL because the routes and config are missing.

- [ ] **Step 3: Add exact machine-route recognition**

Add this helper to `middleware.py` and use it to bypass DingTalk Session and browser Origin/CSRF checks only for exact relay routes:

```python
_WORKER_RUN_ROUTE = re.compile(
    r"/api/v1/execution-worker/runs/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/"
    r"(?:dispatched|events|terminal)\Z"
)

def is_execution_worker_request(method: str, path: str) -> bool:
    return method == "POST" and (
        path in {
            "/api/v1/execution-worker/lease",
            "/api/v1/execution-worker/heartbeat",
        }
        or _WORKER_RUN_ROUTE.fullmatch(path) is not None
    )
```

Machine routes still pass through trusted-proxy address resolution, coarse authenticated-machine rate limits and their route-level signature verifier.

- [ ] **Step 4: Add configuration and app wiring**

Add these required production settings when `PLATFORM_EXECUTION_RELAY_ENABLED=1`:

```text
PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE
PLATFORM_EXECUTION_RELAY_LEASE_SECONDS=45
PLATFORM_EXECUTION_RELAY_MAX_BODY_BYTES=1048576
```

Validate the keyring as an absolute, service-owned mode-0600 regular file with purpose `platform-content-encryption`. Construct the codec, repository, verifier and router in `create_app()`. Fail startup if any required relay dependency is unavailable; do not start a partially unencrypted relay.

- [ ] **Step 5: Implement routes and run tests GREEN**

Parse the raw request body once, verify its signature before JSON decoding, validate strict Pydantic bodies, and return:

- `200` plus a lease, or `204` when no job is available;
- `200` for idempotent duplicate event uploads;
- `409` for an illegal state transition;
- `401` for worker authentication failure;
- `413` for an oversized body;
- `503` for repository or crypto unavailability.

Run Step 2. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/execution_relay/routes.py backend/app/control_plane/middleware.py backend/app/main.py backend/app/config.py backend/tests/test_execution_relay_api.py backend/tests/test_config.py backend/tests/test_r1_authorization.py
git commit -m "feat(relay): expose signed worker API"
```

---

### Task 5: Build the local durable worker store and MetaBot client

**Files:**
- Create: `backend/app/execution_relay/worker_store.py`
- Create: `backend/app/execution_relay/metabot_client.py`
- Create: `backend/tests/test_execution_worker_store.py`
- Create: `backend/tests/test_metabot_relay_client.py`

**Interfaces:**
- Consumes: existing MetaBot Core Chat routes and runtime contract schema version 2.
- Produces: `WorkerStore`, `MetaBotRuntimeMap`, and `MetaBotClient` for the worker runtime.

- [ ] **Step 1: Write failing SQLite durability tests**

The local database owns:

```sql
create table local_runs (
  run_id text primary key,
  job_id text not null unique,
  agent_id text not null,
  metabot_port integer not null,
  callback_token text not null,
  state text not null,
  leased_at text not null,
  dispatched_at text,
  terminal_at text
);
create table event_outbox (
  run_id text not null,
  seq integer not null,
  event_json text not null,
  delivered_at text,
  primary key(run_id,seq)
);
```

Test transactionally recording a lease, marking `dispatching` before the HTTP call, deduplicating callback events, reading only contiguous undelivered events, marking delivery, and recovering state after reopening the SQLite file.

- [ ] **Step 2: Write failing MetaBot client tests**

Using `respx`, assert `MetaBotClient.start_run()` sends the existing contract:

```json
{
  "runId": "00000000-0000-4000-8000-000000000101",
  "conversationId": "00000000-0000-4000-8000-000000000102",
  "triggerMessageId": "00000000-0000-4000-8000-000000000103",
  "targetBot": "hr-bot",
  "prompt": "请根据岗位要求形成候选人画像。",
  "eventCallbackUrl": "http://127.0.0.1:9120/callbacks/00000000-0000-4000-8000-000000000101/bm9uLXNlY3JldC10ZXN0LXRva2Vu",
  "executionChatId": "platform-00000000-0000-4000-8000-000000000102-hr-bot",
  "userId": "platform-user",
  "maxTurns": 24
}
```

The request goes only to the runtime-contract port for the exact `agent_id`, uses the local bearer secret, sets a 10-second acceptance timeout, and never sends DingTalk IDs, cloud credentials or a model override. `cancel_run()` calls `/api/core-chat/runs/{run_id}/cancel` on the same port.

- [ ] **Step 3: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_execution_worker_store.py tests/test_metabot_relay_client.py -q
```

Expected: FAIL because the worker modules are missing.

- [ ] **Step 4: Implement the store and runtime map**

`MetaBotRuntimeMap.from_contract(path)` must require schema version 2, select exactly the seven approved bot IDs, require loopback ports 1–65535, reject duplicates, and ignore `feishu-default`, `codex-assistant`, and `test-bot`. Expose:

Expose `WorkerStore.record_lease(lease: RelayLease, port: int, callback_token: str) -> None`, `mark_dispatching(run_id: UUID) -> None`, `mark_dispatched(run_id: UUID) -> None`, `append_event(event: RelayEvent) -> bool`, `contiguous_outbox(run_id: UUID, limit: int = 100) -> tuple[RelayEvent, ...]`, `mark_delivered(run_id: UUID, through_seq: int) -> None`, and `mark_terminal(run_id: UUID, status: str) -> None`.

Enable SQLite WAL, `foreign_keys=on`, `busy_timeout=5000`, full synchronous writes, and mode `0600` for the database file.

- [ ] **Step 5: Implement MetaBot client and run tests GREEN**

Read the MetaBot bearer secret from an absolute owner-only mode-0600 file supplied to the worker. Never invoke Keychain or `security`. Redact authorization and prompt bodies from exceptions and logs.

Run Step 3. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/execution_relay/worker_store.py backend/app/execution_relay/metabot_client.py backend/tests/test_execution_worker_store.py backend/tests/test_metabot_relay_client.py
git commit -m "feat(relay): persist local runs and call MetaBot"
```

---

### Task 6: Implement the outbound worker runtime and callback receiver

**Files:**
- Create: `backend/app/execution_relay/worker.py`
- Create: `backend/tests/test_execution_worker_runtime.py`

**Interfaces:**
- Consumes: signed cloud API, `WorkerStore`, `MetaBotRuntimeMap`, and `MetaBotClient`.
- Produces: `python -m app.execution_relay.worker` long-running local service.

- [ ] **Step 1: Write failing end-to-end worker tests**

Run fake cloud and fake MetaBot servers on loopback. Prove this exact sequence:

```text
signed lease -> local lease commit -> local dispatching commit
-> MetaBot 202 -> cloud dispatched acknowledgement
-> callback seq 1..N committed locally
-> signed cloud event upload -> local delivered marker
-> signed terminal upload
```

Also test: cloud offline before lease; cloud offline after MetaBot completion; worker restart with undelivered events; duplicate callback; callback token mismatch; sequence gap; MetaBot 401; MetaBot acceptance timeout; cancellation request; SIGTERM during a run. No test may observe an automatic second MetaBot POST for the same run ID.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_execution_worker_runtime.py -q
```

Expected: FAIL because the runtime is missing.

- [ ] **Step 3: Implement the runtime**

Use one asyncio process with four bounded functions: `lease_loop(runtime: WorkerRuntime) -> None`, `upload_loop(runtime: WorkerRuntime) -> None`, `heartbeat_loop(runtime: WorkerRuntime) -> None`, and `callback_server(runtime: WorkerRuntime) -> None`.

The callback server binds only `127.0.0.1`, validates the per-run 256-bit URL token, caps bodies at 1 MiB, validates the MetaBot event schema, and commits before returning `204`. The lease loop accepts one job at a time until proven stable, then may use configuration up to three concurrent runs; it must never exceed the runtime contract's per-instance capacity.

Backoff uses 1, 2, 4, 8, 15, 30 seconds with ±20% jitter and resets after one successful request. Heartbeat interval is 15 seconds. Logs contain worker ID, run ID, agent ID, state and error class only—never prompt, result, bearer token, signature, callback token or event payload.

- [ ] **Step 4: Run tests and verify GREEN**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/execution_relay/worker.py backend/tests/test_execution_worker_runtime.py
git commit -m "feat(relay): run durable outbound executor"
```

---

### Task 7: Add key bootstrap, worker installation and cloud deployment wiring

**Files:**
- Create: `deploy/local-execution-worker/generate-worker-key.py`
- Create: `backend/app/execution_relay/register_worker.py`
- Create: `deploy/local-execution-worker/com.orbbec.agent-execution-worker.plist.template`
- Create: `deploy/local-execution-worker/install.sh`
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/deploy.sh`
- Modify: `deploy/cloud/accept-dingtalk-production.sh`
- Create: `backend/tests/test_execution_worker_deployment.py`

**Interfaces:**
- Consumes: current immutable cloud release flow and `agentops` LaunchAgent conventions.
- Produces: reproducible worker key registration, mode-0600 runtime config, one LaunchAgent, cloud keyring mount and acceptance gates.

- [ ] **Step 1: Write failing deployment policy tests**

Assert:

- no plist contains a private key, MetaBot bearer, database URL or inline secret;
- the worker runs as `agentops`, not `neo` or `root`;
- the worker has no `KeepAlive.NetworkState` loop that spawns duplicates;
- the callback binds loopback;
- cloud Compose mounts the content keyring read-only and does not publish a new port;
- installation does not call `/usr/bin/security`, Keychain, `ssh -R`, `sudo`, or `su`;
- production acceptance rejects a missing/revoked worker, stale heartbeat over 60 seconds, wrong key fingerprint, or reachable public 9101–9108 port.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_execution_worker_deployment.py -q
```

Expected: FAIL because deployment assets are missing.

- [ ] **Step 3: Implement deterministic key bootstrap**

`generate-worker-key.py` creates an Ed25519 private key file and public registration JSON:

```json
{
  "worker_id": "agentops-mac-primary",
  "key_id": "worker-v1",
  "public_key_base64url": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
  "allowed_agent_ids": [
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot"
  ]
}
```

If the private key exists, rerunning preserves it and prints only the public fingerprint. Registration in cloud is an audited migration/maintenance action using the public document; the private key never leaves the Mac.

Implement `python -m app.execution_relay.register_worker register PUBLIC_JSON CHANGE_REFERENCE`, `add-key WORKER_ID PUBLIC_JSON CHANGE_REFERENCE`, `revoke-key WORKER_ID KEY_ID CHANGE_REFERENCE`, and `revoke-worker WORKER_ID CHANGE_REFERENCE`. The command reads the maintenance DSN only from `PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE`, requires an uppercase reference matching `^[A-Z][A-Z0-9_-]{7,63}$`, changes exactly one worker or key in a transaction, and appends a sanitized audit event containing only worker ID, key ID, public fingerprint, allowed Agent IDs and reference. `add-key` creates a bounded dual-acceptance window; after the new key is deployed and accepted, `revoke-key` retires the previous key. Reusing a key ID with different bytes is rejected.

- [ ] **Step 4: Implement install and cloud wiring**

The installed worker environment contains only absolute file paths and non-secret settings:

```text
PLATFORM_WORKER_ID=agentops-mac-primary
PLATFORM_WORKER_KEY_ID=worker-v1
PLATFORM_WORKER_PRIVATE_KEY_FILE=/Users/agentops/AgentRuntime/private/execution-worker-ed25519.key
PLATFORM_WORKER_STATE_DB=/Users/agentops/AgentRuntime/execution-worker/state.sqlite3
PLATFORM_WORKER_CALLBACK_PORT=9120
PLATFORM_WORKER_CLOUD_URL=https://agent.orbbec.com.cn
PLATFORM_METABOT_RUNTIME_CONTRACT=/Users/agentops/AgentRuntime/metabot/runtime-contract.json
PLATFORM_METABOT_API_SECRET_FILE=/Users/agentops/AgentRuntime/private/metabot-api-token
```

The LaunchAgent uses `RunAtLoad=true`, `KeepAlive=true`, bounded restart throttling, explicit stdout/stderr files and the Python 3.11 virtual environment. Installation validates all paths and permissions before bootstrapping; it does not prompt for a password or read a user Keychain.

- [ ] **Step 5: Run deployment tests and verify GREEN**

Run Step 2. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add deploy/local-execution-worker backend/app/execution_relay/register_worker.py deploy/cloud/compose.yaml deploy/cloud/deploy.sh deploy/cloud/accept-dingtalk-production.sh backend/tests/test_execution_worker_deployment.py
git commit -m "ops(relay): deploy the local execution worker"
```

---

### Task 8: Add full acceptance, recovery runbook and release gate

**Files:**
- Create: `deploy/local-execution-worker/accept.sh`
- Create: `docs/runbooks/agent-execution-relay.md`
- Modify: `docs/runbooks/cloud-platform.md`
- Modify: `README.md`
- Create: `backend/tests/test_execution_relay_acceptance_policy.py`

**Interfaces:**
- Consumes: all prior relay tasks.
- Produces: one command proving real HR and Marketing execution plus documented recovery without duplicate dispatch.

- [ ] **Step 1: Write failing acceptance-policy tests**

The acceptance script must prove:

1. cloud API, database and worker heartbeat are healthy;
2. no new public listener exists on the Mac or cloud host;
3. the registered public-key fingerprint matches the local private key;
4. a synthetic `hr-bot` job reaches the real MetaBot API and returns ordered state plus terminal events;
5. a synthetic `marketing-intelligence-bot` job does the same;
6. identical event re-upload is idempotent;
7. killing the worker after local completion retains events in SQLite and uploads them after restart;
8. killing the worker after `dispatching` never produces a second MetaBot POST;
9. revoking the worker key makes lease and upload return `401` while existing Session/history APIs remain available;
10. FAE external domain and existing management-replica synchronization remain unchanged.

- [ ] **Step 2: Run policy tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_execution_relay_acceptance_policy.py -q
```

Expected: FAIL because acceptance and runbook files are missing.

- [ ] **Step 3: Write acceptance and recovery procedures**

The runbook must include exact commands for status, log inspection, key rotation, worker revocation, local spool backup, stuck-job inspection, explicit interruption, restart, rollback and removal. It must state that `dispatching|dispatched|running` jobs are never requeued automatically; recovery either resumes event upload from the same local state or terminalizes the run as `interrupted`.

`accept.sh` prints one final machine-readable line only after every gate passes:

```text
AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 public_ports_added=0 duplicate_dispatches=0
```

- [ ] **Step 4: Run targeted and full verification**

```bash
cd backend
.venv/bin/pytest tests/test_execution_relay_*.py tests/test_execution_worker_*.py tests/test_metabot_relay_client.py -q
.venv/bin/pytest -q
cd ../webui
npm test -- --run
npm run build
```

Expected: all backend tests pass; all frontend tests pass; TypeScript and Vite build succeed.

- [ ] **Step 5: Run real staged acceptance**

Run the worker against a non-production relay registration first, then the reviewed production registration. Expected final output is the exact `AGENT_EXECUTION_RELAY_OK` line above. Do not enable user-facing Chat or Agent Brain routes in this increment.

- [ ] **Step 6: Commit**

```bash
git add deploy/local-execution-worker/accept.sh docs/runbooks/agent-execution-relay.md docs/runbooks/cloud-platform.md README.md backend/tests/test_execution_relay_acceptance_policy.py
git commit -m "docs(relay): add acceptance and recovery gates"
```

---

## Completion Gate

This prerequisite increment is complete only when all eight tasks are committed, the full backend/frontend suite passes, the real staged relay acceptance prints `AGENT_EXECUTION_RELAY_OK`, and production still exposes no local MetaBot port. The next implementation plan may then build Platform-owned direct Agent Sessions on `ExecutionRelayRepository.enqueue()`; it must not invent another execution path.
