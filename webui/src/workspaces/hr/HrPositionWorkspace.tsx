import { useEffect, useMemo, useRef, useState } from "react";

import type { Account } from "../../auth";
import type { AgentCapabilityCard } from "../../brainTypes";
import { listConversations, type ConversationStartScope, type ConversationSubmission } from "../../conversationApi";
import type { Conversation, ConversationAttachment, ConversationPage, TurnSubmission } from "../../conversationTypes";
import { createHrApi, type HrApi } from "../../hrApi";
import { createHrR12Api, type HrR12Api } from "../../hrR12Api";
import type { HrContextVersion, HrPositionMaterialItem, HrPositionSection, HrTaskKind, HrTaskRecord } from "../../hrR12Types";
import type { HrPositionDetail } from "../../hrTypes";
import type { ConversationPageClient } from "../../pages/ConversationPage";
import { PlatformLink } from "../../components/PlatformLink";
import { navigate } from "../../router";
import { DirectAgentWorkspace, type AgentHistoryClient } from "../direct/DirectAgentWorkspace";
import { HrPositionContextPanel } from "./HrPositionContextPanel";
import { HrCandidateWorkspace } from "./HrCandidateWorkspace";
import { HrPositionResourcesPanel } from "./HrPositionResourcesPanel";


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
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [materialNotice, setMaterialNotice] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<HrPositionSection>(section);
  const taskController = useRef<AbortController | null>(null);

  useEffect(() => setActiveSection(section), [section]);

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
    const controller = new AbortController(); setTurnMaterialIds([]); setAvailableMaterials([]); setCurrentContext(null); setActiveTasks([]);
    void Promise.allSettled([
      r12.resources(positionId, controller.signal),
      r12.context(positionId, controller.signal),
      r12.activeTasks(positionId, controller.signal),
    ]).then(([resources, context, tasks]) => {
      if (controller.signal.aborted) return;
      if (resources.status === "fulfilled") setAvailableMaterials(resources.value.materials.filter((item) => item.state === "ready" && item.downloadAvailable));
      if (context.status === "fulfilled") setCurrentContext(context.value.current);
      if (tasks.status === "fulfilled") setActiveTasks(tasks.value);
    });
    return () => { controller.abort(); taskController.current?.abort(); };
  }, [attempt, positionId, r12]);

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
      if (active) await client.promoteMaterial(positionId, attachment.attachmentId, crypto.randomUUID());
      else await client.removeMaterial(positionId, attachment.attachmentId, crypto.randomUUID());
      setPromotedMaterialIds((current) => active
        ? current.includes(attachment.attachmentId) ? current : [...current, attachment.attachmentId]
        : current.filter((id) => id !== attachment.attachmentId));
    } catch {
      setMaterialNotice("岗位材料操作未完成，请重试。");
    }
  }

  const header = <header className="hr-position-context">
    <div className="hr-position-context-main">
      <PlatformLink href="/hr/">← 所有岗位</PlatformLink>
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

  async function quickTask(taskKind: HrTaskKind) {
    taskController.current?.abort(); const controller = new AbortController(); taskController.current = controller;
    try {
      const started = await r12.startTask(positionId, taskKind, crypto.randomUUID(), {
        ...(currentContext ? { contextVersionId: currentContext.contextVersionId } : {}),
        materialIds: turnMaterialIds,
        conversationId: selectedConversationId,
      }, controller.signal);
      if (!controller.signal.aborted && ["accepted", "running"].includes(started.status)) setActiveTasks((items) => [started, ...items.filter((item) => item.taskId !== started.taskId)]);
    } catch { if (!controller.signal.aborted) setMaterialNotice("岗位任务未启动，请重试。"); }
  }
  const sections: Array<[HrPositionSection, string]> = [["chat", "对话"], ["context", "上下文"], ["candidates", "候选人"], ["artifacts", "材料与成果"]];
  const navigation = <nav className="hr-position-sections" aria-label="岗位工作台分区"><div role="tablist">{sections.map(([value, label]) => <button aria-controls={`hr-position-panel-${value}`} id={`hr-position-tab-${value}`} key={value} type="button" role="tab" aria-selected={activeSection === value} onClick={() => { setActiveSection(value); onOpenConversation(`/hr/positions/${encodeURIComponent(positionId)}/${value}`); }}>{label}</button>)}</div></nav>;
  const quickTasks = <section className="hr-position-taskbar" aria-label="岗位快捷任务"><div className="hr-position-quick-tasks"><button type="button" onClick={() => void quickTask("jd")}>生成JD</button><button type="button" onClick={() => void quickTask("jr")}>生成JR</button><button type="button" onClick={() => void quickTask("talent_profile")}>生成人才画像</button><button type="button" onClick={() => void quickTask("sourcing_strategy")}>生成搜寻策略</button><button type="button" onClick={() => void quickTask("position_interview_plan")}>生成面试方案</button></div><details className="hr-turn-materials"><summary>本轮材料（已选 {turnMaterialIds.length}）</summary>{availableMaterials.length === 0 ? <p>当前没有可用岗位材料。</p> : availableMaterials.map((material) => <label key={material.attachmentId}><input name="quick-task-material" type="checkbox" checked={turnMaterialIds.includes(material.attachmentId)} onChange={() => setTurnMaterialIds((ids) => ids.includes(material.attachmentId) ? ids.filter((id) => id !== material.attachmentId) : [...ids, material.attachmentId])} />{material.filename}</label>)}<small>默认不选；只会把本轮明确勾选的材料交给 Agent。</small></details></section>;
  const sectionView = activeSection === "context" ? <HrPositionContextPanel api={r12} positionId={positionId} />
    : activeSection === "candidates" ? <HrCandidateWorkspace api={r12} csrfToken={account.csrf_token} currentContextVersionId={currentContext?.contextVersionId ?? null} positionId={positionId} />
      : <HrPositionResourcesPanel api={r12} positionId={positionId} />;

  return <main className="hr-position-workspace" data-position-id={positionId}>
    {header}{navigation}{quickTasks}{activeTasks.length > 0 && <p className="hr-task-recovery" role="status">任务仍在执行，刷新后已恢复状态（{activeTasks.length}）</p>}
    <section aria-label={sections.find(([value]) => value === activeSection)?.[1]} aria-labelledby={`hr-position-tab-${activeSection}`} className="hr-position-section-panel" id={`hr-position-panel-${activeSection}`} role="tabpanel">{activeSection === "chat" ? <DirectAgentWorkspace
      account={account}
      agentId="hr-bot"
      autoFocusComposer
      conversationClient={conversationClient}
      conversationId={selectedConversationId}
      conversationPath={(id) => positionConversationPath(positionId, id)}
      createSubmission={createSubmission}
      header={null}
      historyClient={historyClient}
      loadCatalog={loadCatalog}
      newConversationScope={scope}
      onOpenConversation={onOpenConversation}
      onPositionMaterialChange={account.hard_stale_read_only ? undefined : changePositionMaterial}
      positionMaterialIds={promotedMaterialIds}
      positionArtifactAttachmentIds={detail.artifactAttachmentIds}
      workspaceLabel="岗位智能工作台"
      workspaceMark="HR"
      workspaceRootPath={`/hr/positions/${encodeURIComponent(positionId)}`}
    /> : sectionView}</section>
  </main>;
}
