# SDK Release Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the first SDK workflow from an approved ZenTao requirement to an auditable manual release record.

**Architecture:** Keep ZenTao as the requirement and task authority. Add immutable candidate baselines and append-only build, test, approval and release rows in the platform `sdk` schema. Compute the release gate on the server from current evidence; expose it through the same-origin BFF and workbench.

**Tech Stack:** PostgreSQL migrations, FastAPI/Pydantic, psycopg, React/TypeScript, pytest, Vitest.

## Global Constraints

- No GitLab API, webhook, merge request or pipeline integration in V1.
- Source SHAs, build outputs and test results are manually registered and labelled as such.
- Every write has a UUID request ID; the same request replay returns the original result, while a changed payload returns 409.
- Release approval requires a different actor from the candidate creator; fresh evidence invalidates approval.
- Existing identity, origin and CSRF middleware remains the request boundary.

---

### Task 1: Evidence schema and repository

**Files:** Create `backend/control_migrations/115_sdk_release_evidence.sql`, `backend/app/sdk_workbench/evidence_repository.py`; modify `backend/tests/test_control_plane_migration.py`; create `backend/tests/test_sdk_evidence_repository.py`.

**Interfaces:** `EvidenceRepository.list_candidates`, `get_candidate`, `create_candidate`, `add_build`, `add_test`, `add_approval`, `add_release`, `gate_inputs`. Candidate rows freeze a packaging SHA, SDK version, script path, 8 named module SHAs, task IDs, and targets. Build rows hold a target, status and optional artifact. Test rows refer to the exact artifact SHA.

- [ ] Write an integration test with real app-role PostgreSQL credentials that inserts one candidate and verifies immutable baseline, request replay, and evidence retrieval.
- [ ] Run `PYTHONPATH=backend ../../backend/.venv/bin/pytest -q backend/tests/test_sdk_evidence_repository.py` and observe the missing repository failure.
- [ ] Add migration and repository, granting only the app role required table operations and using transactions for request replay and evidence version increments.
- [ ] Run the same test to green and commit.

### Task 2: Gate and route contract

**Files:** Create `backend/app/sdk_workbench/evidence_service.py`; modify `backend/app/sdk_workbench/routes.py`, `backend/app/main.py`; create `backend/tests/test_sdk_evidence_routes.py`.

**Interfaces:** `GET/POST /candidates`, `GET /candidates/{id}`, `POST /candidates/{id}/builds`, `POST /artifacts/{id}/tests`, `GET /candidates/{id}/gate`, `POST /candidates/{id}/approval`, `POST /candidates/{id}/release`.

- [ ] Write route tests for approved current-version requirement and linked task, invalid SHA list, two targets, incomplete/failed/blocked/mismatched evidence, actor separation, stale approval and replay.
- [ ] Run route tests and observe missing endpoints.
- [ ] Implement strict request bodies, SDK role checks, server-side gate calculation, evidence-version approval and release serialization.
- [ ] Run route and repository tests to green and commit.

### Task 3: Usable workbench and operator guide

**Files:** Modify `webui/src/sdk/sdkApi.ts`, `webui/src/sdk/SdkWorkbench.tsx`, `webui/src/sdk/sdkWorkbench.css`, `deploy/sdk/README.md`; add or extend `webui/src/sdk/*.test.tsx`.

- [ ] Write a component test for candidate creation, evidence registration, gate reasons and release trace.
- [ ] Run `npm test -- --run src/sdk` in `webui` and observe the missing UI behavior.
- [ ] Add forms and read-only trace views; show manual-evidence status and server gate reasons.
- [ ] Run tests and `npm run build`; document ZenTao setup and a complete manual acceptance run; commit.

### Task 4: Final verification

- [ ] Run backend SDK tests and migration tests, full backend suite if feasible, frontend tests and production build.
- [ ] Review security, authorization, idempotency, failure paths, migrations and changed files; fix defects and rerun focused checks.
- [ ] Report whether a real ZenTao 22.6 trial and browser acceptance actually ran, with any remaining blocker.
