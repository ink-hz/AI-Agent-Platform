# HR Conversation-first Workspace Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `/hr/*` into an independent, Office-inspired HR application whose default and primary experience is AI conversation, with positions retained as a secondary working context.

**Architecture:** Add an HR-specific application shell inside the Platform SPA and make AppShell suppress shared Platform chrome for every HR route. Introduce a dedicated `/hr/positions` index route while changing `/hr/` to render the existing direct conversation workspace. Reuse all proven conversation, attachment, feedback and HR domain components; change composition, navigation, copy and CSS rather than rebuilding data paths.

**Tech Stack:** React 18, TypeScript, Vite, Vitest, Testing Library, existing Platform CSS and router.

## Global Constraints

- Use `HR 智能工作台` as the sole HR product name; do not use `岗位智能工作台`.
- `/hr/` must be the default new-conversation experience.
- `/hr/positions` must expose the existing official, internal and draft position data.
- Do not change backend contracts, persistent data, `/office/*`, FAE, VOC, Marketing, shared Nginx or deployment files.
- Preserve `backend/.venv` without adding, editing or deleting it.
- Every behavior change follows red-green-refactor.
- Validate responsive behavior at 375px before publishing.

---

### Task 1: Isolate HR routes from Platform chrome

**Files:**
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/AppShell.brain.test.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Consumes: existing `Route` union and `route.name` values.
- Produces: `is-hr-workspace-shell` and `is-hr-workspace` class boundaries for every HR route.

- [ ] **Step 1: Write failing AppShell tests**

Add assertions that an HR route places `is-hr-workspace-shell` on `.app`, `is-hr-workspace` on `.page`, and keeps HR separate from the generic `is-brain-workspace-shell` identity.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `npm test -- --run src/AppShell.brain.test.tsx src/styles.test.ts`

Expected: failures because the HR-specific classes and CSS rules do not exist.

- [ ] **Step 3: Implement the route boundary**

Create an `hrWorkspace` predicate covering `hr`, `hr-chat`, `hr-positions`, `hr-position`, `hr-position-section`, `hr-position-conversation`, and `hr-conversation`. Apply its classes independently of `brainWorkspace`. Add CSS that makes the shell `100dvh`, hides `.topbar` and `.site-foot` inside it, and gives the page full width with zero outer padding.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `npm test -- --run src/AppShell.brain.test.tsx src/styles.test.ts`

Expected: all selected tests pass.

### Task 2: Make conversation the HR entry and positions a dedicated route

**Files:**
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/App.hrPositionSection.test.tsx`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`
- Modify: `webui/src/accessEventReporter.tsx`
- Modify: `webui/src/accessEventReporter.test.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.test.tsx`

**Interfaces:**
- Produces: route `{ name: "hr-positions" }` at `/hr/positions`.
- Produces: `HrWorkspacePage` prop `positions?: boolean`.
- Preserves: existing position detail and conversation URLs.

- [ ] **Step 1: Write failing router and workspace tests**

Assert that `/hr/positions` parses and serializes as `hr-positions`, `/hr/` renders `DirectAgentWorkspace`, and `positions` renders `HrPositionIndex`. Assert that the document title is `岗位 · HR 智能工作台` and access reporting distinguishes `hr.chat` from `hr.positions`.

- [ ] **Step 2: Run tests and confirm RED**

Run: `npm test -- --run src/router.test.ts src/workspaces/hr/HrWorkspacePage.test.tsx src/App.hrPositionSection.test.tsx src/documentTitle.test.tsx src/accessEventReporter.test.tsx`

Expected: failures for the missing `hr-positions` route and reversed default page.

- [ ] **Step 3: Implement minimal routing and composition**

Add `hr-positions` to the route union, parser, serializer, App route switch, document titles and access events. Change `HrWorkspacePage` so the default branch renders HR direct chat, while `positions` renders `HrPositionIndex`. Keep `/hr/chat` compatible with the direct-chat branch.

- [ ] **Step 4: Run tests and confirm GREEN**

Run the same command from Step 2. Expected: all selected tests pass.

### Task 3: Add the independent HR application shell and conversation-first copy

**Files:**
- Create: `webui/src/workspaces/hr/HrWorkspaceShell.tsx`
- Create: `webui/src/workspaces/hr/HrWorkspaceShell.test.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.tsx`
- Modify: `webui/src/workspaces/direct/DirectAgentWorkspace.tsx`
- Modify: `webui/src/pages/AgentUsePage.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Produces: `HrWorkspaceShell({ account, current, children })` where `current` is `"chat" | "positions"`.
- Produces: optional `DirectAgentWorkspace` props `showWorkspaceBackLink?: boolean` and `newConversationHeader?: ReactNode`.

- [ ] **Step 1: Write failing shell and conversation tests**

Assert that the shell shows `HR 智能工作台`, links to `/hr/` and `/hr/positions`, displays the account name, and provides a light Platform return link. Assert the HR new-conversation view does not render `返回专业 Agent` and renders `今天想推进哪项招聘工作？`.

- [ ] **Step 2: Run tests and confirm RED**

Run: `npm test -- --run src/workspaces/hr/HrWorkspaceShell.test.tsx src/workspaces/hr/HrWorkspacePage.test.tsx src/pages/AgentUsePage.test.tsx`

Expected: failures because the shell and customization props do not exist.

- [ ] **Step 3: Implement the shell and compose existing features**

Build the shell with a compact HR mark, two working navigation links, the account chip and a Platform return link. Wrap chat, position index and position detail branches. Add the two optional direct-workspace props with existing behavior as defaults, then pass a recruitment-focused welcome block from HR.

- [ ] **Step 4: Run tests and confirm GREEN**

Run the same command from Step 2. Expected: all selected tests pass.

### Task 4: Apply the Office-inspired responsive visual system

**Files:**
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`
- Modify: `webui/src/workspaces/hr/HrPositionIndex.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionIndex.test.tsx`

**Interfaces:**
- Consumes: `.hr-workspace-shell`, `.hr-workspace-topbar`, existing `.agent-use-workspace[data-agent-id="hr-bot"]`, and position class names.
- Produces: desktop and 375px responsive layouts without changing data behavior.

- [ ] **Step 1: Write failing structure and CSS contract tests**

Assert that the position page title is `岗位`, its primary action remains `用对话新建岗位`, no `岗位智能工作台` text exists, the HR topbar uses a constrained glass surface, and the 700px media block collapses the position list and HR navigation safely.

- [ ] **Step 2: Run tests and confirm RED**

Run: `npm test -- --run src/workspaces/hr/HrPositionIndex.test.tsx src/styles.test.ts`

Expected: failures for old title and missing HR visual rules.

- [ ] **Step 3: Implement visual styles and reduced position hierarchy**

Replace the green HR override with scoped blue/teal workspace variables, a calm gradient canvas, glass topbar, stable white conversation plane, low-noise messages and a single emphasized composer. Reduce the position Hero and metrics, switch position cards to a compact list, and add 1000px/700px responsive rules. Do not copy Office assets or add dependencies.

- [ ] **Step 4: Run tests and confirm GREEN**

Run the same command from Step 2. Expected: all selected tests pass.

### Task 5: Full verification and integration readiness

**Files:**
- Test only; no production changes expected.

**Interfaces:**
- Verifies all existing HR and conversation capabilities remain intact.

- [ ] **Step 1: Run HR and shared conversation suites**

Run: `npm test -- --run src/workspaces/hr src/pages/AgentUsePage.test.tsx src/pages/ConversationPage.test.tsx src/components/conversation src/router.test.ts src/AppShell.brain.test.tsx src/documentTitle.test.tsx src/accessEventReporter.test.tsx src/styles.test.ts`

Expected: all tests pass with no unhandled errors.

- [ ] **Step 2: Run full WebUI tests**

Run: `npm test`

Expected: all WebUI tests pass.

- [ ] **Step 3: Build production assets**

Run: `npm run build`

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 4: Inspect desktop and 375px UI**

Open `/hr/`, one historical conversation, `/hr/positions`, and one position detail. Confirm conversation-first entry, independent shell, attachment controls, downloads, feedback detail, no horizontal scroll, and no occluded composer. If browser control is unavailable, record visual QA as blocked and do not publish.

- [ ] **Step 5: Commit the isolated feature**

Stage only the design, plan, HR frontend, router and related test files. Verify `backend/.venv` remains untracked. Commit with `feat(hr): make workspace conversation first`.
