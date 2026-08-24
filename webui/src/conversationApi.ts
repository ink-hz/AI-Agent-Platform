import { platformPath } from "./auth";
import type {
  Conversation,
  ConversationCancelResult,
  ConversationDeliveryStatus,
  ConversationDetail,
  ConversationEvent,
  ConversationFeedback,
  ConversationFeedbackRating,
  ConversationMessage,
  ConversationMessageRole,
  ConversationMode,
  ConversationPage,
  ConversationStatus,
  ConversationSubmissionResult,
  ConversationTurn,
  ConversationTurnStatus,
} from "./conversationTypes";


const CONVERSATION_KEYS = new Set([
  "conversation_id", "mode", "direct_agent_id", "title", "status",
  "summary_through_seq", "created_at", "updated_at", "archived_at",
]);
const MESSAGE_KEYS = new Set([
  "message_id", "conversation_id", "seq", "role", "content", "turn_id",
  "mission_id", "delivery_status", "created_at", "completed_at",
]);
const TURN_KEYS = new Set([
  "turn_id", "conversation_id", "user_message_id", "assistant_message_id",
  "mission_id", "retry_of_turn_id", "status", "created_at", "updated_at",
]);
const EVENT_KEYS = new Set([
  "event_id", "conversation_id", "seq", "turn_id", "mission_id",
  "event_type", "payload", "created_at",
]);
const SUBMISSION_KEYS = new Set(["conversation", "message", "turn"]);
const DETAIL_KEYS = new Set(["conversation", "current_turn"]);
const PAGE_KEYS = new Set(["items", "next_cursor"]);
const MESSAGE_PAGE_KEYS = new Set(["items"]);
const CANCEL_KEYS = new Set([
  "conversation_id", "turn_id", "mission_id", "cancel_requested",
]);
const FEEDBACK_KEYS = new Set([
  "feedback_id", "conversation_id", "message_id", "turn_id", "mission_id",
  "rating", "created_at",
]);

const CONVERSATION_MODES = new Set<ConversationMode>(["brain", "direct_agent"]);
const CONVERSATION_STATUSES = new Set<ConversationStatus>(["active", "archived"]);
const MESSAGE_ROLES = new Set<ConversationMessageRole>(["user", "assistant", "system"]);
const DELIVERY_STATUSES = new Set<ConversationDeliveryStatus>([
  "accepted", "streaming", "completed", "failed",
]);
const TURN_STATUSES = new Set<ConversationTurnStatus>([
  "accepted", "running", "waiting_agents", "waiting_user", "completing",
  "completed", "failed", "cancelled", "interrupted",
]);

export const MAX_CONVERSATION_INPUT_BYTES = 32 * 1024;


export class ConversationApiError extends Error {
  constructor(public status: number, public detail: unknown = null) {
    super(`Conversation API ${status}`);
  }
}


function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


function hasExactKeys(value: Record<string, unknown>, expected: ReadonlySet<string>): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}


function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}


function isNullableString(value: unknown): value is string | null {
  return value === null || isNonEmptyString(value);
}


function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}


function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}


async function responseDetail(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}


async function checked(response: Response): Promise<Response> {
  if (!response.ok) throw new ConversationApiError(response.status, await responseDetail(response));
  return response;
}


function parseConversation(value: unknown): Conversation {
  if (!isObject(value) || !hasExactKeys(value, CONVERSATION_KEYS)
    || !isNonEmptyString(value.conversation_id)
    || !CONVERSATION_MODES.has(value.mode as ConversationMode)
    || !isNullableString(value.direct_agent_id)
    || typeof value.title !== "string"
    || !CONVERSATION_STATUSES.has(value.status as ConversationStatus)
    || !isNonNegativeInteger(value.summary_through_seq)
    || !isNonEmptyString(value.created_at)
    || !isNonEmptyString(value.updated_at)
    || !isNullableString(value.archived_at)
    || (value.mode === "brain" && value.direct_agent_id !== null)
    || (value.mode === "direct_agent" && !isNonEmptyString(value.direct_agent_id))
    || (value.status === "active" && value.archived_at !== null)
    || (value.status === "archived" && !isNonEmptyString(value.archived_at))
  ) throw new Error("Conversation response invalid");
  return value as unknown as Conversation;
}


function parseMessage(value: unknown): ConversationMessage {
  if (!isObject(value) || !hasExactKeys(value, MESSAGE_KEYS)
    || !isNonEmptyString(value.message_id)
    || !isNonEmptyString(value.conversation_id)
    || !isPositiveInteger(value.seq)
    || !MESSAGE_ROLES.has(value.role as ConversationMessageRole)
    || typeof value.content !== "string"
    || !isNullableString(value.turn_id)
    || !isNullableString(value.mission_id)
    || !DELIVERY_STATUSES.has(value.delivery_status as ConversationDeliveryStatus)
    || !isNonEmptyString(value.created_at)
    || !isNullableString(value.completed_at)
  ) throw new Error("Message response invalid");
  return value as unknown as ConversationMessage;
}


function parseTurn(value: unknown): ConversationTurn {
  if (!isObject(value) || !hasExactKeys(value, TURN_KEYS)
    || !isNonEmptyString(value.turn_id)
    || !isNonEmptyString(value.conversation_id)
    || !isNonEmptyString(value.user_message_id)
    || !isNullableString(value.assistant_message_id)
    || !isNullableString(value.mission_id)
    || !isNullableString(value.retry_of_turn_id)
    || !TURN_STATUSES.has(value.status as ConversationTurnStatus)
    || !isNonEmptyString(value.created_at)
    || !isNonEmptyString(value.updated_at)
  ) throw new Error("Turn response invalid");
  return value as unknown as ConversationTurn;
}


function parseEvent(value: unknown): ConversationEvent {
  if (!isObject(value) || !hasExactKeys(value, EVENT_KEYS)
    || !isNonEmptyString(value.event_id)
    || !isNonEmptyString(value.conversation_id)
    || !isPositiveInteger(value.seq)
    || !isNullableString(value.turn_id)
    || !isNullableString(value.mission_id)
    || !isNonEmptyString(value.event_type)
    || !isObject(value.payload)
    || !isNonEmptyString(value.created_at)
  ) throw new Error("Conversation event invalid");
  return value as unknown as ConversationEvent;
}


function parseSubmission(value: unknown): ConversationSubmissionResult {
  if (!isObject(value) || !hasExactKeys(value, SUBMISSION_KEYS)) {
    throw new Error("Conversation submission response invalid");
  }
  const result = {
    conversation: parseConversation(value.conversation),
    message: parseMessage(value.message),
    turn: parseTurn(value.turn),
  };
  if (result.message.conversation_id !== result.conversation.conversation_id
    || result.turn.conversation_id !== result.conversation.conversation_id
    || result.turn.user_message_id !== result.message.message_id
    || result.message.turn_id !== result.turn.turn_id
    || result.message.mission_id !== result.turn.mission_id
  ) throw new Error("Conversation submission response invalid");
  return result;
}


function parseDetail(value: unknown): ConversationDetail {
  if (!isObject(value) || !hasExactKeys(value, DETAIL_KEYS)) {
    throw new Error("Conversation detail response invalid");
  }
  const conversation = parseConversation(value.conversation);
  const currentTurn = value.current_turn === null ? null : parseTurn(value.current_turn);
  if (currentTurn && currentTurn.conversation_id !== conversation.conversation_id) {
    throw new Error("Conversation detail response invalid");
  }
  return { conversation, current_turn: currentTurn };
}


function parsePage(value: unknown): ConversationPage {
  if (!isObject(value) || !hasExactKeys(value, PAGE_KEYS) || !Array.isArray(value.items)
    || (value.next_cursor !== null && typeof value.next_cursor !== "string")) {
    throw new Error("Conversation list response invalid");
  }
  return { items: value.items.map(parseConversation), next_cursor: value.next_cursor as string | null };
}


function parseMessages(value: unknown): ConversationMessage[] {
  if (!isObject(value) || !hasExactKeys(value, MESSAGE_PAGE_KEYS) || !Array.isArray(value.items)) {
    throw new Error("Conversation messages response invalid");
  }
  const items = value.items.map(parseMessage);
  for (let index = 1; index < items.length; index += 1) {
    if (items[index].conversation_id !== items[0].conversation_id || items[index].seq <= items[index - 1].seq) {
      throw new Error("Conversation messages response invalid");
    }
  }
  return items;
}


function parseCancelResult(value: unknown): ConversationCancelResult {
  if (!isObject(value) || !hasExactKeys(value, CANCEL_KEYS)
    || !isNonEmptyString(value.conversation_id)
    || !isNonEmptyString(value.turn_id)
    || !isNullableString(value.mission_id)
    || value.cancel_requested !== true) {
    throw new Error("Conversation cancel response invalid");
  }
  return value as unknown as ConversationCancelResult;
}


function parseFeedback(value: unknown): ConversationFeedback {
  if (!isObject(value) || !hasExactKeys(value, FEEDBACK_KEYS)
    || !isNonEmptyString(value.feedback_id)
    || !isNonEmptyString(value.conversation_id)
    || !isNonEmptyString(value.message_id)
    || !isNonEmptyString(value.turn_id)
    || !isNullableString(value.mission_id)
    || (value.rating !== "helpful" && value.rating !== "unhelpful")
    || !isNonEmptyString(value.created_at)) {
    throw new Error("Conversation feedback response invalid");
  }
  return value as unknown as ConversationFeedback;
}


export function conversationInputTooLarge(text: string): boolean {
  return new TextEncoder().encode(text).byteLength > MAX_CONVERSATION_INPUT_BYTES;
}


function normalizedInput(text: string): string {
  const selected = text.trim();
  if (!selected) throw new Error("Conversation text required");
  if (conversationInputTooLarge(selected)) throw new Error("Conversation text exceeds 32 KiB");
  return selected;
}


export interface ConversationSubmission {
  readonly idempotencyKey: string;
  send(signal?: AbortSignal): Promise<ConversationSubmissionResult>;
}


function submission(path: string, text: string, csrfToken: string): ConversationSubmission {
  const selectedText = normalizedInput(text);
  const idempotencyKey = crypto.randomUUID();
  return Object.freeze({
    idempotencyKey,
    async send(signal?: AbortSignal): Promise<ConversationSubmissionResult> {
      const response = await checked(await fetch(platformPath(path), {
        method: "POST",
        credentials: "include",
        signal,
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({ text: selectedText }),
      }));
      return parseSubmission(await response.json());
    },
  });
}


export function startConversation(text: string, csrfToken: string, agentId?: string): ConversationSubmission {
  const path = agentId
    ? `/api/v1/agents/${encodeURIComponent(agentId)}/conversations`
    : "/api/v1/conversations";
  return submission(path, text, csrfToken);
}


export function createConversationMessageSubmission(
  conversationId: string,
  text: string,
  csrfToken: string,
): ConversationSubmission {
  return submission(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`, text, csrfToken);
}


export function appendConversationMessage(
  conversationId: string,
  text: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<ConversationSubmissionResult> {
  return createConversationMessageSubmission(conversationId, text, csrfToken).send(signal);
}


export async function fetchConversation(conversationId: string, signal?: AbortSignal): Promise<ConversationDetail> {
  const response = await checked(await fetch(platformPath(`/api/v1/conversations/${encodeURIComponent(conversationId)}`), {
    credentials: "include", headers: { Accept: "application/json" }, signal,
  }));
  return parseDetail(await response.json());
}


export async function listConversations(
  signal?: AbortSignal,
  before?: string,
  limit = 20,
  directAgentId?: string,
): Promise<ConversationPage> {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new Error("Conversation list limit invalid");
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  if (directAgentId) params.set("direct_agent_id", directAgentId);
  const response = await checked(await fetch(platformPath(`/api/v1/conversations?${params}`), {
    credentials: "include", headers: { Accept: "application/json" }, signal,
  }));
  return parsePage(await response.json());
}


export async function fetchConversationMessages(
  conversationId: string,
  signal?: AbortSignal,
): Promise<ConversationMessage[]> {
  const response = await checked(await fetch(platformPath(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`), {
    credentials: "include", headers: { Accept: "application/json" }, signal,
  }));
  const messages = parseMessages(await response.json());
  if (messages.some((item) => item.conversation_id !== conversationId)) {
    throw new Error("Conversation messages response invalid");
  }
  return messages;
}


export async function cancelCurrentTurn(
  conversationId: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<ConversationCancelResult> {
  const response = await checked(await fetch(platformPath(`/api/v1/conversations/${encodeURIComponent(conversationId)}/turns/current/cancel`), {
    method: "POST", credentials: "include", signal,
    headers: { Accept: "application/json", "X-CSRF-Token": csrfToken },
  }));
  const result = parseCancelResult(await response.json());
  if (result.conversation_id !== conversationId) throw new Error("Conversation cancel response invalid");
  return result;
}


export function retryConversationTurn(
  conversationId: string,
  turnId: string,
  csrfToken: string,
): ConversationSubmission {
  const idempotencyKey = crypto.randomUUID();
  return Object.freeze({
    idempotencyKey,
    async send(signal?: AbortSignal): Promise<ConversationSubmissionResult> {
      const response = await checked(await fetch(platformPath(
        `/api/v1/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/retry`,
      ), {
        method: "POST",
        credentials: "include",
        signal,
        headers: {
          Accept: "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": idempotencyKey,
        },
      }));
      return parseSubmission(await response.json());
    },
  });
}


export async function archiveConversation(
  conversationId: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<Conversation> {
  const response = await checked(await fetch(platformPath(`/api/v1/conversations/${encodeURIComponent(conversationId)}/archive`), {
    method: "POST", credentials: "include", signal,
    headers: { Accept: "application/json", "X-CSRF-Token": csrfToken },
  }));
  const result = parseConversation(await response.json());
  if (result.conversation_id !== conversationId || result.status !== "archived") {
    throw new Error("Conversation archive response invalid");
  }
  return result;
}


export async function submitConversationFeedback(
  messageId: string,
  rating: ConversationFeedbackRating,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<ConversationFeedback> {
  const response = await checked(await fetch(platformPath(
    `/api/v1/messages/${encodeURIComponent(messageId)}/feedback`,
  ), {
    method: "POST",
    credentials: "include",
    signal,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify({ rating }),
  }));
  const result = parseFeedback(await response.json());
  if (result.message_id !== messageId || result.rating !== rating) {
    throw new Error("Conversation feedback response invalid");
  }
  return result;
}


export interface ConversationStreamOptions {
  after: number;
  signal: AbortSignal;
  onEvent: (event: ConversationEvent) => void;
}


function consumeFrame(frame: string, cursor: number, conversationId: string): ConversationEvent | null {
  if (!frame || frame.startsWith(":")) return null;
  const fields = frame.split("\n");
  const idLines = fields.filter((line) => line.startsWith("id: "));
  const eventLines = fields.filter((line) => line.startsWith("event: "));
  const dataLines = fields.filter((line) => line.startsWith("data: "));
  if (idLines.length !== 1 || eventLines.length !== 1 || dataLines.length !== 1
    || eventLines[0] !== "event: conversation") throw new Error("Conversation stream invalid");
  const id = Number(idLines[0].slice(4));
  if (!Number.isSafeInteger(id) || id <= cursor) throw new Error("Conversation stream sequence invalid");
  const event = parseEvent(JSON.parse(dataLines[0].slice(6)));
  if (event.seq !== id || event.conversation_id !== conversationId) {
    throw new Error("Conversation stream sequence invalid");
  }
  return event;
}


export async function streamConversationEvents(
  conversationId: string,
  options: ConversationStreamOptions,
): Promise<void> {
  if (!Number.isSafeInteger(options.after) || options.after < 0) {
    throw new Error("Conversation stream cursor invalid");
  }
  const response = await checked(await fetch(platformPath(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/events?after=${options.after}`,
  ), {
    credentials: "include", headers: { Accept: "text/event-stream" }, signal: options.signal,
  }));
  if (!response.headers.get("Content-Type")?.toLowerCase().startsWith("text/event-stream") || !response.body) {
    throw new Error("Conversation stream unavailable");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let cursor = options.after;
  while (true) {
    const { done, value } = await reader.read();
    buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = consumeFrame(frame, cursor, conversationId);
      if (event) {
        cursor = event.seq;
        options.onEvent(event);
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) throw new Error("Conversation stream truncated");
}
