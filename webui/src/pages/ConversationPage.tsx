import { useEffect, useRef, useState } from "react";

import type { Account } from "../auth";
import {
  cancelCurrentTurn,
  createConversationMessageSubmission,
  fetchConversation,
  fetchConversationMessages,
  retryConversationTurn,
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
  ConversationMessage,
} from "../conversationTypes";
import { TERMINAL_CONVERSATION_TURN_STATUSES } from "../conversationTypes";
import { reconnectDelay } from "../brainApi";
import { ConversationComposer } from "../components/conversation/ConversationComposer";
import { ConversationMessages } from "../components/conversation/ConversationMessages";
import { PublicProgress } from "../components/conversation/PublicProgress";
import { UserInputRequest } from "../components/conversation/UserInputRequest";


export interface ConversationPageClient {
  fetchConversation(conversationId: string, signal?: AbortSignal): Promise<ConversationDetail>;
  fetchMessages(conversationId: string, signal?: AbortSignal): Promise<ConversationMessage[]>;
  createMessageSubmission(conversationId: string, text: string, csrfToken: string): ConversationSubmission;
  streamEvents(conversationId: string, options: ConversationStreamOptions): Promise<void>;
  cancelCurrentTurn(conversationId: string, csrfToken: string, signal?: AbortSignal): Promise<ConversationCancelResult>;
  submitFeedback(messageId: string, rating: ConversationFeedbackRating, csrfToken: string, signal?: AbortSignal): Promise<ConversationFeedback>;
  retryTurn(conversationId: string, turnId: string, csrfToken: string): ConversationSubmission;
  reconnectDelay(signal: AbortSignal): Promise<void>;
}

const DEFAULT_CLIENT: ConversationPageClient = {
  fetchConversation,
  fetchMessages: fetchConversationMessages,
  createMessageSubmission: createConversationMessageSubmission,
  streamEvents: streamConversationEvents,
  cancelCurrentTurn,
  submitFeedback: submitConversationFeedback,
  retryTurn: retryConversationTurn,
  reconnectDelay,
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
}: {
  conversationId: string;
  account: Account;
  client?: ConversationPageClient;
  onConversationUpdated?: (conversation: Conversation) => void;
  expectedAgentId?: string;
  assistantLabel?: string;
  personaSubtitle?: string | null;
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
  const retained = useRef<{ text: string; submission: ConversationSubmission } | null>(null);
  const writeController = useRef<AbortController | null>(null);
  const eventCursor = useRef(0);
  const inFlight = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null); setMessages([]); setEvents([]); setLoading(true); setLoadFailure(false);
    setText(""); setSendFailure(false); setCancelFailure(false); setCancelRequested(false);
    setFeedback({});
    retained.current = null; eventCursor.current = 0;
    void Promise.all([
      client.fetchConversation(conversationId, controller.signal),
      client.fetchMessages(conversationId, controller.signal),
    ]).then(([snapshot, loadedMessages]) => {
      if (controller.signal.aborted) return;
      if (expectedAgentId && (
        snapshot.conversation.mode !== "direct_agent"
        || snapshot.conversation.direct_agent_id !== expectedAgentId
      )) throw new Error("Conversation Agent scope mismatch");
      setDetail(snapshot); setMessages(loadedMessages); setLoading(false); setStreamEpoch((value) => value + 1);
      onConversationUpdated?.(snapshot.conversation);
    }).catch(() => {
      if (!controller.signal.aborted) { setLoadFailure(true); setLoading(false); }
    });
    return () => { controller.abort(); writeController.current?.abort(); };
  }, [client, conversationId, expectedAgentId, onConversationUpdated]);

  useEffect(() => {
    if (!streamEpoch || !detail) return;
    const controller = new AbortController();
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
            },
          });
          const [snapshot, loadedMessages] = await Promise.all([
            client.fetchConversation(conversationId, controller.signal),
            client.fetchMessages(conversationId, controller.signal),
          ]);
          if (controller.signal.aborted) return;
          setDetail(snapshot); setMessages((current) => mergeMessages(current, loadedMessages));
          setConnection("live");
          if (!turnIsActive(snapshot)) return;
          setConnection("offline");
        } catch {
          if (controller.signal.aborted) return;
          setConnection("offline");
        }
        await client.reconnectDelay(controller.signal);
      }
    };
    void run();
    return () => controller.abort();
  // streamEpoch deliberately starts a fresh stream after a newly accepted Turn.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, conversationId, streamEpoch]);

  const sendValue = async (value: string) => {
    const normalized = value.trim();
    const waitingUser = detail?.current_turn?.status === "waiting_user";
    if (!normalized || inFlight.current || account.hard_stale_read_only
      || (turnIsActive(detail) && !waitingUser)) return;
    let selected = retained.current;
    if (!selected || selected.text !== normalized) {
      selected = {
        text: normalized,
        submission: client.createMessageSubmission(conversationId, normalized, account.csrf_token),
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
      setMessages((current) => mergeMessages(current, [result.message]));
      setDetail({ conversation: result.conversation, current_turn: result.turn });
      onConversationUpdated?.(result.conversation);
      setCancelRequested(false);
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

  const send = async () => sendValue(text);

  const retryTurn = async () => {
    const turn = detail?.current_turn;
    if (!turn || !["failed", "interrupted"].includes(turn.status)
      || inFlight.current || account.hard_stale_read_only) return;
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

  const rate = async (messageId: string, rating: ConversationFeedbackRating) => {
    if (account.hard_stale_read_only || feedback[messageId] === "pending") return;
    const controller = new AbortController();
    setFeedback((current) => ({ ...current, [messageId]: "pending" }));
    try {
      const result = await client.submitFeedback(
        messageId, rating, account.csrf_token, controller.signal,
      );
      setFeedback((current) => ({ ...current, [messageId]: result.rating }));
    } catch {
      setFeedback((current) => ({ ...current, [messageId]: "error" }));
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
    disabled={cancelRequested || account.hard_stale_read_only}
    onClick={() => void stop()}
    type="button"
  >{cancelRequested ? "正在停止" : "停止"}</button>;
  return <div className="conversation-page">
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
      onFeedback={account.hard_stale_read_only ? undefined : (messageId, rating) => void rate(messageId, rating)}
    />
    <PublicProgress
      active={active && !waitingUser}
      assistantLabel={assistantLabel}
      events={events}
      mode={detail.conversation.mode}
      stopButton={stopButton}
    />
    {cancelFailure && <p className="conversation-action-error" role="alert">停止请求暂未送达，请稍后重试。</p>}
    {waitingUser && typeof waitingQuestion === "string" && <UserInputRequest
      disabled={account.hard_stale_read_only}
      onSubmit={(answer) => void sendValue(answer)}
      pending={pending}
      question={waitingQuestion}
    />}
    {detail.current_turn && ["failed", "interrupted"].includes(detail.current_turn.status)
      && <button className="conversation-turn-retry" disabled={pending || account.hard_stale_read_only} onClick={() => void retryTurn()} type="button">重试本轮</button>}
    <ConversationComposer
      disabled={active || account.hard_stale_read_only}
      onChange={(value) => {
        setText(value); setSendFailure(false);
        if (retained.current?.text !== value.trim()) retained.current = null;
      }}
      onSubmit={() => void send()}
      pending={pending}
      value={text}
    />
    {account.hard_stale_read_only && <p className="conversation-read-only" role="status">当前为只读状态，已有对话仍可查看。</p>}
    {sendFailure && <div className="conversation-action-error" role="alert"><span>消息暂未发送成功，可以使用同一次请求安全重试。</span><button className="conversation-retry" disabled={pending} onClick={() => void send()} type="button">重新发送</button></div>}
  </div>;
}
