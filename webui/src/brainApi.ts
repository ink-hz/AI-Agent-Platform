import { platformPath } from "./auth";
import type {
  AgentAttachmentLimits,
  AgentCapabilityCard,
  AgentContentType,
  Mission,
  MissionEvent,
  MissionPage,
  MissionStatus,
} from "./brainTypes";


const MISSION_STATUSES = new Set<MissionStatus>([
  "planning", "delegated", "synthesizing", "completed", "partially_completed",
  "failed", "cancelled", "interrupted",
]);

export const MAX_MISSION_INPUT_BYTES = 32 * 1024;

export function missionInputTooLarge(text: string): boolean {
  return new TextEncoder().encode(text).byteLength > MAX_MISSION_INPUT_BYTES;
}

export class BrainApiError extends Error {
  constructor(public status: number, public detail: unknown = null) {
    super(`Agent Brain API ${status}`);
  }
}

export interface AgentLaunch {
  launch_url: string;
  expires_at: string;
}

function parseAgentLaunch(value: unknown): AgentLaunch {
  if (!isObject(value)
    || typeof value.launch_url !== "string"
    || typeof value.expires_at !== "string"
    || !Number.isFinite(Date.parse(value.expires_at))) {
    throw new Error("Agent launch response invalid");
  }
  let target: URL;
  try {
    target = new URL(value.launch_url);
  } catch {
    throw new Error("Agent launch response invalid");
  }
  if (target.origin !== "https://agent.orbbec.com.cn"
    || target.username !== ""
    || target.password !== ""
    || target.pathname !== "/fae/"
    || target.search !== ""
    || !/^#platform_launch=[A-Za-z0-9_-]{32,256}$/.test(target.hash)
    || value.launch_url !== `https://agent.orbbec.com.cn/fae/${target.hash}`) {
    throw new Error("Agent launch response invalid");
  }
  return { launch_url: value.launch_url, expires_at: value.expires_at };
}
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}

const CONTENT_TYPES = new Set<AgentContentType>(["text", "image", "pdf", "office"]);
const ATTACHMENT_LIMIT_KEYS = new Set([
  "max_file_bytes", "max_files_per_message", "max_bytes_per_message",
  "max_files_per_conversation", "max_bytes_per_conversation",
]);

function hasExactKeys(value: Record<string, unknown>, expected: ReadonlySet<string>): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function contentTypes(value: unknown): value is AgentContentType[] {
  return Array.isArray(value)
    && value.length > 0
    && value[0] === "text"
    && value.length === new Set(value).size
    && value.every((item) => typeof item === "string" && CONTENT_TYPES.has(item as AgentContentType));
}

function attachmentLimits(value: unknown): value is AgentAttachmentLimits {
  if (!isObject(value) || !hasExactKeys(value, ATTACHMENT_LIMIT_KEYS)) return false;
  const integers = Object.values(value);
  return integers.every((item) => Number.isSafeInteger(item) && Number(item) > 0)
    && Number(value.max_bytes_per_message) >= Number(value.max_file_bytes)
    && Number(value.max_files_per_conversation) >= Number(value.max_files_per_message)
    && Number(value.max_bytes_per_conversation) >= Number(value.max_bytes_per_message);
}

async function responseDetail(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}

async function checked(response: Response): Promise<Response> {
  if (!response.ok) throw new BrainApiError(response.status, await responseDetail(response));
  return response;
}

function parseMission(value: unknown): Mission {
  if (!isObject(value)) throw new Error("Mission response invalid");
  const status = value.status;
  if (
    typeof value.mission_id !== "string" || !value.mission_id
    || (value.mode !== "brain" && value.mode !== "direct_agent")
    || (value.direct_agent_id !== null && typeof value.direct_agent_id !== "string")
    || typeof status !== "string" || !MISSION_STATUSES.has(status as MissionStatus)
    || typeof value.cancel_requested !== "boolean"
    || !Number.isSafeInteger(value.row_version) || Number(value.row_version) < 0
    || typeof value.created_at !== "string" || typeof value.updated_at !== "string"
    || (value.terminal_at !== null && typeof value.terminal_at !== "string")
    || typeof value.prompt !== "string" || typeof value.content_available !== "boolean"
  ) throw new Error("Mission response invalid");
  return value as unknown as Mission;
}

function parseMissionPage(value: unknown): MissionPage {
  if (!isObject(value) || !Array.isArray(value.items)
    || (value.next_cursor !== null && typeof value.next_cursor !== "string")) {
    throw new Error("Mission list response invalid");
  }
  return { items: value.items.map(parseMission), next_cursor: value.next_cursor as string | null };
}

function parseMissionEvent(value: unknown): MissionEvent {
  if (!isObject(value) || typeof value.event_id !== "string" || !value.event_id
    || typeof value.mission_id !== "string" || !value.mission_id
    || (value.run_id !== null && typeof value.run_id !== "string")
    || !Number.isSafeInteger(value.seq) || Number(value.seq) <= 0
    || typeof value.event_type !== "string" || !value.event_type
    || !isObject(value.payload) || typeof value.created_at !== "string") {
    throw new Error("Mission event invalid");
  }
  return value as unknown as MissionEvent;
}

function parseCapabilityCard(value: unknown): AgentCapabilityCard {
  const modes = isObject(value) && Array.isArray(value.interaction_modes)
    ? value.interaction_modes : [];
  const validModes = modes.length > 0 && modes.every((mode) =>
    mode === "direct_chat" || mode === "brain_delegation" || mode === "external_workspace");
  const hasExternalWorkspace = modes.includes("external_workspace");
  const isCallable = modes.includes("direct_chat") || modes.includes("brain_delegation");
  const externalOnly = modes.length === 1 && modes[0] === "external_workspace";
  const acceptedTypes = isObject(value) ? value.accepted_input_types : null;
  const outputTypes = isObject(value) ? value.output_types : null;
  const hasAttachmentInput = contentTypes(acceptedTypes)
    && acceptedTypes.some((item) => item !== "text");
  const hasAttachmentOutput = contentTypes(outputTypes)
    && outputTypes.some((item) => item !== "text");
  if (!isObject(value)
    || typeof value.agent_id !== "string" || !value.agent_id
    || typeof value.display_name !== "string" || !value.display_name
    || (value.persona_subtitle !== null
      && (typeof value.persona_subtitle !== "string" || !value.persona_subtitle))
    || typeof value.domain_group !== "string" || !value.domain_group
    || typeof value.mission !== "string" || !value.mission
    || !isStringArray(value.capabilities) || !isStringArray(value.exclusions)
    || !isStringArray(value.example_tasks) || !isStringArray(value.required_inputs)
    || !contentTypes(value.accepted_input_types) || !contentTypes(value.output_types)
    || value.supports_attachments_in !== hasAttachmentInput
    || value.supports_attachments_out !== hasAttachmentOutput
    || (hasAttachmentInput ? !attachmentLimits(value.attachment_limits) : value.attachment_limits !== null)
    || typeof value.supports_evidence !== "boolean" || typeof value.supports_streaming !== "boolean"
    || typeof value.supports_cancellation !== "boolean" || typeof value.supports_idempotency !== "boolean"
    || !Number.isSafeInteger(value.max_duration_seconds) || Number(value.max_duration_seconds) <= 0
    || value.data_classification !== "internal" || !validModes
    || (value.workspace_url !== null && typeof value.workspace_url !== "string")
    || (value.adapter_id !== null && (typeof value.adapter_id !== "string" || !value.adapter_id))
    || (value.adapter_kind !== null && (typeof value.adapter_kind !== "string" || !value.adapter_kind))
    || !Number.isSafeInteger(value.adapter_config_version) || Number(value.adapter_config_version) <= 0
    || value.output_contract !== "normalized_task_result_v1"
    || (hasExternalWorkspace ? typeof value.workspace_url !== "string" : value.workspace_url !== null)
    || (isCallable
      ? (typeof value.adapter_id !== "string" || typeof value.adapter_kind !== "string")
      : (!externalOnly || value.adapter_id !== null || value.adapter_kind !== null))
    || !Number.isSafeInteger(value.capability_version) || Number(value.capability_version) <= 0
  ) throw new Error("Agent catalog response invalid");
  return value as unknown as AgentCapabilityCard;
}

export async function listMissions(signal?: AbortSignal, before?: string): Promise<MissionPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (before) params.set("before", before);
  const response = await checked(await fetch(platformPath(`/api/v1/brain/missions?${params}`), {
    credentials: "include", headers: { Accept: "application/json" }, signal,
  }));
  return parseMissionPage(await response.json());
}

export async function fetchMission(missionId: string, signal?: AbortSignal): Promise<Mission> {
  const response = await checked(await fetch(platformPath(`/api/v1/brain/missions/${encodeURIComponent(missionId)}`), {
    credentials: "include", headers: { Accept: "application/json" }, signal,
  }));
  return parseMission(await response.json());
}

export async function cancelMission(missionId: string, csrfToken: string, signal?: AbortSignal): Promise<Mission> {
  const response = await checked(await fetch(platformPath(`/api/v1/brain/missions/${encodeURIComponent(missionId)}/cancel`), {
    method: "POST", credentials: "include", signal,
    headers: { Accept: "application/json", "X-CSRF-Token": csrfToken },
  }));
  return parseMission(await response.json());
}

export async function fetchAgentCatalog(signal?: AbortSignal): Promise<AgentCapabilityCard[]> {
  const response = await checked(await fetch(platformPath("/api/v1/catalog/agents"), {
    credentials: "include", headers: { Accept: "application/json" }, signal,
  }));
  const value: unknown = await response.json();
  if (!isObject(value) || !Array.isArray(value.agents)) throw new Error("Agent catalog response invalid");
  return value.agents.map(parseCapabilityCard);
}

export async function launchAgent(
  agentId: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<AgentLaunch> {
  const response = await checked(await fetch(platformPath(
    `/api/v1/agents/${encodeURIComponent(agentId)}/launch`,
  ), {
    method: "POST",
    credentials: "include",
    signal,
    headers: { Accept: "application/json", "X-CSRF-Token": csrfToken },
  }));
  return parseAgentLaunch(await response.json());
}

export interface MissionSubmission {
  readonly idempotencyKey: string;
  send(signal?: AbortSignal): Promise<Mission>;
}

export function createMissionSubmission(text: string, csrfToken: string, agentId?: string): MissionSubmission {
  const selectedText = text.trim();
  if (!selectedText) throw new Error("Mission text required");
  if (missionInputTooLarge(selectedText)) throw new Error("Mission text exceeds 32 KiB");
  const idempotencyKey = crypto.randomUUID();
  const path = agentId
    ? `/api/v1/agents/${encodeURIComponent(agentId)}/missions`
    : "/api/v1/brain/missions";
  return Object.freeze({
    idempotencyKey,
    async send(signal?: AbortSignal): Promise<Mission> {
      const response = await checked(await fetch(platformPath(path), {
        method: "POST", credentials: "include", signal,
        headers: {
          Accept: "application/json", "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken, "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({ text: selectedText }),
      }));
      return parseMission(await response.json());
    },
  });
}

export interface MissionStreamOptions {
  after: number;
  signal: AbortSignal;
  onEvent: (event: MissionEvent) => void;
}

function consumeFrame(frame: string, cursor: number): MissionEvent | null {
  if (!frame || frame.startsWith(":")) return null;
  const fields = frame.split("\n");
  const idLines = fields.filter((line) => line.startsWith("id: "));
  const eventLines = fields.filter((line) => line.startsWith("event: "));
  const dataLines = fields.filter((line) => line.startsWith("data: "));
  if (idLines.length !== 1 || eventLines.length !== 1 || dataLines.length !== 1
    || eventLines[0] !== "event: mission") throw new Error("Mission stream invalid");
  const id = Number(idLines[0].slice(4));
  if (!Number.isSafeInteger(id) || id <= cursor) throw new Error("Mission stream sequence invalid");
  const event = parseMissionEvent(JSON.parse(dataLines[0].slice(6)));
  if (event.seq !== id) throw new Error("Mission stream sequence invalid");
  return event;
}

export async function streamMissionEvents(missionId: string, options: MissionStreamOptions): Promise<void> {
  if (!Number.isSafeInteger(options.after) || options.after < 0) throw new Error("Mission stream cursor invalid");
  const response = await checked(await fetch(platformPath(`/api/v1/brain/missions/${encodeURIComponent(missionId)}/events?after=${options.after}`), {
    credentials: "include", headers: { Accept: "text/event-stream" }, signal: options.signal,
  }));
  if (!response.headers.get("Content-Type")?.toLowerCase().startsWith("text/event-stream") || !response.body) {
    throw new Error("Mission stream unavailable");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let cursor = options.after;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = consumeFrame(frame, cursor);
      if (event) {
        cursor = event.seq;
        options.onEvent(event);
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) throw new Error("Mission stream truncated");
}

export function reconnectDelay(signal: AbortSignal, milliseconds = 1_500): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = globalThis.setTimeout(done, milliseconds);
    function done() {
      globalThis.clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}
