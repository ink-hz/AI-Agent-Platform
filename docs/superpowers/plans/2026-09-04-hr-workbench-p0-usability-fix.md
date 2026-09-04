# HR Workbench P0 Usability Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HR chat start promptly, show real MetaBot progress, survive chat/position switching, provide a usable composer, and populate the position workspace with verified current data.

**Architecture:** Correct the execution relay polling contract at the worker boundary, then project persisted conversation events into the direct-chat UI. Keep the HR chat tree mounted while the position route is visible, and reuse the existing attachment and import pipelines rather than creating parallel state machines.

**Tech Stack:** Python 3.12, FastAPI, PostgreSQL, React 19, TypeScript, Vitest, pytest, Docker.

## Global Constraints

- P0 order is runtime stability, real progress, state-preserving navigation, composer usability, then position import.
- Do not touch Office, FAE, VOC, Marketing, Admin, shared Nginx, or other bots.
- Do not add, remove, or commit `backend/.venv`.
- Releases contain code/build artifacts only; staging is `/data/staging/agent-platform/<deployment_id>/` and is removed with an exact-target trap.
- Root disk must have at least 25 GB free before release and remain at or below 75% used afterward.
- Keep current plus two rollback releases/images on root; archive older releases under `/data/archive/agent-platform/releases/` under the existing retention policy.

---

### Task 1: Stop the worker from self-rate-limiting

**Files:**
- Modify: `backend/app/execution_relay/worker.py`
- Test: `backend/tests/test_execution_worker_runtime.py`

**Interfaces:**
- Consumes: `WorkerRuntime.lease_once()` and `WorkerRuntime.pause(seconds)`.
- Produces: `LeaseOutcome = Literal["leased", "empty", "at_capacity", "failed"]` and an idle delay of at least 1 second.

- [ ] **Step 1: Write failing loop tests**

Add tests that feed an empty `FakeCloud`, capture `runtime.pause`, and assert the first delay is `1.0`; add a second test that feeds a real lease and asserts the post-lease delay remains `0.25`.

- [ ] **Step 2: Verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_execution_worker_runtime.py -k 'lease_loop and (empty or leased)' -q`

Expected: the empty case receives `0.25` instead of `1.0` or the new outcome type is missing.

- [ ] **Step 3: Implement the explicit outcome**

Return `"at_capacity"`, `"failed"`, `"empty"`, or `"leased"` from `lease_once`; in `lease_loop`, use exponential backoff only for `"failed"`, `1.0` seconds for `"empty"`, and `0.25` seconds otherwise. Keep existing exception sanitization and shutdown semantics.

- [ ] **Step 4: Verify GREEN**

Run: `backend/.venv/bin/pytest backend/tests/test_execution_worker_runtime.py backend/tests/test_execution_worker_store.py backend/tests/test_execution_relay_api.py -q`

Expected: all selected tests pass with no warnings introduced by this change.

- [ ] **Step 5: Commit**

Run: `git add backend/app/execution_relay/worker.py backend/tests/test_execution_worker_runtime.py && git commit -m "fix(relay): keep idle worker within request quota"`

### Task 2: Render real direct-Agent progress

**Files:**
- Modify: `webui/src/components/conversation/PublicProgress.tsx`
- Modify: `webui/src/styles.css`
- Test: `webui/src/components/conversation/PublicProgress.test.tsx`
- Test: `webui/src/pages/ConversationPage.test.tsx`

**Interfaces:**
- Consumes: ordered `ConversationEvent[]` containing persisted `agent.accepted`, `agent.progress`, `agent.work_update`, `agent.result`, and terminal events.
- Produces: a de-duplicated, ordered live status list for the active direct-Agent turn.

- [ ] **Step 1: Write failing projection/render tests**

Cover empty events (`已进入队列`), accepted events, multiple progress summaries in sequence order, duplicate event IDs, and absence of synthetic percentages. Assert terminal events are not shown as an active progress panel.

- [ ] **Step 2: Verify RED**

Run: `cd webui && npm test -- src/components/conversation/PublicProgress.test.tsx src/pages/ConversationPage.test.tsx`

Expected: current component ignores all supplied events and only renders its static sentence.

- [ ] **Step 3: Implement the minimal progress projection**

Select user-safe text from `payload.summary`, `payload.text`, or `payload.objective_summary`; keep the last accepted state plus distinct progress updates; render them in an `aria-live="polite"` region with the existing explicit stop action.

- [ ] **Step 4: Verify GREEN and accessibility behavior**

Run: `cd webui && npm test -- src/components/conversation/PublicProgress.test.tsx src/pages/ConversationPage.test.tsx`

Expected: all tests pass; only actual events or the honest queued fallback are visible.

- [ ] **Step 5: Commit**

Run: `git add webui/src/components/conversation/PublicProgress.tsx webui/src/components/conversation/PublicProgress.test.tsx webui/src/pages/ConversationPage.test.tsx webui/src/styles.css && git commit -m "fix(hr): show real agent execution progress"`

### Task 3: Preserve HR chat across position navigation

**Files:**
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspaceShell.tsx`
- Modify: `webui/src/styles.css`
- Test: `webui/src/workspaces/hr/HrWorkspacePage.test.tsx`
- Test: `webui/src/workspaces/hr/HrWorkspaceShell.test.tsx`

**Interfaces:**
- Consumes: the current HR chat route and the requested position route.
- Produces: a persistently mounted `DirectAgentWorkspace` and a `chatHref` pointing to the last active chat route.

- [ ] **Step 1: Write failing state-preservation tests**

Render the HR page, type a draft, switch props to positions and back, then assert the same textarea value remains. Repeat for an existing conversation ID and assert the chat navigation target is `/hr/conversations/<id>`.

- [ ] **Step 2: Verify RED**

Run: `cd webui && npm test -- src/workspaces/hr/HrWorkspacePage.test.tsx src/workspaces/hr/HrWorkspaceShell.test.tsx`

Expected: the direct workspace unmounts and the draft/chat route is lost.

- [ ] **Step 3: Keep chat mounted and hide inactive panels**

Render the chat panel for all HR routes, toggle it with `hidden`/scoped classes, render the position panel alongside it, retain the latest conversation path in HR workspace state, and pass it to `HrWorkspaceShell` as `chatHref`. Do not call the cancel API on route switches.

- [ ] **Step 4: Verify GREEN**

Run: `cd webui && npm test -- src/workspaces/hr/HrWorkspacePage.test.tsx src/workspaces/hr/HrWorkspaceShell.test.tsx src/pages/AgentUsePage.test.tsx`

Expected: draft, attachment component state, and conversation route survive both switches.

- [ ] **Step 5: Commit**

Run: `git add webui/src/workspaces/hr/HrWorkspacePage.tsx webui/src/workspaces/hr/HrWorkspaceShell.tsx webui/src/workspaces/hr/HrWorkspacePage.test.tsx webui/src/workspaces/hr/HrWorkspaceShell.test.tsx webui/src/styles.css && git commit -m "fix(hr): preserve chat state across workspace views"`

### Task 4: Make the HR composer usable

**Files:**
- Modify: `webui/src/workspaces/direct/DirectAgentWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.tsx`
- Modify: `webui/src/styles.css`
- Test: `webui/src/pages/AgentUsePage.test.tsx`

**Interfaces:**
- Consumes: existing `AttachmentUploader`, upload queue, ready attachments, and the `showTaskStarters` display flag.
- Produces: textarea → attachment controls/cards/status → footer/send DOM order with HR task starters disabled.

- [ ] **Step 1: Replace the obsolete starter test with failing usability tests**

Assert the HR workspace has no `.agent-task-starter`, has a 16px-capable scoped composer class, and places `.attachment-composer` after the textarea but before the action footer. Keep tests for paste/drop/selection, only-attachment send, retry, and limits.

- [ ] **Step 2: Verify RED**

Run: `cd webui && npm test -- src/pages/AgentUsePage.test.tsx`

Expected: starters still render and attachment controls precede the form.

- [ ] **Step 3: Implement the unified composer**

Add `showTaskStarters?: boolean` defaulting to true, pass false from HR, move the existing uploader/cards/error into the form below the textarea, remove the HR form label, and apply the approved height, contrast, background, focus, desktop, and mobile rules scoped to `data-agent-id="hr-bot"`.

- [ ] **Step 4: Verify GREEN and build**

Run: `cd webui && npm test -- src/pages/AgentUsePage.test.tsx && npm run build`

Expected: focused tests and production build pass.

- [ ] **Step 5: Commit**

Run: `git add webui/src/workspaces/direct/DirectAgentWorkspace.tsx webui/src/workspaces/hr/HrWorkspacePage.tsx webui/src/pages/AgentUsePage.test.tsx webui/src/styles.css && git commit -m "fix(hr): rebuild the primary chat composer"`

### Task 5: Import current official and historical positions

**Files:**
- Verify/execute: `/Users/neo/Developer/work/Orbbec-Agent-Team/services/hr-jd-sync`
- Verify/execute: `backend/app/hr/import_cli.py`
- Record: `docs/operations/2026-09-04-hr-workbench-p0-release.md`

**Interfaces:**
- Consumes: a healthy current `hr-jd-sync` snapshot, authorized HR owner IDs, fixed run IDs, and the existing conversation archive.
- Produces: non-empty idempotent `platform_hr.positions` plus evidence rows and reviewable drafts for ambiguity.

- [ ] **Step 1: Run read-only source and owner checks**

Record sync health, latest successful snapshot timestamp/version/count, current HR-authorized owners, and pre-import position/evidence/turn counts. Stop if the current snapshot cannot be proven healthy.

- [ ] **Step 2: Dry-run with fixed run IDs**

Run the existing `python -m app.hr.import_cli` dry-run separately for official and conversation sources. Record created, matched, draft, ambiguous, and rejected counts without changing production.

- [ ] **Step 3: Apply and prove idempotency**

Apply each approved dry-run with the same fixed run ID, rerun it once, and assert the second execution creates zero additional positions, drafts, evidence rows, conversations, or turns.

- [ ] **Step 4: Verify APIs and UI data**

Use the authenticated production API to assert the list is non-empty, an `official_site` item exists, and one detail opens. Confirm original conversation and turn counts are unchanged.

- [ ] **Step 5: Commit only the release record**

Run: `git add docs/operations/2026-09-04-hr-workbench-p0-release.md && git commit -m "docs(hr): record position import evidence"`

### Task 6: Full verification and disciplined Platform-only deployment

**Files:**
- Verify: all modified source and tests
- Update: `docs/operations/2026-09-04-hr-workbench-p0-release.md`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: tested master commit and a production release with complete disk, image, route, canary, and latency evidence.

- [ ] **Step 1: Run full verification**

Run backend focused relay/brain/HR suites, full WebUI tests, TypeScript/Vite production build, and `git diff --check`. Record exact pass counts and commit SHA.

- [ ] **Step 2: Run release gates**

Capture `df -B1 / /data`, current/rollback releases and images, release input size, FAE/Nginx fingerprints, and projected root free space. Stop on any user-defined gate failure or unexplained projected growth over 1 GB.

- [ ] **Step 3: Deploy only Platform/HR**

Use a unique exact `/data/staging/agent-platform/<deployment_id>/` target with trap cleanup. Do not copy persistent directories, restart unrelated services, push the feature branch, or modify Nginx.

- [ ] **Step 4: Run production acceptance**

Confirm `/hr/`, Office, and FAE HTTP status; relay canary; a real “介绍一下你自己” run; queue-to-lease timing; visible progress; final answer; chat/position state preservation; attachment upload/download; and populated position list/detail.

- [ ] **Step 5: Merge and push master**

After all evidence is green, merge the feature branch to local master with `--no-ff`, push master only, and record current plus two rollback versions/images, archive/prune actions, staging count zero, before/after disk, net growth, and unchanged FAE/Nginx fingerprints.
