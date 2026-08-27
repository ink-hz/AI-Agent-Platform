# Shared Agent Attachment Substrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Platform's current read-only attachment ticket path into the private upload, validation, scanning, preview, task-grant, output-ingestion, retention, and erasure substrate shared by all Agents.

**Architecture:** PostgreSQL `platform_attachments` owns metadata, ownership, bindings, grants, audit, and erasure state. Private MinIO stores opaque blobs. Uploads move through real validation and ClamAV states before `ready`; both authorization and object state are checked at grant issuance and again at Media Gateway read time. Agent integrations receive task-scoped grants, never object keys or storage credentials.

**Tech Stack:** Python 3.11, FastAPI, PostgreSQL, psycopg 3, boto3/S3 API, MinIO, ClamAV, libmagic/file signatures, Pillow, PDF/text extraction, OCR, pytest.

## Global Constraints

- Start only after migrations 049 and 050 are frozen; reserve `051_platform_attachment_substrate.sql` only after a mainline preflight proves 051 remains free.
- Preserve the existing read-only ticket/stream routes until their consumers have migrated.
- Object keys are random and contain no user, filename, Conversation, Agent, or DingTalk identifiers.
- Non-`ready` objects are denied both when issuing a grant/ticket and when opening content.
- Malware scanning fails closed. `scanning` means a real ClamAV job, not a UI placeholder.
- Content, original names, object references, erasure reasons, and derived sensitive text follow existing ciphertext/key-version/hash discipline.
- Default retention is one year; owner-authorized emergency erasure is audited and can end as `partial`.
- No Agent receives MinIO credentials. MetaBot local downloads also use expiring grants.

---

### Task 1: Freeze the migration boundary and extend the schema

**Files:**
- Create: `backend/control_migrations/051_platform_attachment_substrate.sql`
- Create: `backend/tests/test_attachment_substrate_migration.py`
- Modify: `backend/tests/test_control_migrations.py`

**Interfaces:**
- Tables: `attachments`, `attachment_uploads`, `attachment_bindings`, `attachment_derivatives`, `attachment_access_grants`, `attachment_access_events`, `attachment_erasure_jobs` in `platform_attachments`.

- [ ] **Step 1: Prove migration 051 is unoccupied on updated mainline**

```bash
git fetch origin
git rebase origin/master
test ! -e backend/control_migrations/051_platform_attachment_substrate.sql
```

Expected: the path is absent. Stop and renumber the plan if mainline has claimed 051.

- [ ] **Step 2: Write failing schema tests**

Assert:

- all seven required tables exist;
- state and erasure-status checks exactly match the design;
- SHA-256 fields are 32 bytes;
- `(task_id, attachment_id, agent_id)` grant uniqueness;
- one active erasure job per attachment;
- ciphertext fields have paired key-version fields;
- application roles cannot directly update grants, state, access events, or erasure outcomes.

- [ ] **Step 3: Prove RED**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_substrate_migration.py
```

- [ ] **Step 4: Implement migration 051 and SECURITY DEFINER commands**

Include explicit commands for upload finalization, scanner result, grant issuance/revocation, access recording, output binding, erasure claim, and erasure completion. Each function validates `current_user` against the expected application role.

- [ ] **Step 5: Prove GREEN and commit**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_attachment_substrate_migration.py \
  backend/tests/test_control_migrations.py
git add backend/control_migrations/051_platform_attachment_substrate.sql backend/tests/test_attachment_substrate_migration.py backend/tests/test_control_migrations.py
git commit -m "feat: add shared attachment substrate schema"
```

### Task 2: Add metadata repository and private object operations

**Files:**
- Modify: `backend/app/attachments/models.py`
- Modify: `backend/app/attachments/repository.py`
- Modify: `backend/app/attachments/store.py`
- Create: `backend/tests/test_attachment_substrate_repository.py`
- Modify: `backend/tests/test_attachment_service.py`

**Interfaces:**
- `begin_upload(owner, declared_size, original_name, declared_mime) -> Upload`
- `put_part(upload_id, part_no, stream, digest)`
- `complete_upload(upload_id) -> attachment_id`
- `abort_upload(upload_id)`
- `open_ready_object(attachment_id, byte_range)`

- [ ] **Step 1: Write failing object-isolation tests**

Assert random keys, encrypted object references, no plaintext name in key/log/response, 0600 credential files, fixed private bucket, bounded retries, and cleanup after failed multipart completion.

- [ ] **Step 2: Prove RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_attachment_substrate_repository.py \
  backend/tests/test_attachment_service.py
```

- [ ] **Step 3: Implement repository and store operations**

Keep S3 calls outside long database transactions. Use explicit state transitions with compare-and-set repository commands.

- [ ] **Step 4: Prove GREEN and commit**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_substrate_repository.py backend/tests/test_attachment_service.py
git add backend/app/attachments backend/tests/test_attachment_substrate_repository.py backend/tests/test_attachment_service.py
git commit -m "feat: add private attachment upload storage"
```

### Task 3: Implement upload API, ownership, CSRF, and quotas

**Files:**
- Modify: `backend/app/attachments/routes.py`
- Modify: `backend/app/attachments/service.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_attachment_upload_api.py`
- Modify: `backend/tests/test_attachment_api.py`

**Interfaces:**
- `POST /api/v1/attachments/uploads`
- `PUT /api/v1/attachments/uploads/{upload_id}/parts/{part_no}`
- `POST /api/v1/attachments/uploads/{upload_id}/complete`
- `DELETE /api/v1/attachments/uploads/{upload_id}`

- [ ] **Step 1: Write failing API security tests**

Cover unauthenticated access, wrong owner, CSRF, Origin mismatch, invalid part order, request/total size limits, quota exhaustion, duplicate completion, content-length mismatch, session logout, and safe error bodies.

- [ ] **Step 2: Prove RED**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_upload_api.py
```

- [ ] **Step 3: Implement streaming upload endpoints**

Never buffer an entire upload in application memory. The completion endpoint moves state to `validating`, not `ready`.

- [ ] **Step 4: Prove GREEN and commit**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_upload_api.py backend/tests/test_attachment_api.py
git add backend/app/attachments backend/app/config.py backend/app/main.py backend/tests/test_attachment_upload_api.py backend/tests/test_attachment_api.py
git commit -m "feat: expose secure attachment uploads"
```

### Task 4: Validate type, digest, and safe metadata

**Files:**
- Create: `backend/app/attachments/validation.py`
- Create: `backend/app/attachments/worker.py`
- Create: `backend/tests/test_attachment_validation.py`
- Create: `backend/tests/fixtures/attachments/`

**Interfaces:**
- Worker claims `validating`, streams object once, computes size/SHA-256, detects magic type, and records safe metadata.

- [ ] **Step 1: Add minimal safe/malicious fixture corpus**

Include valid PNG/JPEG/PDF/text/Office samples, extension mismatch, polyglot/signature mismatch, decompression bomb metadata, truncated file, and over-limit object.

- [ ] **Step 2: Write failing validation tests**

Assert declared MIME is never authoritative; invalid objects become `rejected`; valid objects become `scanning`; raw filenames and extracted text do not enter logs.

- [ ] **Step 3: Implement one-pass validation**

Persist SHA-256 and detected MIME only after full stream verification. Use bounded parser metadata reads and reject decompression bombs before derivative creation.

- [ ] **Step 4: Run tests and commit**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_validation.py
git add backend/app/attachments/validation.py backend/app/attachments/worker.py backend/tests/test_attachment_validation.py backend/tests/fixtures/attachments
git commit -m "feat: validate uploaded attachment bytes"
```

### Task 5: Add fail-closed ClamAV scanning

**Files:**
- Create: `backend/app/attachments/scanner.py`
- Modify: `backend/app/attachments/worker.py`
- Modify: `backend/app/config.py`
- Modify: `deploy/cloud/compose.yaml`
- Create: `backend/tests/test_attachment_scanner.py`

**Interfaces:**
- Clean -> derivative queue/`ready`.
- Malware -> `quarantined`.
- Scanner unavailable/error -> remains unavailable to readers and retries with bounded backoff; never `ready`.

- [ ] **Step 1: Write failing scanner tests**

Use EICAR, clean content, timeout, malformed scanner response, unavailable daemon, and retry exhaustion.

- [ ] **Step 2: Implement streaming clamd integration**

Do not write a second plaintext copy to local disk. Health reports scanner readiness without revealing daemon details publicly.

- [ ] **Step 3: Run tests**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_scanner.py
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/attachments/scanner.py backend/app/attachments/worker.py backend/app/config.py deploy/cloud/compose.yaml backend/tests/test_attachment_scanner.py
git commit -m "feat: scan attachments with clamav"
```

### Task 6: Create safe derivatives only after clean scan

**Files:**
- Create: `backend/app/attachments/derivatives.py`
- Modify: `backend/app/attachments/worker.py`
- Create: `backend/tests/test_attachment_derivatives.py`

**Interfaces:**
- Images: dimensions/orientation and safe thumbnail.
- PDFs/documents: bounded preview, extracted text, OCR only when needed.
- Derivatives have their own opaque object refs, digests, sizes, and readiness.

- [ ] **Step 1: Write failing ordering and parser tests**

Assert no derivative function runs before a clean scan. Cover parser crash, page/image limits, encrypted PDF, OCR timeout, bad image metadata, and derivative cleanup on failure.

- [ ] **Step 2: Implement bounded derivative jobs**

Use process/time/memory limits appropriate to each parser. A derivative failure may leave the clean original usable only if the capability contract permits it; expose that state explicitly.

- [ ] **Step 3: Run tests and commit**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_derivatives.py
git add backend/app/attachments/derivatives.py backend/app/attachments/worker.py backend/tests/test_attachment_derivatives.py
git commit -m "feat: derive safe attachment previews"
```

### Task 7: Implement bindings, grants, and the Media Gateway

**Files:**
- Create: `backend/app/attachments/grants.py`
- Create: `backend/app/attachments/media_gateway.py`
- Modify: `backend/app/attachments/routes.py`
- Modify: `backend/app/attachments/repository.py`
- Create: `backend/tests/test_attachment_grants.py`
- Create: `backend/tests/test_attachment_media_gateway.py`

**Interfaces:**
- Bindings: message, turn, Agent task input/output, or domain reference.
- Grant scope: `(task_id, attachment_id, agent_id, audience, purpose, max_reads, max_bytes, expires_at)`.

- [ ] **Step 1: Write failing dual-gate tests**

At grant issuance and read time independently test owner, binding, Agent, task, audience, purpose, expiry, user active state, authorization revocation, task terminal state, attachment state, read count, and byte budget.

- [ ] **Step 2: Prove non-ready isolation**

```python
@pytest.mark.parametrize("state", ["uploading", "validating", "scanning", "quarantined", "rejected", "deleted"])
def test_non_ready_object_never_leaves_gateway(state):
    assert issue_grant(state).reason == "attachment_not_ready"
    assert open_existing_grant_after_state_change(state).reason == "attachment_not_ready"
```

- [ ] **Step 3: Implement signed opaque grant handles and streamed reads**

The URL and token contain no object key. Access is recorded without raw token, filename, or content. Range reads count against `max_bytes`.

- [ ] **Step 4: Run tests and commit**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_grants.py backend/tests/test_attachment_media_gateway.py
git add backend/app/attachments backend/tests/test_attachment_grants.py backend/tests/test_attachment_media_gateway.py
git commit -m "feat: add task-scoped attachment media gateway"
```

### Task 8: Ingest Agent output artifacts

**Files:**
- Create: `backend/app/attachments/output_ingestion.py`
- Modify: `backend/app/agent_brain/models.py`
- Modify: `backend/app/agent_brain/adapters/http_task.py`
- Create: `backend/tests/test_attachment_output_ingestion.py`

**Interfaces:**
- Adapter result carries artifact metadata plus an upload authorization, not a storage key.
- Output becomes an attachment bound to `agent_task_output` and the owning Conversation/Turn.

- [ ] **Step 1: Write failing output tests**

Cover wrong task/Agent, declared digest mismatch, size overflow, duplicate artifact, task terminal race, unsafe content, and replayed upload authorization.

- [ ] **Step 2: Implement ingestion through the same validation pipeline**

Agent output is untrusted and must pass validation/scanning before user access. The task may complete while the artifact visibly remains processing; it must not expose bytes early.

- [ ] **Step 3: Run tests and commit**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_output_ingestion.py
git add backend/app/attachments/output_ingestion.py backend/app/agent_brain backend/tests/test_attachment_output_ingestion.py
git commit -m "feat: ingest professional agent artifacts"
```

### Task 9: Enforce one-year retention and emergency erasure

**Files:**
- Create: `backend/app/attachments/retention.py`
- Create: `backend/app/attachments/erasure.py`
- Modify: `backend/app/attachments/routes.py`
- Create: `backend/tests/test_attachment_retention.py`
- Create: `backend/tests/test_attachment_erasure.py`

**Interfaces:**
- Normal GC after `retention_until`.
- Owner emergency erasure requires reason and audit.
- Erasure covers original, derivatives, previews, exports, run-event copies, and downstream cleanup reports.

- [ ] **Step 1: Write failing retention tests**

Cover one-year boundary, legal/operational hold if configured, active task grant, clock boundary, retry, and idempotent deletion.

- [ ] **Step 2: Write failing erasure tests**

Cover completed, partial, failed, retry after partial, downstream unsupported deletion, object missing, derivative cleanup, and immutable historical audit.

- [ ] **Step 3: Implement workers and audited owner command**

`partial` is terminal for the attempt. A retry creates a new attempt; it does not rewrite prior evidence. Tombstone display name/content in projections.

- [ ] **Step 4: Run tests and commit**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_retention.py backend/tests/test_attachment_erasure.py
git add backend/app/attachments backend/tests/test_attachment_retention.py backend/tests/test_attachment_erasure.py
git commit -m "feat: retain and erase agent attachments"
```

### Task 10: Add frontend upload, preview, artifact, and status components

**Files:**
- Create: `webui/src/components/AttachmentComposer.tsx`
- Create: `webui/src/components/AttachmentStatus.tsx`
- Create: `webui/src/components/AttachmentPreview.tsx`
- Create: `webui/src/components/ArtifactCard.tsx`
- Modify: `webui/src/pages/BrainWorkspacePage.tsx`
- Modify: `webui/src/pages/AgentUsePage.tsx`
- Create: `webui/src/components/AttachmentComposer.test.tsx`
- Create: `webui/src/components/AttachmentPreview.test.tsx`

- [ ] **Step 1: Write failing UI tests**

Cover upload progress, validating/scanning states, quarantined/rejected safety copy, retry, removal before send, preview authorization expiry, artifact processing, mobile layout, and no exposure of object keys.

- [ ] **Step 2: Implement shared components**

Both Brain and direct-Agent pages use the same attachment components. Show only real states; no timer-driven simulated progress.

- [ ] **Step 3: Run tests and commit**

```bash
cd webui
npm test -- --run src/components/AttachmentComposer.test.tsx src/components/AttachmentPreview.test.tsx
cd ..
git add webui/src/components webui/src/pages
git commit -m "feat: add shared agent attachment interface"
```

### Task 11: Integrate FAE and MetaBot local consumers

**Files:**
- Modify: `backend/app/agent_catalog/models.py`
- Modify: `backend/app/agent_catalog/catalog.yaml`
- Modify: `backend/app/execution_relay/models.py`
- Modify: `backend/app/agent_brain/adapters/metabot_local.py`
- Create: `backend/tests/test_attachment_agent_capabilities.py`
- Create: `backend/tests/test_metabot_attachment_grants.py`

- [ ] **Step 1: Write capability and delivery tests**

Assert an Agent cannot receive attachment refs unless its current Catalog version declares the matching MIME/size capability. Assert local MetaBot uses a short-lived download grant and cannot access another task's attachment.

- [ ] **Step 2: Implement capability negotiation and relay envelope**

Old local workers ignore unknown structural fields but must not receive a task that requires attachments; advertise unavailable until upgraded.

- [ ] **Step 3: Run tests and commit**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_agent_capabilities.py backend/tests/test_metabot_attachment_grants.py
git add backend/app/agent_catalog backend/app/execution_relay backend/app/agent_brain/adapters backend/tests/test_attachment_agent_capabilities.py backend/tests/test_metabot_attachment_grants.py
git commit -m "feat: deliver attachments to professional agents"
```

### Task 12: Production acceptance and rollback

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/accept.sh`
- Create: `docs/operations/attachment-substrate.md`

- [ ] **Step 1: Run the complete backend/frontend suites**

```bash
backend/.venv/bin/python -m pytest -q
cd webui && npm test -- --run && npm run build && cd ..
```

- [ ] **Step 2: Deploy storage and scanners before enabling uploads**

Verify private MinIO network binding, private bucket policy, 0600 secrets, ClamAV definitions/health, DB backup, and migration 051. Keep upload routes disabled.

- [ ] **Step 3: Run A0-A3 acceptance**

- A0: metadata/migration/object isolation.
- A1: upload, validation, scan, status, ready-only preview.
- A2: bindings, grants, audit, retention, erasure.
- A3: FAE image/document task and MetaBot local grant download.

- [ ] **Step 4: Enable by capability and verify rollback**

Enable uploads for owner/test scope, then FAE, then other Agents. Rollback revokes new grants and disables new uploads first; existing ready objects remain governed and auditable. Never make quarantined objects readable during rollback.

- [ ] **Step 5: Commit**

```bash
git add deploy/cloud/compose.yaml deploy/cloud/accept.sh docs/operations/attachment-substrate.md
git commit -m "ops: release shared agent attachment substrate"
```

## Attachment Completion Gate

- [ ] Uploads are streamed, private, owner-bound, quota-limited, validated, and scanned fail-closed.
- [ ] Non-ready content is denied at grant issuance and Media Gateway read.
- [ ] All Agents consume task-scoped grants, never MinIO credentials or object keys.
- [ ] Safe previews/derivatives are created only after a clean scan.
- [ ] Output artifacts re-enter the same untrusted validation pipeline.
- [ ] One-year retention and emergency erasure, including `partial`, have audit and retry evidence.
- [ ] FAE image/document input and MetaBot local download pass real integration tests.
