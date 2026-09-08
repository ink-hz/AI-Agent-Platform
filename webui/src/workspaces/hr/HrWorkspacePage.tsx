import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import type { Conversation, ConversationPage, HrKnowledgeSelection } from "../../conversationTypes";
import { createHrApi } from "../../hrApi";
import { createHrR12Api } from "../../hrR12Api";
import type { HrContextVersion, HrPositionSection } from "../../hrR12Types";
import type { HrConfirmedPositionPackage, HrPosition, HrPositionDetail, HrPositionPackage } from "../../hrTypes";
import { directConversationPath } from "../../platform/workspaces";
import { navigate } from "../../router";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";
import {
  DirectAgentWorkspace, type AgentHistoryClient, type DirectAgentDraftSnapshot,
} from "../direct/DirectAgentWorkspace";
import { HrConversationOutcomePanel } from "./HrConversationOutcomePanel";
import { HrPanoramaWorkspace } from "./HrPanoramaWorkspace";
import { HrPositionDetailsDrawer, type HrPositionDetailsTab } from "./HrPositionDetailsDrawer";
import { HrPositionPicker } from "./HrPositionPicker";
import { HrKnowledgePanel } from "./HrKnowledgePanel";
import { HrPositionIndex } from "./HrPositionIndex";
import { HrPositionWorkspace, loadPositionConversations } from "./HrPositionWorkspace";
import { HrWorkspaceShell } from "./HrWorkspaceShell";
import { useHrChatPosition } from "./useHrChatPosition";


function hrConversationPath(conversationId: string): string {
  return directConversationPath("hr-bot", conversationId)
    ?? `/hr/conversations/${encodeURIComponent(conversationId)}`;
}


function scopedHistory(items: Conversation[]): AgentHistoryClient {
  return {
    async list(_signal, _before, _limit, _agentId, status = "active"): Promise<ConversationPage> {
      return { items: items.filter((item) => item.status === status), next_cursor: null };
    },
  };
}


function fallbackDetail(positionId: string, positionPackage: HrPositionPackage | null): HrPositionDetail {
  const timestamp = positionPackage?.updatedAt ?? "1970-01-01T00:00:00Z";
  return {
    positionId,
    sourceKind: "manual",
    officialJobId: null,
    title: positionPackage?.title ?? "岗位资料",
    department: null,
    locations: [],
    officialStatus: null,
    internalStatus: "active",
    sourceVersion: null,
    rowVersion: positionPackage?.rowVersion ?? 0,
    createdAt: positionPackage?.createdAt ?? timestamp,
    updatedAt: timestamp,
    conversationCount: 0,
    materialCount: 0,
    artifactCount: 0,
    conversationIds: [],
    materialAttachmentIds: [],
    artifactIds: [],
    artifactAttachmentIds: [],
  };
}


export function HrWorkspacePage(props: { account: Account; conversationId?: string; positionId?: string; section?: HrPositionSection; freeChat?: boolean; positions?: boolean; panorama?: boolean; panoramaReportId?: string }) {
  const positionsActive = Boolean(props.positions || props.positionId);
  const panoramaActive = Boolean(props.panorama || props.panoramaReportId);
  const positionDetailActive = Boolean(props.positionId);
  const positionConversationRoute = Boolean(props.positionId && props.conversationId);
  const lastChatTarget = useRef<{ conversationId: string; positionId?: string } | undefined>(undefined);
  const freeChatDraftSnapshots = useRef(new Map<string, DirectAgentDraftSnapshot>());
  const [confirmedPosition, setConfirmedPosition] = useState<{
    ownerId: string; confirmed: HrConfirmedPositionPackage; positionPackage: HrPositionPackage;
  } | null>(null);
  const positionApi = useMemo(() => createHrApi(props.account.csrf_token), [props.account.csrf_token]);
  const positionDetailsApi = useMemo(() => createHrR12Api(props.account.csrf_token), [props.account.csrf_token]);
  const [continuedPositionDetail, setContinuedPositionDetail] = useState<HrPositionDetail | null>(null);
  const [continuedPositionDetailState, setContinuedPositionDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [continuedPositionDetailAttempt, setContinuedPositionDetailAttempt] = useState(0);
  const [continuedPositionContext, setContinuedPositionContext] = useState<HrContextVersion | null>(null);
  const [fallbackPositionPackage, setFallbackPositionPackage] = useState<HrPositionPackage | null>(null);
  const [positionRouteFailure, setPositionRouteFailure] = useState<"invalid" | "error" | null>(null);
  const [validatedRoute, setValidatedRoute] = useState<{
    positionId: string; conversationId: string; conversations: Conversation[];
  } | null>(null);
  const [revealedRouteKey, setRevealedRouteKey] = useState<string | null>(null);
  const [positionDetailsOpen, setPositionDetailsOpen] = useState(false);
  const [positionDetailsTab, setPositionDetailsTab] = useState<HrPositionDetailsTab>("position");
  const [conversationRevision, setConversationRevision] = useState(0);
  const [selectedChatPosition, setSelectedChatPosition] = useState<HrPosition | null>(null);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const knowledgeSelections = useRef(new Map<string, HrKnowledgeSelection[]>());
  const [, setKnowledgeRevision] = useState(0);
  const draftOwnerId = props.account.internal_user_id;
  const retainFreeChatDraft = useCallback((snapshot: DirectAgentDraftSnapshot) => {
    freeChatDraftSnapshots.current.set(draftOwnerId, snapshot);
  }, [draftOwnerId]);
  const handleConversationSettled = useCallback(() => {
    setConversationRevision((value) => value + 1);
  }, []);

  if (!positionsActive && !panoramaActive && props.conversationId) {
    lastChatTarget.current = { conversationId: props.conversationId };
  }
  const retainedPositionHost = Boolean(positionConversationRoute
    && props.conversationId === lastChatTarget.current?.conversationId);
  const positionRouteValidated = Boolean(positionConversationRoute
    && validatedRoute?.positionId === props.positionId
    && validatedRoute?.conversationId === props.conversationId);
  const currentPositionRouteKey = positionConversationRoute
    ? `${props.positionId}:${props.conversationId}` : null;
  const positionRouteReady = positionRouteValidated && revealedRouteKey === currentPositionRouteKey;
  const chatTarget = props.conversationId
    ? { conversationId: props.conversationId, positionId: props.positionId }
    : positionsActive || panoramaActive ? lastChatTarget.current : undefined;
  const chatConversationId = chatTarget?.conversationId;
  const chatHref = chatTarget?.positionId
    ? `/hr/positions/${encodeURIComponent(chatTarget.positionId)}/conversations/${encodeURIComponent(chatTarget.conversationId)}`
    : chatConversationId ? hrConversationPath(chatConversationId) : "/hr/";
  const knowledgeOwner = `${draftOwnerId}:${chatConversationId ?? "new"}`;
  const selectedKnowledge = knowledgeSelections.current.get(knowledgeOwner) ?? [];
  const keepChatHost = !positionDetailActive || Boolean(positionConversationRoute && (retainedPositionHost || positionRouteValidated));
  const positionConversationPath = (conversationId: string) => props.positionId
    ? `/hr/positions/${encodeURIComponent(props.positionId)}/conversations/${encodeURIComponent(conversationId)}`
    : hrConversationPath(conversationId);
  const handleConfirmed = useCallback((confirmed: HrConfirmedPositionPackage, positionPackage: HrPositionPackage) => {
    setConfirmedPosition({ ownerId: draftOwnerId, confirmed, positionPackage });
  }, [draftOwnerId]);
  const activeConfirmedPosition = confirmedPosition
    && confirmedPosition.ownerId === draftOwnerId
    && confirmedPosition.confirmed.positionId === props.positionId
    && confirmedPosition.confirmed.conversationId === props.conversationId
    && confirmedPosition.positionPackage.conversationId === props.conversationId
    ? confirmedPosition : null;
  const trustedConfirmedRoute = Boolean(positionConversationRoute && activeConfirmedPosition);
  const positionThreadVisible = positionRouteReady || trustedConfirmedRoute;
  const historyClient = useMemo(
    () => scopedHistory(positionRouteValidated ? validatedRoute?.conversations ?? [] : []),
    [positionRouteValidated, validatedRoute],
  );

  useEffect(() => {
    if (!positionRouteValidated || !currentPositionRouteKey) return;
    const timeout = window.setTimeout(() => setRevealedRouteKey(currentPositionRouteKey), 0);
    return () => window.clearTimeout(timeout);
  }, [currentPositionRouteKey, positionRouteValidated]);

  useEffect(() => {
    setPositionDetailsOpen(false);
    setContinuedPositionDetail(null);
    setContinuedPositionDetailState("idle");
    setContinuedPositionContext(null);
    setFallbackPositionPackage(null);
    setPositionRouteFailure(null);
    setValidatedRoute(null);
    setRevealedRouteKey(null);
    if (!positionConversationRoute || !props.positionId || !props.conversationId) return;

    const controller = new AbortController();
    const positionId = props.positionId;
    const conversationId = props.conversationId;
    setContinuedPositionDetailState("loading");
    void positionApi.position(positionId, controller.signal).then(async (detail) => {
      if (controller.signal.aborted) return;
      setContinuedPositionDetail(detail);
      if (!detail.conversationIds.includes(conversationId)) {
        setContinuedPositionDetailState("ready");
        setPositionRouteFailure("invalid");
        return;
      }
      const conversations = await loadPositionConversations(detail.conversationIds, controller.signal);
      if (controller.signal.aborted) return;
      const allowed = new Set(detail.conversationIds);
      setValidatedRoute({
        positionId, conversationId,
        conversations: conversations.filter((item) => allowed.has(item.conversation_id)),
      });
      lastChatTarget.current = { conversationId, positionId };
      setContinuedPositionDetailState("ready");
      void positionDetailsApi.context(positionId, controller.signal).then((context) => {
        if (!controller.signal.aborted) setContinuedPositionContext(context.current);
      }).catch(() => undefined);
    }).catch(() => {
      if (!controller.signal.aborted) {
        setContinuedPositionDetailState("error");
        setPositionRouteFailure("error");
        void positionApi.positionPackage(conversationId, controller.signal).then((positionPackage) => {
          if (!controller.signal.aborted) setFallbackPositionPackage(positionPackage);
        }).catch(() => undefined);
      }
    });
    return () => controller.abort();
  }, [continuedPositionDetailAttempt, positionApi, positionConversationRoute, positionDetailsApi, props.conversationId, props.positionId]);

  const degradedDetail = props.positionId ? fallbackDetail(
    props.positionId, fallbackPositionPackage ?? activeConfirmedPosition?.positionPackage ?? null,
  ) : null;
  const activeChatPositionId = positionRouteReady ? props.positionId
    : !chatConversationId && !positionsActive && !panoramaActive ? selectedChatPosition?.positionId : undefined;
  const chatPosition = useHrChatPosition({
    positionId: activeChatPositionId,
    conversationId: positionRouteValidated ? props.conversationId : undefined,
    api: positionApi,
    r12: positionDetailsApi,
    readOnly: props.account.hard_stale_read_only,
    onOpenResults: () => { setPositionDetailsTab("resources"); setPositionDetailsOpen(true); },
  });
  const drawerDetail = chatPosition.detail ?? continuedPositionDetail ?? degradedDetail;

  useEffect(() => { setPositionDetailsOpen(false); }, [activeChatPositionId]);

  return <HrWorkspaceShell account={props.account} chatHref={chatHref} current={panoramaActive ? "panorama" : positionsActive && !positionConversationRoute ? "positions" : "chat"} onOpenKnowledge={() => setKnowledgeOpen(true)}>
    {keepChatHost && <div
      className={`hr-workspace-chat-panel${positionConversationRoute ? " is-position-conversation" : ""}`}
      hidden={panoramaActive || (positionsActive && !positionThreadVisible)}
    >
      {trustedConfirmedRoute && !positionRouteReady && <header className="hr-confirmed-position-bar">
        <PlatformLink href="/hr/positions">← 岗位库</PlatformLink>
        <div><span>{continuedPositionDetailState === "error" ? "岗位资料加载失败" : "已确认岗位"}</span>
          <h1>{activeConfirmedPosition?.positionPackage.title ?? "岗位对话"}</h1></div>
        <div className="hr-confirmed-position-actions">
          <strong>已加入岗位库</strong>
          <button onClick={() => setPositionDetailsOpen(true)} type="button">岗位资料</button>
          {continuedPositionDetailState === "error" && <button
            onClick={() => setContinuedPositionDetailAttempt((value) => value + 1)} type="button"
          >重新读取岗位资料</button>}
        </div>
      </header>}
      <WorkspaceErrorBoundary title="HR 智能工作台">
        <DirectAgentWorkspace
          account={props.account}
          agentId="hr-bot"
          autoFocusComposer
          conversationId={chatConversationId}
          conversationPath={positionRouteReady ? positionConversationPath : hrConversationPath}
          createdConversationPath={selectedChatPosition && !positionRouteReady
            ? (id) => {
              lastChatTarget.current = { conversationId: id, positionId: selectedChatPosition.positionId };
              return `/hr/positions/${encodeURIComponent(selectedChatPosition.positionId)}/conversations/${encodeURIComponent(id)}`;
            }
            : positionRouteReady ? positionConversationPath : hrConversationPath}
          header={chatPosition.status}
          positionMaterialIds={chatPosition.detail?.materialAttachmentIds}
          positionArtifactAttachmentIds={chatPosition.detail?.artifactAttachmentIds}
          onPositionMaterialChange={activeChatPositionId && !props.account.hard_stale_read_only ? chatPosition.changeMaterial : undefined}
          composerTools={(pending) => <><HrPositionPicker
            api={positionApi}
            selected={positionRouteReady ? continuedPositionDetail : chatConversationId ? null : selectedChatPosition}
            disabled={pending || chatPosition.working || props.account.hard_stale_read_only}
            existingConversation={Boolean(chatConversationId)}
            onOpenDetails={drawerDetail && activeChatPositionId ? () => { setPositionDetailsTab("position"); setPositionDetailsOpen(true); } : undefined}
            onSelect={(position) => {
              if (positionRouteReady && position?.positionId === props.positionId) return;
              setSelectedChatPosition(position);
              if (chatConversationId) navigate("/hr/");
            }}
          />{chatPosition.menu(pending)}</>}
          historyClient={positionRouteValidated ? historyClient : undefined}
          initialDraftSnapshot={freeChatDraftSnapshots.current.get(draftOwnerId)}
          key={`hr-chat:${draftOwnerId}`}
          layout={positionRouteReady ? "focused" : "standard"}
          newConversationScope={positionRouteReady && props.positionId ? { positionId: props.positionId } : selectedChatPosition ? { positionId: selectedChatPosition.positionId } : undefined}
          newConversationHeader={<section className="hr-conversation-welcome">
            <span>AI 招聘协作</span>
            <h1>{selectedChatPosition ? selectedChatPosition.title : "今天想推进哪项招聘工作？"}</h1>
            <p>{selectedChatPosition ? "已选择岗位，直接发送需求或上传材料，开始招聘协作。" : "直接聊，或在输入框旁搜索选择岗位，再一起推进招聘工作。"}</p>
          </section>}
          onDraftSnapshotChange={retainFreeChatDraft}
          onConversationSettled={handleConversationSettled}
          selectedKnowledgeResources={selectedKnowledge}
          onKnowledgeResourcesSubmitted={() => {
            knowledgeSelections.current.delete(knowledgeOwner); setKnowledgeRevision((value) => value + 1);
          }}
          onRemoveKnowledgeResource={(id) => {
            knowledgeSelections.current.set(knowledgeOwner, selectedKnowledge.filter((item) => item.id !== id));
            setKnowledgeRevision((value) => value + 1);
          }}
          showTaskStarters={false}
          showWorkspaceBackLink={false}
          threadSupplement={chatConversationId ? <HrConversationOutcomePanel
            confirmed={positionRouteValidated || trustedConfirmedRoute}
            conversationId={chatConversationId}
            csrfToken={props.account.csrf_token}
            onConfirmed={handleConfirmed}
            readOnly={props.account.hard_stale_read_only}
            refreshKey={conversationRevision}
          /> : undefined}
          workspaceLabel="HR 智能工作台"
          workspaceMark="HR"
          workspaceRootPath="/hr/"
        />
      </WorkspaceErrorBoundary>
    </div>}

    {positionConversationRoute && !positionThreadVisible && <div className="hr-workspace-position-panel">
      {positionRouteFailure === "invalid" ? <main className="hr-position-state" role="alert">
        <h1>无法打开这段岗位对话</h1>
        <p>该对话不属于这个岗位，已阻止显示和发送。</p>
        <PlatformLink href={`/hr/positions/${encodeURIComponent(props.positionId!)}`}>返回岗位</PlatformLink>
      </main> : positionRouteFailure === "error" ? <>
        <header className="hr-confirmed-position-bar">
          <PlatformLink href="/hr/positions">← 岗位库</PlatformLink>
          <div><span>岗位资料降级显示</span><h1>{degradedDetail?.title ?? "岗位资料"}</h1></div>
          <div className="hr-confirmed-position-actions">
            <button onClick={() => setPositionDetailsOpen(true)} type="button">岗位资料</button>
            <button onClick={() => setContinuedPositionDetailAttempt((value) => value + 1)} type="button">重新读取岗位资料</button>
          </div>
        </header>
        <main className="hr-position-state" role="alert"><p>岗位归属暂时无法验证，对话显示和发送已暂停。</p></main>
      </> : <main className="hr-position-state"><p>正在验证岗位对话归属…</p></main>}
    </div>}

    {positionsActive && !positionConversationRoute && <div className="hr-workspace-position-panel">
      <WorkspaceErrorBoundary title="HR 智能工作台">
        {props.positionId
          ? <HrPositionWorkspace account={props.account} conversationId={props.conversationId} positionId={props.positionId} section={props.section} />
          : <HrPositionIndex account={props.account} />}
      </WorkspaceErrorBoundary>
    </div>}

    {panoramaActive && <div className="hr-workspace-panorama-panel">
      <WorkspaceErrorBoundary title="全景分析">
        <HrPanoramaWorkspace account={props.account} insightVersionId={props.panoramaReportId} />
      </WorkspaceErrorBoundary>
    </div>}

    {(positionConversationRoute || activeChatPositionId) && drawerDetail && <HrPositionDetailsDrawer
      api={positionDetailsApi}
      activeTab={positionDetailsTab}
      onActiveTabChange={setPositionDetailsTab}
      csrfToken={props.account.csrf_token}
      currentContextVersionId={chatPosition.context?.contextVersionId ?? continuedPositionContext?.contextVersionId ?? null}
      contextRefreshGeneration={chatPosition.refreshGeneration}
      resourceRefreshGeneration={chatPosition.refreshGeneration}
      degraded={continuedPositionDetailState === "error"}
      detail={drawerDetail}
      onClose={() => setPositionDetailsOpen(false)}
      onConfirmed={(context) => { setContinuedPositionContext(context); chatPosition.confirmContext(context); }}
      onRetryDetail={() => setContinuedPositionDetailAttempt((value) => value + 1)}
      open={positionDetailsOpen}
      readOnly={props.account.hard_stale_read_only}
      taskConversationId={positionRouteValidated ? props.conversationId : undefined}
    />}
    {knowledgeOpen && <HrKnowledgePanel onClose={() => setKnowledgeOpen(false)} onSelect={(selection) => {
      knowledgeSelections.current.set(knowledgeOwner, [selection]);
      setKnowledgeRevision((value) => value + 1);
      setKnowledgeOpen(false);
      navigate(chatHref);
    }} />}
  </HrWorkspaceShell>;
}
