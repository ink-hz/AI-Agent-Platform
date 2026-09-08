import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react";

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
  fetchConversationSnapshot,
  fetchConversationMessages,
  fetchConversationTurnMessages,
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
  HrKnowledgeSelection,
  HrTurnScope, HrComposerDraft, HrStandardConsent,
  TurnSubmission,
  TurnSnapshot,
} from "../conversationTypes";
import { TERMINAL_CONVERSATION_TURN_STATUSES } from "../conversationTypes";
import type { WorkroomAction } from "../workroomTypes";
import { reconnectDelay } from "../brainApi";
import { ConversationComposer } from "../components/conversation/ConversationComposer";
import { useConversationScroll } from "../components/conversation/useConversationScroll";
import { ConversationMessages } from "../components/conversation/ConversationMessages";
import { MessageMarkdown } from "../components/MessageMarkdown";
import type { MessageActionsPresentation } from "../components/conversation/MessageActions";
import { AttachmentUploader, type UploadQueueItem } from "../components/conversation/AttachmentUploader";
import { SessionMaterialsDrawer } from "../components/conversation/SessionMaterialsDrawer";
import { MultiAgentWorkroom } from "../components/conversation/MultiAgentWorkroom";
import { PublicProgress } from "../components/conversation/PublicProgress";
import { UserInputRequest } from "../components/conversation/UserInputRequest";
import { projectWorkroom } from "../workroomProjection";
import { scheduleSnapshotPolling } from "./snapshotPolling";


export interface ConversationPageClient {
  fetchSnapshot?(conversationId: string, signal?: AbortSignal): Promise<TurnSnapshot>;
  fetchTurnMessages?(conversationId: string, turnId: string, signal?: AbortSignal): Promise<ConversationMessage[]>;
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
  fetchSnapshot: fetchConversationSnapshot,
  fetchTurnMessages: fetchConversationTurnMessages,
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
  const deliveryRank = (status: ConversationMessage["delivery_status"]) => ({
    accepted: 0, streaming: 1, completed: 2, failed: 2,
  })[status];
  const mergeResultStatus = (
    accepted: ConversationMessage["result_delivery_status"],
    next: ConversationMessage["result_delivery_status"],
  ) => {
    if (accepted === "completed" || accepted === "failed") return accepted;
    if (accepted === "pending" && (next === undefined || next === null)) return accepted;
    return next;
  };
  const byId = new Map(current.map((message) => [message.message_id, message]));
  for (const message of incoming) {
    const accepted = byId.get(message.message_id);
    if (!accepted) {
      byId.set(message.message_id, message);
      continue;
    }
    if (deliveryRank(message.delivery_status) < deliveryRank(accepted.delivery_status)) continue;
    if (["completed", "failed"].includes(accepted.delivery_status)) {
      byId.set(message.message_id, {
        ...message,
        content: accepted.content,
        delivery_status: accepted.delivery_status,
        completed_at: accepted.completed_at,
        result_delivery_status: mergeResultStatus(
          accepted.result_delivery_status, message.result_delivery_status,
        ),
      });
      continue;
    }
    byId.set(message.message_id, {
      ...message,
      result_delivery_status: mergeResultStatus(
        accepted.result_delivery_status, message.result_delivery_status,
      ),
    });
  }
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}


function mergeEvent(current: ConversationEvent[], incoming: ConversationEvent): ConversationEvent[] {
  const byId = new Map(current.map((event) => [event.event_id, event]));
  if (!byId.has(incoming.event_id)) byId.set(incoming.event_id, incoming);
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}


function turnIsActive(detail: ConversationDetail | null): boolean {
  return Boolean(detail?.current_turn && !TERMINAL_CONVERSATION_TURN_STATUSES.has(detail.current_turn.status));
}


function terminalTurnHasReferencedMessage(
  detail: ConversationDetail,
  acceptedMessages: ConversationMessage[],
): boolean {
  const turn = detail.current_turn;
  if (!turn || !TERMINAL_CONVERSATION_TURN_STATUSES.has(turn.status)) return false;
  if (!turn.assistant_message_id) return turn.status !== "completed";
  const answer = acceptedMessages.find((message) => message.message_id === turn.assistant_message_id);
  return Boolean(answer && ["assistant", "system"].includes(answer.role)
    && ["completed", "failed"].includes(answer.delivery_status));
}


export function ConversationPage({
  conversationId,
  account,
  client = DEFAULT_CLIENT,
  onConversationUpdated,
  onConversationSettled,
  expectedAgentId,
  assistantLabel = "Agent 大脑",
  personaSubtitle,
  attachmentLimits,
  positionMaterialIds,
  positionArtifactAttachmentIds,
  onPositionMaterialChange,
  composerTools,
  threadSupplement,
  turnScope, composerDraft, renderTurnContext,
  materialsPresentation = "sidebar",
  materialsOpen,
  onMaterialsOpenChange,
  showMaterialsTrigger = true,
  messageActionsPresentation = "legacy",
  selectedKnowledgeResources = [],
  onRemoveKnowledgeResource,
  onKnowledgeResourcesSubmitted,
}: {
  conversationId: string;
  account: Account;
  client?: ConversationPageClient;
  onConversationUpdated?: (conversation: Conversation) => void;
  onConversationSettled?: () => void;
  expectedAgentId?: string;
  assistantLabel?: string;
  personaSubtitle?: string | null;
  attachmentLimits?: { max_file_bytes: number; max_files_per_message: number; max_bytes_per_message: number; max_files_per_conversation: number; max_bytes_per_conversation: number } | null;
  positionMaterialIds?: readonly string[];
  positionArtifactAttachmentIds?: readonly string[];
  onPositionMaterialChange?: (attachment: ConversationAttachment, active: boolean) => void | Promise<void>;
  composerTools?: ReactNode;
  threadSupplement?: ReactNode;
  turnScope?:HrTurnScope;
  composerDraft?:HrComposerDraft;
  renderTurnContext?:(turnId:string)=>ReactNode;
  materialsPresentation?: "sidebar" | "drawer" | "hidden";
  materialsOpen?: boolean;
  onMaterialsOpenChange?: (open: boolean) => void;
  showMaterialsTrigger?: boolean;
  messageActionsPresentation?: MessageActionsPresentation;
  selectedKnowledgeResources?: HrKnowledgeSelection[];
  onRemoveKnowledgeResource?: (id: string) => void;
  onKnowledgeResourcesSubmitted?: () => void;
}) {
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [workerSnapshot, setWorkerSnapshot] = useState<TurnSnapshot | null>(null);
  const [retainedWorkerAnswers, setRetainedWorkerAnswers] = useState<{
    answer: NonNullable<TurnSnapshot["answer"]>;
    beforeMessageSeq: number;
  }[]>([]);
  const workerOwned = detail?.conversation.execution_owner === "worker_direct";
  const workerActive = workerOwned && (!workerSnapshot || Boolean(workerSnapshot.attempt && ["queued", "running", "reconciling"].includes(workerSnapshot.attempt.status)));
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const messagesRef = useRef<ConversationMessage[]>([]);
  const [events, setEvents] = useState<ConversationEvent[]>([]);
  const [text, setText] = useState("");
  const [standardConsent,setStandardConsent]=useState<HrStandardConsent|undefined>();
  useEffect(()=>{if(composerDraft){setText(composerDraft.text);setStandardConsent(composerDraft.standardConsent);}},[composerDraft]);
  useEffect(()=>{if(composerDraft?.positionId!==turnScope?.positionId)setStandardConsent(undefined);},[turnScope?.positionId,composerDraft]);
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
  const [internalMaterialsOpen, setInternalMaterialsOpen] = useState(false);
  const { pageRef, following, jumpToLatest } = useConversationScroll(
    conversationId, expectedAgentId === "hr-bot" && !loading && !loadFailure && Boolean(detail),
  );
  const retained = useRef<{
    text: string;
    submission: ConversationSubmission<ConversationSubmissionResult | ConversationInterventionResult>;
  } | null>(null);
  const writeController = useRef<AbortController | null>(null);
  const eventCursor = useRef(0);
  const inFlight = useRef(false);
  const terminalTurnIds = useRef(new Set<string>());
  const settledTurnIds = useRef(new Set<string>());
  const replaceMessages = (next: ConversationMessage[]) => {
    messagesRef.current = next;
    setMessages(next);
  };
  const mergeIntoMessages = (incoming: ConversationMessage[]) => {
    replaceMessages(mergeMessages(messagesRef.current, incoming));
  };
  const readOnly = account.hard_stale_read_only || detail?.conversation.status === "archived";
  const materialsDrawerOpen = materialsOpen ?? internalMaterialsOpen;
  const changeMaterialsOpen = useCallback((open: boolean) => {
    if (materialsOpen === undefined) setInternalMaterialsOpen(open);
    onMaterialsOpenChange?.(open);
  }, [materialsOpen, onMaterialsOpenChange]);

  useEffect(() => {
    if (!materialsDrawerOpen || materialsPresentation !== "drawer") return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") changeMaterialsOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [changeMaterialsOpen, materialsDrawerOpen, materialsPresentation]);
  const loadTaskDetail = useCallback(
    (turnId: string, taskId: string, signal: AbortSignal) => client.fetchTaskDetail(
      conversationId, turnId, taskId, signal,
    ),
    [client, conversationId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setWorkerSnapshot(null);
    setRetainedWorkerAnswers([]);
    setDetail(null); replaceMessages([]); setEvents([]); setLoading(true); setLoadFailure(false);
    setText(""); setSendFailure(false); setCancelFailure(false); setCancelRequested(false);
    setFeedback({}); setAttachments([]); setActiveAttachmentIds([]); setNewAttachmentIds([]); setUploadQueue([]); setAttachmentError(null);
    retained.current = null; eventCursor.current = 0;
    terminalTurnIds.current.clear(); settledTurnIds.current.clear();
    const detailRead = client.fetchConversation(conversationId, controller.signal);
    let workerInitialized = false;
    // History/materials are independent views, not the worker's Result checkpoint.
    void detailRead.then((snapshot) => {
      if (controller.signal.aborted || snapshot.conversation.execution_owner !== "worker_direct") return;
      if (expectedAgentId && snapshot.conversation.direct_agent_id !== expectedAgentId) throw new Error("Conversation Agent scope mismatch");
      workerInitialized = true;
      setDetail(snapshot); setLoading(false); setStreamEpoch((value) => value + 1);
      onConversationUpdated?.(snapshot.conversation);
    }).catch(() => undefined);
    void Promise.all([
      detailRead,
      client.fetchMessages(conversationId, controller.signal),
      attachmentLimits && client.listAttachments
        ? client.listAttachments(conversationId, controller.signal)
          .then((items) => ({ items, failed: false }), () => ({ items: [], failed: true }))
        : Promise.resolve({ items: [], failed: false }),
    ]).then(([snapshot, loadedMessages, attachmentResult]) => {
      if (controller.signal.aborted) return;
      if (expectedAgentId && (
        snapshot.conversation.mode !== "direct_agent"
        || snapshot.conversation.direct_agent_id !== expectedAgentId
      )) throw new Error("Conversation Agent scope mismatch");
      const projected = loadedMessages.flatMap((message) => [...message.input_attachments, ...message.output_attachments]);
      const materialMap = new Map([...attachmentResult.items, ...projected].map((item) => [item.attachmentId, item]));
      const lastUser = [...loadedMessages].reverse().find((message) => message.role === "user");
      setAttachments([...materialMap.values()]); setActiveAttachmentIds(lastUser?.active_attachment_ids ?? []);
      if (attachmentResult.failed) setAttachmentError("会话材料暂时无法读取，请刷新页面重试。");
      if (workerInitialized) mergeIntoMessages(loadedMessages);
      else {
        setDetail(snapshot); replaceMessages(loadedMessages); setLoading(false);
        setStreamEpoch((value) => value + 1);
        onConversationUpdated?.(snapshot.conversation);
      }
    }).catch(() => {
      if (!controller.signal.aborted && !workerInitialized) { setLoadFailure(true); setLoading(false); }
    });
    return () => { controller.abort(); writeController.current?.abort(); };
  }, [attachmentLimits, client, conversationId, expectedAgentId, onConversationUpdated]);

  useEffect(() => {
    if (!streamEpoch || !detail) return;
    if (detail.conversation.execution_owner === "worker_direct") {
      const controller = new AbortController();
      let inFlight = false;
      const loadingTurns = new Set<string>();
      const enrichmentVersions = new Map<string, number>();
      const refresh = async () => {
        if (inFlight || controller.signal.aborted) return;
        inFlight = true;
        try {
          const value = await (client.fetchSnapshot ?? fetchConversationSnapshot)(conversationId, controller.signal);
          if (controller.signal.aborted) return;
          setWorkerSnapshot((previous) => previous && previous.read_version > value.read_version ? previous : value);
          const turnId = value.turn?.turn_id;
          const pendingTurns = messagesRef.current.filter(message => message.result_delivery_status === "pending").map(message => message.turn_id).filter((id): id is string => Boolean(id));
          for (const candidateId of new Set([...(turnId ? [turnId] : []), ...pendingTurns])) {
            const current = candidateId === turnId;
            const missing = current && (!messagesRef.current.some(message => message.turn_id === candidateId)
              || Boolean(value.answer && !messagesRef.current.some(message => message.message_id === value.answer?.message_id)));
            const enrichmentChanged = (pendingTurns.includes(candidateId) || (current && value.result_enrichment.status !== "none"))
              && enrichmentVersions.get(candidateId) !== value.read_version;
            if (loadingTurns.has(candidateId) || (!missing && !enrichmentChanged)) continue;
            loadingTurns.add(candidateId);
            // Independent rendering/enrichment read. Its failure cannot withhold
            // the authoritative answer or block the next snapshot refresh.
            void (client.fetchTurnMessages ?? fetchConversationTurnMessages)(conversationId, candidateId, controller.signal)
              .then(items => { if (!controller.signal.aborted) {
                mergeIntoMessages(items); enrichmentVersions.set(candidateId, value.read_version);
                setAttachments(previous => [...new Map([...previous, ...items.flatMap(message => message.output_attachments)].map(item => [item.attachmentId, item])).values()]);
              } })
              .catch(() => undefined).finally(() => loadingTurns.delete(candidateId));
          }
          eventCursor.current = Math.max(eventCursor.current, value.event_cursor);
          setConnection("live");
          if (value.outcome && value.turn && !settledTurnIds.current.has(value.turn.turn_id)) {
            settledTurnIds.current.add(value.turn.turn_id); onConversationSettled?.();
          }
        } catch { if (!controller.signal.aborted) setConnection("offline"); }
        finally { inFlight = false; }
      };
      void refresh();
      const stop = scheduleSnapshotPolling(() => void refresh(), 2_000);
      // The stream is a read-only wake-up/progress view; Result comes from snapshot.
      void client.streamEvents(conversationId, { after: eventCursor.current, signal: controller.signal,
        onEvent: (event) => { if (!controller.signal.aborted && event.conversation_id === conversationId) {
          setEvents((current) => mergeEvent(current, event)); void refresh();
        } } }).then(() => void refresh(), () => void refresh());
      return () => { stop(); controller.abort(); };
    }
    const controller = new AbortController();
    const streamController = new AbortController();
    let stopPolling: (() => void) | undefined;
    let acceptedDetail = detail;
    if (detail.current_turn && TERMINAL_CONVERSATION_TURN_STATUSES.has(detail.current_turn.status)) {
      terminalTurnIds.current.add(detail.current_turn.turn_id);
    }
    let refreshInFlight: Promise<{
      snapshot: ConversationDetail;
      terminal: boolean;
      terminalReady: boolean;
      resultDeliveryPending: boolean;
      needsPolling: boolean;
    } | null> | null = null;
    const rememberSnapshot = (snapshot: ConversationDetail, loadedMessages: ConversationMessage[]) => {
      const incomingTurn = snapshot.current_turn;
      const incomingIsTerminal = Boolean(
        incomingTurn && TERMINAL_CONVERSATION_TURN_STATUSES.has(incomingTurn.status),
      );
      if (incomingTurn && incomingIsTerminal) terminalTurnIds.current.add(incomingTurn.turn_id);
      if (!(incomingTurn && !incomingIsTerminal && terminalTurnIds.current.has(incomingTurn.turn_id))) {
        acceptedDetail = snapshot;
      }
      const acceptedMessages = mergeMessages(messagesRef.current, loadedMessages);
      messagesRef.current = acceptedMessages;
      setDetail(acceptedDetail);
      setMessages(acceptedMessages);
      const terminal = Boolean(acceptedDetail.current_turn
        && TERMINAL_CONVERSATION_TURN_STATUSES.has(acceptedDetail.current_turn.status));
      const terminalReady = terminalTurnHasReferencedMessage(acceptedDetail, acceptedMessages);
      const resultDeliveryPending = acceptedMessages.some(
        (message) => message.result_delivery_status === "pending",
      );
      return {
        snapshot: acceptedDetail,
        terminal,
        terminalReady,
        resultDeliveryPending,
        needsPolling: turnIsActive(acceptedDetail)
          || (terminal && !terminalReady)
          || resultDeliveryPending,
      };
    };
    const performRefresh = async () => {
      const pairController = new AbortController();
      const abortPair = () => pairController.abort();
      controller.signal.addEventListener("abort", abortPair, { once: true });
      if (controller.signal.aborted) pairController.abort();
      const detailRead = client.fetchConversation(conversationId, pairController.signal);
      const messageRead = client.fetchMessages(conversationId, pairController.signal);
      try {
        const [snapshot, loadedMessages] = await Promise.all([detailRead, messageRead]);
        if (controller.signal.aborted) return null;
        return rememberSnapshot(snapshot, loadedMessages);
      } catch (error) {
        pairController.abort();
        await Promise.allSettled([detailRead, messageRead]);
        throw error;
      } finally {
        controller.signal.removeEventListener("abort", abortPair);
      }
    };
    const refreshSnapshot = () => {
      if (refreshInFlight) return refreshInFlight;
      const refresh = performRefresh();
      const owned = refresh.then(
        (result) => {
          if (refreshInFlight === owned) refreshInFlight = null;
          return result;
        },
        (error) => {
          if (refreshInFlight === owned) refreshInFlight = null;
          throw error;
        },
      );
      refreshInFlight = owned;
      return owned;
    };
    const activeTurnWasObserved = turnIsActive(detail);
    const terminalWasIncomplete = Boolean(detail.current_turn
      && TERMINAL_CONVERSATION_TURN_STATUSES.has(detail.current_turn.status)
      && !terminalTurnHasReferencedMessage(detail, messagesRef.current));
    const stopReading = () => {
      setConnection("live");
      stopPolling?.();
      controller.abort();
      streamController.abort();
    };
    const settle = (result: {
      snapshot: ConversationDetail;
      needsPolling: boolean;
    }) => {
      setConnection("live");
      const turnId = result.snapshot.current_turn?.turn_id;
      if ((activeTurnWasObserved || terminalWasIncomplete)
        && turnId && !settledTurnIds.current.has(turnId)) {
        settledTurnIds.current.add(turnId);
        onConversationSettled?.();
      }
      streamController.abort();
      if (!result.needsPolling) stopReading();
    };
    const initiallyNeedsPolling = activeTurnWasObserved || terminalWasIncomplete || messagesRef.current.some(
      (message) => message.result_delivery_status === "pending",
    );
    if (initiallyNeedsPolling) {
      stopPolling = scheduleSnapshotPolling(() => {
        if (controller.signal.aborted) return;
        void refreshSnapshot()
          .then((result) => {
            if (result?.terminalReady) settle(result);
            else if (result && !result.needsPolling) stopReading();
          })
          .catch(() => undefined);
      }, 5_000);
    }
    const run = async () => {
      while (!controller.signal.aborted && !streamController.signal.aborted) {
        setConnection(eventCursor.current === 0 ? "connecting" : "live");
        try {
          await client.streamEvents(conversationId, {
            after: eventCursor.current,
            signal: streamController.signal,
            onEvent: (event) => {
              if (streamController.signal.aborted || event.conversation_id !== conversationId || event.seq <= eventCursor.current) return;
              eventCursor.current = event.seq;
              setEvents((current) => mergeEvent(current, event));
              setConnection("live");
              if (!account.hard_stale_read_only && client.markRead && [
                "brain.answer_submitted", "brain.failed", "brain.user_input_requested",
              ].includes(event.event_type)) {
                void client.markRead(
                  conversationId, event.seq, account.csrf_token, streamController.signal,
                ).catch(() => undefined);
              }
            },
          });
          const result = await refreshSnapshot();
          if (!result) return;
          setConnection("live");
          if (result.terminalReady) return settle(result);
          if (!result.needsPolling) return stopReading();
          setConnection("offline");
        } catch {
          if (controller.signal.aborted || streamController.signal.aborted) return;
          try {
            const result = await refreshSnapshot();
            if (!result) return;
            if (result.terminalReady) return settle(result);
            if (!result.needsPolling) return stopReading();
          } catch {
            if (controller.signal.aborted) return;
          }
          setConnection("offline");
        }
        await client.reconnectDelay(streamController.signal);
      }
    };
    void run();
    return () => {
      stopPolling?.();
      controller.abort();
      streamController.abort();
    };
  // streamEpoch deliberately starts a fresh stream after a newly accepted Turn.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.csrf_token, account.hard_stale_read_only, client, conversationId, onConversationSettled, streamEpoch]);

  const sendValue = async (value: string) => {
    const normalized = value.trim();
    const waitingUser = detail?.current_turn?.status === "waiting_user";
    if ((!normalized && newAttachmentIds.length === 0) || inFlight.current || readOnly
      || ((workerOwned ? workerActive : turnIsActive(detail)) && detail?.conversation.mode === "direct_agent" && !waitingUser)) return;
    const submissionInput: TurnSubmission = {
      text: normalized,
      attachmentIds: [...newAttachmentIds],
      activeAttachmentIds: [...activeAttachmentIds],
      ...(turnScope ? {scope:{...turnScope,attachmentIds:[...new Set([...turnScope.attachmentIds,...newAttachmentIds,...activeAttachmentIds])]}} : {}),
      ...(standardConsent && normalized===`确认所选的 ${standardConsent.selectedChangeIds.length} 项岗位标准。` ? {standardConsent} : {}),
      ...(selectedKnowledgeResources.length ? { userSelectedResources: selectedKnowledgeResources } : {}),
    };
    const submissionKey = JSON.stringify(submissionInput);
    let selected = retained.current;
    if (!selected || selected.text !== submissionKey) {
      selected = {
        text: submissionKey,
        submission: client.createMessageSubmission(
          conversationId,
          attachmentLimits || turnScope || newAttachmentIds.length > 0 || activeAttachmentIds.length > 0 || selectedKnowledgeResources.length > 0 ? submissionInput : normalized,
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
      if (expectedAgentId === "hr-bot") jumpToLatest();
      retained.current = null;
      setText(""); setStandardConsent(undefined);
      if (selectedKnowledgeResources.length) onKnowledgeResourcesSubmitted?.();
      setNewAttachmentIds([]); setUploadQueue([]);
      mergeIntoMessages([result.message]);
      if ("conversation" in result) {
        if (result.conversation.execution_owner === "worker_direct") {
          const answer = workerSnapshot?.answer;
          if (answer) setRetainedWorkerAnswers((current) => current.some(item => item.answer.message_id === answer.message_id)
            ? current : [...current, { answer, beforeMessageSeq: result.message.seq }]);
          setWorkerSnapshot(null);
        }
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
      mergeIntoMessages([result.message]);
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
      mergeIntoMessages([result.message]);
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
  const active = workerOwned ? workerActive : turnIsActive(detail);
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
  // A snapshot answer is not a ConversationMessage: its message seq and other
  // metadata are unknown. Anchor it before the actual next accepted message,
  // then prefer real metadata whenever that independent read recovers.
  const messageGroups: { key: string; messages: ConversationMessage[]; answer: TurnSnapshot["answer"] }[] = [];
  let remainingMessages = messages;
  for (const item of retainedWorkerAnswers) {
    if (messages.some(message => message.message_id === item.answer.message_id)) continue;
    messageGroups.push({ key: item.answer.message_id,
      messages: remainingMessages.filter(message => message.seq < item.beforeMessageSeq), answer: item.answer });
    remainingMessages = remainingMessages.filter(message => message.seq >= item.beforeMessageSeq);
  }
  messageGroups.push({ key: "remaining", messages: remainingMessages, answer: null });
  const workerFailure = workerOwned && workerSnapshot?.outcome
    && ["failed", "interrupted"].includes(workerSnapshot.outcome.kind) ? workerSnapshot.outcome.kind : null;
  const materialsTrigger = attachmentLimits && materialsPresentation === "drawer" && showMaterialsTrigger
    ? <button aria-expanded={materialsDrawerOpen} className="conversation-materials-trigger"
      onClick={() => changeMaterialsOpen(true)} type="button">会话材料</button>
    : null;
  const conversationContent = <div className="conversation-page">
    {expectedAgentId !== "hr-bot" && <header className="conversation-header">
      <div>
        <h1>{assistantLabel}</h1>
        {personaSubtitle && <p>{personaSubtitle}</p>}
      </div>
      {materialsTrigger}
    </header>}
    <div className={expectedAgentId === "hr-bot" ? "conversation-scroll-region" : "conversation-flow"}>
    <div className={expectedAgentId === "hr-bot" ? "conversation-scroll-content" : "conversation-flow"} ref={pageRef}>
    {connection === "offline" && <aside className="conversation-connection is-offline" role="status"><strong>连接暂时中断</strong><span>正在从上次进度继续连接，不会重复提交请求。</span></aside>}
    {connection === "connecting" && <aside className="conversation-connection" role="status">正在连接对话…</aside>}
    {messageGroups.map(group => <Fragment key={group.key}><ConversationMessages
      assistantLabel={assistantLabel}
      messages={group.messages}
      feedback={feedback}
      messageActionsPresentation={messageActionsPresentation}
      onDownloadAll={() => void downloadAllArtifacts()}
      onFeedback={readOnly ? undefined : (messageId, rating, reason, comment) => void rate(messageId, rating, reason, comment)}
      onOpenAttachment={(attachment, purpose) => void openAttachment(attachment, purpose)}
      onRetry={readOnly ? undefined : (message) => void resumeSearch(message)}
      renderAfterUserTurn={(turnId) => {
        const workroom = workrooms.get(turnId);
        return <>{renderTurnContext?.(turnId)}{workroom ? <MultiAgentWorkroom
          loadTaskDetail={loadTaskDetail}
          onConfirmAction={readOnly ? undefined : (actionId, actionDigest) => client.confirmAction(
            conversationId, actionId, actionDigest, account.csrf_token,
          )}
          onRejectAction={readOnly ? undefined : (actionId) => client.rejectAction(
            conversationId, actionId, account.csrf_token,
          )}
          workroom={workroom}
        /> : null}</>;
      }}
    />
    {group.answer && <article className="conversation-message conversation-message-assistant" aria-label={`${assistantLabel} 回答`}>
      <MessageMarkdown content={group.answer.content} />
    </article>}
    </Fragment>)}
    {workerSnapshot?.answer && !messages.some((message) => message.message_id === workerSnapshot.answer?.message_id) && <article className="conversation-message conversation-message-assistant" aria-label={`${assistantLabel} 回答`}>
      <MessageMarkdown content={workerSnapshot.answer.content} />
    </article>}
    {workerFailure && <p className="conversation-action-error" role="alert">
      {workerFailure === "failed" ? "本轮处理失败。" : "本轮处理已中断。"}
      {workerSnapshot?.answer ? "已保存的回答仍可查看。" : "本轮未生成完整回答。"}
      不会自动重新执行。
    </p>}
    {threadSupplement}
    <PublicProgress
      active={active && !waitingUser}
      assistantLabel={assistantLabel}
      events={events.filter((event) => event.turn_id === (workerOwned ? workerSnapshot?.turn?.turn_id : detail.current_turn?.turn_id))}
      mode={detail.conversation.mode}
      stopButton={stopButton}
      workerOwned={workerOwned}
      reconciling={workerSnapshot?.attempt?.status === "reconciling"}
    />
    {cancelFailure && <p className="conversation-action-error" role="alert">停止请求暂未送达，请稍后重试。</p>}
    {waitingUser && typeof waitingQuestion === "string" && <UserInputRequest
      disabled={readOnly}
      onSubmit={(answer) => void sendValue(answer)}
      pending={pending}
      question={waitingQuestion}
    />}
    {!workerOwned && detail.current_turn && ["failed", "interrupted"].includes(detail.current_turn.status)
      && <button className="conversation-turn-retry" disabled={pending || readOnly} onClick={() => void retryTurn()} type="button">重试本轮</button>}
    </div>
    </div>
    <ConversationComposer
      compact={expectedAgentId === "hr-bot"}
      navigation={expectedAgentId === "hr-bot" && !following
        ? <button className="conversation-latest" onClick={jumpToLatest} type="button">↓ 回到最新</button>
        : undefined}
      attachmentControls={attachmentLimits ? <AttachmentUploader
        conversationId={conversationId} csrfToken={account.csrf_token}
        compact={expectedAgentId === "hr-bot"}
        disabled={readOnly || (active && detail.conversation.mode === "direct_agent")}
        conversationBytes={attachments.filter((item) => item.source === "user").reduce((sum, item) => sum + item.sizeBytes, 0)}
        conversationFileCount={attachments.filter((item) => item.source === "user").length} onError={setAttachmentError}
        onQueueChange={setUploadQueue} onReady={addReadyAttachment}
      /> : undefined}
      context={selectedKnowledgeResources.length > 0 ? <div className="hr-knowledge-selection" aria-label="本轮指定方法">
        {selectedKnowledgeResources.map((item) => <span key={`${item.sourceCommit}:${item.id}`}><strong>{item.id}</strong><small>版本 {item.revision}</small>
          <button aria-label={`移除方法 ${item.id}`} onClick={() => onRemoveKnowledgeResource?.(item.id)} type="button">×</button></span>)}
      </div> : undefined}
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
        setText(value); setStandardConsent(undefined); setSendFailure(false);
        if (retained.current?.text !== value.trim()) retained.current = null;
      }}
      onSubmit={() => void send()}
      pending={pending}
      hasReadyAttachment={newAttachmentIds.length > 0}
      placeholder={active && detail.conversation.mode === "brain"
        ? "补充范围、修改优先级，或给正在协作的 Agent 新指令…"
        : undefined}
      value={text}
      tools={expectedAgentId === "hr-bot" ? <>{composerTools}{materialsTrigger}</> : composerTools}
    />
    {readOnly && <p className="conversation-read-only" role="status">当前为只读状态，已有对话仍可查看。</p>}
    {sendFailure && <div className="conversation-action-error" role="alert"><span>消息暂未发送成功，可以使用同一次请求安全重试。</span><button className="conversation-retry" disabled={pending} onClick={() => void send()} type="button">重新发送</button></div>}
    {attachmentError && <p className="conversation-action-error" role="alert">{attachmentError}</p>}
  </div>;
  const showMaterials = Boolean(attachmentLimits && materialsPresentation === "sidebar");
  const materialsDrawer = attachmentLimits && materialsPresentation === "drawer" && materialsDrawerOpen
    ? <aside aria-label="会话材料" aria-modal="false" className="session-materials-overlay" role="dialog">
      <button aria-label="关闭会话材料" className="session-materials-overlay-close" onClick={() => changeMaterialsOpen(false)} type="button">关闭</button>
      <SessionMaterialsDrawer
        activeIds={activeAttachmentIds} attachments={attachments} limits={attachmentLimits} onDelete={(item) => void removeAttachment(item)}
        onOpen={(item, purpose) => void openAttachment(item, purpose)} onToggle={toggleAttachment}
        positionMaterialIds={positionMaterialIds} onPositionMaterialChange={onPositionMaterialChange}
        positionArtifactAttachmentIds={positionArtifactAttachmentIds}
        readOnly={readOnly}
      />
    </aside>
    : null;
  return <div className={`${showMaterials ? "conversation-workspace-grid" : "conversation-workspace-content"}${expectedAgentId === "hr-bot" ? " is-hr-conversation" : ""}`}>
    {conversationContent}
    {showMaterials && attachmentLimits && <SessionMaterialsDrawer
      activeIds={activeAttachmentIds} attachments={attachments} limits={attachmentLimits} onDelete={(item) => void removeAttachment(item)}
      onOpen={(item, purpose) => void openAttachment(item, purpose)} onToggle={toggleAttachment}
      positionMaterialIds={positionMaterialIds} onPositionMaterialChange={onPositionMaterialChange}
      positionArtifactAttachmentIds={positionArtifactAttachmentIds}
      readOnly={readOnly}
    />}
    {materialsDrawer}
  </div>;
}
