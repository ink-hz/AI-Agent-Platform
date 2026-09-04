# Access History by Nickname Implementation Plan

> Implement in the isolated `feat/access-history-by-nickname` worktree. Follow test-driven development and do not modify HR business behavior or Office routes.

**Goal:** Replace the flat owner access log with a nickname-indexed, department-aware expandable timeline while preserving the closed page catalog and owner-only boundary.

**Architecture:** Migration 067 adds page module metadata and owner-only subject/event projections. FastAPI exposes a paginated subject endpoint and enriches event rows. React loads people first and lazy-loads one person’s grouped event timeline. Reporters only send fixed catalog keys.

**Tech Stack:** PostgreSQL, Python 3.11/FastAPI/psycopg, React/TypeScript/Vitest.

---

### Task 1: Freeze migration and API contracts with failing tests

- Update migration tests to require contiguous version 067, module metadata, subject projection, owner checks and no table grants.
- Add API tests for subject summaries, departments, module names, pagination, filters, and 403/503 behavior.
- Add repository integration tests for grouping by internal identity, unique nickname enforcement, current department projection, and historical events without departments.

### Task 2: Implement migration 067 and backend repository/API

- Add `067_access_history_subject_index.sql`.
- Add subject/filter dataclasses and v67 event projection in `access_history.py`.
- Add `GET /api/v1/manage/access-subjects` and extend the event response.
- Keep v65 write ingestion and retention unchanged.

### Task 3: Build the nickname-indexed UI test-first

- Replace flat-table tests with person summary, lazy expansion, date grouping, filters, pagination and error-state tests.
- Extend `accessHistoryApi.ts` with exact response validation for subjects and enriched events.
- Rebuild `AccessHistoryPage.tsx` as accessible expandable person cards.
- Add responsive styles and style assertions.

### Task 4: Improve fixed Platform route precision

- Add failing reporter tests for HR workspace, position, free chat, position conversation and generic conversation.
- Register new fixed HR page keys in migration 067 and update `accessEventReporter.tsx`.
- Never include position or conversation IDs in an event.

### Task 5: Deployment contracts and verification

- Update acceptance scripts for migration 067 and the new endpoint.
- Run focused red/green tests, full backend tests, full frontend tests and production build.
- Review diff against local master and verify unrelated worktree files are untouched.
- Merge locally to master only after verification; do not create or push a remote feature branch.
