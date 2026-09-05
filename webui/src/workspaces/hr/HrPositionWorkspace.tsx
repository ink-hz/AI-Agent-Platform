import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Account } from "../../auth";
import type { AgentCapabilityCard } from "../../brainTypes";
import { listConversations, type ConversationStartScope, type ConversationSubmission } from "../../conversationApi";
import type { Conversation, ConversationAttachment, ConversationPage, TurnSubmission } from "../../conversationTypes";
import { createHrApi, type HrApi } from "../../hrApi";
import { createHrR12Api, type HrR12Api } from "../../hrR12Api";
import type { HrContextVersion, HrPositionMaterialItem, HrPositionSection, HrPositionTaskKind, HrTaskRecord } from "../../hrR12Types";
import type { HrPositionDetail } from "../../hrTypes";
import type { ConversationPageClient } from "../../pages/ConversationPage";
import { navigate } from "../../router";
import { DirectAgentWorkspace, type AgentHistoryClient } from "../direct/DirectAgentWorkspace";
import { completeMutationRequest, retainMutationRequest } from "./hrMutationRequest";
import { HrPositionDetailsDrawer, type HrPositionDetailsTab } from "./HrPositionDetailsDrawer";
import { HrPositionHeader } from "./HrPositionHeader";
import { HrPositionTaskMenu } from "./HrPositionTaskMenu";


type PositionWorkspaceApi = Pick<HrApi, "position" | "promoteMaterial" | "removeMaterial">;
type PositionConversationLoader = (conversationIds: readonly string[], signal?: AbortSignal) => Promise<Conversation[]>;


export async function loadPositionConversations(
  conversationIds: readonly string[],
  signal?: AbortSignal,
): Promise<Conversation[]> {
  const allowed = new Set(conversationIds);
  if (allowed.size === 0) return [];
  const found = new Map<string, Conversation>();
  for (const status of ["active", "archived"] as const) {
    let cursor: string | undefined;
    const seenCursors = new Set<string>();
    do {
      const page = await listConversations(signal, cursor, 100, "hr-bot", status);
      for (const item of page.items) {
        if (allowed.has(item.conversation_id)) found.set(item.conversation_id, item);
      }
      if (!page.next_cursor || seenCursors.has(page.next_cursor)) break;
      seenCursors.add(page.next_cursor);
      cursor = page.next_cursor;
    } while (found.size < allowed.size);
    if (found.size === allowed.size) break;
  }
  return [...found.values()];
}


function positionConversationPath(positionId: string, conversationId: string): string {
  return `/hr/positions/${encodeURIComponent(positionId)}/conversations/${encodeURIComponent(conversationId)}`;
}


function detailsTabForSection(section?: HrPositionSection): HrPositionDetailsTab | null {
  if (section === "context") return "position";
  if (section === "candidates") return "candidates";
  if (section === "artifacts") return "resources";
  return null;
}


function historyFrom(items: Conversation[]): AgentHistoryClient {
  return {
    async list(_signal, _before, _limit, _agentId, status = "active"): Promise<ConversationPage> {
      return { items: items.filter((item) => item.status === status), next_cursor: null };
    },
  };
}


export function HrPositionWorkspace({
  account,
  positionId,
  conversationId,
  api,
  loadPositionConversations: loadScopedConversations = loadPositionConversations,
  loadCatalog,
  createSubmission,
  conversationClient,
  onOpenConversation = navigate,
  r12Api,
  section,
}: {
  account: Account;
  positionId: string;
  conversationId?: string;
  api?: PositionWorkspaceApi;
  loadPositionConversations?: PositionConversationLoader;
  loadCatalog?: (signal?: AbortSignal) => Promise<AgentCapabilityCard[]>;
  createSubmission?: (
    input: string | TurnSubmission, csrfToken: string, agentId?: string, scope?: ConversationStartScope,
  ) => ConversationSubmission;
  conversationClient?: ConversationPageClient;
  onOpenConversation?: (path: string) => void;
  r12Api?: HrR12Api;
  section?: HrPositionSection;
}) {
  const defaultApi = useMemo(() => createHrApi(account.csrf_token), [account.csrf_token]);
  const client = api ?? defaultApi;
  const defaultR12Api = useMemo(() => createHrR12Api(account.csrf_token), [account.csrf_token]);
  const r12 = r12Api ?? defaultR12Api;
  const [detail, setDetail] = useState<HrPositionDetail | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [promotedMaterialIds, setPromotedMaterialIds] = useState<string[]>([]);
  const [turnMaterialIds, setTurnMaterialIds] = useState<string[]>([]);
  const [availableMaterials, setAvailableMaterials] = useState<HrPositionMaterialItem[]>([]);
  const [currentContext, setCurrentContext] = useState<HrContextVersion | null>(null);
  const [activeTasks, setActiveTasks] = useState<HrTaskRecord[]>([]);
  const [taskState, setTaskState] = useState<"loading" | "ready" | "unavailable">("loading");
  const [taskRefresh, setTaskRefresh] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [materialNotice, setMaterialNotice] = useState<string | null>(null);
  const [resourceRefreshGeneration, setResourceRefreshGeneration] = useState(0);
  const [contextRefreshGeneration, setContextRefreshGeneration] = useState(0);
  const [openDrawer, setOpenDrawer] = useState<"materials" | "position" | null>(
    () => detailsTabForSection(section) !== null ? "position" : null,
  );
  const [detailsTab, setDetailsTab] = useState<HrPositionDetailsTab>(() => detailsTabForSection(section) ?? "position");
  const taskController = useRef<AbortController | null>(null);
  const artifactRefreshController = useRef<AbortController | null>(null);

  const refreshArtifactProjection = useCallback(async () => {
    artifactRefreshController.current?.abort();
    const controller = new AbortController();
    artifactRefreshController.current = controller;
    try {
      const refreshed = await client.position(positionId, controller.signal);
      if (controller.signal.aborted) return;
      setDetail((current) => current ? {
        ...current,
        artifactCount: refreshed.artifactCount,
        artifactIds: refreshed.artifactIds,
        artifactAttachmentIds: refreshed.artifactAttachmentIds,
      } : current);
    } catch {
      // The resources panel refresh remains useful even when this secondary projection is unavailable.
    } finally {
      if (artifactRefreshController.current === controller) artifactRefreshController.current = null;
    }
  }, [client, positionId]);

  useEffect(() => () => artifactRefreshController.current?.abort(), [positionId]);
  useEffect(() => { setTurnMaterialIds([]); }, [conversationId]);
  useEffect(() => {
    const routeTab = detailsTabForSection(section);
    if (routeTab === null) {
      setOpenDrawer(null);
      return;
    }
    setDetailsTab(routeTab);
    setOpenDrawer("position");
  }, [section]);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading"); setDetail(null); setConversations([]); setMaterialNotice(null);
    void client.position(positionId, controller.signal).then(async (loaded) => {
      const loadedConversations = await loadScopedConversations(loaded.conversationIds, controller.signal);
      if (controller.signal.aborted) return;
      const allowed = new Set(loaded.conversationIds);
      setDetail(loaded);
      setPromotedMaterialIds(loaded.materialAttachmentIds);
      setConversations(loadedConversations.filter((item) => allowed.has(item.conversation_id)));
      setState("ready");
    }).catch(() => { if (!controller.signal.aborted) setState("error"); });
    return () => controller.abort();
  }, [attempt, client, loadScopedConversations, positionId]);

  useEffect(() => {
    const controller = new AbortController(); setTurnMaterialIds([]); setAvailableMaterials([]); setCurrentContext(null);
    void Promise.allSettled([
      r12.resources(positionId, controller.signal),
      r12.context(positionId, controller.signal),
    ]).then(([resources, context]) => {
      if (controller.signal.aborted) return;
      if (resources.status === "fulfilled") setAvailableMaterials(resources.value.materials.filter((item) => item.state === "ready" && item.downloadAvailable));
      if (context.status === "fulfilled") setCurrentContext(context.value.current);
    });
    return () => { controller.abort(); taskController.current?.abort(); };
  }, [attempt, positionId, r12]);

  useEffect(() => {
    const controller = new AbortController(); setActiveTasks([]); setTaskState("loading");
    void r12.activeTasks(positionId, controller.signal).then((tasks) => {
      if (!controller.signal.aborted) { setActiveTasks(tasks); setTaskState("ready"); }
    }).catch(() => { if (!controller.signal.aborted) { setActiveTasks([]); setTaskState("unavailable"); } });
    return () => controller.abort();
  }, [attempt, positionId, r12, taskRefresh]);

  const hasActiveTasks = activeTasks.some((task) => task.status === "accepted" || task.status === "running");
  useEffect(() => {
    if (!hasActiveTasks) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      void r12.activeTasks(positionId, controller.signal).then((tasks) => {
        if (!controller.signal.aborted) {
          const nextActiveIds = new Set(tasks.filter((task) => task.status === "accepted" || task.status === "running").map((task) => task.taskId));
          if (activeTasks.some((task) => (task.status === "accepted" || task.status === "running") && !nextActiveIds.has(task.taskId))) {
            setResourceRefreshGeneration((value) => value + 1);
            setContextRefreshGeneration((value) => value + 1);
            void refreshArtifactProjection();
          }
          setActiveTasks(tasks); setTaskState("ready");
        }
      }).catch(() => { if (!controller.signal.aborted) { setActiveTasks([]); setTaskState("unavailable"); } });
    }, 2_000);
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [activeTasks, hasActiveTasks, positionId, r12, refreshArtifactProjection]);

  const historyClient = useMemo(() => historyFrom(conversations), [conversations]);

  if (state === "loading") return <main className="hr-position-state"><p>正在打开岗位工作区…</p></main>;
  if (state === "error" || !detail) return <main className="hr-position-state" role="alert">
    <h1>岗位工作区暂时不可用</h1><p>岗位数据没有被修改，可以安全重试。</p>
    <button type="button" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
  </main>;

  const selectedConversationId = conversationId && detail.conversationIds.includes(conversationId)
    ? conversationId : undefined;
  const scope: ConversationStartScope = { positionId };

  async function changePositionMaterial(attachment: ConversationAttachment, active: boolean) {
    if (account.hard_stale_read_only) return;
    setMaterialNotice(null);
    try {
      const operation = retainMutationRequest(`position-material:${positionId}:${active ? "promote" : "remove"}`, { attachmentId: attachment.attachmentId });
      if (active) await client.promoteMaterial(positionId, attachment.attachmentId, operation.requestId);
      else await client.removeMaterial(positionId, attachment.attachmentId, operation.requestId);
      completeMutationRequest(operation.key);
      setPromotedMaterialIds((current) => active
        ? current.includes(attachment.attachmentId) ? current : [...current, attachment.attachmentId]
        : current.filter((id) => id !== attachment.attachmentId));
      const refreshed = await r12.resources(positionId);
      const ready = refreshed.materials.filter((item) => item.state === "ready" && item.downloadAvailable);
      setAvailableMaterials(ready);
      setTurnMaterialIds((ids) => ids.filter((id) => ready.some((item) => item.attachmentId === id)));
      setResourceRefreshGeneration((value) => value + 1);
    } catch {
      setMaterialNotice("岗位材料操作未完成，请重试。");
    }
  }

  async function quickTask(taskKind: HrPositionTaskKind) {
    if (account.hard_stale_read_only) return;
    taskController.current?.abort(); const controller = new AbortController(); taskController.current = controller;
    const input = {
      ...(currentContext ? { contextVersionId: currentContext.contextVersionId } : {}),
      materialIds: turnMaterialIds,
      conversationId: selectedConversationId,
    };
    const operation = retainMutationRequest(`position-task:${positionId}:${taskKind}`, input);
    try {
      const started = await r12.startTask(positionId, taskKind, operation.requestId, input, controller.signal);
      if (!controller.signal.aborted) {
        completeMutationRequest(operation.key);
        if (started.status !== "failed") setTurnMaterialIds([]);
        setTaskState("ready");
        setActiveTasks((items) => [started, ...items.filter((item) => item.taskId !== started.taskId)]);
        if (started.status === "completed") {
          setResourceRefreshGeneration((value) => value + 1);
          setContextRefreshGeneration((value) => value + 1);
          void refreshArtifactProjection();
        }
      }
    } catch { if (!controller.signal.aborted) setMaterialNotice("岗位任务未启动，请重试。"); }
  }
  const taskLabel: Record<string, string> = { jd: "JD", jr: "JR", talent_profile: "人才画像", sourcing_strategy: "搜寻策略", position_interview_plan: "面试方案", candidate_match: "候选人匹配", candidate_interview_plan: "候选人面试题", candidate_comparison: "候选人比较" };
  const taskStatusLabel: Record<string, string> = { accepted: "已受理", running: "执行中", completed: "已完成", failed: "执行失败" };
  const visibleTasks = activeTasks.filter((task) => task.status === "accepted" || task.status === "running" || task.status === "failed");
  const taskStatus = taskState === "unavailable"
    ? <section aria-label="岗位任务状态" className="hr-position-task-status" aria-live="polite"><p>任务状态暂时不可用。<button type="button" onClick={() => setTaskRefresh((value) => value + 1)}>刷新任务状态</button></p></section>
    : taskState === "ready" && visibleTasks.length > 0
      ? <section aria-label="岗位任务状态" className="hr-position-task-status" aria-live="polite"><ul>{visibleTasks.map((task) => <li key={task.taskId}>{taskLabel[task.taskKind] ?? task.taskKind}：{taskStatusLabel[task.status] ?? task.status}{task.error ? ` · ${task.error}` : ""}</li>)}</ul></section>
      : null;

  return <main className="hr-position-workspace is-chat-first" data-position-id={positionId}>
    <HrPositionHeader detail={detail} readOnly={account.hard_stale_read_only}
      onNewConversation={() => onOpenConversation(`/hr/positions/${encodeURIComponent(positionId)}`)}
      onOpenDetails={() => setOpenDrawer("position")}
      onOpenMaterials={selectedConversationId ? () => setOpenDrawer("materials") : undefined} />
    {conversationId && !selectedConversationId && <p className="hr-position-scope-error" role="alert">该对话不属于当前岗位，已阻止跨岗位读取。</p>}
    {materialNotice && <p className="hr-position-scope-error" role="status">{materialNotice}</p>}
    <section aria-label="岗位对话" className="hr-position-chat-surface"><DirectAgentWorkspace
      account={account}
      agentId="hr-bot"
      autoFocusComposer
      conversationClient={conversationClient}
      conversationId={selectedConversationId}
      conversationPath={(id) => positionConversationPath(positionId, id)}
      composerTools={<HrPositionTaskMenu
        disabled={account.hard_stale_read_only}
        materials={availableMaterials}
        onSelectedMaterialIdsChange={setTurnMaterialIds}
        onStart={(kind) => void quickTask(kind)}
        selectedMaterialIds={turnMaterialIds}
      />}
      createSubmission={createSubmission}
      header={taskStatus}
      historyClient={historyClient}
      layout="focused"
      loadCatalog={loadCatalog}
      newConversationHeader={<section className="hr-position-conversation-welcome">
        <span>岗位对话</span><h2>围绕这个岗位，直接开始协作</h2>
        <p>当前岗位、明确选择的材料和后续生成结果会保留在同一上下文中。</p>
      </section>}
      newConversationScope={scope}
      onOpenConversation={onOpenConversation}
      materialsOpen={openDrawer === "materials"}
      onMaterialsOpenChange={(open) => setOpenDrawer(open ? "materials" : null)}
      showMaterialsTrigger={false}
      onPositionMaterialChange={account.hard_stale_read_only ? undefined : changePositionMaterial}
      positionMaterialIds={promotedMaterialIds}
      positionArtifactAttachmentIds={detail.artifactAttachmentIds}
      showTaskStarters={false}
      showWorkspaceBackLink={false}
      workspaceLabel="岗位对话"
      workspaceMark="HR"
      workspaceRootPath={`/hr/positions/${encodeURIComponent(positionId)}`}
    /></section>
    <HrPositionDetailsDrawer activeTab={detailsTab} api={r12} csrfToken={account.csrf_token}
      currentContextVersionId={currentContext?.contextVersionId ?? null}
      detail={detail} open={openDrawer === "position"} readOnly={account.hard_stale_read_only}
      onActiveTabChange={setDetailsTab} onClose={() => setOpenDrawer(null)} onConfirmed={setCurrentContext}
      contextRefreshGeneration={contextRefreshGeneration}
      resourceRefreshGeneration={resourceRefreshGeneration} />
  </main>;
}
