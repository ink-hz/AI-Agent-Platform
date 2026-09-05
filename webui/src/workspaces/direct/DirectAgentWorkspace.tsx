import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import type { Account } from "../../auth";
import { fetchAgentCatalog } from "../../brainApi";
import type { AgentCapabilityCard } from "../../brainTypes";
import { ConversationSidebar } from "../../components/conversation/ConversationSidebar";
import { AttachmentUploader, type AttachmentUploaderHandle, type UploadQueueItem } from "../../components/conversation/AttachmentUploader";
import { AttachmentCard } from "../../components/conversation/AttachmentCard";
import { ErrorState, LoadingState } from "../../components/DataState";
import { PlatformLink } from "../../components/PlatformLink";
import {
  archiveConversation,
  conversationInputTooLarge,
  listConversations,
  renameConversation,
  restoreConversation,
  startConversation,
  type ConversationStartScope,
  type ConversationSubmission,
} from "../../conversationApi";
import type { Conversation, ConversationAttachment, ConversationPage, TurnSubmission } from "../../conversationTypes";
import { ConversationPage as ConversationThread, type ConversationPageClient } from "../../pages/ConversationPage";
import { workspaceLaunchPath } from "../../platform/workspaces";
import { navigate } from "../../router";


export interface DirectAgentWorkspaceProps {
  account: Account;
  agentId: string;
  conversationId?: string;
  conversationPath: (conversationId: string) => string;
  header?: ReactNode;
  workspaceLabel?: string;
  workspaceMark?: string;
  workspaceRootPath?: string;
  newConversationScope?: ConversationStartScope;
  autoFocusComposer?: boolean;
  showWorkspaceBackLink?: boolean;
  newConversationHeader?: ReactNode;
  positionMaterialIds?: readonly string[];
  positionArtifactAttachmentIds?: readonly string[];
  onPositionMaterialChange?: (attachment: ConversationAttachment, active: boolean) => void | Promise<void>;
  showTaskStarters?: boolean;
  layout?: "standard" | "focused";
  composerTools?: ReactNode;
  threadSupplement?: ReactNode;
  initialDraftSnapshot?: DirectAgentDraftSnapshot;
  onDraftSnapshotChange?: (snapshot: DirectAgentDraftSnapshot) => void;
  onConversationSettled?: () => void;
}

export interface DirectAgentDraftSnapshot {
  text: string;
  attachments: ConversationAttachment[];
  uploadQueue: UploadQueueItem[];
}

export interface AgentHistoryClient {
  list(signal?: AbortSignal, before?: string, limit?: number, directAgentId?: string, status?: "active" | "archived"): Promise<ConversationPage>;
}

export interface DirectAgentWorkspaceDependencies {
  loadCatalog?: (signal?: AbortSignal) => Promise<AgentCapabilityCard[]>;
  createSubmission?: (input: string | TurnSubmission, csrfToken: string, agentId?: string, scope?: ConversationStartScope) => ConversationSubmission;
  historyClient?: AgentHistoryClient;
  conversationClient?: ConversationPageClient;
  onOpenConversation?: (path: string) => void;
}

const DEFAULT_HISTORY_CLIENT: AgentHistoryClient = { list: listConversations };

function mergeConversations(current: Conversation[], incoming: Conversation[]): Conversation[] {
  return [...new Map([...current, ...incoming].map((item) => [item.conversation_id, item])).values()]
    .sort((left, right) => new Date(right.updated_at).valueOf() - new Date(left.updated_at).valueOf());
}

function rootPath(agentId: string): string {
  return workspaceLaunchPath(agentId) ?? `/agents/${encodeURIComponent(agentId)}`;
}


export function DirectAgentWorkspace({
  account,
  agentId,
  conversationId,
  conversationPath,
  header,
  workspaceLabel,
  workspaceMark,
  workspaceRootPath,
  newConversationScope,
  autoFocusComposer = false,
  showWorkspaceBackLink = true,
  newConversationHeader,
  positionMaterialIds,
  positionArtifactAttachmentIds,
  onPositionMaterialChange,
  showTaskStarters = true,
  layout = "standard",
  composerTools,
  threadSupplement,
  initialDraftSnapshot,
  onDraftSnapshotChange,
  onConversationSettled,
  loadCatalog = fetchAgentCatalog,
  createSubmission = startConversation,
  historyClient = DEFAULT_HISTORY_CLIENT,
  conversationClient,
  onOpenConversation = (path) => navigate(path),
}: DirectAgentWorkspaceProps & DirectAgentWorkspaceDependencies) {
  const [catalog, setCatalog] = useState<AgentCapabilityCard[] | null>(null);
  const [loadFailure, setLoadFailure] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [archivedConversations, setArchivedConversations] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyFailure, setHistoryFailure] = useState(false);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [text, setText] = useState(() => initialDraftSnapshot?.text ?? "");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState(false);
  const [attachments, setAttachments] = useState<ConversationAttachment[]>(
    () => initialDraftSnapshot?.attachments.map((item) => ({ ...item })) ?? [],
  );
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>(
    () => initialDraftSnapshot?.uploadQueue.map((item) => ({ ...item })) ?? [],
  );
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const retained = useRef<{ text: string; submission: ConversationSubmission } | null>(null);
  const inFlight = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const uploaderRef = useRef<AttachmentUploaderHandle | null>(null);
  const card = catalog?.find((item) => item.agent_id === agentId) ?? null;
  const inputTooLarge = conversationInputTooLarge(text.trim());
  const submitDisabled = (!text.trim() && attachments.length === 0)
    || uploadQueue.some((item) => ["queued", "uploading", "processing"].includes(item.state))
    || inputTooLarge || pending || account.hard_stale_read_only;
  const workspacePath = workspaceRootPath ?? rootPath(agentId);

  useEffect(() => {
    const controller = new AbortController();
    setLoadFailure(false);
    loadCatalog(controller.signal).then((cards) => {
      if (!controller.signal.aborted) setCatalog(cards);
    }).catch(() => { if (!controller.signal.aborted) setLoadFailure(true); });
    return () => controller.abort();
  }, [loadCatalog]);

  useEffect(() => {
    if (!card || !card.interaction_modes.includes("direct_chat")) {
      setHistoryLoading(false);
      return;
    }
    const controller = new AbortController();
    setConversations([]); setCursor(null); setHistoryLoading(true); setHistoryFailure(false);
    void historyClient.list(controller.signal, undefined, 20, agentId).then((page) => {
      if (controller.signal.aborted) return;
      setConversations(page.items); setCursor(page.next_cursor); setHistoryLoading(false);
    }).catch(() => {
      if (!controller.signal.aborted) { setHistoryFailure(true); setHistoryLoading(false); }
    });
    return () => controller.abort();
  }, [agentId, card, historyAttempt, historyClient]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  useEffect(() => {
    onDraftSnapshotChange?.({
      text,
      attachments: attachments.map((item) => ({ ...item })),
      uploadQueue: uploadQueue.map((item) => ({ ...item })),
    });
  }, [attachments, onDraftSnapshotChange, text, uploadQueue]);

  const upsertConversation = useCallback((conversation: Conversation) => {
    if (conversation.direct_agent_id === agentId) {
      setConversations((current) => mergeConversations(current, [conversation]));
    }
  }, [agentId]);

  const loadMore = async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true); setHistoryFailure(false);
    try {
      const page = await historyClient.list(undefined, cursor, 20, agentId);
      setConversations((current) => mergeConversations(current, page.items));
      setCursor(page.next_cursor);
    } catch {
      setHistoryFailure(true);
    } finally {
      setLoadingMore(false);
    }
  };

  const loadArchived = async () => {
    try {
      const page = await historyClient.list(undefined, undefined, 100, agentId, "archived");
      setArchivedConversations(page.items);
    } catch {
      setHistoryFailure(true);
    }
  };

  const renameHistory = async (selectedId: string, title: string) => {
    const updated = await renameConversation(selectedId, title, account.csrf_token);
    setConversations((current) => current.map((item) => item.conversation_id === selectedId ? updated : item));
  };

  const archiveHistory = async (selectedId: string) => {
    const archived = await archiveConversation(selectedId, account.csrf_token);
    setConversations((current) => current.filter((item) => item.conversation_id !== selectedId));
    setArchivedConversations((current) => mergeConversations(current, [archived]));
    if (selectedId === conversationId) onOpenConversation(workspacePath);
  };

  const restoreHistory = async (selectedId: string) => {
    const restored = await restoreConversation(selectedId, account.csrf_token);
    setArchivedConversations((current) => current.filter((item) => item.conversation_id !== selectedId));
    setConversations((current) => mergeConversations(current, [restored]));
  };

  const send = async () => {
    const normalized = text.trim();
    const readyIds = attachments.filter((item) => item.state === "ready").map((item) => item.attachmentId);
    const uploadPending = uploadQueue.some((item) => ["queued", "uploading", "processing"].includes(item.state));
    if (!card || (!normalized && readyIds.length === 0) || uploadPending || inputTooLarge || inFlight.current || account.hard_stale_read_only) return;
    const input: string | TurnSubmission = card.attachment_limits ? {
      text: normalized, attachmentIds: readyIds, activeAttachmentIds: readyIds,
    } : normalized;
    const requestKey = typeof input === "string" ? input : JSON.stringify(input);
    let selected = retained.current;
    if (!selected || selected.text !== requestKey) {
      selected = { text: requestKey, submission: newConversationScope
        ? createSubmission(input, account.csrf_token, card.agent_id, newConversationScope)
        : createSubmission(input, account.csrf_token, card.agent_id) };
      retained.current = selected;
    }
    const controller = new AbortController();
    controllerRef.current?.abort(); controllerRef.current = controller;
    inFlight.current = true; setPending(true); setFailure(false);
    try {
      const result = await selected.submission.send(controller.signal);
      retained.current = null; upsertConversation(result.conversation);
      onOpenConversation(conversationPath(result.conversation.conversation_id));
    } catch {
      if (!controller.signal.aborted) setFailure(true);
    } finally {
      if (controllerRef.current === controller) {
        inFlight.current = false;
        if (!controller.signal.aborted) setPending(false);
      }
    }
  };

  const submit = (event: FormEvent) => { event.preventDefault(); void send(); };

  if (loadFailure) return <>{showWorkspaceBackLink && <PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink>}<ErrorState /></>;
  if (!catalog) return <LoadingState label="正在打开专业 Agent" />;
  if (!card || !card.interaction_modes.includes("direct_chat")) {
    return <>{showWorkspaceBackLink && <PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink>}<ErrorState /></>;
  }

  return <div className={`brain-workspace agent-use-workspace${layout === "focused" ? " is-focused" : ""}`} data-agent-id={agentId}>
    <button aria-expanded={mobileOpen} aria-label={layout === "focused" ? "打开对话记录" : "打开对话列表"} className="brain-workspace-menu" onClick={() => setMobileOpen(true)} type="button">☰</button>
    {mobileOpen && <button aria-label={layout === "focused" ? "关闭对话列表遮罩" : "关闭对话列表"} className="conversation-sidebar-backdrop" onClick={() => setMobileOpen(false)} type="button" />}
    <ConversationSidebar
      archivedConversations={archivedConversations}
      conversationHref={conversationPath}
      title={card.display_name}
      label={workspaceLabel}
      mark={workspaceMark}
      conversations={conversations}
      selectedConversationId={conversationId}
      loading={historyLoading}
      error={historyFailure}
      hasMore={cursor !== null}
      loadingMore={loadingMore}
      mobileOpen={mobileOpen}
      onCloseMobile={() => setMobileOpen(false)}
      onArchive={account.hard_stale_read_only ? undefined : archiveHistory}
      onLoadArchived={() => void loadArchived()}
      onLoadMore={() => void loadMore()}
      onNewConversation={() => { setMobileOpen(false); onOpenConversation(workspacePath); }}
      onRename={account.hard_stale_read_only ? undefined : renameHistory}
      onRestore={account.hard_stale_read_only ? undefined : restoreHistory}
      onRetry={() => setHistoryAttempt((value) => value + 1)}
      onOpenConversation={(selected) => {
        setConversations((current) => current.map((item) => item.conversation_id === selected ? { ...item, unread: false } : item));
        onOpenConversation(conversationPath(selected));
      }}
    />
    <section className="brain-workspace-main">
      {header}
      {conversationId
        ? <ConversationThread
          account={account}
          assistantLabel={card.display_name}
          client={conversationClient}
          conversationId={conversationId}
          expectedAgentId={agentId}
          attachmentLimits={card.attachment_limits}
          positionMaterialIds={positionMaterialIds}
          positionArtifactAttachmentIds={positionArtifactAttachmentIds}
          onPositionMaterialChange={onPositionMaterialChange}
          onConversationSettled={onConversationSettled}
          onConversationUpdated={upsertConversation}
          personaSubtitle={card.persona_subtitle}
          composerTools={composerTools}
          messageActionsPresentation={agentId === "hr-bot" ? "icon" : "legacy"}
          threadSupplement={threadSupplement}
          materialsPresentation={layout === "focused" ? "hidden" : "sidebar"}
        />
        : <div className="agent-use-page">{showWorkspaceBackLink && <PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink>}
          {newConversationHeader ?? <section className="agent-use-profile is-compact"><span>{card.domain_group}</span><h1>{card.display_name}</h1>
            {card.persona_subtitle && <p className="agent-persona-subtitle">{card.persona_subtitle}</p>}
            <p>{card.mission}</p>
          </section>}
          {showTaskStarters && <section aria-label="常用任务" className="agent-task-starters">
            {card.example_tasks.slice(0, 4).map((example) => <button
              className="agent-task-starter"
              key={example}
              onClick={() => { setText(example); retained.current = null; setFailure(false); }}
              type="button"
            >{example}</button>)}
          </section>}
          <form className="agent-direct-composer" onSubmit={submit}
            onDragOver={(event) => {
              if (Array.from(event.dataTransfer.types).includes("Files")) event.preventDefault();
            }}
            onDrop={(event) => {
              if ((event.target as Element).closest(".attachment-uploader")) return;
              const files = Array.from(event.dataTransfer.files);
              if (files.length > 0) {
                event.preventDefault();
                uploaderRef.current?.addFiles(files);
              }
            }}
            onPaste={(event) => {
              if ((event.target as Element).closest(".attachment-uploader")) return;
              const files = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
              if (files.length > 0) {
                event.preventDefault();
                uploaderRef.current?.addFiles(files);
              }
            }}>
            <textarea aria-label={`交给 ${card.display_name}`} autoFocus={autoFocusComposer} id="direct-agent-request" rows={8} maxLength={32 * 1024} value={text} disabled={account.hard_stale_read_only}
              placeholder="描述招聘任务、粘贴岗位说明或候选人资料……"
              onChange={(event) => { const next = event.target.value; setText(next); if (retained.current?.text !== next.trim()) retained.current = null; setFailure(false); }}
              onKeyDown={(event) => {
                if (event.key !== "Enter" || event.shiftKey
                  || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229
                  || submitDisabled) return;
                event.preventDefault();
                void send();
              }} />
            {card.attachment_limits && <section className="agent-direct-attachments" aria-label="新对话附件">
              <AttachmentUploader ref={uploaderRef} acceptedInputTypes={card.accepted_input_types} conversationId={null}
                compact={agentId === "hr-bot"} csrfToken={account.csrf_token} disabled={account.hard_stale_read_only}
                initialItems={initialDraftSnapshot?.uploadQueue} limits={card.attachment_limits} onChange={setUploadQueue} onError={setAttachmentError}
                onReady={(attachment) => { setAttachments((current) => [...current, attachment]); setFailure(false); retained.current = null; }}
                onRemoveReady={(attachment) => {
                  setAttachments((current) => current.filter((item) => item.attachmentId !== attachment.attachmentId));
                  setFailure(false);
                  retained.current = null;
                }} />
              {attachments.map((attachment) => <AttachmentCard active attachment={attachment} key={attachment.attachmentId}
                onActiveChange={() => undefined} />)}
              {attachmentError && <p className="conversation-action-error" role="alert">{attachmentError}</p>}
            </section>}
            <div className="agent-direct-composer-actions">{composerTools && <div className="conversation-composer-tools">{composerTools}</div>}<span>Enter 发送；Shift+Enter 换行。文字、图片和文件会随本轮一起发送。</span><button className="agent-direct-submit" disabled={submitDisabled} type="submit">{pending ? "正在创建…" : "发送"}</button></div>
          </form>
          {inputTooLarge && <p className="mission-input-error" role="alert">输入超过 32 KiB，请精简后再提交。</p>}
          {failure && <div className="brain-submit-error" role="alert"><span>对话暂未创建成功，可安全重试。</span><button onClick={() => void send()} type="button">重新提交</button></div>}
        </div>}
    </section>
  </div>;
}
