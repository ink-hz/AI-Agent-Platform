import { useEffect, useRef, useState } from "react";

import type { Account } from "../auth";
import {
  cancelCurrentTurn,
  createConversationMessageSubmission,
  fetchConversation,
  fetchConversationMessages,
  streamConversationEvents,
  type ConversationStreamOptions,
  type ConversationSubmission,
} from "../conversationApi";
import type {
  ConversationCancelResult,
  ConversationDetail,
  ConversationEvent,
  ConversationMessage,
} from "../conversationTypes";
import { TERMINAL_CONVERSATION_TURN_STATUSES } from "../conversationTypes";
import { reconnectDelay } from "../brainApi";
import { ConversationComposer } from "../components/conversation/ConversationComposer";
import { ConversationMessages } from "../components/conversation/ConversationMessages";
import { ExecutionCard } from "../components/conversation/ExecutionCard";
import { professionalAgentLabel } from "../components/conversation/agentLabels";
import { PlatformLink } from "../components/PlatformLink";


export interface ConversationPageClient {
  fetchConversation(conversationId: string, signal?: AbortSignal): Promise<ConversationDetail>;
  fetchMessages(conversationId: string, signal?: AbortSignal): Promise<ConversationMessage[]>;
  createMessageSubmission(conversationId: string, text: string, csrfToken: string): ConversationSubmission;
  streamEvents(conversationId: string, options: ConversationStreamOptions): Promise<void>;
  cancelCurrentTurn(conversationId: string, csrfToken: string, signal?: AbortSignal): Promise<ConversationCancelResult>;
  reconnectDelay(signal: AbortSignal): Promise<void>;
}

const DEFAULT_CLIENT: ConversationPageClient = {
  fetchConversation,
  fetchMessages: fetchConversationMessages,
  createMessageSubmission: createConversationMessageSubmission,
  streamEvents: streamConversationEvents,
  cancelCurrentTurn,
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
}: {
  conversationId: string;
  account: Account;
  client?: ConversationPageClient;
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
  const [streamEpoch, setStreamEpoch] = useState(0);
  const retained = useRef<{ text: string; submission: ConversationSubmission } | null>(null);
  const writeController = useRef<AbortController | null>(null);
  const eventCursor = useRef(0);
  const inFlight = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null); setMessages([]); setEvents([]); setLoading(true); setLoadFailure(false);
    setText(""); setSendFailure(false); setCancelFailure(false); setCancelRequested(false);
    retained.current = null; eventCursor.current = 0;
    void Promise.all([
      client.fetchConversation(conversationId, controller.signal),
      client.fetchMessages(conversationId, controller.signal),
    ]).then(([snapshot, loadedMessages]) => {
      if (controller.signal.aborted) return;
      setDetail(snapshot); setMessages(loadedMessages); setLoading(false); setStreamEpoch((value) => value + 1);
    }).catch(() => {
      if (!controller.signal.aborted) { setLoadFailure(true); setLoading(false); }
    });
    return () => { controller.abort(); writeController.current?.abort(); };
  }, [client, conversationId]);

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

  const send = async () => {
    const normalized = text.trim();
    if (!normalized || inFlight.current || account.hard_stale_read_only || turnIsActive(detail)) return;
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

  if (loading) return <section className="conversation-load-state" aria-live="polite"><h1>正在打开对话</h1><p>正在读取已保存的消息与执行记录。</p></section>;
  if (loadFailure || !detail) return <section className="conversation-load-state" role="alert"><h1>暂时无法读取对话</h1><p>对话仍安全保存在平台，请稍后刷新。</p></section>;
  const active = turnIsActive(detail);
  return <div className="conversation-page">
    <header className="conversation-header">
      <div>
        <PlatformLink href="/conversations">← 历史对话</PlatformLink>
        <p>{detail.conversation.mode === "direct_agent" ? detail.conversation.direct_agent_id : "Agent 大脑"}</p>
        <h1>{detail.conversation.title}</h1>
      </div>
      <PlatformLink className="conversation-new" href="/">新建对话</PlatformLink>
    </header>
    {connection === "offline" && <aside className="conversation-connection is-offline" role="status"><strong>连接暂时中断</strong><span>正在从最后一条执行记录继续连接，不会重复创建任务。</span></aside>}
    {connection === "connecting" && <aside className="conversation-connection" role="status">正在连接执行进度…</aside>}
    <ConversationMessages
      assistantLabel={detail.conversation.mode === "direct_agent"
        ? professionalAgentLabel(detail.conversation.direct_agent_id) ?? "专业 Agent"
        : "Agent 大脑"}
      messages={messages}
    />
    {active && <section className="conversation-running" aria-live="polite">
      <span>{cancelRequested ? "正在停止本轮执行…" : "Agent 正在处理本轮需求…"}</span>
      <button className="conversation-stop" disabled={cancelRequested || account.hard_stale_read_only} onClick={() => void stop()} type="button">
        {cancelRequested ? "正在停止" : "停止"}
      </button>
    </section>}
    {cancelFailure && <p className="conversation-action-error" role="alert">停止请求暂未送达，请稍后重试。</p>}
    <ExecutionCard events={events} mode={detail.conversation.mode} directAgentId={detail.conversation.direct_agent_id} />
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
