import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Account } from "../../auth";
import { directConversationPath } from "../../platform/workspaces";
import { createHrApi } from "../../hrApi";
import { createHrR12Api } from "../../hrR12Api";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";
import { DirectAgentWorkspace, type DirectAgentDraftSnapshot } from "../direct/DirectAgentWorkspace";
import { HrPositionIndex } from "./HrPositionIndex";
import { HrPositionWorkspace } from "./HrPositionWorkspace";
import { HrWorkspaceShell } from "./HrWorkspaceShell";
import type { HrContextVersion, HrPositionSection } from "../../hrR12Types";
import type { HrConfirmedPositionPackage, HrPositionDetail, HrPositionPackage } from "../../hrTypes";
import { PlatformLink } from "../../components/PlatformLink";
import { navigate } from "../../router";
import { HrConversationOutcomePanel } from "./HrConversationOutcomePanel";
import { HrPositionDetailsDrawer } from "./HrPositionDetailsDrawer";
import { HrPositionHeader } from "./HrPositionHeader";


function hrConversationPath(conversationId: string): string {
  return directConversationPath("hr-bot", conversationId)
    ?? `/hr/conversations/${encodeURIComponent(conversationId)}`;
}


export function HrWorkspacePage(props: { account: Account; conversationId?: string; positionId?: string; section?: HrPositionSection; freeChat?: boolean; positions?: boolean }) {
  const positionsActive = Boolean(props.positions || props.positionId);
  const positionDetailActive = Boolean(props.positionId);
  const lastChatConversationId = useRef<string | undefined>(undefined);
  const freeChatDraftSnapshots = useRef(new Map<string, DirectAgentDraftSnapshot>());
  const [confirmedPosition, setConfirmedPosition] = useState<{
    confirmed: HrConfirmedPositionPackage; positionPackage: HrPositionPackage;
  } | null>(null);
  const positionApi = useMemo(() => createHrApi(props.account.csrf_token), [props.account.csrf_token]);
  const positionDetailsApi = useMemo(() => createHrR12Api(props.account.csrf_token), [props.account.csrf_token]);
  const [continuedPositionDetail, setContinuedPositionDetail] = useState<HrPositionDetail | null>(null);
  const [continuedPositionDetailState, setContinuedPositionDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [continuedPositionDetailAttempt, setContinuedPositionDetailAttempt] = useState(0);
  const [continuedPositionContext, setContinuedPositionContext] = useState<HrContextVersion | null>(null);
  const [positionDetailsOpen, setPositionDetailsOpen] = useState(false);
  const draftOwnerId = props.account.internal_user_id;
  const retainFreeChatDraft = useCallback((snapshot: DirectAgentDraftSnapshot) => {
    freeChatDraftSnapshots.current.set(draftOwnerId, snapshot);
  }, [draftOwnerId]);
  if (!positionsActive) lastChatConversationId.current = props.conversationId;
  const chatConversationId = props.conversationId ?? (positionsActive ? lastChatConversationId.current : undefined);
  const chatHref = chatConversationId
    ? hrConversationPath(chatConversationId)
    : "/hr/";
  const continuingPositionConversation = Boolean(
    positionDetailActive && props.conversationId && chatConversationId === props.conversationId,
  );
  const keepChatHost = !positionDetailActive || continuingPositionConversation;
  const positionConversationPath = (conversationId: string) => props.positionId
    ? `/hr/positions/${encodeURIComponent(props.positionId)}/conversations/${encodeURIComponent(conversationId)}`
    : hrConversationPath(conversationId);
  const handleConfirmed = useCallback((confirmed: HrConfirmedPositionPackage, positionPackage: HrPositionPackage) => {
    setConfirmedPosition({ confirmed, positionPackage });
  }, []);
  const activeConfirmedPosition = confirmedPosition
    && confirmedPosition.confirmed.positionId === props.positionId
    && confirmedPosition.confirmed.conversationId === props.conversationId
    ? confirmedPosition : null;

  useEffect(() => {
    setPositionDetailsOpen(false);
    setContinuedPositionDetail(null);
    setContinuedPositionDetailState("idle");
    setContinuedPositionContext(null);
    if (!continuingPositionConversation || !props.positionId) return;
    const controller = new AbortController();
    setContinuedPositionDetailState("loading");
    void positionApi.position(props.positionId, controller.signal).then((detail) => {
      if (!controller.signal.aborted) { setContinuedPositionDetail(detail); setContinuedPositionDetailState("ready"); }
    }).catch(() => { if (!controller.signal.aborted) setContinuedPositionDetailState("error"); });
    void positionDetailsApi.context(props.positionId, controller.signal).then((context) => {
      if (!controller.signal.aborted) setContinuedPositionContext(context.current);
    }).catch(() => undefined);
    return () => controller.abort();
  }, [continuedPositionDetailAttempt, continuingPositionConversation, positionApi, positionDetailsApi, props.positionId]);

  return <HrWorkspaceShell
    account={props.account}
    chatHref={chatHref}
    current={positionsActive ? "positions" : "chat"}
  >
    {keepChatHost && <div className={`hr-workspace-chat-panel${continuingPositionConversation ? " is-position-conversation" : ""}`} hidden={positionsActive && !continuingPositionConversation}>
      {continuingPositionConversation && continuedPositionDetail ? <HrPositionHeader
        detail={continuedPositionDetail}
        onNewConversation={() => navigate(`/hr/positions/${encodeURIComponent(continuedPositionDetail.positionId)}`)}
        onOpenDetails={() => setPositionDetailsOpen(true)}
        readOnly={props.account.hard_stale_read_only}
      /> : <header className="hr-confirmed-position-bar" hidden={!continuingPositionConversation}>
        <PlatformLink href="/hr/positions">← 岗位库</PlatformLink>
        <div><span>已确认岗位</span><h1>{activeConfirmedPosition?.positionPackage.title ?? "岗位对话"}</h1></div>
        <div className="hr-confirmed-position-actions"><strong>已加入岗位库</strong>{continuedPositionDetailState === "error"
          ? <button onClick={() => setContinuedPositionDetailAttempt((value) => value + 1)} type="button">重新读取岗位资料</button>
          : <button disabled type="button">{continuedPositionDetailState === "loading" ? "正在读取岗位资料…" : "岗位资料"}</button>}</div>
      </header>}
      <WorkspaceErrorBoundary title="HR 智能工作台">
        <DirectAgentWorkspace
          account={props.account}
          agentId="hr-bot"
          autoFocusComposer
          conversationId={chatConversationId}
          conversationPath={continuingPositionConversation ? positionConversationPath : hrConversationPath}
          initialDraftSnapshot={freeChatDraftSnapshots.current.get(draftOwnerId)}
          key={`hr-chat:${draftOwnerId}:${chatConversationId ?? "new"}`}
          layout={continuingPositionConversation ? "focused" : "standard"}
          newConversationScope={continuingPositionConversation && props.positionId ? { positionId: props.positionId } : undefined}
          newConversationHeader={<section className="hr-conversation-welcome">
            <span>AI 招聘协作</span>
            <h1>今天想推进哪项招聘工作？</h1>
            <p>找岗位、做人才研究、筛简历、准备面试或整理招聘材料，直接告诉我。</p>
          </section>}
          onDraftSnapshotChange={retainFreeChatDraft}
          showTaskStarters={false}
          showWorkspaceBackLink={false}
          threadSupplement={chatConversationId ? <HrConversationOutcomePanel
            conversationId={chatConversationId}
            csrfToken={props.account.csrf_token}
            onConfirmed={handleConfirmed}
            readOnly={props.account.hard_stale_read_only}
          /> : undefined}
          workspaceLabel="HR 智能工作台"
          workspaceMark="HR"
          workspaceRootPath={continuingPositionConversation && props.positionId
            ? `/hr/positions/${encodeURIComponent(props.positionId)}` : "/hr/"}
        />
      </WorkspaceErrorBoundary>
      {continuingPositionConversation && continuedPositionDetail && <HrPositionDetailsDrawer
        api={positionDetailsApi}
        csrfToken={props.account.csrf_token}
        currentContextVersionId={continuedPositionContext?.contextVersionId ?? null}
        detail={continuedPositionDetail}
        onClose={() => setPositionDetailsOpen(false)}
        onConfirmed={setContinuedPositionContext}
        open={positionDetailsOpen}
        readOnly={props.account.hard_stale_read_only}
      />}
    </div>}
    {positionsActive && !continuingPositionConversation && <div className="hr-workspace-position-panel">
      <WorkspaceErrorBoundary title="HR 智能工作台">
        {props.positionId
          ? <HrPositionWorkspace account={props.account} conversationId={props.conversationId} positionId={props.positionId} section={props.section} />
          : <HrPositionIndex account={props.account} />}
      </WorkspaceErrorBoundary>
    </div>}
  </HrWorkspaceShell>;
}
