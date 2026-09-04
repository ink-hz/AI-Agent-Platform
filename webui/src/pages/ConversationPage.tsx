import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import type { Account } from "../auth";
import {
  deleteConversationAttachment,
  downloadConversationArtifacts,
  issueAttachmentTicket,
  listConversationAttachments,
} from "../attachmentApi";
import {
  cancelCurrentTurn,
  confirmConversationAction,
  createConversationMessageSubmission,
  fetchConversation,
  fetchConversationMessages,
  fetchConversationTaskDetail,
  markConversationRead,
  rejectConversationAction,
  retryConversationTurn,
  resumeConversationSearch,
  streamConversationEvents,
  submitConversationFeedback,
  type ConversationStreamOptions,
  type ConversationSubmission,
} from "../conversationApi";
import type {
  Conversation,
  ConversationCancelResult,
  ConversationDetail,
  ConversationEvent,
  ConversationFeedback,
  ConversationFeedbackRating,
  ConversationFeedbackReason,
  ConversationInterventionResult,
  ConversationMessage,
  ConversationSubmissionResult,
  ConversationTaskDetail,
  ConversationAttachment,
  TurnSubmission,
} from "../conversationTypes";
import { TERMINAL_CONVERSATION_TURN_STATUSES } from "../conversationTypes";
import type { WorkroomAction } from "../workroomTypes";
import { reconnectDelay } from "../brainApi";
import { ConversationComposer } from "../components/conversation/ConversationComposer";
import { ConversationMessages } from "../components/conversation/ConversationMessages";
import { AttachmentUploader, type UploadQueueItem } from "../components/conversation/AttachmentUploader";
import { SessionMaterialsDrawer } from "../components/conversation/SessionMaterialsDrawer";
import { MultiAgentWorkroom } from "../components/conversation/MultiAgentWorkroom";
import { PublicProgress } from "../components/conversation/PublicProgress";
import { UserInputRequest } from "../components/conversation/UserInputRequest";
import { projectWorkroom } from "../workroomProjection";


export interface ConversationPageClient {
  fetchConversation(conversationId: string, signal?: AbortSignal): Promise<ConversationDetail>;
  fetchMessages(conversationId: string, signal?: AbortSignal): Promise<ConversationMessage[]>;
  createMessageSubmission(conversationId: string, input: string | TurnSubmission, csrfToken: string): ConversationSubmission<ConversationSubmissionResult | ConversationInterventionResult>;
  fetchTaskDetail(conversationId: string, turnId: string, taskId: string, signal?: AbortSignal): Promise<ConversationTaskDetail>;
  streamEvents(conversationId: string, options: ConversationStreamOptions): Promise<void>;
  cancelCurrentTurn(conversationId: string, csrfToken: string, signal?: AbortSignal): Promise<ConversationCancelResult>;
  confirmAction(conversationId: string, actionId: string, actionDigest: string, csrfToken: string, signal?: AbortSignal): Promise<WorkroomAction>;
  rejectAction(conversationId: string, actionId: string, csrfToken: string, signal?: AbortSignal): Promise<WorkroomAction>;
  submitFeedback(messageId: string, rating: ConversationFeedbackRating, reason: ConversationFeedbackReason | null, comment: string | null, csrfToken: string, signal?: AbortSignal): Promise<ConversationFeedback>;
  retryTurn(conversationId: string, turnId: string, csrfToken: string): ConversationSubmission;
  reconnectDelay(signal: AbortSignal): Promise<void>;
  listAttachments?(conversationId: string, signal?: AbortSignal): Promise<ConversationAttachment[]>;
  issueAttachmentTicket?(attachmentId: string, purpose: "preview" | "download", csrfToken: string, signal?: AbortSignal): Promise<{ contentPath: string }>;
  deleteAttachment?(attachmentId: string, csrfToken: string, signal?: AbortSignal): Promise<void>;
  downloadArtifacts?(conversationId: string, csrfToken: string, signal?: AbortSignal): Promise<void>;
  resumeSearch?(conversationId: string, turnId: string, csrfToken: string): ConversationSubmission;
  markRead?(conversationId: string, lastSeenEventSeq: number, csrfToken: string, signal?: AbortSignal): Promise<unknown>;
}

const DEFAULT_CLIENT: ConversationPageClient = {
  fetchConversation,
  fetchMessages: fetchConversationMessages,
  createMessageSubmission: createConversationMessageSubmission,
  fetchTaskDetail: fetchConversationTaskDetail,
  streamEvents: streamConversationEvents,
  cancelCurrentTurn,
  confirmAction: confirmConversationAction,
  rejectAction: rejectConversationAction,
  submitFeedback: submitConversationFeedback,
  retryTurn: retryConversationTurn,
  reconnectDelay,
  listAttachments: listConversationAttachments,
  issueAttachmentTicket,
  deleteAttachment: deleteConversationAttachment,
  downloadArtifacts: downloadConversationArtifacts,
  resumeSearch: resumeConversationSearch,
  markRead: markConversationRead,
};


function mergeMessages(current: ConversationMessage[], incoming: ConversationMessage[]): ConversationMessage[] {
  return [...new Map([...current, ...incoming].map((message) => [message.message_id, message])).values()]
    .sort((left, right) => left.seq - right.seq);
}


function mergeEvent(current: ConversationEvent[], incoming: ConversationEvent): ConversationEvent[] {
  const byId = new Map(current.map((event) => [event.event_id, event]));
  if (!byId.has(incoming.event_id)) byId.set(incoming.event_id, incoming);
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}


function turnIsActive(detail: ConversationDetail | null): boolean {
  return Boolean(detail?.current_turn && !TERMINAL_CONVERSATION_TURN_STATUSES.has(detail.current_turn.status));
}


export function ConversationPage({
  conversationId,
  account,
  client = DEFAULT_CLIENT,
  onConversationUpdated,
  expectedAgentId,
  assistantLabel = "Agent 大脑",
  personaSubtitle,
  attachmentLimits,
  positionMaterialIds,
  positionArtifactAttachmentIds,
  onPositionMaterialChange,
  composerTools,
  threadSupplement,
  materialsPresentation = "sidebar",
}: {
  conversationId: string;
  account: Account;
  client?: ConversationPageClient;
  onConversationUpdated?: (conversation: Conversation) => void;
  expectedAgentId?: string;
  assistantLabel?: string;
  personaSubtitle?: string | null;
  attachmentLimits?: { max_file_bytes: number; max_files_per_message: number; max_bytes_per_message: number; max_files_per_conversation: number; max_bytes_per_conversation: number } | null;
  positionMaterialIds?: readonly string[];
  positionArtifactAttachmentIds?: readonly string[];
  onPositionMaterialChange?: (attachment: ConversationAttachment, active: boolean) => void | Promise<void>;
  composerTools?: ReactNode;
  threadSupplement?: ReactNode;
  materialsPresentation?: "sidebar" | "hidden";
}) {
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [events, setEvents] = useState<ConversationEvent[]>([]);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadFailure, setLoadFailure] = useState(false);
  const [connection, setConnection] = useState<"connecting" | "live" | "offline">("connecting");
  const [pending, setPending] = useState(false);
  const [sendFailure, setSendFailure] = useState(false);
  const [cancelFailure, setCancelFailure] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [feedback, setFeedback] = useState<Record<string, ConversationFeedbackRating | "pending" | "error">>({});
  const [streamEpoch, setStreamEpoch] = useState(0);
  const [attachments, setAttachments] = useState<ConversationAttachment[]>([]);
  const [activeAttachmentIds, setActiveAttachmentIds] = useState<string[]>([]);
  const [newAttachmentIds, setNewAttachmentIds] = useState<string[]>([]);
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const retained = useRef<{
    text: string;
    submission: ConversationSubmission<ConversationSubmissionResult | ConversationInterventionResult>;
  } | null>(null);
  const writeController = useRef<AbortController | null>(null);
  const eventCursor = useRef(0);
  const inFlight = useRef(false);
  const readOnly = account.hard_stale_read_only || detail?.conversation.status === "archived";
  const loadTaskDetail = useCallback(
    (turnId: string, taskId: string, signal: AbortSignal) => client.fetchTaskDetail(
      conversationId, turnId, taskId, signal,
    ),
    [client, conversationId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null); setMessages([]); setEvents([]); setLoading(true); setLoadFailure(false);
    setText(""); setSendFailure(false); setCancelFailure(false); setCancelRequested(false);
    setFeedback({}); setAttachments([]); setActiveAttachmentIds([]); setNewAttachmentIds([]); setUploadQueue([]); setAttachmentError(null);
    retained.current = null; eventCursor.current = 0;
    void Promise.all([
      client.fetchConversation(conversationId, controller.signal),
      client.fetchMessages(conversationId, controller.signal),
      attachmentLimits && client.listAttachments ? client.listAttachments(conversationId, controller.signal) : Promise.resolve([]),
    ]).then(([snapshot, loadedMessages, loadedAttachments]) => {
      if (controller.signal.aborted) return;
      if (expectedAgentId && (
        snapshot.conversation.mode !== "direct_agent"
        || snapshot.conversation.direct_agent_id !== expectedAgentId
      )) throw new Error("Conversation Agent scope mismatch");
      const projected = loadedMessages.flatMap((message) => [...message.input_attachments, ...message.output_attachments]);
      const materialMap = new Map([...loadedAttachments, ...projected].map((item) => [item.attachmentId, item]));
      const lastUser = [...loadedMessages].reverse().find((message) => message.role === "user");
      setAttachments([...materialMap.values()]); setActiveAttachmentIds(lastUser?.active_attachment_ids ?? []);
      setDetail(snapshot); setMessages(loadedMessages); setLoading(false); setStreamEpoch((value) => value + 1);
      onConversationUpdated?.(snapshot.conversation);
    }).catch(() => {
      if (!controller.signal.aborted) { setLoadFailure(true); setLoading(false); }
    });
    return () => { controller.abort(); writeController.current?.abort(); };
  }, [attachmentLimits, client, conversationId, expectedAgentId, onConversationUpdated]);

  useEffect(() => {
    if (!streamEpoch || !detail) return;
    const controller = new AbortController();
    const refreshSnapshot = async () => {
      const [snapshot, loadedMessages] = await Promise.all([
        client.fetchConversation(conversationId, controller.signal),
        client.fetchMessages(conversationId, controller.signal),
      ]);
      if (controller.signal.aborted) return null;
      setDetail(snapshot); setMessages((current) => mergeMessages(current, loadedMessages));
      return snapshot;
    };
    const run = async () => {
      while (!controller.signal.aborted) {
        setConnection(eventCursor.current === 0 ? "connecting" : "live");
        try {
          await client.streamEvents(conversationId, {
            after: eventCursor.current,
            signal: controller.signal,
            onEvent: (event) => {
              if (controller.signal.aborted || event.conversation_id !== conversationId || event.seq <= eventCursor.current) return;
              eventCursor.current = event.seq;
              setEvents((current) => mergeEvent(current, event));
              setConnection("live");
              if (!account.hard_stale_read_only && client.markRead && [
                "brain.answer_submitted", "brain.failed", "brain.user_input_requested",
              ].includes(event.event_type)) {
                void client.markRead(
                  conversationId, event.seq, account.csrf_token, controller.signal,
                ).catch(() => undefined);
              }
            },
          });
          const snapshot = await refreshSnapshot();
          if (!snapshot) return;
          setConnection("live");
          if (!turnIsActive(snapshot)) return;
          setConnection("offline");
        } catch {
          if (controller.signal.aborted) return;
          try {
            const snapshot = await refreshSnapshot();
            if (!snapshot) return;
            if (!turnIsActive(snapshot)) {
              setConnection("live");
              return;
            }
          } catch {
            if (controller.signal.aborted) return;
          }
          setConnection("offline");
        }
        await client.reconnectDelay(controller.signal);
      }
    };
    void run();
    return () => controller.abort();
  // streamEpoch deliberately starts a fresh stream after a newly accepted Turn.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.csrf_token, account.hard_stale_read_only, client, conversationId, streamEpoch]);

  const sendValue = async (value: string) => {
    const normalized = value.trim();
    const waitingUser = detail?.current_turn?.status === "waiting_user";
    if ((!normalized && newAttachmentIds.length === 0) || inFlight.current || readOnly
      || (turnIsActive(detail) && detail?.conversation.mode === "direct_agent" && !waitingUser)) return;
    const submissionInput: TurnSubmission = {
      text: normalized,
      attachmentIds: [...newAttachmentIds],
      activeAttachmentIds: [...activeAttachmentIds],
    };
    const submissionKey = JSON.stringify(submissionInput);
    let selected = retained.current;
    if (!selected || selected.text !== submissionKey) {
      selected = {
        text: submissionKey,
        submission: client.createMessageSubmission(
          conversationId,
          attachmentLimits || newAttachmentIds.length > 0 || activeAttachmentIds.length > 0 ? submissionInput : normalized,
          account.csrf_token,
        ),
      };
      retained.current = selected;
    }
    const controller = new AbortController();
    writeController.current?.abort(); writeController.current = controller;
    inFlight.current = true; setPending(true); setSendFailure(false);
    try {
      const result = await selected.submission.send(controller.signal);
      if (controller.signal.aborted) return;
      retained.current = null;
      setText("");
      setNewAttachmentIds([]); setUploadQueue([]);
      setMessages((current) => mergeMessages(current, [result.message]));
      if ("conversation" in result) {
        setDetail({ conversation: result.conversation, current_turn: result.turn });
        onConversationUpdated?.(result.conversation);
        setStreamEpoch((value) => value + 1);
      } else {
        setDetail((current) => current ? { ...current, current_turn: result.turn } : current);
      }
      setCancelRequested(false);
    } catch {
      if (!controller.signal.aborted) setSendFailure(true);
    } finally {
      if (writeController.current === controller) {
        inFlight.current = false;
        if (!controller.signal.aborted) setPending(false);
      }
    }
  };

  const send = async () => sendValue(text);

  const retryTurn = async () => {
    const turn = detail?.current_turn;
    if (!turn || !["failed", "interrupted"].includes(turn.status)
      || inFlight.current || readOnly) return;
    const controller = new AbortController();
    writeController.current?.abort(); writeController.current = controller;
    inFlight.current = true; setPending(true); setSendFailure(false);
    try {
      const result = await client.retryTurn(
        conversationId, turn.turn_id, account.csrf_token,
      ).send(controller.signal);
      if (controller.signal.aborted) return;
      setMessages((current) => mergeMessages(current, [result.message]));
      setDetail({ conversation: result.conversation, current_turn: result.turn });
      setStreamEpoch((value) => value + 1);
    } catch {
      if (!controller.signal.aborted) setSendFailure(true);
    } finally {
      if (writeController.current === controller) {
        inFlight.current = false;
        if (!controller.signal.aborted) setPending(false);
      }
    }
  };

  const resumeSearch = async (message: ConversationMessage) => {
    if (!message.turn_id || !message.search_recovery?.resumable || !client.resumeSearch
      || inFlight.current || readOnly) return;
    const controller = new AbortController();
    writeController.current?.abort(); writeController.current = controller;
    inFlight.current = true; setPending(true); setSendFailure(false);
    try {
      const result = await client.resumeSearch(
        conversationId, message.turn_id, account.csrf_token,
      ).send(controller.signal);
      if (controller.signal.aborted) return;
      setMessages((current) => mergeMessages(current, [result.message]));
      setDetail({ conversation: result.conversation, current_turn: result.turn });
      setStreamEpoch((value) => value + 1);
    } catch {
      if (!controller.signal.aborted) setSendFailure(true);
    } finally {
      if (writeController.current === controller) {
        inFlight.current = false;
        if (!controller.signal.aborted) setPending(false);
      }
    }
  };

  const stop = async () => {
    const controller = new AbortController();
    writeController.current?.abort(); writeController.current = controller;
    setCancelFailure(false);
    try {
      await client.cancelCurrentTurn(conversationId, account.csrf_token, controller.signal);
      if (!controller.signal.aborted) setCancelRequested(true);
    } catch {
      if (!controller.signal.aborted) setCancelFailure(true);
    }
  };

  const rate = async (messageId: string, rating: ConversationFeedbackRating, reason: ConversationFeedbackReason | null, comment: string | null) => {
    if (readOnly || feedback[messageId] === "pending") return;
    const controller = new AbortController();
    setFeedback((current) => ({ ...current, [messageId]: "pending" }));
    try {
      const result = await client.submitFeedback(
        messageId, rating, reason, comment, account.csrf_token, controller.signal,
      );
      setFeedback((current) => ({ ...current, [messageId]: result.rating }));
    } catch {
      setFeedback((current) => ({ ...current, [messageId]: "error" }));
    }
  };

  const addReadyAttachment = (attachment: ConversationAttachment) => {
    setAttachments((current) => [...new Map([...current, attachment].map((item) => [item.attachmentId, item])).values()]);
    setNewAttachmentIds((current) => current.includes(attachment.attachmentId) ? current : [...current, attachment.attachmentId]);
    setActiveAttachmentIds((current) => current.includes(attachment.attachmentId) ? current : [...current, attachment.attachmentId]);
    setAttachmentError(null); retained.current = null;
  };
  const toggleAttachment = (attachmentId: string, enabled: boolean) => {
    setActiveAttachmentIds((current) => enabled
      ? current.includes(attachmentId) ? current : [...current, attachmentId]
      : current.filter((value) => value !== attachmentId));
    retained.current = null;
  };
  const openAttachment = async (attachment: ConversationAttachment, purpose: "preview" | "download") => {
    if (!client.issueAttachmentTicket) return;
    try {
      const ticket = await client.issueAttachmentTicket(attachment.attachmentId, purpose, account.csrf_token);
      if (!/^\/api\/v1\/attachments\/content\/[A-Za-z0-9_-]+$/.test(ticket.contentPath)) throw new Error("invalid ticket path");
      window.open(ticket.contentPath, "_blank", "noopener,noreferrer"); setAttachmentError(null);
    } catch { setAttachmentError("附件暂时无法打开，请重试"); }
  };
  const removeAttachment = async (attachment: ConversationAttachment) => {
    if (!client.deleteAttachment) return;
    try {
      await client.deleteAttachment(attachment.attachmentId, account.csrf_token);
      setAttachments((current) => current.filter((item) => item.attachmentId !== attachment.attachmentId));
      setActiveAttachmentIds((current) => current.filter((id) => id !== attachment.attachmentId));
      setNewAttachmentIds((current) => current.filter((id) => id !== attachment.attachmentId));
    } catch { setAttachmentError("附件删除失败，请重试"); }
  };
  const downloadAllArtifacts = async () => {
    if (!client.downloadArtifacts) return;
    try {
      await client.downloadArtifacts(conversationId, account.csrf_token);
      setAttachmentError(null);
    } catch {
      setAttachmentError("结果文件暂时无法打包下载，请重试");
    }
  };

  if (loading) return <section className="conversation-load-state" aria-live="polite"><h1>正在打开对话</h1><p>正在读取已保存的消息与执行记录。</p></section>;
  if (loadFailure || !detail) return <section className="conversation-load-state" role="alert"><h1>暂时无法读取对话</h1><p>对话仍安全保存在平台，请稍后刷新。</p></section>;
  const active = turnIsActive(detail);
  const waitingUser = detail.current_turn?.status === "waiting_user";
  const waitingUserEvent = [...events].reverse().find(
    (event) => event.event_type === "brain.user_input_requested",
  );
  const waitingQuestion = waitingUserEvent?.payload.objective_summary;
  const stopButton = <button
    className="conversation-stop"
    disabled={cancelRequested || readOnly}
    onClick={() => void stop()}
    type="button"
  >{cancelRequested ? "正在停止" : "停止"}</button>;
  const workrooms = (() => {
    const grouped = new Map<string, ConversationEvent[]>();
    for (const item of events) {
      if (!item.turn_id) continue;
      const selected = grouped.get(item.turn_id) ?? [];
      selected.push(item); grouped.set(item.turn_id, selected);
    }
    return new Map([...grouped].flatMap(([turnId, selected]) => {
      const workroom = projectWorkroom(selected);
      return workroom ? [[turnId, workroom] as const] : [];
    }));
  })();
  const uploadPending = uploadQueue.some((item) => item.state === "queued" || item.state === "uploading" || item.state === "processing");
  const conversationContent = <div className="conversation-page">
    <header className="conversation-header">
      <div>
        <h1>{assistantLabel}</h1>
        {personaSubtitle && <p>{personaSubtitle}</p>}
      </div>
    </header>
    {connection === "offline" && <aside className="conversation-connection is-offline" role="status"><strong>连接暂时中断</strong><span>正在从上次进度继续连接，不会重复提交请求。</span></aside>}
    {connection === "connecting" && <aside className="conversation-connection" role="status">正在连接对话…</aside>}
    <ConversationMessages
      assistantLabel={assistantLabel}
      messages={messages}
      feedback={feedback}
      onDownloadAll={() => void downloadAllArtifacts()}
      onFeedback={readOnly ? undefined : (messageId, rating, reason, comment) => void rate(messageId, rating, reason, comment)}
      onOpenAttachment={(attachment, purpose) => void openAttachment(attachment, purpose)}
      onRetry={readOnly ? undefined : (message) => void resumeSearch(message)}
      renderAfterUserTurn={(turnId) => {
        const workroom = workrooms.get(turnId);
        return workroom ? <MultiAgentWorkroom
          loadTaskDetail={loadTaskDetail}
          onConfirmAction={readOnly ? undefined : (actionId, actionDigest) => client.confirmAction(
            conversationId, actionId, actionDigest, account.csrf_token,
          )}
          onRejectAction={readOnly ? undefined : (actionId) => client.rejectAction(
            conversationId, actionId, account.csrf_token,
          )}
          workroom={workroom}
        /> : null;
      }}
    />
    {threadSupplement}
    <PublicProgress
      active={active && !waitingUser}
      assistantLabel={assistantLabel}
      events={events.filter((event) => event.turn_id === detail.current_turn?.turn_id)}
      mode={detail.conversation.mode}
      stopButton={stopButton}
    />
    {cancelFailure && <p className="conversation-action-error" role="alert">停止请求暂未送达，请稍后重试。</p>}
    {waitingUser && typeof waitingQuestion === "string" && <UserInputRequest
      disabled={readOnly}
      onSubmit={(answer) => void sendValue(answer)}
      pending={pending}
      question={waitingQuestion}
    />}
    {detail.current_turn && ["failed", "interrupted"].includes(detail.current_turn.status)
      && <button className="conversation-turn-retry" disabled={pending || readOnly} onClick={() => void retryTurn()} type="button">重试本轮</button>}
    <ConversationComposer
      attachmentControls={attachmentLimits ? <AttachmentUploader
        conversationId={conversationId} csrfToken={account.csrf_token}
        compact={expectedAgentId === "hr-bot"}
        disabled={readOnly || (active && detail.conversation.mode === "direct_agent")}
        conversationBytes={attachments.filter((item) => item.source === "user").reduce((sum, item) => sum + item.sizeBytes, 0)}
        conversationFileCount={attachments.filter((item) => item.source === "user").length} onError={setAttachmentError}
        onQueueChange={setUploadQueue} onReady={addReadyAttachment}
      /> : undefined}
      attachmentPending={uploadPending}
      disabled={(active && (detail.conversation.mode === "direct_agent" || waitingUser)) || readOnly}
      disabledMessage={account.hard_stale_read_only
        ? "当前账号为只读状态。"
        : detail.conversation.status === "archived"
          ? "当前对话已归档，不能继续发送消息。"
          : waitingUser
            ? "请先回答上方问题。"
            : active && detail.conversation.mode === "direct_agent"
              ? `${assistantLabel} 正在处理上一条消息…`
              : undefined}
      label={active && detail.conversation.mode === "brain" ? "补充当前任务" : "继续对话"}
      onChange={(value) => {
        setText(value); setSendFailure(false);
        if (retained.current?.text !== value.trim()) retained.current = null;
      }}
      onSubmit={() => void send()}
      pending={pending}
      hasReadyAttachment={newAttachmentIds.length > 0}
      placeholder={active && detail.conversation.mode === "brain"
        ? "补充范围、修改优先级，或给正在协作的 Agent 新指令…"
        : undefined}
      value={text}
      tools={composerTools}
    />
    {readOnly && <p className="conversation-read-only" role="status">当前为只读状态，已有对话仍可查看。</p>}
    {sendFailure && <div className="conversation-action-error" role="alert"><span>消息暂未发送成功，可以使用同一次请求安全重试。</span><button className="conversation-retry" disabled={pending} onClick={() => void send()} type="button">重新发送</button></div>}
    {attachmentError && <p className="conversation-action-error" role="alert">{attachmentError}</p>}
  </div>;
  return attachmentLimits && materialsPresentation === "sidebar" ? <div className="conversation-workspace-grid">{conversationContent}<SessionMaterialsDrawer
    activeIds={activeAttachmentIds} attachments={attachments} limits={attachmentLimits} onDelete={(item) => void removeAttachment(item)}
    onOpen={(item, purpose) => void openAttachment(item, purpose)} onToggle={toggleAttachment}
    positionMaterialIds={positionMaterialIds} onPositionMaterialChange={onPositionMaterialChange}
    positionArtifactAttachmentIds={positionArtifactAttachmentIds}
    readOnly={readOnly}
  /></div> : conversationContent;
}
