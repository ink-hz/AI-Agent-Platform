/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import type { AgentCapabilityCard } from "../../brainTypes";
import type { ConversationMessage, ConversationTurn } from "../../conversationTypes";

const fixture = vi.hoisted(() => ({
  account: {
    internal_user_id: "combined-p0",
    display_name: "HR",
    role: "member",
    departments: [],
    gender: null,
    observation_agent_ids: [],
    workspace_scopes: [],
    directory_freshness: "fresh",
    hard_stale_read_only: false,
    csrf_token: "csrf",
  },
  conversation: "10000000-0000-4000-8000-000000000001",
  draft: "10000000-0000-4000-8000-000000000002",
  draftVersion: "10000000-0000-4000-8000-000000000003",
  position: "10000000-0000-4000-8000-000000000004",
  context: "10000000-0000-4000-8000-000000000005",
  upload: "20000000-0000-4000-8000-000000000001",
  attachment: "20000000-0000-4000-8000-000000000002",
  insight: "30000000-0000-4000-8000-000000000001",
  run: "30000000-0000-4000-8000-000000000002",
  sources: [
    "40000000-0000-4000-8000-000000000001",
    "40000000-0000-4000-8000-000000000002",
    "40000000-0000-4000-8000-000000000003",
  ],
  snapshots: [
    "41000000-0000-4000-8000-000000000001",
    "41000000-0000-4000-8000-000000000002",
  ],
  observations: [
    "42000000-0000-4000-8000-000000000001",
    "42000000-0000-4000-8000-000000000002",
  ],
  candidates: [
    "50000000-0000-4000-8000-000000000001",
    "50000000-0000-4000-8000-000000000002",
  ],
  relations: [
    "60000000-0000-4000-8000-000000000001",
    "60000000-0000-4000-8000-000000000002",
  ],
  documents: [
    "70000000-0000-4000-8000-000000000001",
    "70000000-0000-4000-8000-000000000002",
  ],
  candidateDrafts: [
    "71000000-0000-4000-8000-000000000001",
    "71000000-0000-4000-8000-000000000002",
    "71000000-0000-4000-8000-000000000003",
  ],
  resumeAttachments: [
    "72000000-0000-4000-8000-000000000001",
    "72000000-0000-4000-8000-000000000002",
    "72000000-0000-4000-8000-000000000003",
  ],
  analyses: [
    "73000000-0000-4000-8000-000000000001",
    "73000000-0000-4000-8000-000000000002",
    "73000000-0000-4000-8000-000000000003",
  ],
  artifact: "80000000-0000-4000-8000-000000000001",
  artifactVersion: "80000000-0000-4000-8000-000000000002",
  pdfAttachment: "80000000-0000-4000-8000-000000000003",
}));

const account = fixture.account as Account;

vi.mock("../../auth", async (original) => ({
  ...await original<typeof import("../../auth")>(),
  identityShellEnabled: () => true,
  loadAccount: vi.fn().mockResolvedValue(fixture.account),
}));
vi.mock("../../AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div data-app-shell>{children}</div>,
}));
vi.mock("../../accessEventReporter", () => ({ AccessEventReporter: () => null }));
vi.mock("../../brainApi", async (original) => ({
  ...await original<typeof import("../../brainApi")>(),
  fetchAgentCatalog: vi.fn(),
  reconnectDelay: vi.fn(),
}));
vi.mock("../../conversationApi", async (original) => ({
  ...await original<typeof import("../../conversationApi")>(),
  fetchConversation: vi.fn(),
  fetchConversationMessages: vi.fn(),
  listConversations: vi.fn(),
  streamConversationEvents: vi.fn(),
  markConversationRead: vi.fn().mockResolvedValue({}),
}));
vi.mock("../../attachmentApi", async (original) => ({
  ...await original<typeof import("../../attachmentApi")>(),
  listConversationAttachments: vi.fn(),
}));

import App from "../../App";
import { listConversationAttachments } from "../../attachmentApi";
import { fetchAgentCatalog, reconnectDelay } from "../../brainApi";
import {
  fetchConversation,
  fetchConversationMessages,
  listConversations,
  streamConversationEvents,
} from "../../conversationApi";

const now = "2026-09-05T08:00:00Z";
const retainedUntil = "2027-09-05T08:00:00Z";
const companies = ["示例光学甲", "示例智能制造乙", "示例硬件系统丙"];
const card: AgentCapabilityCard = {
  agent_id: "hr-bot",
  display_name: "HR Agent",
  domain_group: "HR",
  persona_subtitle: "Hannah · 招聘协作",
  mission: "完成招聘闭环",
  capabilities: ["岗位与候选人分析"],
  exclusions: ["不替代录用决定"],
  example_tasks: [],
  required_inputs: [],
  accepted_input_types: ["text", "image", "pdf", "office"],
  output_types: ["text", "pdf"],
  supports_attachments_in: true,
  supports_attachments_out: true,
  attachment_limits: {
    max_file_bytes: 52_428_800,
    max_files_per_message: 5,
    max_bytes_per_message: 52_428_800,
    max_files_per_conversation: 50,
    max_bytes_per_conversation: 524_288_000,
  },
  supports_evidence: true,
  supports_streaming: true,
  supports_cancellation: true,
  supports_idempotency: true,
  max_duration_seconds: 300,
  data_classification: "internal",
  adapter_id: "metabot-core-chat",
  capability_version: 1,
  adapter_kind: "metabot_local",
  adapter_config_version: 1,
  output_contract: "normalized_task_result_v1",
  interaction_modes: ["direct_chat"],
  workspace_url: null,
};

const conversation = {
  conversation_id: fixture.conversation,
  mode: "direct_agent" as const,
  direct_agent_id: "hr-bot",
  title: "高级结构工程师招聘",
  status: "active" as const,
  summary_through_seq: 0,
  created_at: now,
  updated_at: now,
  archived_at: null,
};
const messages: ConversationMessage[] = [
  {
    message_id: "message-user",
    conversation_id: fixture.conversation,
    seq: 1,
    role: "user",
    content: "需要一名懂喷嘴和挤出工艺的高级结构工程师",
    turn_id: "turn-1",
    delivery_status: "completed",
    created_at: now,
    completed_at: now,
    input_attachments: [],
    output_attachments: [],
    active_attachment_ids: [],
  },
  {
    message_id: "message-assistant",
    conversation_id: fixture.conversation,
    seq: 2,
    role: "assistant",
    content: "岗位需求、JD 和 JR 已生成，可以确认后进入岗位库。",
    turn_id: "turn-1",
    delivery_status: "completed",
    created_at: now,
    completed_at: now,
    input_attachments: [],
    output_attachments: [],
    active_attachment_ids: [],
  },
];

function rawContext() {
  return {
    context_version_id: fixture.context,
    position_id: fixture.position,
    version_number: 2,
    state: "confirmed",
    modules: {
      mission: { text: "交付稳定可靠且可量产的精密挤出结构。" },
      jd: { text: "负责喷嘴、挤出系统、可靠性验证和量产良率改进。" },
      jr: { text: "具备五年以上精密机械经验，掌握挤出工艺与失效分析。" },
    },
    summary: "已参考全景招聘证据修订 JD/JR",
    official_version_id: null,
    base_context_version_id: "10000000-0000-4000-8000-000000000099",
    source_conversation_id: fixture.conversation,
    source_turn_id: "90000000-0000-4000-8000-000000000001",
    source_artifact_version_id: null,
    source_material_attachment_ids: [],
    agent_id: "hr-bot",
    model_version: "combined-p0",
    row_version: 1,
    created_at: now,
    confirmed_at: now,
  };
}

function source(index: number) {
  return {
    source_id: fixture.sources[index],
    source_kind: "company",
    canonical_name: companies[index],
    aliases: [],
    approved_urls: [`https://example.com/company-${index + 1}`],
    active: true,
    created_at: now,
    updated_at: now,
  };
}

function insight() {
  return {
    insight_version_id: fixture.insight,
    run_id: fixture.run,
    version_number: 1,
    selected_source_ids: fixture.sources,
    snapshot_ids: fixture.snapshots,
    facts: [0, 1].map((index) => ({
      fact_id: `fact-${index + 1}`,
      text: `${companies[index]}公开招聘研发岗位`,
      snapshot_id: fixture.snapshots[index],
      observation_id: fixture.observations[index],
      source_url: `https://example.com/company-${index + 1}/jobs/structure`,
      observed_at: now,
    })),
    inferences: [{ text: "两家公司持续投入精密结构方向", basis_fact_ids: ["fact-1", "fact-2"] }],
    unknowns: [{ text: "团队编制仍待确认" }],
    direction_clusters: { 精密结构: 2 },
    summary: "全景招聘分析已完成",
    source_conversation_id: fixture.conversation,
    source_turn_id: "90000000-0000-4000-8000-000000000002",
    agent_id: "hr-bot",
    model_version: "combined-p0",
    created_at: now,
  };
}

function rawCandidate(index: number) {
  return {
    candidate_id: fixture.candidates[index],
    stable_name: `匿名候选人${index === 0 ? "甲" : "乙"}`,
    facts: { skills: [index === 0 ? "挤出系统量产" : "精密机械设计"] },
    created_at: now,
    updated_at: now,
  };
}

function rawRelation(index: number) {
  return {
    position_candidate_id: fixture.relations[index],
    position_id: fixture.position,
    candidate_id: fixture.candidates[index],
    context_version_id: fixture.context,
    source_draft_id: fixture.candidateDrafts[index],
    status: "active",
    row_version: 1,
    created_at: now,
    updated_at: now,
  };
}

function rawDocument(index: number) {
  return {
    document_id: fixture.documents[index],
    candidate_id: fixture.candidates[index],
    attachment_id: fixture.resumeAttachments[index],
    source_draft_id: fixture.candidateDrafts[index],
    document_kind: "resume",
    version_number: 1,
    content_sha256: `${index + 1}`.repeat(64),
    status: "active",
    created_at: now,
  };
}

function rawAnalyses(index: number) {
  const match = {
    analysis_version_id: fixture.analyses[index],
    position_candidate_id: fixture.relations[index],
    position_id: fixture.position,
    candidate_id: fixture.candidates[index],
    context_version_id: fixture.context,
    version_number: 1,
    analysis_kind: "match",
    document_ids: [fixture.documents[index]],
    feedback_ids: [],
    result: {
      summary: `匿名候选人${index === 0 ? "甲" : "乙"}匹配分析`,
      dimensions: { technical: index === 0 ? "强匹配" : "相邻能力" },
      evidence: [{ resume_fact: index === 0 ? "负责挤出系统量产" : "负责精密机械设计" }],
      gaps: [index === 0 ? "海外交付经历未说明" : "挤出工艺经验待验证"],
      risks: ["量产规模待核实"],
      unknowns: ["团队规模待验证"],
      verification_questions: ["请说明一次量产指标改进。"],
    },
    evidence: [{ resume_fact: index === 0 ? "负责挤出系统量产" : "负责精密机械设计" }],
    unknowns: ["团队规模待验证"],
    conflicts: [],
    verification_questions: ["请说明一次量产指标改进。"],
    agent_version: "hr-bot",
    model_version: "combined-p0",
    created_at: now,
    source_artifact_version_id: null,
  };
  if (index !== 0) return [match];
  return [match, {
    ...match,
    analysis_version_id: fixture.analyses[2],
    version_number: 2,
    analysis_kind: "candidate_interview_plan",
    result: {
      title: "高级结构工程师-匿名候选人甲-面试题",
      questions: [{
        verification_goal: "验证挤出系统量产经验",
        candidate_reason: "简历提及挤出系统量产",
        question: "请复盘一次喷嘴或挤出系统从设计到量产的完整过程。",
        follow_ups: ["关键失效模式是什么？"],
        strong_evidence: ["给出基线、措施和量化结果"],
        risk_signals: ["无法说明个人贡献"],
      }],
    },
    evidence: [],
    unknowns: [],
    verification_questions: ["请复盘一次喷嘴或挤出系统从设计到量产的完整过程。"],
    source_artifact_version_id: fixture.artifactVersion,
  }];
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function body(init?: RequestInit): Record<string, unknown> {
  expect(typeof init?.body).toBe("string");
  return JSON.parse(String(init?.body)) as Record<string, unknown>;
}

function setInput(input: HTMLTextAreaElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitFor(check: () => void) {
  let error: unknown;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await settle();
    try {
      check();
      return;
    } catch (caught) {
      error = caught;
    }
  }
  throw error;
}

function button(container: HTMLElement, label: string) {
  const found = [...container.querySelectorAll<HTMLButtonElement>("button")]
    .find((item) => item.textContent === label);
  expect(found, `missing button: ${label}`).toBeTruthy();
  return found!;
}

async function click(container: HTMLElement, label: string) {
  await act(async () => button(container, label).click());
  await settle();
}

async function follow(container: HTMLElement, label: string) {
  const link = [...container.querySelectorAll<HTMLAnchorElement>("a")]
    .find((item) => item.textContent === label);
  expect(link, `missing link: ${label}`).toBeTruthy();
  await act(async () => link!.dispatchEvent(new MouseEvent("click", {
    bubbles: true,
    cancelable: true,
    button: 0,
  })));
  await settle();
}

function expectSafeUi(container: HTMLElement) {
  const text = container.textContent ?? "";
  expect(text.match(/不可用/g) ?? []).toHaveLength(0);
  for (const code of [
    "queued",
    "completed",
    "partially_completed",
    "search_unavailable",
    "SEARCH_UNAVAILABLE",
    "parser_response_invalid",
  ]) expect(text).not.toContain(code);
}

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let confirmed: boolean;
let ticketRequests: string[];
let opened: Array<{ replace: ReturnType<typeof vi.fn> }>;
let requests: string[];

beforeEach(() => {
  window.history.replaceState({}, "", `/hr/conversations/${fixture.conversation}`);
  confirmed = false;
  ticketRequests = [];
  opened = [];
  requests = [];
  vi.mocked(fetchAgentCatalog).mockResolvedValue([card]);
  vi.mocked(reconnectDelay).mockResolvedValue(undefined);
  vi.mocked(listConversationAttachments).mockResolvedValue([]);
  vi.mocked(fetchConversation).mockImplementation(async (conversationId) => {
    expect(conversationId).toBe(fixture.conversation);
    return { conversation, current_turn: null as ConversationTurn | null };
  });
  vi.mocked(fetchConversationMessages).mockImplementation(async (conversationId) => {
    expect(conversationId).toBe(fixture.conversation);
    return messages;
  });
  vi.mocked(listConversations).mockImplementation(async (
    _signal, _before, _limit, _agent, status = "active",
  ) => ({ items: status === "active" ? [conversation] : [], next_cursor: null }));
  vi.mocked(streamConversationEvents).mockImplementation((_id, options) => (
    new Promise<void>((resolve) => options.signal.addEventListener("abort", () => resolve(), { once: true }))
  ));
  Object.defineProperty(window, "scrollTo", { configurable: true, value: () => undefined });
  vi.spyOn(window, "open").mockImplementation(() => {
    const target = { replace: vi.fn() };
    opened.push(target);
    return { opener: null, close: vi.fn(), location: { replace: target.replace } } as unknown as Window;
  });

  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
      window.location.origin,
    );
    const path = `${url.pathname}${url.search}`;
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    requests.push(`${method} ${path}`);
    if (path === "/api/v1/attachments/uploads" && method === "POST") {
      expect(body(init)).toEqual({
        conversation_id: fixture.conversation,
        original_name: "岗位补充.pdf",
        declared_mime: "application/pdf",
        declared_size: 4,
      });
      return json({
        upload_id: fixture.upload,
        attachment_id: fixture.attachment,
        conversation_id: fixture.conversation,
        original_name: "岗位补充.pdf",
        declared_mime: "application/pdf",
        declared_size: 4,
        state: "uploading",
        uploaded_bytes: 0,
        expires_at: retainedUntil,
      });
    }
    if (path === `/api/v1/attachments/uploads/${fixture.upload}/content` && method === "PUT") {
      return json({
        upload_id: fixture.upload,
        attachment_id: fixture.attachment,
        conversation_id: fixture.conversation,
        original_name: "岗位补充.pdf",
        declared_mime: "application/pdf",
        declared_size: 4,
        state: "uploading",
        uploaded_bytes: 4,
        expires_at: retainedUntil,
      });
    }
    if (path === `/api/v1/attachments/uploads/${fixture.upload}/complete` && method === "POST") {
      return json({
        attachment_id: fixture.attachment,
        conversation_id: fixture.conversation,
        original_name: "岗位补充.pdf",
        declared_mime: "application/pdf",
        detected_mime: "application/pdf",
        size_bytes: 4,
        state: "ready",
        created_at: now,
        retained_until: retainedUntil,
      });
    }
    if (path === `/api/hr/conversations/${fixture.conversation}/position-package`) {
      return json({
        draft_id: fixture.draft,
        draft_version_id: fixture.draftVersion,
        conversation_id: fixture.conversation,
        version_number: 2,
        title: "高级结构工程师",
        modules: rawContext().modules,
        row_version: 1,
        created_at: now,
        updated_at: now,
      });
    }
    if (path === `/api/hr/position-drafts/${fixture.draft}/versions/${fixture.draftVersion}/confirm` && method === "POST") {
      expect(body(init)).toEqual({ expected_row_version: 1 });
      confirmed = true;
      return json({
        position_id: fixture.position,
        context_version_id: fixture.context,
        conversation_id: fixture.conversation,
      });
    }
    if (path === `/api/hr/positions/${fixture.position}`) {
      expect(confirmed).toBe(true);
      return json({
        position_id: fixture.position,
        source_kind: "manual",
        official_job_id: null,
        title: "高级结构工程师",
        department: "研发",
        locations: ["深圳"],
        official_status: null,
        internal_status: "active",
        source_version: null,
        row_version: 1,
        created_at: now,
        updated_at: now,
        conversation_count: 1,
        material_count: 3,
        artifact_count: 1,
        conversation_ids: [fixture.conversation],
        material_attachment_ids: fixture.resumeAttachments,
        artifact_ids: [fixture.artifact],
        artifact_attachment_ids: [fixture.pdfAttachment],
      });
    }
    if (path === `/api/hr/positions/${fixture.position}/context`) {
      return json({ current: rawContext(), drafts: [] });
    }
    if (path === `/api/hr/positions/${fixture.position}/context/versions`) {
      return json({ items: [rawContext()] });
    }
    if (path === "/api/hr/panorama/sources?limit=100") {
      return json({ items: fixture.sources.map((_id, index) => source(index)) });
    }
    if (path === "/api/hr/panorama/reports?limit=100") {
      return json({ items: [insight()] });
    }
    if (path === `/api/hr/panorama/reports/${fixture.insight}`) {
      const value = insight();
      return json({
        insight: value,
        sources: fixture.sources.map((_id, index) => source(index)),
        snapshots: value.facts.map((fact, index) => ({
          snapshot_id: fact.snapshot_id,
          run_id: fixture.run,
          source_id: fixture.sources[index],
          public_job_key: `structure-${index + 1}`,
          title: "高级结构工程师",
          location: "深圳",
          duty_excerpt: "负责精密结构研发与量产",
          requirement_excerpt: "需要可靠性与工艺经验",
          source_url: fact.source_url,
          observed_at: now,
          content_sha256: `${index + 1}`.repeat(64),
          status: "open",
          created_at: now,
        })),
      });
    }
    if (path === `/api/hr/panorama/runs/${fixture.run}`) {
      return json({
        run_id: fixture.run,
        selected_source_ids: fixture.sources,
        conversation_id: fixture.conversation,
        state: "partially_completed",
        error_code: null,
        source_failures: { [fixture.sources[2]]: "search_unavailable" },
        row_version: 3,
        started_at: now,
        finished_at: now,
        created_at: now,
        updated_at: now,
      });
    }
    if (path === `/api/hr/positions/${fixture.position}/candidate-drafts`) {
      return json({ items: [] });
    }
    if (path === `/api/hr/positions/${fixture.position}/candidates`) {
      return json({ items: [rawRelation(0), rawRelation(1)] });
    }
    const candidate = /^\/api\/hr\/candidates\/([^/]+)$/.exec(path);
    if (candidate) {
      const index = fixture.candidates.indexOf(candidate[1]);
      expect(index).toBeGreaterThanOrEqual(0);
      return json(rawCandidate(index));
    }
    const documents = /^\/api\/hr\/candidates\/([^/]+)\/documents$/.exec(path);
    if (documents) {
      const index = fixture.candidates.indexOf(documents[1]);
      expect(index).toBeGreaterThanOrEqual(0);
      return json({ items: [rawDocument(index)] });
    }
    const analyses = /^\/api\/hr\/position-candidates\/([^/]+)\/analyses$/.exec(path);
    if (analyses) {
      const index = fixture.relations.indexOf(analyses[1]);
      expect(index).toBeGreaterThanOrEqual(0);
      return json({ items: rawAnalyses(index) });
    }
    const feedback = /^\/api\/hr\/position-candidates\/([^/]+)\/feedback$/.exec(path);
    if (feedback) return json({ items: [] });
    if (path === `/api/hr/positions/${fixture.position}/tasks?status=active`) {
      return json({ items: [] });
    }
    if (path === `/api/hr/positions/${fixture.position}/resources`) {
      return json({
        materials: [],
        artifacts: [{
          artifact_id: fixture.artifact,
          artifact_version_id: fixture.artifactVersion,
          attachment_id: fixture.pdfAttachment,
          artifact_version: 1,
          filename: "高级结构工程师-匿名候选人甲-面试题.pdf",
          media_type: "application/pdf",
          state: "ready",
          size_bytes: 128,
          created_at: now,
          source_conversation_id: fixture.conversation,
          source_turn_id: "90000000-0000-4000-8000-000000000003",
          preview_available: true,
          download_available: true,
        }],
      });
    }
    if (path === `/api/hr/positions/${fixture.position}/resources/${fixture.pdfAttachment}/ticket` && method === "POST") {
      expect(body(init)).toEqual({ purpose: "download" });
      const requestId = new Headers(init?.headers).get("Idempotency-Key");
      expect(requestId).toMatch(/^[0-9a-f-]{36}$/i);
      ticketRequests.push(requestId!);
      const token = (ticketRequests.length === 1 ? "a" : "b").repeat(32);
      return json({
        content_path: `/api/v1/attachments/content/${token}`,
        expires_at: retainedUntil,
      }, 201);
    }
    throw new Error(`unexpected request: ${method} ${path}`);
  }));

  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container?.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("reopens the combined P0 results without losing the mounted recruiting conversation", async () => {
  await act(async () => root.render(<App />));
  await waitFor(() => expect(container.textContent).toContain("岗位需求、JD 和 JR 已生成"));
  const chatHost = container.querySelector<HTMLElement>('.agent-use-workspace[data-agent-id="hr-bot"]');
  expect(chatHost).not.toBeNull();
  expect(container.querySelectorAll('.agent-use-workspace[data-agent-id="hr-bot"]')).toHaveLength(1);

  const composer = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="继续对话"]')!;
  await act(async () => setInput(composer, "保留这段岗位对话草稿"));
  const upload = container.querySelector<HTMLInputElement>('.conversation-composer input[type="file"]')!;
  Object.defineProperty(upload, "files", {
    configurable: true,
    value: [new File(["test"], "岗位补充.pdf", { type: "application/pdf" })],
  });
  await act(async () => upload.dispatchEvent(new Event("change", { bubbles: true })));
  await waitFor(() => expect(container.textContent).toContain("岗位补充.pdf已就绪"));

  await click(container, "确认并加入岗位库");
  await waitFor(() => expect(window.location.pathname).toBe(
    `/hr/positions/${fixture.position}/conversations/${fixture.conversation}`,
  ));
  await waitFor(() => expect(container.textContent).toContain("已加入岗位库"));
  expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(chatHost);
  await waitFor(() => expect(
    chatHost?.classList.contains("is-focused"), JSON.stringify(requests),
  ).toBe(true));

  await follow(container, "全景分析");
  await waitFor(() => expect(container.textContent).toContain("全景招聘分析已完成"));
  expect(container.textContent).toContain("示例光学甲公开招聘研发岗位");
  expect(container.textContent).toContain("两家公司持续投入精密结构方向");
  expectSafeUi(container);

  await follow(container, "对话");
  await waitFor(() => expect(window.location.pathname).toBe(
    `/hr/positions/${fixture.position}/conversations/${fixture.conversation}`,
  ));
  expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(chatHost);
  expect(container.querySelector<HTMLTextAreaElement>('textarea[aria-label="继续对话"]')?.value)
    .toBe("保留这段岗位对话草稿");
  expect(container.textContent).toContain("岗位补充.pdf已就绪");
  await click(container, "JD");
  expect(container.textContent).toContain("负责喷嘴、挤出系统、可靠性验证和量产良率改进");
  await click(container, "JR");
  expect(container.textContent).toContain("掌握挤出工艺与失效分析");

  await click(container, "岗位资料");
  await waitFor(() => expect(container.textContent).toContain("已参考全景招聘证据修订 JD/JR"));
  await click(container, "候选人");
  await waitFor(() => expect(container.textContent).toContain("2 位已确认"));
  await click(container, "查看匿名候选人甲");
  await waitFor(() => expect(container.textContent).toContain("匿名候选人甲匹配分析"));
  expect(container.textContent).toContain("负责挤出系统量产");
  expect(container.textContent).toContain("请复盘一次喷嘴或挤出系统从设计到量产的完整过程");
  expect(container.querySelector('article[aria-label="候选人专属面试题 v2"]')).not.toBeNull();
  expectSafeUi(container);

  const download = container.querySelector<HTMLButtonElement>('button[aria-label="下载面试题 PDF"]')!;
  await act(async () => download.click());
  await waitFor(() => expect(opened).toHaveLength(1));
  await act(async () => download.click());
  await waitFor(() => expect(opened).toHaveLength(2));
  expect(ticketRequests).toHaveLength(2);
  expect(ticketRequests[0]).not.toBe(ticketRequests[1]);
  expect(opened[0].replace).toHaveBeenCalledWith(
    `/api/v1/attachments/content/${"a".repeat(32)}`,
  );
  expect(opened[1].replace).toHaveBeenCalledWith(
    `/api/v1/attachments/content/${"b".repeat(32)}`,
  );

  await act(async () => root.unmount());
  root = createRoot(container);
  window.history.replaceState(
    {},
    "",
    `/hr/positions/${fixture.position}/conversations/${fixture.conversation}`,
  );
  await act(async () => root.render(<App />));
  await waitFor(() => expect(container.textContent).toContain("岗位需求、JD 和 JR 已生成"));
  expect(container.querySelectorAll('.agent-use-workspace[data-agent-id="hr-bot"]')).toHaveLength(1);
  await click(container, "JD");
  expect(container.textContent).toContain("负责喷嘴、挤出系统、可靠性验证和量产良率改进");
  await click(container, "JR");
  expect(container.textContent).toContain("掌握挤出工艺与失效分析");
  await click(container, "岗位资料");
  await click(container, "候选人");
  await waitFor(() => expect(container.textContent).toContain("2 位已确认"));
  await click(container, "查看匿名候选人甲");
  await waitFor(() => expect(container.textContent).toContain("匿名候选人甲匹配分析"));
  expect(container.querySelector('article[aria-label="候选人专属面试题 v2"]')).not.toBeNull();
  expect(container.querySelector('button[aria-label="下载面试题 PDF"]')).not.toBeNull();
  await click(container, "关闭详情");
  await click(container, "查看匿名候选人乙");
  await waitFor(() => expect(container.textContent).toContain("匿名候选人乙匹配分析"));
  expect(container.textContent).toContain("负责精密机械设计");
  expectSafeUi(container);
  await click(container, "关闭详情");
  await act(async () => container.querySelector<HTMLButtonElement>(
    'button[aria-label="关闭岗位资料"]',
  )!.click());
  await settle();
  await follow(container, "全景分析");
  await waitFor(() => expect(container.textContent).toContain("全景招聘分析已完成"));
  expectSafeUi(container);
});
