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
import { PlatformLink } from "../../components/PlatformLink";
import { navigate } from "../../router";
import { DirectAgentWorkspace, type AgentHistoryClient } from "../direct/DirectAgentWorkspace";
import { HrPositionContextPanel } from "./HrPositionContextPanel";
import { HrCandidateWorkspace } from "./HrCandidateWorkspace";
import { HrPositionResourcesPanel } from "./HrPositionResourcesPanel";
import { trapDialogFocus } from "./modalFocus";
import { completeMutationRequest, retainMutationRequest } from "./hrMutationRequest";


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


function statusLabel(detail: HrPositionDetail): string {
  if (detail.internalStatus === "archived") return "已归档";
  if (detail.officialStatus === "inactive") return "官网已下线";
  if (detail.officialStatus === "stale" || detail.officialStatus === "suspected_inactive") return "官网状态待核验";
  return "进行中";
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
  section = "chat",
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
  const [activeSection, setActiveSection] = useState<HrPositionSection>(section);
  const [materialDrawerOpen, setMaterialDrawerOpen] = useState(false);
  const taskController = useRef<AbortController | null>(null);
  const artifactRefreshController = useRef<AbortController | null>(null);
  const materialDrawerClose = useRef<HTMLButtonElement>(null);
  const materialDrawerButton = useRef<HTMLButtonElement>(null);
  const materialDrawerWasOpen = useRef(false);
  const tabRefs = useRef<Partial<Record<HrPositionSection, HTMLButtonElement | null>>>({});

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
  useEffect(() => setActiveSection(section), [section]);
  useEffect(() => { setTurnMaterialIds([]); setMaterialDrawerOpen(false); }, [conversationId]);
  useEffect(() => { if (materialDrawerOpen) materialDrawerClose.current?.focus(); else if (materialDrawerWasOpen.current) materialDrawerButton.current?.focus(); materialDrawerWasOpen.current = materialDrawerOpen; }, [materialDrawerOpen]);

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
    const controller = new AbortController(); setTaskState("loading");
    void r12.activeTasks(positionId, controller.signal).then((tasks) => {
      if (!controller.signal.aborted) { setActiveTasks(tasks); setTaskState("ready"); }
    }).catch(() => { if (!controller.signal.aborted) setTaskState("unavailable"); });
    return () => controller.abort();
  }, [positionId, r12, taskRefresh]);

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
      }).catch(() => { if (!controller.signal.aborted) setTaskState("unavailable"); });
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

  const header = <header className="hr-position-context">
    <div className="hr-position-context-main">
      <PlatformLink href="/hr/positions">← 所有岗位</PlatformLink>
      <div><span className="hr-position-eyebrow">POSITION CONTEXT</span>
        <h1>{detail.title}</h1>
        <p>{[detail.department, ...detail.locations].filter(Boolean).join(" · ") || "岗位信息待完善"}</p>
      </div>
      <div className="hr-position-context-tags"><span>{detail.officialJobId ?? "内部岗位"}</span><span>{statusLabel(detail)}</span>
        {account.hard_stale_read_only && <strong>目录信息已过期，当前岗位只读</strong>}</div>
    </div>
    <dl className="hr-position-context-metrics">
      <div><dt>对话</dt><dd>{detail.conversationCount} 个对话</dd></div>
      <div><dt>岗位材料</dt><dd>{detail.materialCount} 份岗位材料</dd></div>
      <div><dt>生成结果</dt><dd>{detail.artifactCount} 个生成结果</dd></div>
    </dl>
    {conversationId && !selectedConversationId && <p className="hr-position-scope-error" role="alert">该对话不属于当前岗位，已阻止跨岗位读取。</p>}
    {materialNotice && <p className="hr-position-scope-error" role="status">{materialNotice}</p>}
  </header>;

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
  const sections: Array<[HrPositionSection, string]> = [["chat", "对话"], ["context", "上下文"], ["candidates", "候选人"], ["artifacts", "材料与成果"]];
  function activateSection(value: HrPositionSection) { setActiveSection(value); onOpenConversation(`/hr/positions/${encodeURIComponent(positionId)}/${value}`); tabRefs.current[value]?.focus(); }
  function tabKey(event: React.KeyboardEvent<HTMLButtonElement>, value: HrPositionSection) {
    const index = sections.findIndex(([item]) => item === value); let next = index;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % sections.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + sections.length) % sections.length;
    else if (event.key === "Home") next = 0; else if (event.key === "End") next = sections.length - 1; else return;
    event.preventDefault(); activateSection(sections[next][0]);
  }
  const navigation = <nav className="hr-position-sections" aria-label="岗位工作台分区"><div role="tablist">{sections.map(([value, label]) => <button aria-controls={`hr-position-panel-${value}`} id={`hr-position-tab-${value}`} key={value} ref={(element) => { tabRefs.current[value] = element; }} tabIndex={activeSection === value ? 0 : -1} type="button" role="tab" aria-selected={activeSection === value} onKeyDown={(event) => tabKey(event, value)} onClick={() => activateSection(value)}>{label}</button>)}</div></nav>;
  const materialChoices = <>{availableMaterials.length === 0 ? <p>当前没有可用岗位材料。</p> : availableMaterials.map((material) => <label key={material.attachmentId}><input disabled={account.hard_stale_read_only} name="quick-task-material" type="checkbox" checked={turnMaterialIds.includes(material.attachmentId)} onChange={() => setTurnMaterialIds((ids) => ids.includes(material.attachmentId) ? ids.filter((id) => id !== material.attachmentId) : [...ids, material.attachmentId])} />{material.filename}</label>)}<small>默认不选；只会把本轮明确勾选的材料交给 Agent。</small></>;
  const quickTasks = <section className="hr-position-taskbar" aria-label="岗位快捷任务"><div className="hr-position-quick-tasks"><button disabled={account.hard_stale_read_only} type="button" onClick={() => void quickTask("jd")}>生成JD</button><button disabled={account.hard_stale_read_only} type="button" onClick={() => void quickTask("jr")}>生成JR</button><button disabled={account.hard_stale_read_only} type="button" onClick={() => void quickTask("talent_profile")}>生成人才画像</button><button disabled={account.hard_stale_read_only} type="button" onClick={() => void quickTask("sourcing_strategy")}>生成搜寻策略</button><button disabled={account.hard_stale_read_only} type="button" onClick={() => void quickTask("position_interview_plan")}>生成面试方案</button></div><button aria-expanded={materialDrawerOpen} ref={materialDrawerButton} type="button" onClick={() => setMaterialDrawerOpen(true)}>本轮材料（已选 {turnMaterialIds.length}）</button>{materialDrawerOpen && <><button aria-label="关闭本轮材料遮罩" className="hr-drawer-backdrop" type="button" onClick={() => setMaterialDrawerOpen(false)} /><aside aria-label="本轮任务材料" aria-modal="true" className="hr-mobile-drawer hr-turn-materials" role="dialog" onKeyDown={(event) => trapDialogFocus(event, () => setMaterialDrawerOpen(false))}><header><h2>本轮任务材料</h2><button aria-label="关闭本轮材料" ref={materialDrawerClose} type="button" onClick={() => setMaterialDrawerOpen(false)}>关闭</button></header>{materialChoices}</aside></>}</section>;
  const sectionView = activeSection === "context" ? <HrPositionContextPanel api={r12} onConfirmed={setCurrentContext} positionId={positionId} readOnly={account.hard_stale_read_only} refreshGeneration={contextRefreshGeneration} />
    : activeSection === "candidates" ? <HrCandidateWorkspace api={r12} csrfToken={account.csrf_token} currentContextVersionId={currentContext?.contextVersionId ?? null} positionId={positionId} readOnly={account.hard_stale_read_only} />
      : <HrPositionResourcesPanel api={r12} positionId={positionId} readOnly={account.hard_stale_read_only} refreshGeneration={resourceRefreshGeneration} />;

  const taskLabel: Record<string, string> = { jd: "JD", jr: "JR", talent_profile: "人才画像", sourcing_strategy: "搜寻策略", position_interview_plan: "面试方案", candidate_match: "候选人匹配", candidate_interview_plan: "候选人面试题", candidate_comparison: "候选人比较" };
  const taskStatusLabel: Record<string, string> = { accepted: "已受理", running: "执行中", completed: "已完成", failed: "执行失败" };
  const taskRecovery = <section aria-label="岗位任务状态" className="hr-task-recovery" aria-live="polite">{taskState === "loading" ? <p>正在恢复任务状态…</p> : taskState === "unavailable" ? <p>任务状态暂时不可用。<button type="button" onClick={() => setTaskRefresh((value) => value + 1)}>刷新任务状态</button></p> : activeTasks.length === 0 ? <p>当前没有执行中任务。</p> : <ul>{activeTasks.map((task) => <li key={task.taskId}>{taskLabel[task.taskKind] ?? task.taskKind}：{taskStatusLabel[task.status] ?? task.status}{task.error ? ` · ${task.error}` : ""}</li>)}</ul>}</section>;

  return <main className="hr-position-workspace" data-position-id={positionId}>
    {header}{navigation}{quickTasks}{taskRecovery}
    <section aria-label={sections.find(([value]) => value === activeSection)?.[1]} aria-labelledby={`hr-position-tab-${activeSection}`} className="hr-position-section-panel" id={`hr-position-panel-${activeSection}`} role="tabpanel"><DirectAgentWorkspace
      account={account}
      agentId="hr-bot"
      autoFocusComposer
      conversationClient={conversationClient}
      conversationId={selectedConversationId}
      conversationPath={(id) => positionConversationPath(positionId, id)}
      createSubmission={createSubmission}
      header={activeSection === "chat" ? null : sectionView}
      historyClient={historyClient}
      loadCatalog={loadCatalog}
      newConversationHeader={<section className="hr-position-conversation-welcome">
        <span>岗位对话</span><h2>从这个岗位开始对话</h2>
        <p>当前岗位、已选材料和后续生成结果会保留在同一上下文中。</p>
      </section>}
      newConversationScope={scope}
      onOpenConversation={onOpenConversation}
      onPositionMaterialChange={account.hard_stale_read_only ? undefined : changePositionMaterial}
      positionMaterialIds={promotedMaterialIds}
      positionArtifactAttachmentIds={detail.artifactAttachmentIds}
      showWorkspaceBackLink={false}
      workspaceLabel="岗位对话"
      workspaceMark="HR"
      workspaceRootPath={`/hr/positions/${encodeURIComponent(positionId)}`}
    /></section>
  </main>;
}
