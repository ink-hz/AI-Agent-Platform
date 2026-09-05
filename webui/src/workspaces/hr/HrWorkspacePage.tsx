import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import type { Conversation, ConversationPage } from "../../conversationTypes";
import { createHrApi } from "../../hrApi";
import { createHrR12Api } from "../../hrR12Api";
import type { HrContextVersion, HrPositionSection } from "../../hrR12Types";
import type { HrConfirmedPositionPackage, HrPositionDetail, HrPositionPackage } from "../../hrTypes";
import { directConversationPath } from "../../platform/workspaces";
import { navigate } from "../../router";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";
import {
  DirectAgentWorkspace, type AgentHistoryClient, type DirectAgentDraftSnapshot,
} from "../direct/DirectAgentWorkspace";
import { HrConversationOutcomePanel } from "./HrConversationOutcomePanel";
import { HrPanoramaWorkspace } from "./HrPanoramaWorkspace";
import { HrPositionDetailsDrawer } from "./HrPositionDetailsDrawer";
import { HrPositionHeader } from "./HrPositionHeader";
import { HrPositionIndex } from "./HrPositionIndex";
import { HrPositionWorkspace, loadPositionConversations } from "./HrPositionWorkspace";
import { HrWorkspaceShell } from "./HrWorkspaceShell";


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
  const draftOwnerId = props.account.internal_user_id;
  const retainFreeChatDraft = useCallback((snapshot: DirectAgentDraftSnapshot) => {
    freeChatDraftSnapshots.current.set(draftOwnerId, snapshot);
  }, [draftOwnerId]);

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
  const drawerDetail = continuedPositionDetail ?? degradedDetail;

  return <HrWorkspaceShell account={props.account} chatHref={chatHref} current={panoramaActive ? "panorama" : positionsActive ? "positions" : "chat"}>
    {keepChatHost && <div
      className={`hr-workspace-chat-panel${positionConversationRoute ? " is-position-conversation" : ""}`}
      hidden={panoramaActive || (positionsActive && !positionThreadVisible)}
    >
      {positionRouteReady && continuedPositionDetail && <HrPositionHeader
        detail={continuedPositionDetail}
        onNewConversation={() => navigate(`/hr/positions/${encodeURIComponent(continuedPositionDetail.positionId)}`)}
        onOpenDetails={() => setPositionDetailsOpen(true)}
        readOnly={props.account.hard_stale_read_only}
      />}
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
          historyClient={positionRouteValidated ? historyClient : undefined}
          initialDraftSnapshot={freeChatDraftSnapshots.current.get(draftOwnerId)}
          key={`hr-chat:${draftOwnerId}:${chatConversationId ?? "new"}`}
          layout={positionRouteReady ? "focused" : "standard"}
          newConversationScope={positionRouteReady && props.positionId ? { positionId: props.positionId } : undefined}
          newConversationHeader={<section className="hr-conversation-welcome">
            <span>AI 招聘协作</span>
            <h1>今天想推进哪项招聘工作？</h1>
            <p>找岗位、做人才研究、筛简历、准备面试或整理招聘材料，直接告诉我。</p>
          </section>}
          onDraftSnapshotChange={retainFreeChatDraft}
          showTaskStarters={false}
          showWorkspaceBackLink={false}
          threadSupplement={chatConversationId ? <HrConversationOutcomePanel
            confirmed={positionRouteValidated || trustedConfirmedRoute}
            conversationId={chatConversationId}
            csrfToken={props.account.csrf_token}
            onConfirmed={handleConfirmed}
            readOnly={props.account.hard_stale_read_only}
          /> : undefined}
          workspaceLabel="HR 智能工作台"
          workspaceMark="HR"
          workspaceRootPath={positionRouteReady && props.positionId
            ? `/hr/positions/${encodeURIComponent(props.positionId)}` : "/hr/"}
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

    {positionConversationRoute && drawerDetail && <HrPositionDetailsDrawer
      api={positionDetailsApi}
      csrfToken={props.account.csrf_token}
      currentContextVersionId={continuedPositionContext?.contextVersionId ?? null}
      degraded={continuedPositionDetailState === "error"}
      detail={drawerDetail}
      onClose={() => setPositionDetailsOpen(false)}
      onConfirmed={setContinuedPositionContext}
      onRetryDetail={() => setContinuedPositionDetailAttempt((value) => value + 1)}
      open={positionDetailsOpen}
      readOnly={props.account.hard_stale_read_only}
      taskConversationId={positionRouteValidated ? props.conversationId : undefined}
    />}
  </HrWorkspaceShell>;
}
