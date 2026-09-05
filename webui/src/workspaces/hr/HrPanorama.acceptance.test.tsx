/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import type { AgentCapabilityCard } from "../../brainTypes";
import type { ConversationMessage, ConversationTurn } from "../../conversationTypes";

const fixture = vi.hoisted(() => ({
  account: {
    internal_user_id: "panorama-acceptance", display_name: "HR", role: "member",
    departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [],
    directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
  },
  conversation: "10000000-0000-4000-8000-000000000001",
  position: "10000000-0000-4000-8000-000000000002",
  context: "10000000-0000-4000-8000-000000000003",
  attachment: "10000000-0000-4000-8000-000000000004",
  upload: "10000000-0000-4000-8000-000000000005",
  run: "20000000-0000-4000-8000-000000000001",
  retryRun: "20000000-0000-4000-8000-000000000002",
  insight: "30000000-0000-4000-8000-000000000001",
  retryInsight: "30000000-0000-4000-8000-000000000002",
  sources: [
    "40000000-0000-4000-8000-000000000001",
    "40000000-0000-4000-8000-000000000002",
    "40000000-0000-4000-8000-000000000003",
  ],
}));

const account = fixture.account as Account;

vi.mock("../../auth", async (original) => ({
  ...await original<typeof import("../../auth")>(), identityShellEnabled: () => true,
  loadAccount: vi.fn().mockResolvedValue(fixture.account),
}));
vi.mock("../../AppShell", () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <div data-app-shell>{children}</div> }));
vi.mock("../../accessEventReporter", () => ({ AccessEventReporter: () => null }));
vi.mock("../../brainApi", async (original) => ({
  ...await original<typeof import("../../brainApi")>(), fetchAgentCatalog: vi.fn(), reconnectDelay: vi.fn(),
}));
vi.mock("../../conversationApi", async (original) => ({
  ...await original<typeof import("../../conversationApi")>(), fetchConversation: vi.fn(),
  fetchConversationMessages: vi.fn(), listConversations: vi.fn(), streamConversationEvents: vi.fn(),
  markConversationRead: vi.fn().mockResolvedValue({}),
}));
vi.mock("../../attachmentApi", async (original) => ({
  ...await original<typeof import("../../attachmentApi")>(), listConversationAttachments: vi.fn(),
}));

import App from "../../App";
import { listConversationAttachments } from "../../attachmentApi";
import { fetchAgentCatalog, reconnectDelay } from "../../brainApi";
import {
  fetchConversation, fetchConversationMessages, listConversations, streamConversationEvents,
} from "../../conversationApi";

const now = "2026-09-05T04:00:00Z";
const companies = ["联合光电", "奥比中光", "舜宇光学"];
const card: AgentCapabilityCard = {
  agent_id: "hr-bot", display_name: "HR Agent", domain_group: "HR",
  persona_subtitle: "招聘协作", mission: "招聘", capabilities: ["岗位修订"], exclusions: [],
  example_tasks: [], required_inputs: [], accepted_input_types: ["text", "pdf"], output_types: ["text"],
  supports_attachments_in: true, supports_attachments_out: true,
  attachment_limits: { max_file_bytes: 52_428_800, max_files_per_message: 5, max_bytes_per_message: 52_428_800, max_files_per_conversation: 50, max_bytes_per_conversation: 524_288_000 },
  supports_evidence: true, supports_streaming: true, supports_cancellation: true, supports_idempotency: true,
  max_duration_seconds: 300, data_classification: "internal", adapter_id: "metabot-core-chat",
  capability_version: 1, adapter_kind: "metabot_local", adapter_config_version: 1,
  output_contract: "normalized_task_result_v1", interaction_modes: ["direct_chat"], workspace_url: null,
};

function source(index: number) {
  return { source_id: fixture.sources[index], source_kind: "company", canonical_name: companies[index], aliases: [],
    approved_urls: [`https://company-${index + 1}.example.com/jobs`], active: true, created_at: now, updated_at: now };
}
function run(runId: string, selected: string[], state: "queued" | "completed" | "partially_completed") {
  return { run_id: runId, selected_source_ids: selected, conversation_id: fixture.conversation, state,
    error_code: null, source_failures: state === "partially_completed" ? { [fixture.sources[2]]: "search_unavailable" } : {},
    row_version: state === "queued" ? 1 : 3, started_at: state === "queued" ? null : now,
    finished_at: state === "queued" ? null : now, created_at: now, updated_at: now };
}
function insight(insightId: string, runId: string, selected: string[], version: number) {
  const retry = runId === fixture.retryRun;
  const activeSources = retry ? [fixture.sources[2]] : fixture.sources.slice(0, 2);
  return { insight_version_id: insightId, run_id: runId, version_number: version,
    selected_source_ids: selected, snapshot_ids: activeSources.map((_, index) => `${retry ? "6" : "5"}0000000-0000-4000-8000-00000000000${index + 1}`),
    facts: activeSources.map((_, index) => ({ fact_id: `${retry ? "retry" : "partial"}-fact-${index + 1}`,
      text: `${retry ? companies[2] : companies[index]}公开招聘高级结构工程师`,
      snapshot_id: `${retry ? "6" : "5"}0000000-0000-4000-8000-00000000000${index + 1}`,
      observation_id: `${retry ? "8" : "7"}0000000-0000-4000-8000-00000000000${index + 1}`,
      source_url: `https://company-${retry ? 3 : index + 1}.example.com/jobs/structure`, observed_at: now })),
    inferences: [{ text: "关注公司持续投入精密结构方向", basis_fact_ids: [`${retry ? "retry" : "partial"}-fact-1`] }],
    unknowns: [{ text: "团队编制仍待确认" }], direction_clusters: { 精密结构: activeSources.length },
    summary: retry ? "舜宇光学招聘情报已更新" : "三家公司精密结构招聘分析", source_conversation_id: fixture.conversation,
    source_turn_id: "90000000-0000-4000-8000-000000000001", agent_id: "hr-bot", model_version: "acceptance-v1", created_at: now };
}
function report(insightId: string, runId: string, selected: string[], version: number) {
  const value = insight(insightId, runId, selected, version);
  return { insight: value, sources: selected.map((id) => source(fixture.sources.indexOf(id))),
    snapshots: value.facts.map((fact, index) => ({ snapshot_id: fact.snapshot_id, run_id: runId,
      source_id: runId === fixture.retryRun ? fixture.sources[2] : fixture.sources[index], public_job_key: `structure-${index + 1}`,
      title: "高级结构工程师", location: "深圳", duty_excerpt: "负责精密结构研发与量产",
      requirement_excerpt: "需要可靠性、量产和跨团队协作经验", source_url: fact.source_url,
      observed_at: now, content_sha256: `${index + 1}`.repeat(64), status: "open", created_at: now })) };
}
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function setInput(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
async function settle() { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }
async function waitFor(check: () => void) {
  let error: unknown;
  for (let attempt = 0; attempt < 40; attempt += 1) { await settle(); try { check(); return; } catch (caught) { error = caught; } }
  throw error;
}
function button(container: HTMLElement, text: string) {
  const found = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent === text);
  expect(found, `missing button ${text}`).toBeTruthy(); return found!;
}
async function click(container: HTMLElement, text: string) { await act(async () => button(container, text).click()); await settle(); }
function expectInternalCodesHidden(container: HTMLElement) {
  for (const code of ["queued", "completed", "partially_completed", "search_unavailable", "SEARCH_UNAVAILABLE"]) {
    expect(container.textContent).not.toContain(code);
  }
}

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let messages: ConversationMessage[];
let currentTurn: ConversationTurn | null;
let streamResolvers: Array<() => void> = [];
let sources: ReturnType<typeof source>[];
let reports: ReturnType<typeof insight>[];
let mainStatusReads: number;
let retryStatusReads: number;
let downloadedBlob: Blob | null;
let submittedBody: Record<string, unknown> | null;

beforeEach(() => {
  window.history.replaceState({}, "", `/hr/positions/${fixture.position}/conversations/${fixture.conversation}`);
  sources = []; reports = []; mainStatusReads = 0; retryStatusReads = 0;
  downloadedBlob = null; submittedBody = null; currentTurn = null; streamResolvers = [];
  const conversation = { conversation_id: fixture.conversation, mode: "direct_agent" as const, direct_agent_id: "hr-bot",
    title: "高级结构工程师招聘", status: "active" as const, summary_through_seq: 0, created_at: now, updated_at: now, archived_at: null };
  messages = [{ message_id: "message-1", conversation_id: fixture.conversation, seq: 1, role: "assistant", content: "岗位对话已保留。",
    turn_id: "turn-1", delivery_status: "completed", created_at: now, completed_at: now,
    input_attachments: [], output_attachments: [], active_attachment_ids: [] }];
  vi.mocked(fetchAgentCatalog).mockResolvedValue([card]);
  vi.mocked(reconnectDelay).mockImplementation((signal) => new Promise((resolve) => signal.addEventListener("abort", () => resolve(), { once: true })));
  vi.mocked(listConversationAttachments).mockResolvedValue([]);
  vi.mocked(fetchConversation).mockResolvedValue({ conversation, current_turn: currentTurn });
  vi.mocked(fetchConversation).mockImplementation(async () => ({ conversation, current_turn: currentTurn }));
  vi.mocked(fetchConversationMessages).mockImplementation(async () => [...messages]);
  vi.mocked(listConversations).mockImplementation(async (_signal, _before, _limit, _agent, status = "active") => ({ items: status === "active" ? [conversation] : [], next_cursor: null }));
  vi.mocked(streamConversationEvents).mockImplementation((_id, options) => new Promise<void>((resolve) => {
    streamResolvers.push(() => { if (!options.signal.aborted) resolve(); });
  }));
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn((blob: Blob) => { downloadedBlob = blob; return "blob:panorama"; }) });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  Object.defineProperty(window, "scrollTo", { configurable: true, value: () => undefined });

  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, window.location.origin);
    const path = `${url.pathname}${url.search}`; const method = (init?.method ?? "GET").toUpperCase();
    if (path === `/api/hr/positions/${fixture.position}`) return json({ position_id: fixture.position, source_kind: "manual", official_job_id: null,
      title: "高级结构工程师", department: "研发", locations: ["深圳"], official_status: null, internal_status: "active", source_version: null,
      row_version: 1, created_at: now, updated_at: now, conversation_count: 1, material_count: 0, artifact_count: 0,
      conversation_ids: [fixture.conversation], material_attachment_ids: [], artifact_ids: [], artifact_attachment_ids: [] });
    if (path === `/api/hr/positions/${fixture.position}/context`) return json({ current: { context_version_id: fixture.context, position_id: fixture.position,
      version_number: 1, state: "confirmed", modules: { jd: { text: "原 JD" }, jr: { text: "原 JR" } }, summary: "高级结构工程师",
      official_version_id: null, base_context_version_id: null, source_conversation_id: fixture.conversation, source_turn_id: null,
      source_artifact_version_id: null, source_material_attachment_ids: [], agent_id: "hr-bot", model_version: "v1", row_version: 1,
      created_at: now, confirmed_at: now }, drafts: [] });
    if (path === `/api/hr/conversations/${fixture.conversation}/position-package`) return json({ detail: "not found" }, 404);
    if (path === "/api/hr/panorama/sources?limit=100" && method === "GET") return json({ items: sources });
    if (path === "/api/hr/panorama/reports?limit=100" && method === "GET") return json({ items: reports });
    if (path === "/api/hr/panorama/sources" && method === "POST") {
      const headers = new Headers(init?.headers); expect(headers.get("X-CSRF-Token")).toBe("csrf"); expect(headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/i);
      const body = JSON.parse(String(init?.body)); const index = companies.indexOf(body.canonical_name); expect(index).toBeGreaterThanOrEqual(0);
      expect(body).toEqual({ canonical_name: companies[index], aliases: [], approved_urls: [`https://company-${index + 1}.example.com/jobs`] });
      const created = source(index); sources = [...sources, created]; return json(created);
    }
    if (path === "/api/hr/panorama/runs" && method === "POST") {
      const headers = new Headers(init?.headers); expect(headers.get("X-CSRF-Token")).toBe("csrf"); expect(headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/i);
      const body = JSON.parse(String(init?.body)); const retry = body.source_ids.length === 1;
      expect(body.source_ids).toEqual(retry ? [fixture.sources[2]] : fixture.sources);
      return json(run(retry ? fixture.retryRun : fixture.run, body.source_ids, "queued"), 202);
    }
    if (path === `/api/hr/panorama/runs/${fixture.run}`) {
      mainStatusReads += 1; if (mainStatusReads === 1) reports = [insight(fixture.insight, fixture.run, fixture.sources, 1)];
      return json(run(fixture.run, fixture.sources, "partially_completed"));
    }
    if (path === `/api/hr/panorama/runs/${fixture.retryRun}`) {
      retryStatusReads += 1; if (retryStatusReads === 1) reports = [insight(fixture.retryInsight, fixture.retryRun, [fixture.sources[2]], 2), insight(fixture.insight, fixture.run, fixture.sources, 1)];
      return json(run(fixture.retryRun, [fixture.sources[2]], "completed"));
    }
    if (path === `/api/hr/panorama/reports/${fixture.insight}`) return json(report(fixture.insight, fixture.run, fixture.sources, 1));
    if (path === `/api/hr/panorama/reports/${fixture.retryInsight}`) return json(report(fixture.retryInsight, fixture.retryRun, [fixture.sources[2]], 2));
    if (path === "/api/v1/attachments/uploads" && method === "POST") return json({ upload_id: fixture.upload, attachment_id: fixture.attachment,
      conversation_id: fixture.conversation, original_name: "岗位补充.pdf", declared_mime: "application/pdf", declared_size: 4,
      state: "uploading", uploaded_bytes: 0, expires_at: "2027-09-05T04:00:00Z" });
    if (path === `/api/v1/attachments/uploads/${fixture.upload}/content` && method === "PUT") return json({ upload_id: fixture.upload,
      attachment_id: fixture.attachment, conversation_id: fixture.conversation, original_name: "岗位补充.pdf", declared_mime: "application/pdf",
      declared_size: 4, state: "uploading", uploaded_bytes: 4, expires_at: "2027-09-05T04:00:00Z" });
    if (path === `/api/v1/attachments/uploads/${fixture.upload}/complete` && method === "POST") return json({ attachment_id: fixture.attachment,
      conversation_id: fixture.conversation, original_name: "岗位补充.pdf", declared_mime: "application/pdf", detected_mime: "application/pdf",
      size_bytes: 4, state: "ready", created_at: now, retained_until: "2027-09-05T04:00:00Z" });
    if (path === `/api/v1/conversations/${fixture.conversation}/messages` && method === "POST") {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      submittedBody = body; const turnId = "90000000-0000-4000-8000-000000000009";
      const user: ConversationMessage = { message_id: "message-user-2", conversation_id: fixture.conversation, seq: 2, role: "user",
        content: String(body.text), turn_id: turnId, delivery_status: "completed", created_at: now, completed_at: now,
        input_attachments: [], output_attachments: [], active_attachment_ids: body.active_attachment_ids as string[] };
      currentTurn = { turn_id: turnId, conversation_id: fixture.conversation, user_message_id: user.message_id, assistant_message_id: null,
        retry_of_turn_id: null, status: "running", created_at: now, updated_at: now };
      messages = [...messages, user]; return json({ conversation, message: user, turn: currentTurn }, 201);
    }
    throw new Error(`unexpected request ${method} ${path}`);
  }));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  streamResolvers.splice(0).forEach((resolve) => resolve());
  if (root) await act(async () => root.unmount());
  await settle(); container?.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});

it("runs partial Panorama research, retries one source, and returns to the preserved Position conversation", async () => {
  await act(async () => root.render(<App />));
  await waitFor(() => expect(container.textContent).toContain("岗位对话已保留"));
  const chatHost = container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]'); expect(chatHost).not.toBeNull();
  const composer = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="继续对话"]')!;
  await act(async () => setInput(composer, "参考三家公司全景分析修订 JD/JR"));
  const upload = container.querySelector<HTMLInputElement>('.conversation-composer input[type="file"]')!;
  Object.defineProperty(upload, "files", { configurable: true, value: [new File(["test"], "岗位补充.pdf", { type: "application/pdf" })] });
  await act(async () => upload.dispatchEvent(new Event("change", { bubbles: true })));
  await waitFor(() => expect(container.textContent).toContain("岗位补充.pdf已就绪"));

  const panoramaLink = [...container.querySelectorAll<HTMLAnchorElement>("a")].find((item) => item.textContent === "全景分析")!;
  await act(async () => panoramaLink.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 })));
  await waitFor(() => expect(window.location.pathname).toBe("/hr/panorama"));
  await waitFor(() => expect(container.textContent).toContain("从公开招聘信息开始"));
  for (let index = 0; index < companies.length; index += 1) {
    await click(container, "添加关注公司");
    const inputs = container.querySelectorAll<HTMLInputElement>('[aria-label="添加关注公司"] input');
    await act(async () => { setInput(inputs[0], companies[index]); setInput(inputs[1], `https://company-${index + 1}.example.com/jobs`); });
    await click(container, "确认关注");
  }
  expect(container.textContent).toContain("分析范围：3 家");

  vi.useFakeTimers();
  await act(async () => button(container, "立即更新").click());
  expect(container.textContent).toContain("正在收集公开招聘岗位");
  await act(async () => vi.advanceTimersByTimeAsync(1_500));
  vi.useRealTimers();
  await waitFor(() => expect(container.textContent).toContain("部分公开来源暂时未能更新"));
  expect(container.textContent).toContain("三家公司精密结构招聘分析");
  expect(container.textContent).toContain("AI 推断");
  expect(container.querySelector('section[data-evidence-kind="inferences"] a[href="https://company-1.example.com/jobs/structure"]')).not.toBeNull();
  expectInternalCodesHidden(container);

  await click(container, "下载报告");
  expect(downloadedBlob).not.toBeNull();
  const downloaded = await downloadedBlob!.text();
  expect(downloaded).toContain("职责：负责精密结构研发与量产");
  expect(downloaded).toContain("要求：需要可靠性、量产和跨团队协作经验");
  expect(downloaded).toContain("https://company-1.example.com/jobs/structure");

  vi.useFakeTimers();
  await act(async () => button(container, "重试 舜宇光学").click());
  await act(async () => vi.advanceTimersByTimeAsync(1_500));
  vi.useRealTimers();
  await waitFor(() => expect(container.textContent).toContain("舜宇光学招聘情报已更新"));
  expectInternalCodesHidden(container);

  const chatLink = [...container.querySelectorAll<HTMLAnchorElement>("a")].find((item) => item.textContent === "对话")!;
  await act(async () => chatLink.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 })));
  await waitFor(() => expect(window.location.pathname).toBe(`/hr/positions/${fixture.position}/conversations/${fixture.conversation}`));
  expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(chatHost);
  expect(container.querySelector<HTMLTextAreaElement>('textarea[aria-label="继续对话"]')!.value).toBe("参考三家公司全景分析修订 JD/JR");

  await click(container, "✨ 发送");
  expect(submittedBody).toEqual({ text: "参考三家公司全景分析修订 JD/JR", attachment_ids: [fixture.attachment], active_attachment_ids: [fixture.attachment] });
  currentTurn = null;
  messages = [...messages, { message_id: "message-assistant-2", conversation_id: fixture.conversation, seq: 3, role: "assistant",
    content: `已参考全景分析第 1 版（${fixture.insight}）：联合光电来源 https://company-1.example.com/jobs/structure、奥比中光来源 https://company-2.example.com/jobs/structure；第 2 版（${fixture.retryInsight}）：舜宇光学来源 https://company-3.example.com/jobs/structure。观测截至 ${now}，并据此修订 JD/JR。`,
    turn_id: "90000000-0000-4000-8000-000000000009", delivery_status: "completed", created_at: now, completed_at: now,
    input_attachments: [], output_attachments: [], active_attachment_ids: [] }];
  await act(async () => streamResolvers.splice(0).forEach((resolve) => resolve()));
  await waitFor(() => expect(container.textContent).toContain("已参考全景分析第 1 版"));
  expect(container.textContent).toContain(fixture.insight);
  expect(container.textContent).toContain("联合光电来源 https://company-1.example.com/jobs/structure");
  expect(container.textContent).toContain("奥比中光来源 https://company-2.example.com/jobs/structure");
  expect(container.textContent).toContain("第 2 版");
  expect(container.textContent).toContain(fixture.retryInsight);
  expect(container.textContent).toContain("舜宇光学来源 https://company-3.example.com/jobs/structure");
  expect(container.textContent).toContain(now);
  expectInternalCodesHidden(container);
});
