# Agent 大脑统一对话工作区 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the separate new-conversation and history pages with one DeepSeek-style Agent 大脑 workspace containing a persistent Session sidebar and a continuous conversation pane.

**Architecture:** Add `BrainWorkspacePage` as the route-level container for both `/` and `/conversations/{id}`. It owns the paginated Conversation list and renders either the existing first-message composer or the existing continuous thread; `ConversationSidebar` renders desktop history and a mobile drawer. Existing encrypted Conversation APIs, SSE execution, Agent authorization, and backend data remain unchanged.

**Tech Stack:** React 18, TypeScript, Vite, Vitest, Testing Library DOM, CSS, existing FastAPI Conversation API.

## Global Constraints

- `/` immediately renders a usable Agent 大脑 workspace.
- Desktop shows Session history on the left and the selected continuous conversation on the right.
- Mobile uses an accessible overlay drawer for the same Session list.
- One Conversation is one Session list item regardless of Turn count.
- Follow-ups stay in the same workspace and Conversation.
- `/conversations/{id}` remains the selected Session deep link.
- `/conversations` redirects to `/`; no independent history page remains.
- Top navigation contains no “历史对话”.
- Sidebar failure cannot block the right pane.
- Authentication, CSRF, idempotency, owner scope, Agent authorization, SSE recovery, and hard-stale behavior remain intact.
- No backend migration or API change is allowed.
- FAE remains unchanged.

---

### Task 1: Unify navigation and shell behavior

**Files:**
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.brain.test.ts`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/AppShell.test.tsx`
- Modify: `webui/src/documentTitle.ts`

**Interfaces:**
- Produces: Conversation routes mapped to the `brain` product section.
- Produces: a full-height AppShell variant without the normal footer.

- [ ] **Step 1: Write failing tests**

```tsx
expect(routeSection({ name: "conversation", conversationId: "c-1" })).toBe("brain");
expect(container.textContent).not.toContain("历史对话");
expect(container.querySelector("main.page.is-brain-workspace")).not.toBeNull();
expect(container.querySelector("footer.site-foot")).toBeNull();
```

- [ ] **Step 2: Verify RED**

```bash
cd webui && npm test -- --run src/router.brain.test.ts src/AppShell.test.tsx
```

Expected: Conversation currently selects `conversations`, history remains in navigation, and the footer always renders.

- [ ] **Step 3: Implement the route and shell contract**

```tsx
if (route.name === "conversation" || route.name === "conversations") return "brain";
const brainWorkspace = route.name === "brain" || route.name === "conversation";
<main className={`page${brainWorkspace ? " is-brain-workspace" : ""}`}>{children}</main>
{!brainWorkspace && <footer className="site-foot"><span>Orbbec Agent Platform</span></footer>}
```

Remove “历史对话” from `USE_NAVIGATION`; keep Mission routes diagnostic-only.

- [ ] **Step 4: Verify and commit**

```bash
cd webui && npm test -- --run src/router.brain.test.ts src/AppShell.test.tsx src/documentTitle.test.ts
cd .. && git add webui/src && git commit -m "feat(ui): make conversations part of Agent Brain"
```

### Task 2: Build the persistent Session sidebar

**Files:**
- Create: `webui/src/components/conversation/ConversationSidebar.tsx`
- Create: `webui/src/components/conversation/ConversationSidebar.test.tsx`
- Create: `webui/src/pages/BrainWorkspacePage.tsx`
- Create: `webui/src/pages/BrainWorkspacePage.test.tsx`
- Modify: `webui/src/pages/BrainPage.tsx`
- Modify: `webui/src/pages/BrainPage.test.tsx`
- Delete: `webui/src/pages/ConversationsPage.tsx`
- Delete: `webui/src/pages/ConversationsPage.test.tsx`

**Interfaces:**
- Produces: `BrainWorkspacePage({ account, conversationId? })`.
- Produces: `ConversationSidebar` with selection, pagination, local retry, new Session, and mobile-close callbacks.
- Consumes: `listConversations(signal?, before?) -> Promise<ConversationPage>`.

- [ ] **Step 1: Write failing sidebar/workspace tests**

```tsx
expect(screen.getByRole("navigation", { name: "对话列表" })).toBeInTheDocument();
expect(screen.getByRole("link", { name: /当前会话/ })).toHaveAttribute("aria-current", "page");
await user.click(screen.getByRole("button", { name: "加载更早对话" }));
expect(list).toHaveBeenLastCalledWith(expect.any(AbortSignal), "cursor-1");
```

Also prove newest-first order, no duplicate IDs, new-conversation navigation, and that list failure leaves the right composer usable.

- [ ] **Step 2: Verify RED**

```bash
cd webui && npm test -- --run src/components/conversation/ConversationSidebar.test.tsx src/pages/BrainWorkspacePage.test.tsx src/pages/BrainPage.test.tsx
```

- [ ] **Step 3: Implement sidebar and workspace state**

`BrainWorkspacePage` loads the first page once, merges by `conversation_id`, sorts descending by `updated_at`, and exposes `upsertConversation(conversation)`. Selecting a row navigates to its deep link; “新对话” navigates to `/` without creating a record. The sidebar owns no authorization decision.

- [ ] **Step 4: Reduce BrainPage to the right-side blank composer**

Remove its own history request and “最近对话” section. Preserve input validation, retained Idempotency-Key, hard-stale behavior, examples, and first send. Add `onConversationCreated?: (conversation: Conversation) => void` and invoke it before deep-link navigation.

- [ ] **Step 5: Remove independent history and commit**

```bash
cd webui && npm test -- --run src/components/conversation/ConversationSidebar.test.tsx src/pages/BrainWorkspacePage.test.tsx src/pages/BrainPage.test.tsx
cd .. && git add -A webui/src && git commit -m "feat(ui): add Agent Brain session sidebar"
```

### Task 3: Keep every selected Session in one workspace

**Files:**
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/pages/ConversationPage.test.tsx`
- Modify: `webui/src/pages/BrainWorkspacePage.tsx`
- Modify: `webui/src/pages/BrainWorkspacePage.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/cloudMode.test.tsx`

**Interfaces:**
- Produces: `ConversationPage({ conversationId, account, client?, onConversationUpdated? })`.
- Produces: root and deep links rendered by the same workspace component.

- [ ] **Step 1: Write failing continuity tests**

Prove both routes render `BrainWorkspacePage`, `/conversations` replaces to `/`, the thread contains no history backlink, and a follow-up updates one existing Session rather than adding another.

- [ ] **Step 2: Verify RED**

```bash
cd webui && npm test -- --run src/pages/ConversationPage.test.tsx src/pages/BrainWorkspacePage.test.tsx src/cloudMode.test.tsx
```

- [ ] **Step 3: Integrate ConversationPage**

Remove its history backlink and redundant new-conversation button. Add `onConversationUpdated?: (conversation: Conversation) => void`; invoke it after detail load and successful follow-up. Preserve abort-on-switch, SSE replay, stop, feedback, execution disclosure, and the persistent composer.

- [ ] **Step 4: Route through BrainWorkspacePage**

```tsx
case "brain": return account ? <BrainWorkspacePage account={account} /> : <PendingPage title="Agent 大脑" description="请启用企业身份后使用。" />;
case "conversation": return account ? <BrainWorkspacePage account={account} conversationId={route.conversationId} /> : <PendingPage title="Agent 大脑" description="请启用企业身份后使用。" />;
case "conversations": return <LegacyRedirect to="/" />;
```

- [ ] **Step 5: Verify and commit**

```bash
cd webui && npm test -- --run src/pages/ConversationPage.test.tsx src/pages/BrainWorkspacePage.test.tsx src/cloudMode.test.tsx
cd .. && git add webui/src && git commit -m "feat(ui): keep conversations in one Agent Brain workspace"
```

### Task 4: Add desktop continuity and mobile drawer

**Files:**
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`
- Modify: `webui/src/components/conversation/ConversationSidebar.tsx`
- Modify: `webui/src/components/conversation/ConversationSidebar.test.tsx`
- Modify: `webui/src/pages/BrainWorkspacePage.tsx`
- Modify: `webui/src/pages/BrainWorkspacePage.test.tsx`

**Interfaces:**
- Produces: independent sidebar/thread scrolling on desktop.
- Produces: accessible mobile drawer with backdrop, Escape close, and selection close.

- [ ] **Step 1: Write failing drawer/layout tests**

```tsx
expect(screen.getByRole("button", { name: "打开对话列表" })).toHaveAttribute("aria-expanded", "false");
await user.click(screen.getByRole("button", { name: "打开对话列表" }));
expect(screen.getByRole("dialog", { name: "对话列表" })).toHaveClass("is-open");
await user.keyboard("{Escape}");
```

Assert CSS includes a 280px two-column grid, independent overflow, viewport height, drawer media query, backdrop, and safe-area composer padding.

- [ ] **Step 2: Verify RED**

```bash
cd webui && npm test -- --run src/components/conversation/ConversationSidebar.test.tsx src/pages/BrainWorkspacePage.test.tsx src/styles.test.ts
```

- [ ] **Step 3: Implement responsive behavior**

```css
.brain-workspace { display:grid; grid-template-columns:280px minmax(0,1fr); height:calc(100dvh - var(--topbar-height)); overflow:hidden; }
.conversation-sidebar { overflow-y:auto; border-right:1px solid var(--line); }
.brain-workspace-main { min-width:0; overflow-y:auto; }
```

At 720px, render the sidebar as a fixed drawer with backdrop. Escape, close, and selection close only the drawer and do not reset the selected Session.

- [ ] **Step 4: Run full frontend verification and commit**

```bash
cd webui && npm test -- --run && npm run build
cd .. && git diff --check
git add webui/src && git commit -m "feat(ui): finish responsive Agent Brain workspace"
```

### Task 5: Review, deploy, and verify production

**Files:**
- Modify only if review finds a defect: files from Tasks 1–4.

**Interfaces:**
- Produces: exact reviewed SHA deployed with Agent Brain enabled.
- Preserves: all eight FAE invariance values and private Platform listeners.

- [ ] **Step 1: Run release gates**

```bash
deploy/cloud/acceptance.sh local
```

Expected: all backend/frontend tests and build pass with `CLOUD_PLATFORM_LOCAL_GATE_OK`.

- [ ] **Step 2: Review full diff**

Reject duplicate Session entries, stale SSE after switching, any history page/navigation, weakened authorization, or expanded FAE scope.

- [ ] **Step 3: Push exact SHA and deploy**

Push `HEAD:master`, verify `HEAD == origin/master`, capture Platform/FAE baseline, and run the existing atomic deploy script. Require `CLOUD_PLATFORM_DEPLOY_OK release=<sha> mode=dingtalk`.

- [ ] **Step 4: Enable and verify**

Use the existing action lock and rollback-safe feature transaction to set only `PLATFORM_AGENT_BRAIN_ENABLED=1`, recreating only `platform-api` and `platform-loopback`. Require five healthy Platform services, correct release/flag, absent action lock, public login behavior, unauthenticated Conversation `401`, private PostgreSQL/8080 listeners, and unchanged FAE identity/image/start/restart/config/mount/domain/IP values.
