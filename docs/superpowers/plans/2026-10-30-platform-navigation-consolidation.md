# Platform Navigation Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver the user's six approved navigation/availability changes, preserving professional services, authorization, observations and audit data.

**Architecture:** Add an explicit public Web-entry projection after existing catalog authorization, separate from legacy Brain adapters. A shared frontend catalog source supplies all launch surfaces; consolidate operation lists and move diagnostic/security links into their contextual pages.

**Tech Stack:** Existing FastAPI/Pydantic, React/TypeScript, pytest, Vitest/jsdom and Vite. No dependency changes.

## Global Constraints

- Only modify the platform repository; preserve Agent code, Loop, models, budgets, dependencies, knowledge, database roles and business data.
- Keep existing real-login authorization and `resolve_agent_use_decision_v41`; no access bypass or implicit grants.
- Hide platform assistant/task UI only; retain backend APIs/components/history and professional Agent conversations and Feishu channels.
- No browser, production business writes, real model calls, production deployment, Nginx/SSH/firewall changes.
- Use isolated workspace, task review and final whole-branch review. Preserve master ancestry and user materials; no reset/force push/worktree cleanup.
- Requirements and exact Web paths are in `../specs/2026-10-30-platform-navigation-consolidation-design.md`.

### Task 1: Authoritative authenticated Web-entry projection

**Files:** Create `backend/app/agent_catalog/web_entries.py`; modify `backend/app/agent_catalog/routes.py`; test `backend/tests/test_agent_catalog_web_entries.py` and existing catalog API tests.

**Interfaces:** Existing `/api/v1/catalog/agents` card adds `web_entry: {kind:"independent_service",path:string}|null`. Exact five mappings in spec; rest null. Existing runtime fields remain byte-equivalent values and catalog authorization runs first.

- [x] Add red API tests with a FastAPI test app and AuthContext: anonymous401, only permitted cards, empty list, HR Web projection despite legacy adapter, DAQ path, five Marketing null; assert existing dispatch/adapter fields unchanged.
- [x] Run `PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/tests/test_agent_catalog_web_entries.py -q` from repository root with existing project test configuration; record expected failures before implementation.
- [x] Implement a finite immutable Web publication mapping and projection function; serialize after authorization using `{**card.model_dump(mode="json"),"dispatchable":card.dispatchable,"web_entry":web_entry_for(card.agent_id)}`. Keep old execution catalog unchanged; avoid source imports from sibling repos.
- [x] Run new tests and affected existing catalog/auth tests, record commands/pass/fail, commit scoped changes and write report; independent task review before Task2.

### Task 2: Unified launch UI and contextual operations/security

**Files:** `webui/src/platform/` shared catalog source/policy; `brainTypes.ts`, `brainApi.ts`; `pages/AgentUseDirectoryPage.tsx`, `PlatformSidebar.tsx`, `App.tsx`, panorama action presentation; `pages/OverviewPage.tsx`, merged list/card helpers; `components/PermissionNavigation.tsx`, `pages/IdentityManagementPage.tsx`, governance display; relevant router/title/layout files only as needed. Tests: `brainApi.test.ts`, `pages/AgentUsePage.test.tsx`, `AppShell.sidebar.test.tsx`, `pages/IdentityManagementPage.test.tsx`, overview and app/panorama integration tests.

**Cross-layer integration:** New `/admin/identity/security` deep link requires one exact entry in `backend/app/control_plane/auth.py` and `backend/tests/test_navigation_security_return.py`; no SQL or role change.

**Interfaces:** Consume Task1 `web_entry`; use a shared identity-bound source for directory/sidebar/home business actions and exact same-origin path validation. Use `platformPath` for internal platform roots and preserve independent service paths. Native business entry path is separate from management scopes.

- [x] Add red tests for the changed user journeys: five Web applications including HR/DAQ, zero Marketing launches, no assistant/history fallbacks, reject malformed/foreign paths/missing metadata, account switch/late response isolation; native modified clicks remain intact.
- [x] Add red app/sidebar/operations/security tests: suppressed surfaces and old assistant routes do not call execution APIs; single running-center navigation/list with business/system partition and clickable details; old list redirects while detail/runtime deep links work; standalone events removed from navigation but reachable from diagnostics; security records under permissions, loaded on demand, unauthorized users rejected. Preserve relevant panorama retention/leave-guard tests.
- [x] Run only changed tests to prove red; implement focused components and shared policy/source without copying independent full catalog loaders. Preserve all actual auth/readonly and runtime-source failure semantics.
- [x] Remove generic actions only at panorama rendering, preserving published SQLite data/editor contract; contextual business actions use the shared Web-entry source. Keep core graph/organization behavior.
- [x] Run affected frontend tests and `npm run build` in webui. No full unrelated suite/model/browser repetition. Update reports and commit; independent task review.

### Task 3: Integration, current documentation and completion

- [x] Run focused real HTTP catalog authorization checks through the local app's real endpoint with explicit identity/PG provider test boundaries, inspect all task reports and final build evidence; record skipped real employee/browser/production acceptance.
- [x] Update operations record plus current HR architecture/workflow entry note if necessary to distinguish Web routing from legacy adapter; do not claim HR execution changes.
- [x] Obtain final whole-branch review against original base using a saved diff package. Spec PASS, Quality APPROVED, C0/I0/M1; no critical/important fix wave required. M1 unused panorama callback explicitly accepted as nonblocking; live native links retain unload protection.
- [x] Complete reviewed mainline closeout with fresh-master/ancestor checks and ordinary push, retaining exact integration identities in the private receipt. Leave production deployment separate; preserve evidence and user worktrees/materials. Initial private archive31files567373bytes verified; final documents/integration manifest added during closeout.
