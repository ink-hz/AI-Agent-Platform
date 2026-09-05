import { describe, expect, it, vi } from "vitest";

import { HrR12ApiError, createHrR12Api } from "./hrR12Api";


const POSITION_ID = "00000000-0000-4000-8000-000000000001";
const REQUEST_ID = "00000000-0000-4000-8000-000000000002";
const ATTACHMENT_ID = "00000000-0000-4000-8000-000000000003";
const CONTEXT_ID = "00000000-0000-4000-8000-000000000004";
const DRAFT_ID = "00000000-0000-4000-8000-000000000005";
const POSITION_CANDIDATE_ID = "00000000-0000-4000-8000-000000000006";
const CANDIDATE_ID = "00000000-0000-4000-8000-000000000007";
const CONVERSATION_ID = "00000000-0000-4000-8000-000000000008";
const TURN_ID = "00000000-0000-4000-8000-000000000009";
const ARTIFACT_VERSION_ID = "00000000-0000-4000-8000-00000000000a";


describe("R1.2 HR API", () => {
  it("uses caller request ids and preserves abort signals for mutations", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ materials: [], artifacts: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ content_path: `/api/v1/attachments/content/${"a".repeat(32)}`, expires_at: "2026-09-04T00:05:00Z" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await createHrR12Api("csrf").resources(POSITION_ID, controller.signal);
    await createHrR12Api("csrf").downloadResource(POSITION_ID, ATTACHMENT_ID, REQUEST_ID, "download", controller.signal);

    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
    const init = fetchMock.mock.calls[1]?.[1];
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(REQUEST_ID);
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf");
    expect(init?.signal).toBe(controller.signal);
  });

  it("issues candidate-document tickets through the exact private path", async () => {
    const contentPath = `/api/v1/attachments/content/${"b".repeat(32)}`;
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      content_path: contentPath,
      expires_at: "2026-09-04T00:05:00Z",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await expect(createHrR12Api("csrf").downloadCandidateDocument(
      DRAFT_ID, REQUEST_ID, "preview", controller.signal,
    )).resolves.toEqual({ contentPath, expiresAt: "2026-09-04T00:05:00Z" });

    expect(fetchMock.mock.calls[0]?.[0]).toContain(
      `/api/hr/candidate-documents/${DRAFT_ID}/ticket`,
    );
    const init = fetchMock.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(REQUEST_ID);
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf");
    expect(init?.signal).toBe(controller.signal);
    expect(JSON.parse(String(init?.body))).toEqual({ purpose: "preview" });
  });

  it("keeps actionable HTTP statuses for UI recovery", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "baseline changed" }), { status: 409 })));

    await expect(createHrR12Api("csrf").resources(POSITION_ID)).rejects.toBeInstanceOf(HrR12ApiError);
    await expect(createHrR12Api("csrf").resources(POSITION_ID)).rejects.toMatchObject({ status: 409 });
  });

  it("normalizes exact material metadata without accepting a storage locator", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ materials: [{
      attachment_id: ATTACHMENT_ID, filename: "岗位说明.pdf", media_type: "application/pdf", state: "ready",
      size_bytes: 2, created_at: "2026-09-04T00:00:00Z", source_conversation_id: null,
      source_turn_id: null, preview_available: true, download_available: true,
    }], artifacts: [] }), { status: 200 })));

    await expect(createHrR12Api("csrf").resources(POSITION_ID)).resolves.toMatchObject({
      materials: [{ attachmentId: ATTACHMENT_ID, filename: "岗位说明.pdf" }],
    });
  });

  it("parses strict candidate match and interview-plan result unions with artifact provenance", async () => {
    const base = {
      analysis_version_id: REQUEST_ID, position_candidate_id: POSITION_CANDIDATE_ID,
      position_id: POSITION_ID, candidate_id: CANDIDATE_ID, context_version_id: CONTEXT_ID,
      version_number: 1, document_ids: [ATTACHMENT_ID], feedback_ids: [],
      evidence: [], unknowns: [], conflicts: [], verification_questions: [],
      agent_version: "hr-bot", model_version: "model-v1", created_at: "2026-09-04T00:00:00Z",
    };
    const matchResult = {
      summary: "总体匹配", dimensions: { technical: "strong" },
      evidence: [{ resume_fact: "负责挤出系统" }], gaps: ["海外交付待补充"],
      risks: ["团队规模不明确"], unknowns: ["量产良率经验待验证"],
      verification_questions: ["请说明量产良率。"],
    };
    const interviewResult = { title: "结构工程师面试题", questions: [{
      verification_goal: "验证量产经验", candidate_reason: "简历提及量产",
      question: "请说明量产挑战。", follow_ups: ["良率如何？"],
      strong_evidence: ["给出量化指标"], risk_signals: ["无法说明本人贡献"],
    }] };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ ...base, analysis_kind: "match", result: matchResult, source_artifact_version_id: null }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ ...base, analysis_kind: "candidate_interview_plan", result: interviewResult, source_artifact_version_id: ARTIFACT_VERSION_ID }] }), { status: 200 })));

    const api = createHrR12Api("csrf");
    await expect(api.candidateAnalyses(POSITION_CANDIDATE_ID)).resolves.toMatchObject([{
      analysisKind: "match", result: matchResult, sourceArtifactVersionId: null,
    }]);
    await expect(api.candidateAnalyses(POSITION_CANDIDATE_ID)).resolves.toMatchObject([{
      analysisKind: "candidate_interview_plan", result: interviewResult,
      sourceArtifactVersionId: ARTIFACT_VERSION_ID,
    }]);
  });

  it("rejects candidate result keys and nested question shapes outside the strict union", async () => {
    const base = {
      analysis_version_id: REQUEST_ID, position_candidate_id: POSITION_CANDIDATE_ID,
      position_id: POSITION_ID, candidate_id: CANDIDATE_ID, context_version_id: CONTEXT_ID,
      version_number: 1, document_ids: [ATTACHMENT_ID], feedback_ids: [], evidence: [],
      unknowns: [], conflicts: [], verification_questions: [], agent_version: "hr-bot",
      model_version: "model-v1", created_at: "2026-09-04T00:00:00Z",
      analysis_kind: "match", source_artifact_version_id: null,
    };
    const malformed = [
      { ...base, result: { summary: "匹配", dimensions: {}, evidence: [], gaps: [], risks: [], unknowns: [], verification_questions: [], locator: "secret" } },
      { ...base, analysis_kind: "candidate_interview_plan", source_artifact_version_id: ARTIFACT_VERSION_ID, result: { title: "面试题", questions: [{ verification_goal: "目标", candidate_reason: "原因", question: "问题", follow_ups: [], strong_evidence: [], risk_signals: [], extra: true }] } },
    ];
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ items: [malformed.shift()] }), { status: 200 }))));
    const api = createHrR12Api("csrf");
    await expect(api.candidateAnalyses(POSITION_CANDIDATE_ID)).rejects.toThrow("analysis response invalid");
    await expect(api.candidateAnalyses(POSITION_CANDIDATE_ID)).rejects.toThrow("analysis response invalid");
  });

  it("keeps existing comparison results readable through the shared analysis parser", async () => {
    const comparisonResult = {
      candidates: [{ position_candidate_id: POSITION_CANDIDATE_ID, candidate_id: CANDIDATE_ID, summary: "匹配", evidence_coverage: 2, unknown_count: 1 }],
      ranking: null, comparison_basis: "same_position_context",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      analysis_version_id: REQUEST_ID, position_candidate_id: POSITION_CANDIDATE_ID,
      position_id: POSITION_ID, candidate_id: CANDIDATE_ID, context_version_id: CONTEXT_ID,
      version_number: 1, analysis_kind: "comparison", document_ids: [ATTACHMENT_ID],
      feedback_ids: [], result: comparisonResult, evidence: [], unknowns: [], conflicts: [],
      verification_questions: [], agent_version: "hr-r12", model_version: "platform",
      created_at: "2026-09-04T00:00:00Z",
    }), { status: 200 })));

    await expect(createHrR12Api("csrf").compareCandidates(
      POSITION_ID, [POSITION_CANDIDATE_ID], CONTEXT_ID, REQUEST_ID,
    )).resolves.toMatchObject({
      analysisKind: "comparison", result: comparisonResult, sourceArtifactVersionId: null,
    });
  });

  it("normalizes an omitted interview artifact version to the missing-PDF state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{
      analysis_version_id: REQUEST_ID, position_candidate_id: POSITION_CANDIDATE_ID,
      position_id: POSITION_ID, candidate_id: CANDIDATE_ID, context_version_id: CONTEXT_ID,
      version_number: 1, analysis_kind: "candidate_interview_plan", document_ids: [ATTACHMENT_ID],
      feedback_ids: [], result: { title: "面试题", questions: [{
        verification_goal: "目标", candidate_reason: "原因", question: "问题",
        follow_ups: [], strong_evidence: [], risk_signals: [],
      }] }, evidence: [], unknowns: [], conflicts: [], verification_questions: [],
      agent_version: "hr-bot", model_version: "model-v1", created_at: "2026-09-04T00:00:00Z",
    }] }), { status: 200 })));

    await expect(createHrR12Api("csrf").candidateAnalyses(POSITION_CANDIDATE_ID)).resolves.toMatchObject([{
      analysisKind: "candidate_interview_plan", sourceArtifactVersionId: null,
    }]);
  });

  it("preserves exact artifact-version identity separately from its downloadable attachment", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ materials: [], artifacts: [{
      artifact_id: REQUEST_ID, artifact_version_id: ARTIFACT_VERSION_ID,
      attachment_id: ATTACHMENT_ID, artifact_version: 3, filename: "面试题.pdf",
      media_type: "application/pdf", state: "ready", size_bytes: 1024,
      created_at: "2026-09-04T00:00:00Z", source_conversation_id: null,
      source_turn_id: null, preview_available: true, download_available: true,
    }] }), { status: 200 })));

    await expect(createHrR12Api("csrf").resources(POSITION_ID)).resolves.toMatchObject({ artifacts: [{
      artifactVersionId: ARTIFACT_VERSION_ID, attachmentId: ATTACHMENT_ID,
    }] });
  });

  it("normalizes the frozen position context contract and confirms against both baselines", async () => {
    const raw = {
      context_version_id: CONTEXT_ID, position_id: POSITION_ID, version_number: 3,
      state: "draft", modules: { profile: { summary: "技术负责人" } }, summary: "新画像",
      official_version_id: null, base_context_version_id: null, source_conversation_id: null,
      source_turn_id: null, source_artifact_version_id: null, source_material_attachment_ids: [],
      agent_id: null, model_version: null, created_by: CANDIDATE_ID, confirmed_by: null,
      created_at: "2026-09-04T00:00:00Z", confirmed_at: null, row_version: 2,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ current: null, drafts: [raw] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [raw] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...raw, state: "confirmed" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const api = createHrR12Api("csrf");
    await expect(api.context(POSITION_ID)).resolves.toMatchObject({
      current: null, drafts: [{ contextVersionId: CONTEXT_ID, displayVersion: 3, status: "draft", rowVersion: 2 }],
      history: [{ contextVersionId: CONTEXT_ID }],
    });
    await api.confirmContext(POSITION_ID, CONTEXT_ID, null, ["profile"], 2, REQUEST_ID);

    expect(fetchMock.mock.calls[2]?.[0]).toContain(`/context/drafts/${CONTEXT_ID}/confirm`);
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({
      expected_current_context_version_id: null,
      expected_draft_row_version: 2,
      module_names: ["profile"],
    });
  });

  it("uses frozen candidate paths and normalizes list envelopes", async () => {
    const rawDraft = {
      draft_id: DRAFT_ID, position_id: POSITION_ID, attachment_id: ATTACHMENT_ID,
      batch_request_id: REQUEST_ID, state: "ready", extracted_facts: { stable_name: "候选人甲" },
      identity_candidates: [], error_code: null, row_version: 2,
      created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:00Z",
    };
    const rawRelation = {
      position_candidate_id: POSITION_CANDIDATE_ID, position_id: POSITION_ID,
      candidate_id: CANDIDATE_ID, context_version_id: CONTEXT_ID, source_draft_id: DRAFT_ID,
      status: "active", row_version: 1, created_at: "2026-09-04T00:00:00Z",
      updated_at: "2026-09-04T00:00:00Z",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [rawDraft] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(rawDraft), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ candidate: { candidate_id: CANDIDATE_ID, stable_name: "候选人甲", facts: {}, created_at: rawDraft.created_at, updated_at: rawDraft.updated_at }, document: { document_id: ATTACHMENT_ID, candidate_id: CANDIDATE_ID, attachment_id: ATTACHMENT_ID, source_draft_id: DRAFT_ID, document_kind: "resume", version_number: 1, content_sha256: "a".repeat(64), status: "active", created_at: rawDraft.created_at }, position_candidate: rawRelation }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createHrR12Api("csrf");

    await expect(api.candidateDrafts(POSITION_ID)).resolves.toMatchObject([{ draftId: DRAFT_ID, state: "ready", rowVersion: 2 }]);
    await api.retryDraft(DRAFT_ID, 2, REQUEST_ID);
    await api.confirmDraft(DRAFT_ID, { expectedRowVersion: 2, contextVersionId: CONTEXT_ID, stableName: "候选人甲", confirmedFacts: {}, mergeCandidateId: null }, REQUEST_ID);

    expect(fetchMock.mock.calls[1]?.[0]).toContain(`/api/hr/candidate-drafts/${DRAFT_ID}:retry`);
    expect(fetchMock.mock.calls[2]?.[0]).toContain(`/api/hr/candidate-drafts/${DRAFT_ID}:confirm`);
  });

  it("exposes the complete frozen candidate workflow without generic comparison tasks", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ items: [] }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    const api = createHrR12Api("csrf");

    await api.positionCandidates(POSITION_ID);
    await api.candidateAnalyses(POSITION_CANDIDATE_ID);
    await api.candidateFeedback(POSITION_CANDIDATE_ID);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      expect.stringContaining(`/positions/${POSITION_ID}/candidates`),
      expect.stringContaining(`/position-candidates/${POSITION_CANDIDATE_ID}/analyses`),
      expect.stringContaining(`/position-candidates/${POSITION_CANDIDATE_ID}/feedback`),
    ]);
  });

  it("starts durable tasks with a position context envelope and paired candidate identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      task_id: REQUEST_ID, status: "accepted", task_kind: "candidate_match", error: "worker unavailable",
    }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    const task = await createHrR12Api("csrf").startTask(POSITION_ID, "candidate_match", REQUEST_ID, {
      contextVersionId: CONTEXT_ID,
      candidate: { candidateId: CANDIDATE_ID, positionCandidateId: POSITION_CANDIDATE_ID },
      materialIds: [],
      conversationId: CONVERSATION_ID,
    });

    expect(task.error).toBe("worker unavailable");

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      task_kind: "candidate_match",
      context_version_id: CONTEXT_ID,
      candidate_id: CANDIDATE_ID,
      position_candidate_id: POSITION_CANDIDATE_ID,
      material_ids: [],
      conversation_id: CONVERSATION_ID,
    });
  });

  it("keeps the conversation and turn created for a position task", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      task_id: REQUEST_ID, status: "accepted", task_kind: "jd", error: null,
      conversation_id: CONVERSATION_ID, turn_id: TURN_ID,
      position_candidate_id: null, candidate_id: null,
    }), { status: 202 })));

    const task = await createHrR12Api("csrf").startTask(
      POSITION_ID, "jd", REQUEST_ID, { materialIds: [] },
    );

    expect(task.conversationId).toBe(CONVERSATION_ID);
    expect(task.turnId).toBe(TURN_ID);
  });

  it("reads an authoritative terminal candidate task with its exact binding", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      task_id: REQUEST_ID, status: "failed", task_kind: "candidate_match", error: "model failed",
      position_candidate_id: POSITION_CANDIDATE_ID, candidate_id: CANDIDATE_ID,
    }), { status: 200 })));

    await expect(createHrR12Api("csrf").taskStatus(POSITION_ID, REQUEST_ID)).resolves.toMatchObject({
      taskId: REQUEST_ID, status: "failed", error: "model failed",
      positionCandidateId: POSITION_CANDIDATE_ID, candidateId: CANDIDATE_ID,
    });
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toContain(`/positions/${POSITION_ID}/tasks/${REQUEST_ID}`);
  });

  it("rejects malformed task envelopes before making a request", () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const unsafe = createHrR12Api("csrf").startTask as unknown as (...args: unknown[]) => unknown;

    expect(() => unsafe(POSITION_ID, "candidate_match", REQUEST_ID, { contextVersionId: CONTEXT_ID })).toThrow("candidate task envelope invalid");
    expect(() => unsafe(POSITION_ID, "jd", REQUEST_ID, { candidate: { candidateId: CANDIDATE_ID, positionCandidateId: POSITION_CANDIDATE_ID } })).toThrow("position task envelope invalid");
    expect(() => unsafe(POSITION_ID, "candidate_comparison", REQUEST_ID, {})).toThrow("task kind invalid");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
