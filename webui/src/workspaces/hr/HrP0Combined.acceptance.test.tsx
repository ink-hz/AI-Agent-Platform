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
  position: "10000000-0000-4000-8000-000000000004",
  context: "10000000-0000-4000-8000-000000000005",
  upload: "20000000-0000-4000-8000-000000000001",
  attachment: "20000000-0000-4000-8000-000000000002",
  insight: "30000000-0000-4000-8000-000000000001",
  publication: "30000000-0000-4000-8000-000000000002",
  productionBatch: "30000000-0000-4000-8000-000000000003",
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
  resumeUploads: [
    "72100000-0000-4000-8000-000000000001",
    "72100000-0000-4000-8000-000000000002",
    "72100000-0000-4000-8000-000000000003",
  ],
  batch: "72200000-0000-4000-8000-000000000001",
  analyses: [
    "73000000-0000-4000-8000-000000000001",
    "73000000-0000-4000-8000-000000000002",
    "73000000-0000-4000-8000-000000000003",
  ],
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
const resumeNames = ["候选人甲.pdf", "候选人乙.pdf", "候选人丙.pdf"];
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
const initialMessages: ConversationMessage[] = [
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
    content: "岗位需求初版已生成，请在对话中补充要求后再确认。",
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

function rawPosition() {
  return {
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
    run_id: null,
    production_batch_id: fixture.productionBatch,
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
    source_conversation_id: null,
    source_turn_id: null,
    agent_id: "hr-intelligence-producer",
    model_version: "configured-model-v1",
    created_at: now,
  };
}

function panoramaPublication() {
  return {
    publication_id: fixture.publication,
    bundle_id: fixture.publication,
    batch_id: fixture.productionBatch,
    insight_version_id: fixture.insight,
    manifest_sha256: "f".repeat(64),
    coverage_state: "partial",
    source_coverage: fixture.sources.map((sourceId, index) => index === 2 ? {
      source_id: sourceId,
      state: "failed",
      observed_at: now,
      source_urls: [`https://example.com/company-${index + 1}`],
      job_count: 0,
      error_code: "search_unavailable",
      channel_failures: { [`https://example.com/company-${index + 1}`]: "search_unavailable" },
    } : {
      source_id: sourceId,
      state: "succeeded",
      observed_at: now,
      source_urls: [`https://example.com/company-${index + 1}`],
      job_count: 1,
    }),
    generated_at: now,
    published_at: now,
  };
}

function panoramaReport() {
  const value = insight();
  return {
    publication: panoramaPublication(),
    insight: value,
    sources: fixture.sources.map((_id, index) => source(index)),
    snapshots: value.facts.map((fact, index) => ({
      snapshot_id: fact.snapshot_id,
      run_id: null,
      production_batch_id: fixture.productionBatch,
      observation_id: fact.observation_id,
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
    evidence: value.facts.map((fact, index) => ({
      source_id: fixture.sources[index],
      source_url: `https://example.com/company-${index + 1}`,
      attempt_number: 1,
      state: "succeeded",
      error_code: null,
      sha256: `${index + 1}`.repeat(64),
      mime: "text/html",
      size_bytes: 2048,
      normalized_job_count: 1,
      observed_at: now,
    })),
    analysis_usage: [],
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

function rawDraft(index: number, state: "ready" | "failed" | "confirmed") {
  return {
    draft_id: fixture.candidateDrafts[index],
    position_id: fixture.position,
    attachment_id: fixture.resumeAttachments[index],
    batch_request_id: fixture.batch,
    state,
    extracted_facts: state === "failed" ? {} : {
      stable_name: `候选人${["甲", "乙", "丙"][index]}`,
      skills: [index === 0 ? "挤出系统" : index === 1 ? "精密机械" : "测试"],
    },
    identity_candidates: [],
    error_code: state === "failed" ? "parser_response_invalid" : null,
    row_version: state === "failed" ? 3 : state === "confirmed" ? 4 : 2,
    created_at: now,
    updated_at: now,
  };
}

function rawResumeUpload(index: number, uploaded: boolean) {
  const size = new Blob([resumeNames[index]]).size;
  return {
    upload_id: fixture.resumeUploads[index],
    attachment_id: fixture.resumeAttachments[index],
    conversation_id: null,
    original_name: resumeNames[index],
    declared_mime: "application/pdf",
    declared_size: size,
    state: "uploading",
    uploaded_bytes: uploaded ? size : 0,
    expires_at: retainedUntil,
  };
}

function rawResumeAttachment(index: number) {
  return {
    attachment_id: fixture.resumeAttachments[index],
    conversation_id: null,
    original_name: resumeNames[index],
    declared_mime: "application/pdf",
    detected_mime: "application/pdf",
    size_bytes: new Blob([resumeNames[index]]).size,
    state: "ready",
    created_at: now,
    retained_until: retainedUntil,
  };
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

function setInput(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = input instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(input, value);
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

async function completeCurrentTurn(content: string) {
  expect(currentTurn).not.toBeNull();
  const completedTurn = currentTurn!;
  const assistantMessage: ConversationMessage = {
    message_id: `message-assistant-${messages.length + 1}`,
    conversation_id: fixture.conversation,
    seq: messages.length + 1,
    role: "assistant",
    content,
    turn_id: currentTurn!.turn_id,
    delivery_status: "completed",
    created_at: now,
    completed_at: now,
    input_attachments: [],
    output_attachments: [],
    active_attachment_ids: [],
  };
  messages = [...messages, assistantMessage];
  currentTurn = {
    ...completedTurn,
    assistant_message_id: assistantMessage.message_id,
    status: "completed",
    updated_at: now,
  };
  const scope = messageBodies[messageBodies.length - 1]?.scope as {
    positionCandidateIds?: string[];
  } | undefined;
  const candidateIndex = fixture.relations.indexOf(scope?.positionCandidateIds?.[0] ?? "");
  if (candidateIndex >= 0) {
    const interview = content.includes("面试");
    savedResults.push({
      turnId: completedTurn.turn_id,
      resultId: fixture.analyses[interview ? 2 : candidateIndex],
      schemaId: interview ? "hr.candidate-interview-plan.v1" : "hr.candidate-analysis.v2",
      contentSha256: `${candidateIndex + 5}`.repeat(64),
      candidateIndex,
    });
  }
  await act(async () => streamResolvers.splice(0).forEach((resolve) => resolve()));
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
let requests: string[];
let drafts: Array<ReturnType<typeof rawDraft>>;
let confirmedCandidates: number[];
let messages: ConversationMessage[];
let currentTurn: ConversationTurn | null;
let streamResolvers: Array<() => void>;
let messageBodies: Record<string, unknown>[];
let savedResults: Array<{
  turnId: string;
  resultId: string;
  schemaId: "hr.candidate-analysis.v2" | "hr.candidate-interview-plan.v1";
  contentSha256: string;
  candidateIndex: number;
}>;

beforeEach(() => {
  window.history.replaceState({}, "", `/hr/conversations/${fixture.conversation}`);
  requests = [];
  drafts = [];
  confirmedCandidates = [];
  messages = [...initialMessages];
  currentTurn = null;
  streamResolvers = [];
  messageBodies = [];
  savedResults = [];
  vi.mocked(fetchAgentCatalog).mockResolvedValue([card]);
  vi.mocked(reconnectDelay).mockResolvedValue(undefined);
  vi.mocked(listConversationAttachments).mockResolvedValue([]);
  vi.mocked(fetchConversation).mockImplementation(async (conversationId) => {
    expect(conversationId).toBe(fixture.conversation);
    return { conversation, current_turn: currentTurn };
  });
  vi.mocked(fetchConversationMessages).mockImplementation(async (conversationId) => {
    expect(conversationId).toBe(fixture.conversation);
    return [...messages];
  });
  vi.mocked(listConversations).mockImplementation(async (
    _signal, _before, _limit, _agent, status = "active",
  ) => ({ items: status === "active" ? [conversation] : [], next_cursor: null }));
  vi.mocked(streamConversationEvents).mockImplementation((_id, options) => (
    new Promise<void>((resolve) => {
      streamResolvers.push(() => {
        if (!options.signal.aborted) resolve();
      });
      options.signal.addEventListener("abort", () => resolve(), { once: true });
    })
  ));
  Object.defineProperty(window, "scrollTo", { configurable: true, value: () => undefined });

  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
      window.location.origin,
    );
    const path = `${url.pathname}${url.search}`;
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    requests.push(`${method} ${path}`);
    if (path === `/api/v1/hr/conversations/${fixture.conversation}/results` && method === "GET") {
      return json({
        turns: savedResults.map((item) => ({
          turnId: item.turnId,
          positionTitle: "高级结构工程师",
          scope: {
            positionId: fixture.position,
            positionCandidateIds: [fixture.relations[item.candidateIndex]],
            attachmentIds: [fixture.resumeAttachments[item.candidateIndex]],
          },
        })),
        results: savedResults.map(({ candidateIndex: _candidateIndex, ...item }) => item),
      });
    }
    const savedResult = /^\/api\/v1\/hr\/results\/([^/]+)$/.exec(path);
    if (savedResult && method === "GET") {
      const item = savedResults.find((candidate) => candidate.resultId === savedResult[1]);
      expect(item).toBeTruthy();
      const candidateName = `匿名候选人${item!.candidateIndex === 0 ? "甲" : "乙"}`;
      const interview = item!.schemaId === "hr.candidate-interview-plan.v1";
      return json({
        positionId: fixture.position,
        positionTitle: "高级结构工程师",
        candidateNames: [candidateName],
        positionCandidateIds: [fixture.relations[item!.candidateIndex]],
        attachmentIds: [fixture.resumeAttachments[item!.candidateIndex]],
        candidateDerived: true,
        result: {
          schemaId: item!.schemaId,
          title: interview ? `${candidateName}专属面试方案` : `${candidateName}匹配分析`,
          markdown: interview
            ? "请复盘一次喷嘴或挤出系统从设计到量产的完整过程。"
            : `${candidateName}：负责挤出系统量产，证据与差距已整理。`,
        },
      });
    }
    if (path === "/api/v1/attachments/uploads" && method === "POST") {
      const request = body(init);
      const resumeIndex = resumeNames.indexOf(String(request.original_name));
      if (resumeIndex >= 0) {
        expect(request).toEqual({
          conversation_id: null,
          original_name: resumeNames[resumeIndex],
          declared_mime: "application/pdf",
          declared_size: new Blob([resumeNames[resumeIndex]]).size,
        });
        return json(rawResumeUpload(resumeIndex, false));
      }
      expect(request).toEqual({
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
    const resumeContent = /^\/api\/v1\/attachments\/uploads\/([^/]+)\/content$/.exec(path);
    if (resumeContent && method === "PUT" && fixture.resumeUploads.includes(resumeContent[1])) {
      return json(rawResumeUpload(fixture.resumeUploads.indexOf(resumeContent[1]), true));
    }
    const resumeComplete = /^\/api\/v1\/attachments\/uploads\/([^/]+)\/complete$/.exec(path);
    if (resumeComplete && method === "POST" && fixture.resumeUploads.includes(resumeComplete[1])) {
      return json(rawResumeAttachment(fixture.resumeUploads.indexOf(resumeComplete[1])));
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
    if (path === `/api/v1/conversations/${fixture.conversation}/messages` && method === "POST") {
      const request = body(init);
      messageBodies.push(request);
      const turnId = `90000000-0000-4000-8000-${String(messageBodies.length).padStart(12, "0")}`;
      const userMessage: ConversationMessage = {
        message_id: `message-user-${messageBodies.length + 1}`,
        conversation_id: fixture.conversation,
        seq: messages.length + 1,
        role: "user",
        content: String(request.text),
        turn_id: turnId,
        delivery_status: "completed",
        created_at: now,
        completed_at: now,
        input_attachments: [],
        output_attachments: [],
        active_attachment_ids: request.active_attachment_ids as string[],
      };
      currentTurn = {
        turn_id: turnId,
        conversation_id: fixture.conversation,
        user_message_id: userMessage.message_id,
        assistant_message_id: null,
        retry_of_turn_id: null,
        status: "running",
        created_at: now,
        updated_at: now,
      };
      messages = [...messages, userMessage];
      return json({ conversation, message: userMessage, turn: currentTurn }, 201);
    }
    if (path === "/api/hr/positions?internal_status=active&limit=100" && method === "GET") {
      return json({ items: [rawPosition()], next_cursor: null });
    }
    if (path === `/api/hr/positions/${fixture.position}`) {
      return json({
        ...rawPosition(),
        conversation_count: 1,
        material_count: 3,
        artifact_count: 0,
        conversation_ids: [fixture.conversation],
        material_attachment_ids: fixture.resumeAttachments,
        artifact_ids: [],
        artifact_attachment_ids: [],
      });
    }
    if (path === `/api/hr/positions/${fixture.position}/context`) {
      return json({ current: rawContext(), drafts: [] });
    }
    if (path === `/api/hr/positions/${fixture.position}/context/versions`) {
      return json({ items: [rawContext()] });
    }
    if (path === "/api/hr/panorama/reports?limit=100" && method === "GET") {
      return json({ items: [{ publication: panoramaPublication(), insight: insight() }] });
    }
    if (path === "/api/hr/panorama/research" && method === "GET") {
      return json({
        edition: "2026-09-05",
        analyzed_at: now,
        observed_at: now,
        covered_job_identities: 2,
        articles: [],
        companies: [],
        questions: [],
      });
    }
    if (path === "/api/hr/panorama/current" && method === "GET") {
      return json(panoramaReport());
    }
    if (path === `/api/hr/panorama/reports/${fixture.publication}` && method === "GET") {
      return json(panoramaReport());
    }
    if (path === `/api/hr/positions/${fixture.position}/candidate-drafts` && method === "GET") {
      return json({ items: drafts });
    }
    if (path === `/api/hr/positions/${fixture.position}/candidate-drafts:batch` && method === "POST") {
      expect(body(init)).toEqual({ attachment_ids: fixture.resumeAttachments });
      drafts = [rawDraft(0, "ready"), rawDraft(1, "ready"), rawDraft(2, "failed")];
      return json({ batch_id: fixture.batch, items: drafts }, 202);
    }
    if (path === `/api/hr/candidate-drafts/${fixture.candidateDrafts[2]}:retry` && method === "POST") {
      expect(body(init)).toEqual({ expected_row_version: 3 });
      drafts = drafts.map((draft, index) => index === 2 ? rawDraft(2, "ready") : draft);
      return json(drafts[2]);
    }
    const confirmCandidate = /^\/api\/hr\/candidate-drafts\/([^/]+):confirm$/.exec(path);
    if (confirmCandidate && method === "POST") {
      const index = fixture.candidateDrafts.indexOf(confirmCandidate[1]);
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(2);
      expect(body(init)).toEqual({
        expected_row_version: 2,
        context_version_id: fixture.context,
        stable_name: `候选人${index === 0 ? "甲" : "乙"}`,
        confirmed_facts: rawDraft(index, "ready").extracted_facts,
        merge_candidate_id: null,
      });
      confirmedCandidates = [...new Set([...confirmedCandidates, index])];
      drafts = drafts.map((draft, draftIndex) => (
        draftIndex === index ? rawDraft(index, "confirmed") : draft
      ));
      return json({
        candidate: rawCandidate(index),
        document: rawDocument(index),
        position_candidate: rawRelation(index),
      }, 201);
    }
    if (path === `/api/hr/positions/${fixture.position}/candidates`) {
      return json({ items: confirmedCandidates.map(rawRelation) });
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
      return json({ items: [] });
    }
    const feedback = /^\/api\/hr\/position-candidates\/([^/]+)\/feedback$/.exec(path);
    if (feedback) return json({ items: [] });
    if (path === `/api/hr/positions/${fixture.position}/resources`) {
      return json({ materials: [], artifacts: [] });
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

// The former legacy-send journey is superseded by cloud primary routing. This
// fixture now verifies the supported historical read path through the real App.
it("preserves historical messages and exact saved results without submitting legacy work", async () => {
  savedResults = [
    {turnId:'turn-1',resultId:fixture.analyses[0],schemaId:'hr.candidate-analysis.v2',contentSha256:'5'.repeat(64),candidateIndex:0},
    {turnId:'turn-1',resultId:fixture.analyses[2],schemaId:'hr.candidate-interview-plan.v1',contentSha256:'6'.repeat(64),candidateIndex:1},
  ];
  await act(async () => root.render(<App />));
  await waitFor(() => expect(container.textContent).toContain("岗位需求初版已生成"));
  expect(container.querySelectorAll('.agent-use-workspace[data-agent-id="hr-bot"]')).toHaveLength(1);
  const composer = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="继续对话"]')!;
  expect(composer.disabled).toBe(true);
  expect(container.textContent).toContain('历史对话仅供查看');
  await click(container, "✨ 发送");
  expect(messageBodies).toHaveLength(0);

  await waitFor(() => expect(container.querySelectorAll("details.hr-turn-result")).toHaveLength(2));
  for (const result of container.querySelectorAll<HTMLDetailsElement>("details.hr-turn-result")) {
    await act(async () => {result.open = true; result.dispatchEvent(new Event("toggle"));});
  }
  await waitFor(() => expect(container.textContent).toContain("匿名候选人甲匹配分析"));
  expect(container.textContent).toContain("负责挤出系统量产，证据与差距已整理");
  expect(container.textContent).toContain("匿名候选人乙专属面试方案");
  expect(container.textContent).toContain("请复盘一次喷嘴或挤出系统从设计到量产的完整过程");
  expectSafeUi(container);
  expect(container.querySelector<HTMLAnchorElement>('.hr-workspace-nav a[href="/hr/"]')?.textContent).toBe('对话');
  expect(requests.filter(request => request.startsWith('POST '))).toEqual([]);
});
