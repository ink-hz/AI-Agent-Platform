/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { listConversationAttachments } from "../../attachmentApi";
import { fetchAgentCatalog, reconnectDelay } from "../../brainApi";
import type { AgentCapabilityCard } from "../../brainTypes";
import {
  fetchConversation, fetchConversationMessages, listConversations, streamConversationEvents,
} from "../../conversationApi";
import type { ConversationMessage, ConversationTurn } from "../../conversationTypes";
import { createHrR12Api } from "../../hrR12Api";
import type { HrCandidateAnalysisVersion, HrCandidateDraft, HrTaskKind } from "../../hrR12Types";
import { useRoute } from "../../router";
import { HrWorkspacePage } from "./HrWorkspacePage";

vi.mock("../../brainApi", async (original) => ({
  ...await original<typeof import("../../brainApi")>(), fetchAgentCatalog: vi.fn(), reconnectDelay: vi.fn(),
}));
vi.mock("../../conversationApi", async (original) => ({
  ...await original<typeof import("../../conversationApi")>(), fetchConversation: vi.fn(),
  fetchConversationMessages: vi.fn(), listConversations: vi.fn(), streamConversationEvents: vi.fn(),
}));
vi.mock("../../attachmentApi", async (original) => ({
  ...await original<typeof import("../../attachmentApi")>(), listConversationAttachments: vi.fn(),
}));

const ids = {
  conversation: "10000000-0000-4000-8000-000000000001",
  draft: "10000000-0000-4000-8000-000000000002",
  draftVersion: "10000000-0000-4000-8000-000000000003",
  position: "10000000-0000-4000-8000-000000000004",
  context: "10000000-0000-4000-8000-000000000005",
  batch: "10000000-0000-4000-8000-000000000006",
  drafts: ["20000000-0000-4000-8000-000000000001", "20000000-0000-4000-8000-000000000002", "20000000-0000-4000-8000-000000000003"],
  attachments: ["30000000-0000-4000-8000-000000000001", "30000000-0000-4000-8000-000000000002", "30000000-0000-4000-8000-000000000003"],
  uploads: ["31000000-0000-4000-8000-000000000001", "31000000-0000-4000-8000-000000000002", "31000000-0000-4000-8000-000000000003"],
  candidates: ["40000000-0000-4000-8000-000000000001", "40000000-0000-4000-8000-000000000002"],
  relations: ["50000000-0000-4000-8000-000000000001", "50000000-0000-4000-8000-000000000002"],
  documents: ["60000000-0000-4000-8000-000000000001", "60000000-0000-4000-8000-000000000002"],
  matches: ["70000000-0000-4000-8000-000000000001", "70000000-0000-4000-8000-000000000002"],
  interview: "70000000-0000-4000-8000-000000000003",
  artifact: "80000000-0000-4000-8000-000000000001",
  artifactVersion: "80000000-0000-4000-8000-000000000002",
  pdfAttachment: "80000000-0000-4000-8000-000000000003",
  turns: ["90000000-0000-4000-8000-000000000001", "90000000-0000-4000-8000-000000000002", "90000000-0000-4000-8000-000000000003"],
};
const now = "2026-09-04T10:00:00Z";
const retainedUntil = "2027-09-04T10:00:00Z";
const filenames = ["候选人甲.pdf", "候选人乙.pdf", "候选人丙.pdf"];
const account: Account = {
  internal_user_id: "p0-hr", display_name: "P0 HR", role: "member", departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};
const hrCard: AgentCapabilityCard = {
  agent_id: "hr-bot", display_name: "HR Agent", domain_group: "HR",
  persona_subtitle: "Hannah · 招聘协作", mission: "完成招聘闭环", capabilities: ["岗位与候选人分析"],
  exclusions: ["不替代录用决定"], example_tasks: ["准备候选人面试题"], required_inputs: ["招聘目标"],
  accepted_input_types: ["text", "pdf", "office"], output_types: ["text", "pdf"],
  supports_attachments_in: true, supports_attachments_out: true,
  attachment_limits: { max_file_bytes: 52_428_800, max_files_per_message: 5, max_bytes_per_message: 52_428_800, max_files_per_conversation: 50, max_bytes_per_conversation: 524_288_000 },
  supports_evidence: true, supports_streaming: true, supports_cancellation: true, supports_idempotency: true,
  max_duration_seconds: 300, data_classification: "internal", adapter_id: "metabot-core-chat",
  capability_version: 1, adapter_kind: "metabot_local", adapter_config_version: 1,
  output_contract: "normalized_task_result_v1", interaction_modes: ["direct_chat", "brain_delegation"], workspace_url: null,
};

function parsedDraft(index: number, state: HrCandidateDraft["state"]): HrCandidateDraft {
  return {
    draftId: ids.drafts[index], positionId: ids.position, attachmentId: ids.attachments[index],
    batchRequestId: ids.batch, state,
    extractedFacts: state === "ready" ? { stable_name: `候选人${["甲", "乙", "丙"][index]}`, skills: [index === 0 ? "挤出系统" : index === 1 ? "精密机械" : "测试"] } : {},
    identityCandidateIds: [], errorCode: state === "failed" ? "parser_response_invalid" : null,
    rowVersion: state === "failed" ? 3 : state === "confirmed" ? 4 : 2, createdAt: now, updatedAt: now,
  };
}

function match(index: number): HrCandidateAnalysisVersion {
  const primary = index === 0;
  const fact = primary ? "负责挤出系统量产" : "负责精密机械设计";
  const question = primary ? "请说明量产良率提升过程。" : "请说明精密机械量产指标。";
  return {
    analysisVersionId: ids.matches[index], positionCandidateId: ids.relations[index],
    positionId: ids.position, candidateId: ids.candidates[index], contextVersionId: ids.context,
    versionNumber: 1, analysisKind: "match", documentIds: [ids.documents[index]], feedbackIds: [],
    result: { summary: `候选人${primary ? "甲" : "乙"}总体匹配`, dimensions: { technical: primary ? "强匹配" : "匹配" },
      evidence: [{ resume_fact: fact, document_id: ids.documents[index] }], gaps: [primary ? "海外交付经历未说明" : "挤出系统经历待核实"],
      risks: ["团队规模待核实"], unknowns: [primary ? "量产良率经验待验证" : "量产指标待验证"], verification_questions: [question] },
    evidence: [{ resume_fact: fact, document_id: ids.documents[index] }], unknowns: [primary ? "量产良率经验待验证" : "量产指标待验证"],
    conflicts: [], verificationQuestions: [question], agentVersion: "hr-bot", modelVersion: "deterministic-agent",
    createdAt: now, sourceArtifactVersionId: null,
  };
}
const interview: HrCandidateAnalysisVersion = {
  ...match(0), analysisVersionId: ids.interview, versionNumber: 2, analysisKind: "candidate_interview_plan",
  result: { title: "高级结构工程师-候选人甲-面试题", questions: [{
    verification_goal: "验证挤出系统量产经验", candidate_reason: "简历明确提及挤出系统量产",
    question: "请说明一次挤出系统量产良率提升过程。", follow_ups: ["你负责的部分是什么？"],
    strong_evidence: ["给出基线、措施和量化结果"], risk_signals: ["无法区分个人贡献与团队成果"],
  }] }, evidence: [], unknowns: [], verificationQuestions: ["请说明一次挤出系统量产良率提升过程。"],
  sourceArtifactVersionId: ids.artifactVersion,
};

function rawDraft(value: HrCandidateDraft) {
  return { draft_id: value.draftId, position_id: value.positionId, attachment_id: value.attachmentId,
    batch_request_id: value.batchRequestId, state: value.state, extracted_facts: value.extractedFacts,
    identity_candidates: value.identityCandidateIds, error_code: value.errorCode, row_version: value.rowVersion,
    created_at: value.createdAt, updated_at: value.updatedAt };
}
function rawCandidate(index: number) {
  return { candidate_id: ids.candidates[index], stable_name: `候选人${index === 0 ? "甲" : "乙"}`,
    facts: { skills: [index === 0 ? "挤出系统" : "精密机械"] }, created_at: now, updated_at: now };
}
function rawDocument(index: number) {
  return { document_id: ids.documents[index], candidate_id: ids.candidates[index], attachment_id: ids.attachments[index],
    source_draft_id: ids.drafts[index], document_kind: "resume", version_number: 1,
    content_sha256: `${index + 1}`.repeat(64), status: "active", created_at: now };
}
function rawRelation(index: number) {
  return { position_candidate_id: ids.relations[index], position_id: ids.position, candidate_id: ids.candidates[index],
    context_version_id: ids.context, source_draft_id: ids.drafts[index], status: "active", row_version: 1,
    created_at: now, updated_at: now };
}
function rawAnalysis(value: HrCandidateAnalysisVersion) {
  return { analysis_version_id: value.analysisVersionId, position_candidate_id: value.positionCandidateId,
    position_id: value.positionId, candidate_id: value.candidateId, context_version_id: value.contextVersionId,
    version_number: value.versionNumber, analysis_kind: value.analysisKind, document_ids: value.documentIds,
    feedback_ids: value.feedbackIds, result: value.result, evidence: value.evidence, unknowns: value.unknowns,
    conflicts: value.conflicts, verification_questions: value.verificationQuestions, agent_version: value.agentVersion,
    model_version: value.modelVersion, created_at: value.createdAt, source_artifact_version_id: value.sourceArtifactVersionId };
}
function rawContext() {
  return { context_version_id: ids.context, position_id: ids.position, version_number: 1, state: "confirmed",
    modules: { mission: { text: "负责高可靠挤出系统交付。" }, jd: { text: "负责喷嘴与挤出系统结构设计和量产。" }, jr: { text: "具备五年以上精密机械量产经验。" } },
    summary: "高级结构工程师", official_version_id: null, base_context_version_id: null,
    source_conversation_id: ids.conversation, source_turn_id: null, source_artifact_version_id: null,
    source_material_attachment_ids: [], agent_id: "hr-bot", model_version: "deterministic-agent",
    row_version: 1, created_at: now, confirmed_at: now };
}
function rawUpload(index: number, uploaded: boolean) {
  const size = new Blob([filenames[index]]).size;
  return { upload_id: ids.uploads[index], attachment_id: ids.attachments[index], conversation_id: null,
    original_name: filenames[index], declared_mime: "application/pdf", declared_size: size, state: "uploading",
    uploaded_bytes: uploaded ? size : 0, expires_at: retainedUntil };
}
function rawAttachment(index: number) {
  return { attachment_id: ids.attachments[index], conversation_id: null, original_name: filenames[index],
    declared_mime: "application/pdf", detected_mime: "application/pdf", size_bytes: new Blob([filenames[index]]).size,
    state: "ready", created_at: now, retained_until: retainedUntil };
}
function rawTask(kind: HrTaskKind, taskIndex: number) {
  const candidateIndex = taskIndex === 2 ? 0 : taskIndex;
  return { task_id: ids.turns[taskIndex], status: "completed", task_kind: kind, error: null,
    conversation_id: ids.conversation, turn_id: ids.turns[taskIndex],
    position_candidate_id: ids.relations[candidateIndex], candidate_id: ids.candidates[candidateIndex] };
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}
function requestBody(init?: RequestInit): Record<string, unknown> {
  expect(typeof init?.body).toBe("string");
  return JSON.parse(String(init?.body)) as Record<string, unknown>;
}
function RoutedWorkspace() {
  const route = useRoute();
  if (route.name === "hr-conversation") return <HrWorkspacePage account={account} conversationId={route.conversationId} />;
  if (route.name === "hr-position-conversation") return <HrWorkspacePage account={account} conversationId={route.conversationId} positionId={route.positionId} />;
  return <p role="alert">unexpected route</p>;
}
async function settle() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 0)); });
}
async function waitFor(check: () => void) {
  let error: unknown;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await settle();
    try { check(); return; } catch (caught) { error = caught; }
  }
  throw error;
}
function findButton(container: HTMLElement, text: string) {
  const selected = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent === text);
  expect(selected, `missing button: ${text}`).toBeTruthy();
  return selected!;
}
async function click(container: HTMLElement, text: string) {
  await act(async () => findButton(container, text).click());
  await settle();
}

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let drafts: HrCandidateDraft[];
let confirmed: number[];
let analyses: Map<number, HrCandidateAnalysisVersion[]>;
let messages: ConversationMessage[];
let currentTurn: ConversationTurn | null;
let streamResolvers: Array<() => void>;
let taskBodies: Record<string, unknown>[];
let batchBodies: Record<string, unknown>[];
let confirmBodies: Record<string, unknown>[];
let ticketIds: string[];
let opened: Array<{ close: ReturnType<typeof vi.fn>; replace: ReturnType<typeof vi.fn> }>;

beforeEach(() => {
  window.history.replaceState({}, "", `/hr/conversations/${ids.conversation}`);
  drafts = []; confirmed = []; analyses = new Map([[0, []], [1, []]]); currentTurn = null;
  streamResolvers = []; taskBodies = []; batchBodies = []; confirmBodies = []; ticketIds = []; opened = [];
  messages = [
    { message_id: "message-user", conversation_id: ids.conversation, seq: 1, role: "user", content: "想招聘一名结构工程师", turn_id: "turn-clarification", delivery_status: "completed", created_at: now, completed_at: now, input_attachments: [], output_attachments: [], active_attachment_ids: [] },
    { message_id: "message-clarification", conversation_id: ids.conversation, seq: 2, role: "assistant", content: "请补充岗位地点和量产范围。", turn_id: "turn-clarification", delivery_status: "completed", created_at: now, completed_at: now, input_attachments: [], output_attachments: [], active_attachment_ids: [] },
    { message_id: "message-package", conversation_id: ids.conversation, seq: 3, role: "assistant", content: "岗位方案已生成。", turn_id: "turn-package", delivery_status: "completed", created_at: now, completed_at: now, input_attachments: [], output_attachments: [], active_attachment_ids: [] },
  ];
  const conversation = { conversation_id: ids.conversation, mode: "direct_agent" as const, direct_agent_id: "hr-bot", title: "高级结构工程师招聘", status: "active" as const, summary_through_seq: 0, created_at: now, updated_at: now, archived_at: null };
  vi.mocked(fetchAgentCatalog).mockResolvedValue([hrCard]);
  vi.mocked(reconnectDelay).mockResolvedValue(undefined);
  vi.mocked(listConversationAttachments).mockResolvedValue([]);
  vi.mocked(fetchConversation).mockImplementation(async (conversationId) => {
    expect(conversationId).toBe(ids.conversation);
    return { conversation, current_turn: currentTurn };
  });
  vi.mocked(fetchConversationMessages).mockImplementation(async (conversationId) => {
    expect(conversationId).toBe(ids.conversation);
    return [...messages];
  });
  vi.mocked(listConversations).mockImplementation(async (_signal, _cursor, _limit, _agent, status = "active") => ({ items: status === "active" ? [conversation] : [], next_cursor: null }));
  vi.mocked(streamConversationEvents).mockImplementation((conversationId, options) => new Promise<void>((resolve) => {
    expect(conversationId).toBe(ids.conversation);
    streamResolvers.push(() => { if (!options.signal.aborted) resolve(); });
  }));

  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, window.location.origin);
    const path = `${url.pathname}${url.search}`;
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    if (path === "/api/v1/attachments/uploads" && method === "POST") {
      const body = requestBody(init); const index = filenames.indexOf(String(body.original_name));
      expect(index).toBeGreaterThanOrEqual(0);
      expect(body).toEqual({ conversation_id: null, original_name: filenames[index], declared_mime: "application/pdf", declared_size: new Blob([filenames[index]]).size });
      return json(rawUpload(index, false));
    }
    const content = /^\/api\/v1\/attachments\/uploads\/([^/]+)\/content$/.exec(path);
    if (content && method === "PUT") { const index = ids.uploads.indexOf(content[1]); expect(index).toBeGreaterThanOrEqual(0); expect(init?.body).toBeInstanceOf(File); return json(rawUpload(index, true)); }
    const complete = /^\/api\/v1\/attachments\/uploads\/([^/]+)\/complete$/.exec(path);
    if (complete && method === "POST") { const index = ids.uploads.indexOf(complete[1]); expect(index).toBeGreaterThanOrEqual(0); return json(rawAttachment(index)); }

    if (path === `/api/hr/conversations/${ids.conversation}/position-package` && method === "GET") return json({ draft_id: ids.draft, draft_version_id: ids.draftVersion, conversation_id: ids.conversation, version_number: 1, title: "高级结构工程师", modules: rawContext().modules, row_version: 1, created_at: now, updated_at: now });
    if (path === `/api/hr/position-drafts/${ids.draft}/versions/${ids.draftVersion}/confirm` && method === "POST") { expect(requestBody(init)).toEqual({ expected_row_version: 1 }); return json({ position_id: ids.position, context_version_id: ids.context, conversation_id: ids.conversation }, 200); }
    if (path === `/api/hr/positions/${ids.position}` && method === "GET") return json({ position_id: ids.position, source_kind: "manual", official_job_id: null, title: "高级结构工程师", department: "研发", locations: ["深圳"], official_status: null, internal_status: "active", source_version: null, row_version: 1, created_at: now, updated_at: now, conversation_count: 1, material_count: 0, artifact_count: 1, conversation_ids: [ids.conversation], material_attachment_ids: [], artifact_ids: [ids.artifact], artifact_attachment_ids: [ids.pdfAttachment] });
    if (path === `/api/hr/positions/${ids.position}/context` && method === "GET") return json({ current: rawContext(), drafts: [] });
    if (path === `/api/hr/positions/${ids.position}/context/versions` && method === "GET") return json({ items: [rawContext()] });
    if (path === `/api/hr/positions/${ids.position}/candidate-drafts` && method === "GET") return json({ items: drafts.map(rawDraft) });
    if (path === `/api/hr/positions/${ids.position}/candidate-drafts:batch` && method === "POST") {
      const body = requestBody(init); batchBodies.push(body);
      if (JSON.stringify(body) !== JSON.stringify({ attachment_ids: ids.attachments })) return json({ detail: "attachment binding invalid" }, 422);
      drafts = [parsedDraft(0, "ready"), parsedDraft(1, "ready"), parsedDraft(2, "failed")];
      return json({ batch_id: ids.batch, items: drafts.map(rawDraft) }, 202);
    }
    if (path === `/api/hr/candidate-drafts/${ids.drafts[2]}:retry` && method === "POST") { expect(requestBody(init)).toEqual({ expected_row_version: 3 }); drafts = drafts.map((item, index) => index === 2 ? parsedDraft(2, "ready") : item); return json(rawDraft(drafts[2])); }
    const confirm = /^\/api\/hr\/candidate-drafts\/([^/]+):confirm$/.exec(path);
    if (confirm && method === "POST") {
      const index = ids.drafts.indexOf(confirm[1]); const body = requestBody(init); confirmBodies.push(body);
      const expected = { expected_row_version: 2, context_version_id: ids.context, stable_name: `候选人${index === 0 ? "甲" : "乙"}`, confirmed_facts: parsedDraft(index, "ready").extractedFacts, merge_candidate_id: null };
      if (index < 0 || index > 1 || JSON.stringify(body) !== JSON.stringify(expected)) return json({ detail: "candidate binding invalid" }, 422);
      confirmed = [...new Set([...confirmed, index])]; drafts = drafts.map((item) => item.draftId === ids.drafts[index] ? parsedDraft(index, "confirmed") : item);
      return json({ candidate: rawCandidate(index), document: rawDocument(index), position_candidate: rawRelation(index) }, 201);
    }
    if (path === `/api/hr/positions/${ids.position}/candidates` && method === "GET") return json({ items: confirmed.map(rawRelation) });
    const candidate = /^\/api\/hr\/candidates\/([^/]+)$/.exec(path);
    if (candidate && method === "GET") { const index = ids.candidates.indexOf(candidate[1]); expect(index).toBeGreaterThanOrEqual(0); return json(rawCandidate(index)); }
    const documents = /^\/api\/hr\/candidates\/([^/]+)\/documents$/.exec(path);
    if (documents && method === "GET") { const index = ids.candidates.indexOf(documents[1]); expect(index).toBeGreaterThanOrEqual(0); return json({ items: [rawDocument(index)] }); }
    const analysis = /^\/api\/hr\/position-candidates\/([^/]+)\/analyses$/.exec(path);
    if (analysis && method === "GET") { const index = ids.relations.indexOf(analysis[1]); expect(index).toBeGreaterThanOrEqual(0); return json({ items: (analyses.get(index) ?? []).map(rawAnalysis) }); }
    const feedback = /^\/api\/hr\/position-candidates\/([^/]+)\/feedback$/.exec(path);
    if (feedback && method === "GET") { expect(ids.relations).toContain(feedback[1]); return json({ items: [] }); }
    if (path === `/api/hr/positions/${ids.position}/tasks?status=active` && method === "GET") return json({ items: [] });
    if (path === `/api/hr/positions/${ids.position}/tasks` && method === "POST") {
      const body = requestBody(init); taskBodies.push(body); const candidateIndex = ids.candidates.indexOf(String(body.candidate_id)); const kind = String(body.task_kind) as HrTaskKind;
      const expected = { task_kind: kind, context_version_id: ids.context, candidate_id: ids.candidates[candidateIndex], position_candidate_id: ids.relations[candidateIndex], material_ids: [], conversation_id: ids.conversation };
      if (candidateIndex < 0 || !["candidate_match", "candidate_interview_plan"].includes(kind) || JSON.stringify(body) !== JSON.stringify(expected) || (kind === "candidate_interview_plan" && candidateIndex !== 0)) return json({ detail: "HR position task conflict" }, 409);
      const taskIndex = kind === "candidate_interview_plan" ? 2 : candidateIndex;
      analyses.set(candidateIndex, kind === "candidate_interview_plan" ? [match(0), interview] : [match(candidateIndex)]);
      messages = [...messages, { message_id: `message-task-${taskIndex}`, conversation_id: ids.conversation,
        seq: messages.length + 1, role: "assistant", content: kind === "candidate_interview_plan" ? "候选人甲专属面试题已完成：请说明一次挤出系统量产良率提升过程。" : `${candidateIndex === 0 ? "候选人甲" : "候选人乙"}匹配分析已完成：${candidateIndex === 0 ? "负责挤出系统量产" : "负责精密机械设计"}`,
        turn_id: ids.turns[taskIndex], delivery_status: "completed", created_at: now, completed_at: now, input_attachments: [], output_attachments: [], active_attachment_ids: [] }];
      currentTurn = taskIndex < 2 ? { turn_id: ids.turns[taskIndex], conversation_id: ids.conversation, user_message_id: "message-user", assistant_message_id: `message-task-${taskIndex}`, retry_of_turn_id: null, status: "running", created_at: now, updated_at: now } : null;
      queueMicrotask(() => streamResolvers.shift()?.());
      return json({
        ...rawTask(kind, taskIndex),
        status: taskIndex === 0 ? "running" : "completed",
      }, 202);
    }
    if (path === `/api/hr/positions/${ids.position}/tasks/${ids.turns[0]}` && method === "GET") {
      return json(rawTask("candidate_match", 0));
    }
    if (path === `/api/hr/positions/${ids.position}/resources` && method === "GET") return json({ materials: [], artifacts: [{ artifact_id: ids.artifact, artifact_version_id: ids.artifactVersion, attachment_id: ids.pdfAttachment, artifact_version: 1, filename: "高级结构工程师-候选人甲-面试题-v1.pdf", media_type: "application/pdf", state: "ready", size_bytes: 128, created_at: now, source_conversation_id: ids.conversation, source_turn_id: ids.turns[2], preview_available: true, download_available: true }] });
    if (path === `/api/hr/positions/${ids.position}/resources/${ids.pdfAttachment}/ticket` && method === "POST") {
      expect(requestBody(init)).toEqual({ purpose: "download" }); const requestId = new Headers(init?.headers).get("Idempotency-Key"); expect(requestId).toMatch(/^[0-9a-f-]{36}$/i); ticketIds.push(requestId!); const token = (ticketIds.length === 1 ? "a" : "b").repeat(32);
      return json({ content_path: `/api/v1/attachments/content/${token}`, expires_at: retainedUntil }, 201);
    }
    throw new Error(`unexpected request: ${method} ${path}`);
  }));
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(window, "open").mockImplementation(() => { const target = { close: vi.fn(), replace: vi.fn() }; opened.push(target); return { opener: null, close: target.close, location: { replace: target.replace } } as unknown as Window; });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  streamResolvers.splice(0).forEach((resolve) => resolve());
  await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});

it("completes the public recruiting loop without Panorama and opens fresh interview PDF tickets", async () => {
  const pushed = vi.spyOn(window.history, "pushState");
  await act(async () => root.render(<RoutedWorkspace />));
  await waitFor(() => expect(container.textContent).toContain("岗位方案已生成。"));
  const chatHost = container.querySelector<HTMLElement>('.agent-use-workspace[data-agent-id="hr-bot"]');
  expect(chatHost).not.toBeNull(); expect(container.textContent).toContain("想招聘一名结构工程师"); expect(container.textContent).toContain("请补充岗位地点和量产范围。");

  await click(container, "确认并加入岗位库");
  await waitFor(() => expect(window.location.pathname).toBe(`/hr/positions/${ids.position}/conversations/${ids.conversation}`));
  expect(pushed).toHaveBeenCalledWith({}, "", `/hr/positions/${ids.position}/conversations/${ids.conversation}`);
  await waitFor(() => expect(container.textContent).toContain("已加入岗位库"));
  expect(container.querySelector('.agent-use-workspace[data-agent-id="hr-bot"]')).toBe(chatHost);

  await click(container, "岗位资料"); await click(container, "候选人");
  const input = container.querySelector<HTMLInputElement>('section[aria-label="批量简历导入"] input[type="file"]');
  expect(input).not.toBeNull(); const files = filenames.map((name) => new File([name], name, { type: "application/pdf" }));
  Object.defineProperty(input, "files", { configurable: true, value: files });
  await act(async () => input!.dispatchEvent(new Event("change", { bubbles: true })));
  await waitFor(() => expect(findButton(container, "开始解析 3 份简历").disabled).toBe(false));
  await click(container, "开始解析 3 份简历");
  expect(batchBodies).toEqual([{ attachment_ids: ids.attachments }]);
  expect(container.textContent).toContain("候选人甲"); expect(container.textContent).toContain("候选人乙");
  expect(container.textContent).toContain("解析失败"); expect(container.textContent).toContain("parser_response_invalid");
  await click(container, "重试解析"); expect(container.textContent).toContain("候选人丙");

  for (const name of ["候选人甲", "候选人乙"]) { await click(container, `审阅${name}`); await click(container, "确认候选人"); }
  expect(confirmBodies).toHaveLength(2); expect(confirmBodies.map((body) => body.context_version_id)).toEqual([ids.context, ids.context]);
  expect(container.textContent).toContain("2 位已确认");

  await click(container, "查看候选人甲");
  vi.useFakeTimers();
  await act(async () => {
    findButton(container, "生成匹配分析").click();
    await Promise.resolve();
    await Promise.resolve();
  });
  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  vi.useRealTimers();
  await waitFor(() => expect(container.textContent).toContain("负责挤出系统量产"));
  await waitFor(() => expect(container.textContent).toContain("候选人甲匹配分析已完成"));
  expect(container.textContent).toContain("量产良率经验待验证"); expect(container.textContent).toContain(ids.documents[0]);

  await click(container, "关闭详情"); await click(container, "查看候选人乙"); await click(container, "生成匹配分析");
  await waitFor(() => expect(container.textContent).toContain("负责精密机械设计"));
  await waitFor(() => expect(container.textContent).toContain("候选人乙匹配分析已完成")); expect(container.textContent).toContain(ids.documents[1]);

  await click(container, "关闭详情"); await click(container, "查看候选人甲"); await click(container, "生成专属面试题");
  await waitFor(() => expect(container.textContent).toContain("候选人甲专属面试题已完成"));
  expect(container.textContent).toContain("请说明一次挤出系统量产良率提升过程。"); expect(container.textContent).toContain("给出基线、措施和量化结果");
  expect(taskBodies).toEqual([
    { task_kind: "candidate_match", context_version_id: ids.context, candidate_id: ids.candidates[0], position_candidate_id: ids.relations[0], material_ids: [], conversation_id: ids.conversation },
    { task_kind: "candidate_match", context_version_id: ids.context, candidate_id: ids.candidates[1], position_candidate_id: ids.relations[1], material_ids: [], conversation_id: ids.conversation },
    { task_kind: "candidate_interview_plan", context_version_id: ids.context, candidate_id: ids.candidates[0], position_candidate_id: ids.relations[0], material_ids: [], conversation_id: ids.conversation },
  ]);
  const wrongBinding = { contextVersionId: ids.context, candidate: { candidateId: ids.candidates[0], positionCandidateId: ids.relations[1] }, materialIds: [], conversationId: ids.conversation };
  await expect(createHrR12Api(account.csrf_token).startTask(
    ids.position, "candidate_match", crypto.randomUUID(), wrongBinding,
  )).rejects.toMatchObject({ status: 409 });
  expect(taskBodies[taskBodies.length - 1]).toEqual({ task_kind: "candidate_match", context_version_id: ids.context,
    candidate_id: ids.candidates[0], position_candidate_id: ids.relations[1], material_ids: [], conversation_id: ids.conversation });
  expect(container.querySelector('article[aria-label="候选人匹配分析 v1"]')).not.toBeNull();
  expect(container.querySelector('article[aria-label="候选人专属面试题 v2"]')).not.toBeNull();
  expect(container.querySelector("pre")).toBeNull(); expect(container.querySelector("code")).toBeNull();
  expect(container.textContent).not.toContain('"verification_goal"'); expect(container.textContent).not.toContain('"resume_fact"');

  const download = container.querySelector<HTMLButtonElement>('button[aria-label="下载面试题 PDF"]'); expect(download).not.toBeNull();
  await act(async () => download!.click()); await waitFor(() => expect(opened).toHaveLength(1));
  await act(async () => download!.click()); await waitFor(() => expect(opened).toHaveLength(2));
  expect(ticketIds).toHaveLength(2); expect(ticketIds[0]).not.toBe(ticketIds[1]);
  expect(opened[0].replace).toHaveBeenCalledWith(`/api/v1/attachments/content/${"a".repeat(32)}`);
  expect(opened[1].replace).toHaveBeenCalledWith(`/api/v1/attachments/content/${"b".repeat(32)}`);
});
