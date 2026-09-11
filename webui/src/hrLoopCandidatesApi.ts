import { platformPath } from "./auth";
import { HrLoopError, type ExactRef } from "./hrLoopApi";

export type CandidateItem = {
  item_id: string;
  batch_id: string;
  attachment_id: string;
  state:
    | "queued"
    | "parsing"
    | "profiling"
    | "awaiting_review"
    | "failed"
    | "confirmed";
  row_version: number;
  generation: number;
  work_id: string | null;
  result_ref: ExactRef | null;
  original_ref: ExactRef | null;
  text_ref: ExactRef | null;
  error_code: string | null;
  failed_stage: "parse" | "profile" | null;
  profile_body: string | null;
  parse_state: string;
  coverage_complete: boolean;
  coverage_notes: string[];
  unread_ranges: [number, number][];
  work_state: string | null;
};
export type CandidateBatch = {
  batch_id: string;
  position_id: string | null;
  items: CandidateItem[];
};
export type CandidateChoice = {
  candidate_id: string;
  available: boolean;
  display_name?: string;
  summary?: string;
};
export type CandidateConfirmation = {
  expected_row_version: number;
  result_ref: ExactRef;
  display_name: string;
  summary: string;
  decision:
    | { kind: "create" }
    | { kind: "link_existing"; candidate_id: string };
  reviewed_limitations: boolean;
};
export type CandidateReceipt = {
  item_id: string;
  candidate_id: string;
  document_id: string;
  state: "confirmed";
  row_version: number;
};
export type CandidateDocument = {
  document_id: string;
  attachment_id: string;
  source_ref: ExactRef;
  result_ref: ExactRef;
  reviewed_limitations: boolean;
  summary?: string;
};
export type CandidateView = {
  candidate_id: string;
  display_name: string;
  summary: string;
  position_ids: string[];
  documents: CandidateDocument[];
};
export type InterviewRecord = {
  record_id: string;
  candidate_id: string;
  material_ref: ExactRef;
  title: string;
  occurred_at: string | null;
  position_id: string | null;
  interview_plan_ref: ExactRef | null;
  authorship: "user_supplied" | "ai_generated";
  created_at: string;
};
export type InterviewRecordView = InterviewRecord & { text: string };
export type InterviewRecordInput = {
  material_ref: ExactRef;
  title: string;
  occurred_at: string | null;
  position_id: string | null;
  interview_plan_ref: ExactRef | null;
};
export function candidateError(error: unknown): string {
  if (error instanceof HrLoopError) {
    if (error.code === "processing_not_authorized")
      return "处理权限待开通，请由组织配置获准的材料处理服务。";
    if (error.code === "revision_conflict")
      return "内容已更新，请重新加载状态并核对后再提交。";
    if (error.code === "review_required")
      return "请先核对原件并确认解析与阅读限制。";
    return error.message;
  }
  return "操作暂未完成，请重试；已登记的材料可以从最近批次找回。";
}
export function createHrLoopCandidatesApi(csrf: string) {
  async function request<T>(
    path: string,
    body?: unknown,
    key?: string,
  ): Promise<T> {
    const response = await fetch(platformPath("/api/hr/agent" + path), {
      credentials: "include",
      cache: "no-store",
      method: body === undefined ? "GET" : "POST",
      headers: {
        Accept: "application/json",
        ...(body === undefined
          ? {}
          : {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrf,
              "Idempotency-Key": key!,
            }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new HrLoopError(
        response.status,
        error.code ?? "",
        error.details ?? {},
      );
    }
    return response.json() as Promise<T>;
  }
  const enc = encodeURIComponent;
  return {
    createBatch: (
      body: {
        attachment_ids: string[];
        position_id: string | null;
        text: string;
        budget_profile: string;
      },
      key: string,
    ) =>
      request<{ batch_id: string; item_ids: string[] }>(
        "/candidate-batches",
        body,
        key,
      ),
    batches: () =>
      request<{ items: { batch_id: string; created_at: string }[] }>(
        "/candidate-batches?limit=50",
      ),
    batch: (id: string) =>
      request<CandidateBatch>("/candidate-batches/" + enc(id)),
    item: (id: string) => request<CandidateItem>("/candidate-items/" + enc(id)),
    retry: (
      id: string,
      body: { expected_row_version: number; stage: "parse" | "profile" },
      key: string,
    ) =>
      request<{ item_id: string; state: "queued"; row_version: number }>(
        `/candidate-items/${enc(id)}/retry`,
        body,
        key,
      ),
    confirm: (id: string, body: CandidateConfirmation, key: string) =>
      request<CandidateReceipt>(
        `/candidate-items/${enc(id)}/confirm`,
        body,
        key,
      ),
    candidates: () =>
      request<{ items: CandidateChoice[] }>("/candidates?limit=100"),
    candidate: (id: string) => request<CandidateView>("/candidates/" + enc(id)),
    interviewRecords: (id: string) =>
      request<{ items: InterviewRecord[] }>(`/candidates/${enc(id)}/interview-records`),
    interviewRecord: (id: string, recordId: string) =>
      request<InterviewRecordView>(`/candidates/${enc(id)}/interview-records/${enc(recordId)}`),
    registerInterviewRecord: (id: string, body: InterviewRecordInput, key: string) =>
      request<InterviewRecord>(`/candidates/${enc(id)}/interview-records`, body, key),
  };
}
export type HrLoopCandidatesApi = ReturnType<typeof createHrLoopCandidatesApi>;
