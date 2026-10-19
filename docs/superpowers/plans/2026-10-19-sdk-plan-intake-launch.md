# SDK Annual Plan Intake and Launch Implementation Plan

> **For agentic workers:** Use the test-driven-development and verification-before-completion skills for every behavior change. Execute inline in this isolated worktree; no subagent delegation was requested.

**Goal:** Import the 2026 SDK task CSV as a traceable, reviewable plan, then launch the SDK workspace without replacing the existing HR, panorama, or DAQ production overlays.

**Architecture:** Parse CSV into immutable, source-linked plan batches in the platform control database. Keep plan intake available with enterprise identity even when ZenTao is not configured. A contributor can turn one plan row into a formal ZenTao requirement only after supplying the existing four required fields; record the source link after the existing idempotent write succeeds or is reconciled. Production uses the exact current Compose stack plus an audited SDK override.

**Tech Stack:** Python 3.11, FastAPI, psycopg/PostgreSQL 17, React/TypeScript/Vitest, Docker Compose, ZenTao API v2.

## Global Constraints

- Source CSV stays in `/Users/neo/Developer/work/AI-SDK-Platform`; no raw business content enters Git.
- Preserve 10 source columns, original text, logical record number, physical start line, module, and file SHA-256. Never infer missing acceptance, device, status, or enterprise owner.
- Use SDK membership for reads, contributor for uploads/conversion, platform owner only for membership administration. Writes require real session, same-origin Origin, and CSRF.
- A plan upload is atomic and deduplicated by file digest. ZenTao create ambiguity stays in the current reconciliation path; no automatic retry.
- Maintain a safe rollback path and compare all current Compose inputs before any production container is replaced.
- Production browser/page acceptance belongs to the user; backend/API and real scenario testing belong to development.

---

### Task 1: CSV parser and source fidelity

**Files:** Create `backend/app/sdk_workbench/plan_csv.py`; create `backend/tests/test_sdk_plan_csv.py`.

**Interfaces:** `parse_plan_csv(payload: bytes) -> ParsedPlan` where `ParsedPlan.sha256` is the source digest and `.rows` contains record number, start line, kind, module, and ten untouched source cells.

- [ ] Write tests for the real sheet shape: BOM, trailing empty columns, embedded newlines, repeated titles, blank records, 67 tasks, 2 headings, 17 modules; use a synthetic fixture with the same edge cases in the committed tests and the actual file only in an opt-in local scenario.
- [ ] Verify expected missing-parser failure. Implement a bounded parser that rejects invalid encoding/header, trailing nonempty columns, >2 MiB, >1000 nonempty records, no tasks, and missing module for task rows.
- [ ] Re-run parser tests and `git diff --check`; commit.

### Task 2: Persistent plan batch and row repository

**Files:** Create `backend/control_migrations/117_sdk_plan_intake.sql`; create `backend/app/sdk_workbench/plan_repository.py`; create `backend/tests/test_sdk_plan_repository.py`.

**Interfaces:** `PlanRepository.import_plan(actor_id, filename, parsed)` returns batch metadata; `.list_batches`, `.list_rows`, `.row`, `.bind_story` use the application database role.

- [ ] Write PostgreSQL integration tests first for atomic 69-row insert, same-digest replay, same-name changed bytes as a new batch, pagination, source fidelity, one-row/one-story uniqueness, role grants, and a failure that leaves no partial batch.
- [ ] Verify red against missing migration/repository. Add tables with owner/environment guard and least-privilege grants matching migration 114. Use transaction and unique file digest; no mutable source fields. Bind rows through a unique association with request ID to support replay and reconciliation.
- [ ] Run new repository and control migration tests; commit.

### Task 3: API and ZenTao-independent plan mode

**Files:** Create `backend/app/sdk_workbench/plan_service.py`; modify `backend/app/sdk_workbench/routes.py`, `backend/app/main.py`, `backend/app/config.py`; create `backend/tests/test_sdk_plan_routes.py`; update existing SDK route tests as needed.

**Interfaces:** `/api/v1/sdk/plan-imports` GET/POST; `/api/v1/sdk/plan-imports/{id}/rows` GET; `/api/v1/sdk/plan-rows/{id}` GET; conversion uses existing `create_requirement` semantics and links only an acknowledged story ID.

- [ ] Write failing ASGI tests for membership, read-only identity, spoofed actor, CSRF/Origin through the app's actual middleware, malformed/oversized upload, digest replay, browse/filter, and plan reads with ZenTao disabled.
- [ ] Add a plan service wired when enterprise identity and control DB are available, without constructing `ZenTaoClient`. Keep formal requirement routes unavailable until SDK ZenTao configuration is valid. Upload uses streamed/bounded bytes; responses are `no-store`.
- [ ] Write conversion/reconciliation tests first: complete fields required, linked story replay returns same ID, upstream uncertainty leaves source unbound, owner reconciliation binds the exact row, same row cannot bind two stories. Implement through the existing write intent, not a second ZenTao create path.
- [ ] Run targeted API, auth, SDK and migration tests; commit.

### Task 4: SDK plan UI

**Files:** Modify `webui/src/sdk/sdkApi.ts`, `webui/src/sdk/SdkWorkbench.tsx`, `webui/src/sdk/sdkWorkbench.css`; add `webui/src/sdk/SdkPlanIntake.test.tsx` and SDK API tests.

**Interfaces:** Show plan batches and rows with module/priority/owner filters, source position and unfilled fields; contributor CSV upload; selected row pre-fills the formal requirement form without creating it.

- [ ] Write failing component/API tests for a 69-record batch, filtering, source detail, upload permission, unsupported file feedback, selection prefill and no accidental create, and safe text rendering of notes.
- [ ] Add small plan components and API methods. Retain the existing requirement and release evidence screens; show plan mode when ZenTao is disabled and a clear unavailable state for formal demand creation.
- [ ] Run SDK Vitest files, `npm run build`, and `git diff --check`; commit.

### Task 5: Local complete scenario

**Files:** Create `backend/tests/test_sdk_plan_scenario.py`; update `deploy/sdk/README.md` and release evidence with measured results.

- [ ] Import the user CSV into isolated PostgreSQL through real ASGI HTTP with enterprise auth test harness; assert 69 records, 67 tasks, 17 modules, preserved source SHA and duplicate tasks. Repeat upload and check no new batch. Verify a nonmember cannot list and stale identity cannot upload.
- [ ] With the already isolated ZenTao test instance, select one row, fill scenario/device/acceptance, create and read back a real story, review, create task, register a blocked hardware test, confirm release gate closed. Exercise one deliberate ambiguous write/reconciliation branch in an isolated product if available. Stop at first real failure and retain the record.
- [ ] Record exact test version, status/counts, and missing enterprise browser or hardware evidence. Do not mark business release accepted from simulated identity.

### Task 6: Production rollout with preserved overlays

**Files:** Create a narrow audited release procedure under `deploy/sdk/`; update `deploy/sdk/README.md` and `docs/reviews/2026-10-19-sdk-v1-release-evidence.md` with actual receipt.

- [ ] Capture current API/Brain/loopback image IDs, health, all ordered Compose files, effective config hashes, mounts, networks, DB migration versions, and backups. Build the release from exact pushed mainline SHA. Compare candidate effective Compose config: only planned API/Brain image, SDK private network/secret mount and SDK settings may change; preserve HR generation, panorama, DAQ and other service definitions byte-for-byte.
- [ ] Pin amd64 ZenTao and database image digests from trusted registries, deploy only on private network with local installer port, verify backups, complete formal product/execution and scoped API account setup. Do not expose native ZenTao publicly.
- [ ] Apply migrations 114–117 in preview then production through the existing migrator contract; verify checksums/role grants. Roll out API and Brain with rollback to saved images/config; require health and unchanged other containers. Activate SDK only after service-account contract checks.
- [ ] Run production real HTTP scenario with enterprise auth: source CSV upload/readback, one scoped requirement/review/task, gate closed without hardware evidence; compare source digest, verify idempotency, and record user browser acceptance as pending. If any prerequisite fails, stop before cutover or roll back and state the exact gate.
