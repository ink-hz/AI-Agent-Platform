# HR Position Chat-First Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn one HR position into a wide, persistent, chat-first workspace where conversations, real task progress, attachments, candidates, and downloadable results remain available without permanent sidebars or dashboard clutter.

**Architecture:** Keep the existing Position, Conversation, Attachment, Candidate, and HR R1.2 services as the sources of truth. Add focused presentation hooks to the shared conversation surface, split the position header/task menu/details drawer into small HR components, and make `HrPositionWorkspace` coordinate them while preserving the current position and conversation scope. The task API already creates a scoped conversation when `conversation_id` is absent; the web parser will retain that returned binding so the UI can navigate to the real task conversation.

**Tech Stack:** React 19, TypeScript 5.6, Vite 7, Vitest 3 with jsdom, existing FastAPI HR R1.2 JSON contract, existing CSS design tokens.

## Global Constraints

- The position page is chat-first: the first screen contains only the compact position bar, current conversation, and composer.
- Desktop conversation content and composer use one `max-width: 1180px`; narrow screens use the available width.
- Do not add ATS workflow, approvals, scheduling, offers, onboarding, dashboards, funnels, or statistic cards.
- Do not expose Mission, Run, Trace, Adapter, database state, internal status codes, or authorization counts.
- Do not duplicate or replace the existing Conversation, attachment, task-recovery, download, Position, or Candidate services.
- The five position tasks execute immediately; they never populate the composer as fake prompt shortcuts.
- Attachments remain current-turn inputs unless the user explicitly promotes them to position materials.
- Empty business collections say `暂无`; only request or parsing failures say `暂时无法读取` and offer a local retry.
- A drawer-tab failure must not disable or replace the main conversation.
- Keep owner scoping, cross-position conversation rejection, hard-stale read-only behavior, idempotent retries, streaming, cancellation, feedback, preview, and download behavior intact.
- Only change the HR workspace web UI and the minimum Platform HR task projection needed by this layout; do not change Nginx, FAE, VOC, administrative apps, or other bots.
- Never add, remove, or commit `backend/.venv`.

---

### Task 1: Preserve the task-created conversation binding

**Files:**
- Modify: `webui/src/hrR12Types.ts`
- Modify: `webui/src/hrR12Api.ts`
- Test: `webui/src/hrR12Api.test.ts`

**Interfaces:**
- Consumes: the existing server response keys `conversation_id` and `turn_id` returned by `POST /api/hr/positions/{position_id}/tasks`.
- Produces: backward-compatible `HrTaskRecord.conversationId?: string | null` and `HrTaskRecord.turnId?: string | null`, validated as UUIDs when present. The parser always populates both fields for HTTP responses; optionality keeps existing injected test clients and non-HTTP adapters source-compatible.

- [ ] **Step 1: Write the failing parser test**

Add a task response fixture with both binding identifiers and assert that the parsed record keeps them:

```tsx
it("keeps the conversation and turn created for a position task", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    task_id: "99999999-9999-4999-8999-999999999999",
    task_kind: "jd",
    status: "accepted",
    error: null,
    conversation_id: "22222222-2222-4222-8222-222222222222",
    turn_id: "33333333-3333-4333-8333-333333333333",
    candidate_id: null,
    position_candidate_id: null,
  }), { status: 202, headers: { "Content-Type": "application/json" } })));

  const task = await createHrR12Api("csrf").startTask(
    POSITION_ID, "jd", REQUEST_ID, { materialIds: [] },
  );

  expect(task.conversationId).toBe("22222222-2222-4222-8222-222222222222");
  expect(task.turnId).toBe("33333333-3333-4333-8333-333333333333");
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `cd webui && npx vitest run src/hrR12Api.test.ts`

Expected: FAIL because `HrTaskRecord` and `task()` do not expose `conversationId` or `turnId`.

- [ ] **Step 3: Extend the exact web contract**

Add the fields and parse a complete or completely absent binding; reject a half binding:

```ts
export interface HrTaskRecord {
  taskId: string;
  status: "accepted" | "running" | "completed" | "failed";
  taskKind: HrTaskKind;
  error: string | null;
  conversationId?: string | null;
  turnId?: string | null;
  positionCandidateId?: string | null;
  candidateId?: string | null;
}
```

```ts
const conversationId = raw.conversation_id == null ? null : identifier(raw.conversation_id);
const turnId = raw.turn_id == null ? null : identifier(raw.turn_id);
if ((conversationId === null) !== (turnId === null)) {
  throw new Error("HR R1.2 task binding invalid");
}
return {
  taskId: string(raw.task_id),
  status: raw.status as HrTaskRecord["status"],
  taskKind: raw.task_kind as HrTaskKind,
  error: raw.error === undefined || raw.error === null ? null : string(raw.error),
  conversationId,
  turnId,
  positionCandidateId,
  candidateId,
};
```

- [ ] **Step 4: Run parser tests and typecheck**

Run: `cd webui && npx vitest run src/hrR12Api.test.ts && npx tsc -b --pretty false`

Expected: all parser tests PASS and TypeScript reports no errors; existing injected test clients remain source-compatible because the two new fields are optional at the interface boundary.

- [ ] **Step 5: Commit the contract fix**

```bash
git add webui/src/hrR12Types.ts webui/src/hrR12Api.ts webui/src/hrR12Api.test.ts
git commit -m "fix(hr): retain task conversation binding"
```

### Task 2: Add focused conversation presentation without changing standard Agents

**Files:**
- Modify: `webui/src/workspaces/direct/DirectAgentWorkspace.tsx`
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/pages/AgentUsePage.test.tsx`
- Test: `webui/src/pages/ConversationPage.test.tsx`

**Interfaces:**
- Consumes: the existing `ConversationSidebar`, `SessionMaterialsDrawer`, new-conversation form, and conversation thread.
- Produces: `DirectAgentWorkspaceProps.layout?: "standard" | "focused"`, `DirectAgentWorkspaceProps.composerTools?: ReactNode`, `DirectAgentWorkspaceProps.threadSupplement?: ReactNode`, and matching `ConversationPage` presentation slots.

- [ ] **Step 1: Write failing focused-layout tests**

Add assertions that focused mode marks the workspace, exposes an overlay history trigger on desktop, leaves the conversation and composer mounted, and does not render a permanent materials column:

```tsx
await act(async () => root.render(<DirectAgentWorkspace
  account={account}
  agentId="hr-bot"
  conversationId={result.conversation.conversation_id}
  conversationPath={(id) => `/hr/conversations/${id}`}
  historyClient={historyClient}
  layout="focused"
  loadCatalog={vi.fn().mockResolvedValue([attachmentCard])}
  composerTools={<button type="button">岗位任务</button>}
  threadSupplement={<section aria-label="岗位任务进度">执行中</section>}
/>));

expect(container.querySelector(".agent-use-workspace")?.classList).toContain("is-focused");
expect(container.querySelector('[aria-label="打开对话记录"]')).not.toBeNull();
expect(container.querySelector(".session-materials-drawer")).toBeNull();
expect(container.textContent).toContain("岗位任务");
expect(container.textContent).toContain("执行中");
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `cd webui && npx vitest run src/pages/AgentUsePage.test.tsx src/pages/ConversationPage.test.tsx`

Expected: FAIL because focused presentation and composer/thread slots do not exist.

- [ ] **Step 3: Add narrow presentation hooks**

Extend the shared props without changing their defaults:

```ts
export interface DirectAgentWorkspaceProps {
  layout?: "standard" | "focused";
  composerTools?: ReactNode;
  threadSupplement?: ReactNode;
}
```

Use `layout = "standard"`. Add `is-focused` only for focused mode. In focused mode, keep `ConversationSidebar` mounted for state preservation but place it behind `mobileOpen`; label the trigger `打开对话记录`, render the backdrop whenever the overlay is open, and close it after selecting a conversation. Pass the two slots to `ConversationPage` and render `composerTools` in the new-conversation composer immediately before the send action.

Extend `ConversationPage` with:

```ts
composerTools?: ReactNode;
threadSupplement?: ReactNode;
materialsPresentation?: "sidebar" | "hidden";
```

Render `threadSupplement` after `ConversationMessages`, render `composerTools` through `ConversationComposer`, and return only `conversationContent` when `materialsPresentation === "hidden"`. Keep defaults `materialsPresentation = "sidebar"` and empty slots so every non-HR Agent remains byte-for-byte equivalent in behavior.

- [ ] **Step 4: Run shared workspace regressions**

Run: `cd webui && npx vitest run src/pages/AgentUsePage.test.tsx src/pages/ConversationPage.test.tsx`

Expected: PASS, including the existing attachment, Enter-to-send, stream, retry, feedback, preview, and download tests.

- [ ] **Step 5: Commit focused presentation**

```bash
git add webui/src/workspaces/direct/DirectAgentWorkspace.tsx webui/src/pages/ConversationPage.tsx webui/src/pages/AgentUsePage.test.tsx webui/src/pages/ConversationPage.test.tsx
git commit -m "feat(hr): add focused conversation presentation"
```

### Task 3: Build the compact position header and accessible task menu

**Files:**
- Create: `webui/src/workspaces/hr/HrPositionHeader.tsx`
- Create: `webui/src/workspaces/hr/HrPositionHeader.test.tsx`
- Create: `webui/src/workspaces/hr/HrPositionTaskMenu.tsx`
- Create: `webui/src/workspaces/hr/HrPositionTaskMenu.test.tsx`

**Interfaces:**
- Consumes: `HrPositionDetail`, hard-stale read-only state, and `HrPositionTaskKind`.
- Produces: `HrPositionHeader({ detail, readOnly, onOpenDetails, onNewConversation })` and `HrPositionTaskMenu({ disabled, selectedMaterialIds, materials, onSelectedMaterialIdsChange, onStart })`.

- [ ] **Step 1: Write failing component tests**

Cover the exact visible hierarchy and direct task behavior:

```tsx
expect(container.textContent).toContain("3D 打印高级结构工程师");
expect(container.textContent).toContain("研发 · 深圳 · 中山");
expect(container.textContent).toContain("进行中");
expect(container.textContent).not.toContain("J11014");
expect(container.textContent).not.toContain("2 个对话");
expect(container.textContent).not.toContain("1 份岗位材料");
expect(container.textContent).not.toContain("3 个生成结果");
```

```tsx
await act(async () => screen.getByRole("button", { name: "岗位任务" }).click());
await act(async () => screen.getByRole("menuitem", { name: /生成岗位说明/ }).click());
expect(onStart).toHaveBeenCalledWith("jd");
expect(onComposerChange).not.toHaveBeenCalled();
expect(screen.queryByRole("menu")).toBeNull();
```

Also assert `Escape` closes the menu and restores focus to `岗位任务`, and hard-stale mode disables both task start and new conversation.

- [ ] **Step 2: Run component tests and verify RED**

Run: `cd webui && npx vitest run src/workspaces/hr/HrPositionHeader.test.tsx src/workspaces/hr/HrPositionTaskMenu.test.tsx`

Expected: FAIL because both components are absent.

- [ ] **Step 3: Implement the compact header**

The component must render this stable structure:

```tsx
<header className="hr-position-bar">
  <PlatformLink href="/hr/positions">← 岗位</PlatformLink>
  <div className="hr-position-bar-copy">
    <h1>{detail.title}</h1>
    <p>{[detail.department, ...detail.locations].filter(Boolean).join(" · ") || "岗位信息待完善"}</p>
  </div>
  <span className="hr-position-status-pill">{statusLabel(detail)}</span>
  <div className="hr-position-bar-actions">
    <button type="button" onClick={onOpenDetails}>岗位资料</button>
    <button type="button" disabled={readOnly} onClick={onNewConversation}>＋ 新对话</button>
  </div>
</header>
```

Do not render official job ID or count metrics in this header.

- [ ] **Step 4: Implement the task menu**

Use one button with `aria-haspopup="menu"`, a `role="menu"` popover, five `role="menuitem"` buttons, document-level outside-click handling, `Escape`, and opener focus restoration. Use these labels/descriptions:

```ts
const POSITION_TASKS = [
  ["jd", "生成岗位说明（JD）", "形成可修改、可下载的岗位说明"],
  ["jr", "梳理岗位要求（JR）", "整理职责、能力和任职要求"],
  ["talent_profile", "生成人才画像", "形成目标候选人的能力组合"],
  ["sourcing_strategy", "生成搜寻策略", "形成渠道、关键词和目标公司建议"],
  ["position_interview_plan", "生成面试方案", "形成结构化问题与评价重点"],
] as const satisfies ReadonlyArray<readonly [HrPositionTaskKind, string, string]>;
```

Show the available position materials as unchecked checkboxes in a secondary `本次任务使用` section. Selection changes only `selectedMaterialIds`; clicking a menu item calls `onStart(kind)` directly.

- [ ] **Step 5: Run component tests and commit**

Run: `cd webui && npx vitest run src/workspaces/hr/HrPositionHeader.test.tsx src/workspaces/hr/HrPositionTaskMenu.test.tsx`

Expected: PASS.

```bash
git add webui/src/workspaces/hr/HrPositionHeader.tsx webui/src/workspaces/hr/HrPositionHeader.test.tsx webui/src/workspaces/hr/HrPositionTaskMenu.tsx webui/src/workspaces/hr/HrPositionTaskMenu.test.tsx
git commit -m "feat(hr): add compact position controls"
```

### Task 4: Move position intelligence into one lazy details drawer

**Files:**
- Create: `webui/src/workspaces/hr/HrPositionDetailsDrawer.tsx`
- Create: `webui/src/workspaces/hr/HrPositionDetailsDrawer.test.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionContextPanel.tsx`
- Modify: `webui/src/workspaces/hr/HrCandidateWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionResourcesPanel.tsx`

**Interfaces:**
- Consumes: `HrPositionDetail`, `HrR12Api`, current confirmed context callbacks, refresh generations, and read-only state.
- Produces: `HrPositionDetailsDrawer({ open, initialTab, detail, api, readOnly, onClose, onConfirmed, contextRefreshGeneration, resourceRefreshGeneration })` with tabs `position | candidates | resources`.

- [ ] **Step 1: Write failing drawer and local-state tests**

Test that a closed drawer makes no candidate request, opening defaults to 岗位信息, selecting 候选人 triggers candidate loading, selecting 材料与成果 renders separate normal empty states, and `Escape` closes and restores focus:

```tsx
expect(api.positionCandidates).not.toHaveBeenCalled();
await act(async () => opener.click());
expect(screen.getByRole("dialog", { name: "岗位资料" })).not.toBeNull();
expect(screen.getByRole("tab", { name: "岗位信息" }).getAttribute("aria-selected")).toBe("true");
await act(async () => screen.getByRole("tab", { name: "候选人" }).click());
expect(api.positionCandidates).toHaveBeenCalledWith(POSITION_ID, expect.any(AbortSignal));
```

Add focused panel tests proving an empty successful response displays `暂无候选人`, `暂无岗位材料`, or `暂无生成成果`, while a rejected request displays `暂时无法读取` and a local `重试` button without removing the last successful list.

- [ ] **Step 2: Run drawer/panel tests and verify RED**

Run: `cd webui && npx vitest run src/workspaces/hr/HrPositionDetailsDrawer.test.tsx src/workspaces/hr/HrPositionContextPanel.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx src/workspaces/hr/HrPositionResourcesPanel.test.tsx`

Expected: FAIL on the missing drawer and any panel that clears valid data or labels empty data as unavailable.

- [ ] **Step 3: Implement the unified drawer**

Render backdrop plus a focus-trapped dialog only when `open` is true. The `position` tab must show the official facts and the business label `当前岗位理解` before the existing context panel:

```tsx
const tabs = [
  ["position", "岗位信息"],
  ["candidates", "候选人"],
  ["resources", "材料与成果"],
] as const;
```

Lazily mount each business panel on its first selection, then keep visited panels mounted with `hidden` so their last valid in-memory data survives tab switches. In each child panel, set new data only on fulfilled requests, keep prior data during refresh, and replace only that tab's status footer on rejection. Reuse `trapDialogFocus` and restore focus to the opener after close.

- [ ] **Step 4: Normalize empty and failure copy**

Use these exact visible states:

```tsx
items.length === 0 && state === "ready" ? <p>暂无候选人</p> : null
materials.length === 0 && state === "ready" ? <p>暂无岗位材料</p> : null
artifacts.length === 0 && state === "ready" ? <p>暂无生成成果</p> : null
state === "error" ? <p role="alert">暂时无法读取。<button type="button" onClick={retry}>重试</button></p> : null
```

Do not emit error copy from unopened tabs.

- [ ] **Step 5: Run drawer/panel tests and commit**

Run: `cd webui && npx vitest run src/workspaces/hr/HrPositionDetailsDrawer.test.tsx src/workspaces/hr/HrPositionContextPanel.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx src/workspaces/hr/HrPositionResourcesPanel.test.tsx`

Expected: PASS.

```bash
git add webui/src/workspaces/hr/HrPositionDetailsDrawer.tsx webui/src/workspaces/hr/HrPositionDetailsDrawer.test.tsx webui/src/workspaces/hr/HrPositionContextPanel.tsx webui/src/workspaces/hr/HrPositionContextPanel.test.tsx webui/src/workspaces/hr/HrCandidateWorkspace.tsx webui/src/workspaces/hr/HrCandidateWorkspace.test.tsx webui/src/workspaces/hr/HrPositionResourcesPanel.tsx webui/src/workspaces/hr/HrPositionResourcesPanel.test.tsx
git commit -m "feat(hr): add position details drawer"
```

### Task 5: Assemble the chat-first position workspace and inline real task state

**Files:**
- Create: `webui/src/workspaces/hr/HrPositionTaskActivity.tsx`
- Create: `webui/src/workspaces/hr/HrPositionTaskActivity.test.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionWorkspace.test.tsx`

**Interfaces:**
- Consumes: `HrPositionHeader`, `HrPositionTaskMenu`, `HrPositionDetailsDrawer`, focused `DirectAgentWorkspace`, and task records with conversation bindings from Task 1.
- Produces: one chat-first page with overlay drawers and `HrPositionTaskActivity`, rendered inside the conversation flow through `threadSupplement`.

- [ ] **Step 1: Replace dashboard-oriented tests with chat-first assertions**

Assert all removed persistent chrome is absent and all primary controls remain:

```tsx
expect(container.querySelector(".hr-position-context-metrics")).toBeNull();
expect(container.querySelector(".hr-position-sections")).toBeNull();
expect(container.querySelector(".hr-position-quick-tasks")).toBeNull();
expect(container.querySelector(".hr-task-recovery")).toBeNull();
expect(container.querySelector(".session-materials-drawer")).toBeNull();
expect(container.querySelector('[aria-label="打开对话记录"]')).not.toBeNull();
expect(screen.getByRole("button", { name: "岗位资料" })).not.toBeNull();
expect(screen.getByRole("button", { name: "岗位任务" })).not.toBeNull();
expect(container.querySelector("textarea")).not.toBeNull();
```

Add a direct execution test for no current conversation:

```tsx
deps.r12Api.startTask.mockResolvedValue({
  taskId: TASK_ID,
  status: "accepted",
  taskKind: "jd",
  error: null,
  conversationId: ACTIVE_ID,
  turnId: TURN_ID,
});
await act(async () => screen.getByRole("button", { name: "岗位任务" }).click());
await act(async () => screen.getByRole("menuitem", { name: /生成岗位说明/ }).click());
expect(deps.r12Api.startTask).toHaveBeenCalledWith(
  POSITION_ID, "jd", expect.any(String),
  expect.objectContaining({ conversationId: undefined, materialIds: [] }),
  expect.any(AbortSignal),
);
expect(deps.onOpenConversation).toHaveBeenCalledWith(
  `/hr/positions/${POSITION_ID}/conversations/${ACTIVE_ID}`,
);
```

Add tests for active, failed, retry, and completed status cards inside `.conversation-flow-supplement`, and prove task-state recovery failure is a local card that leaves the textarea enabled.

```tsx
const records: HrTaskRecord[] = [{
  taskId: TASK_ID,
  taskKind: "position_interview_plan",
  status: "failed",
  error: "模型服务暂时不可用",
  conversationId: ACTIVE_ID,
  turnId: TURN_ID,
}];
await act(async () => root.render(<HrPositionTaskActivity
  records={records}
  recoveryState="ready"
  onRetryRecovery={vi.fn()}
  onRetryTask={onRetryTask}
/>));
expect(container.textContent).toContain("面试方案：执行失败");
expect(container.textContent).toContain("模型服务暂时不可用");
await act(async () => screen.getByRole("button", { name: "重试本任务" }).click());
expect(onRetryTask).toHaveBeenCalledWith("position_interview_plan");
```

- [ ] **Step 2: Run the position workspace test and verify RED**

Run: `cd webui && npx vitest run src/workspaces/hr/HrPositionWorkspace.test.tsx`

Expected: FAIL because the old header, tabs, five buttons, permanent task bar, and side columns still render.

- [ ] **Step 3: Refactor `HrPositionWorkspace` into an orchestrator**

Keep the existing position/conversation scoping, material mutation, context recovery, idempotency, polling, and artifact refresh functions. Replace `header`, `navigation`, `quickTasks`, `taskRecovery`, and `sectionView` with:

```tsx
return <main className="hr-position-workspace is-chat-first" data-position-id={positionId}>
  <HrPositionHeader
    detail={detail}
    readOnly={account.hard_stale_read_only}
    onNewConversation={() => onOpenConversation(`/hr/positions/${encodeURIComponent(positionId)}`)}
    onOpenDetails={() => setDetailsOpen(true)}
  />
  {scopeNotice}
  <section className="hr-position-chat-surface">
    <DirectAgentWorkspace
    account={account}
    agentId="hr-bot"
    autoFocusComposer
    conversationClient={conversationClient}
    conversationId={selectedConversationId}
    conversationPath={(id) => positionConversationPath(positionId, id)}
    createSubmission={createSubmission}
    historyClient={historyClient}
    layout="focused"
    loadCatalog={loadCatalog}
    newConversationHeader={<section className="hr-position-conversation-welcome">
      <span>岗位对话</span>
      <h2>围绕这个岗位，直接开始协作</h2>
      <p>当前岗位、明确选择的材料和后续生成结果会保留在同一上下文中。</p>
    </section>}
    newConversationScope={{ positionId }}
    onOpenConversation={onOpenConversation}
    onPositionMaterialChange={account.hard_stale_read_only ? undefined : changePositionMaterial}
    positionMaterialIds={promotedMaterialIds}
    positionArtifactAttachmentIds={detail.artifactAttachmentIds}
    showTaskStarters={false}
    showWorkspaceBackLink={false}
    workspaceLabel="岗位对话"
    workspaceMark="HR"
    workspaceRootPath={`/hr/positions/${encodeURIComponent(positionId)}`}
    composerTools={<HrPositionTaskMenu
      disabled={account.hard_stale_read_only}
      materials={availableMaterials}
      selectedMaterialIds={turnMaterialIds}
      onSelectedMaterialIdsChange={setTurnMaterialIds}
      onStart={(kind) => void startPositionTask(kind)}
    />}
    threadSupplement={<HrPositionTaskActivity
      records={activeTasks}
      recoveryState={taskState}
      onRetryRecovery={() => setTaskRefresh((value) => value + 1)}
      onRetryTask={(kind) => void startPositionTask(kind)}
    />}
    />
  </section>
  <HrPositionDetailsDrawer
    api={r12}
    detail={detail}
    open={detailsOpen}
    readOnly={account.hard_stale_read_only}
    onClose={() => setDetailsOpen(false)}
    onConfirmed={setCurrentContext}
    contextRefreshGeneration={contextRefreshGeneration}
    resourceRefreshGeneration={resourceRefreshGeneration}
  />
</main>;
```

`startPositionTask` must use the retained mutation request, close the menu through its own successful callback, preserve selected materials after uncertain or resolved failure, clear them after accepted/completed start, add the returned record immediately, and navigate when `started.conversationId` differs from the current conversation.

- [ ] **Step 4: Render truthful task activity inside the conversation**

`HrPositionTaskActivity` must render nothing when recovery is ready and there are no records. For records, use exact business labels and status text:

```ts
const taskStatusLabel = {
  accepted: "已受理",
  running: "执行中",
  completed: "已完成",
  failed: "执行失败",
} as const;
```

Use one list item per durable record, keep the task kind on its retry callback, and put the recovery failure in the same supplement:

```tsx
export function HrPositionTaskActivity({ records, recoveryState, onRetryRecovery, onRetryTask }: Props) {
  if (recoveryState === "ready" && records.length === 0) return null;
  return <section aria-label="岗位任务进度" className="conversation-flow-supplement" aria-live="polite">
    {recoveryState === "loading" && <p>正在恢复任务进度…</p>}
    {recoveryState === "unavailable" && <p>任务进度暂时无法刷新。<button type="button" onClick={onRetryRecovery}>重新连接</button></p>}
    {records.length > 0 && <ul>{records.map((record) => <li key={record.taskId} data-status={record.status}>
      <strong>{taskLabel[record.taskKind] ?? record.taskKind}：{taskStatusLabel[record.status]}</strong>
      {record.error && <span>{record.error}</span>}
      {record.status === "failed" && <button type="button" onClick={() => onRetryTask(record.taskKind)}>重试本任务</button>}
    </li>)}</ul>}
  </section>;
}
```

Failed cards include `重试本任务`; recovery failure includes `任务进度暂时无法刷新` and `重新连接`. Neither creates a page-level alert. Completed records cause resource/context projections to refresh without remounting `DirectAgentWorkspace`.

- [ ] **Step 5: Run the full position workspace suite and commit**

Run: `cd webui && npx vitest run src/workspaces/hr/HrPositionWorkspace.test.tsx src/workspaces/hr/HrWorkspacePage.test.tsx src/App.hrPositionSection.test.tsx`

Expected: PASS, including cross-position rejection, hard-stale read-only, position-scoped creation, material idempotency, polling, and route reuse.

```bash
git add webui/src/workspaces/hr/HrPositionTaskActivity.tsx webui/src/workspaces/hr/HrPositionTaskActivity.test.tsx webui/src/workspaces/hr/HrPositionWorkspace.tsx webui/src/workspaces/hr/HrPositionWorkspace.test.tsx
git commit -m "feat(hr): make position workspace chat first"
```

### Task 6: Implement the 1180px responsive visual system and scrolling contract

**Files:**
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Consumes: `.is-chat-first`, `.is-focused`, `.hr-position-bar`, `.hr-position-details-drawer`, `.hr-position-task-menu`, and `.conversation-flow-supplement` from Tasks 2–5.
- Produces: one desktop conversation column, sticky compact header/composer, overlay history/details panels, and one usable mobile scroll axis.

- [ ] **Step 1: Rewrite the failing style-contract assertions**

Replace the old assertions for `268px` history, `296px` materials, `960px` chat, metrics, tabs, and task bar with:

```ts
expect(rule('.agent-use-workspace[data-agent-id="hr-bot"].is-focused')).toContain("grid-template-columns: minmax(0, 1fr)");
expect(rule('.agent-use-workspace[data-agent-id="hr-bot"].is-focused .conversation-page')).toContain("max-width: 1180px");
expect(rule('.agent-use-workspace[data-agent-id="hr-bot"].is-focused .agent-use-page')).toContain("max-width: 1180px");
expect(rule(".hr-position-bar")).toContain("position: sticky");
expect(rule(".hr-position-details-drawer")).toContain("position: fixed");
expect(rule(".hr-position-details-drawer")).toContain("overflow-y: auto");
expect(rule('.agent-use-workspace.is-focused .conversation-sidebar')).toContain("position: fixed");
expect(rule('.agent-use-workspace[data-agent-id="hr-bot"].is-focused .conversation-composer')).toContain("bottom: 0");
```

Assert the old HR position metrics/tab/taskbar selectors are absent from the stylesheet and that the mobile media rule makes the details drawer full width without adding a permanent grid column.

- [ ] **Step 2: Run style tests and verify RED**

Run: `cd webui && npx vitest run src/styles.test.ts`

Expected: FAIL on the old widths and missing focused selectors.

- [ ] **Step 3: Replace the old position layout CSS**

Use this geometry as the fixed contract:

```css
.hr-position-workspace.is-chat-first { display: grid; height: 100%; min-height: 0; grid-template-rows: auto minmax(0,1fr); overflow: hidden; background: #f6f8fb; color: #172b42; }
.hr-position-bar { position: sticky; z-index: 20; top: 0; display: grid; grid-template-columns: auto minmax(0,1fr) auto auto; align-items: center; gap: 14px; min-height: 62px; padding: 10px clamp(18px,3vw,38px); border-bottom: 1px solid #d9e3ed; background: rgba(255,255,255,.96); }
.agent-use-workspace[data-agent-id="hr-bot"].is-focused { grid-template-columns: minmax(0,1fr); min-height: 0; }
.agent-use-workspace[data-agent-id="hr-bot"].is-focused .brain-workspace-main { min-height: 0; overflow-y: auto; }
.agent-use-workspace[data-agent-id="hr-bot"].is-focused .conversation-page,
.agent-use-workspace[data-agent-id="hr-bot"].is-focused .agent-use-page { width: min(1180px,100%); max-width: 1180px; box-sizing: border-box; margin: 0 auto; padding-inline: clamp(18px,3vw,38px); }
.agent-use-workspace[data-agent-id="hr-bot"].is-focused .conversation-message { width: min(1040px,100%); }
.agent-use-workspace[data-agent-id="hr-bot"].is-focused .conversation-composer { position: sticky; z-index: 8; bottom: 0; }
.agent-use-workspace.is-focused .conversation-sidebar { position: fixed; z-index: 42; top: 0; bottom: 0; left: 0; width: min(360px,calc(100vw - 32px)); transform: translateX(-105%); }
.agent-use-workspace.is-focused .conversation-sidebar.is-open { transform: translateX(0); }
.hr-position-details-drawer { position: fixed; z-index: 42; top: 0; right: 0; bottom: 0; width: min(560px,calc(100vw - 32px)); overflow-y: auto; background: #fff; box-shadow: -18px 0 48px rgba(24,52,92,.18); }
```

Use at least `14px` for operational body text and `16px` in textareas. Remove the obsolete persistent position metrics, section tabs, quick-task bar, task-recovery bar, and permanent HR materials-column rules.

- [ ] **Step 4: Add mobile geometry and scroll protection**

Inside `@media screen and (max-width: 720px)`, set the position bar to two rows, make both drawers `width: 100%`, keep `.brain-workspace-main` as the only page scroll container, add bottom padding equal to the sticky composer footprint, and keep attachment/result actions reachable above the composer.

- [ ] **Step 5: Run style and build verification, then commit**

Run: `cd webui && npx vitest run src/styles.test.ts && npm run build`

Expected: style tests PASS and Vite production build succeeds with no TypeScript errors.

```bash
git add webui/src/styles.css webui/src/styles.test.ts
git commit -m "style(hr): widen position conversation workspace"
```

### Task 7: Full regression, interaction verification, and release handoff

**Files:**
- Modify only if verification reveals an HR-scoped regression: files already named in Tasks 1–6.

**Interfaces:**
- Consumes: the complete chat-first position workspace.
- Produces: verified build artifacts and a release-ready feature branch; deployment remains a separate explicit release step under the production disk discipline.

- [ ] **Step 1: Run all web tests**

Run: `cd webui && npm test`

Expected: every Vitest suite passes with zero failed tests.

- [ ] **Step 2: Run the production web build**

Run: `cd webui && npm run build`

Expected: TypeScript and Vite complete successfully and write only `webui/dist` build output.

- [ ] **Step 3: Run the HR/authorization backend regression set**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_hr_routes.py backend/tests/test_hr_position_task_adapter.py backend/tests/test_hr_r12_http_routes.py backend/tests/test_r1_authorization.py`

Expected: all selected backend tests pass. The virtual environment remains untracked and unmodified by git operations.

- [ ] **Step 4: Verify the rendered page at desktop and mobile widths**

Run the local Vite server and inspect one existing position route at `1440×900` and `390×844`. Confirm: no permanent left/right column, 1180px desktop content cap, sticky composer, real progress events, history overlay, details drawer tabs, attachment upload/preview/download, task direct start, `Escape`, focus return, and a reachable final message while scrolling.

- [ ] **Step 5: Check repository scope and create the final implementation commit only if needed**

Run: `git status --short && git diff --check && git log --oneline --decorate -12`

Expected: no whitespace errors; only the planned HR files and ignored/untracked `backend/.venv` are present; every implementation task already has a focused commit. If a verification-only HR correction was required, commit exactly those files with:

```bash
git add webui/src/hrR12Types.ts webui/src/hrR12Api.ts webui/src/hrR12Api.test.ts webui/src/workspaces/direct/DirectAgentWorkspace.tsx webui/src/pages/ConversationPage.tsx webui/src/pages/AgentUsePage.test.tsx webui/src/pages/ConversationPage.test.tsx webui/src/workspaces/hr webui/src/styles.css webui/src/styles.test.ts
git commit -m "fix(hr): complete chat-first workspace verification"
```

- [ ] **Step 6: Prepare the production release gate**

Before any deployment, run `df -B1 / /data` on production; stop if root free space is below 25 GB, predicted root free space after staging/images is below 20 GB, or predicted post-release root usage exceeds 75%. Stage only code/build output under `/data/staging/ai-agent-platform/<deployment_id>/`, clean that exact directory with a trap on success or failure, retain current plus two rollback releases/images, do not modify shared Nginx or other apps, and report before/after `df`, added sizes, current/rollback versions, archived/deleted versions, staging cleanup, retained images, HR HTTP checks, and shared-system changes.
