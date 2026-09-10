import { platformPath } from "./auth";

export type ExactRef = {
  kind: string;
  id: string;
  revision: string;
  sha256: string;
};
export type WorkObject = { kind: string; id: string };
export type InputContent = {
  text: string;
  objects: WorkObject[];
  references: ExactRef[];
};
export type WorkInput = InputContent & {
  thread_id: string | null;
  budget_profile: string;
};
export type AppendInput = InputContent & {
  expected_input_revision: number;
  question_id: string | null;
};
export type BudgetAddition = {
  model_calls: number;
  total_tokens: number;
  active_seconds: number;
};
export type ExtendInput = {
  expected_budget_revision: number;
  addition: BudgetAddition;
  reason: string;
};
export type SavedResult = {
  ref: ExactRef;
  kind: string;
  title: string;
  body: string;
  objects: WorkObject[];
  changes: {
    change_id: string;
    action: string;
    target_item_id: string | null;
    text: string | null;
  }[];
  base_standard_ref: ExactRef | null;
  basis: {
    kind: string;
    ref: ExactRef | null;
    input_revision: number | null;
  }[];
};
export type WorkView = {
  work_id: string;
  thread_id: string;
  input_revision: number;
  state: string;
  phase: string;
  answer_state: string;
  result_refs: ExactRef[];
  pending_question_id: string | null;
  block_reason: string | null;
  budget: {
    revision: number;
    charged_calls: number;
    charged_tokens: number;
    active_seconds: number;
    usage_quality?: string;
    limits: {
      model_calls: number;
      total_tokens: number;
      active_seconds: number;
    };
  };
  checkpoint: {
    open_questions: string[];
    readings: {
      state: string;
      remaining_ranges: { start: number; end: number }[];
    }[];
  };
};
export type MaterialView = {
  attachment_id: string;
  state: string;
  parse_state: string;
  text_ref: ExactRef | null;
  original_ref: ExactRef | null;
  coverage_complete: boolean;
  coverage_notes?: string[];
  error_code?: string | null;
};
export type StandardView = {
  ref: ExactRef;
  position_id: string;
  items: { item_id: string; text: string }[];
  selected_change_ids: string[];
  confirmed_at: string;
};
export type ResourceItem = {
  ref: ExactRef;
  title: string;
  description: string;
  objects?: WorkObject[];
};
export type Page<T> = { items: T[]; next_cursor: string | null };
export type Message = {
  entry_id: string;
  seq: number;
  kind: string;
  body: string | null;
  options?: string[];
  visibility?: "available" | "restricted";
};
export type WorkEvent = {
  seq: number;
  type: string;
  message: string;
  error: { code: string } | null;
  tool_status: string | null;
};
export type Confirmation = {
  proposal_ref: ExactRef;
  selected_change_ids: string[];
  expected_standard_revision: string | null;
};

export function errorMessage(code: string, status = 0): string {
  if (status === 401) return "登录已失效，请重新登录后继续。";
  if (status === 403) return "当前账号或本次工作无法使用这些材料。";
  if (code === "revision_conflict")
    return "内容已在另一处更新。请重新阅读当前标准并调整提案后确认。";
  if (code === "personal_source_not_allowed")
    return "提案包含候选人范围来源，不能确认为通用岗位标准。";
  if (
    status === 404 ||
    code === "reference_unavailable" ||
    code === "dependency_revoked"
  )
    return "材料已不可用或访问权限发生变化，请重新选择。";
  if (status === 422) return "提交内容不完整或已失效，请检查后重试。";
  if (status === 413) return "文件或请求超过限制，请缩小后重试。";
  return "服务暂时不可用，请稍后重试。已保存的工作可以继续查看。";
}
export class HrLoopError extends Error {
  constructor(
    public status: number,
    public code: string,
    public details: Record<string, unknown>,
  ) {
    super(errorMessage(code, status));
  }
}
const enc = encodeURIComponent;
export function createHrLoopApi(csrf: string) {
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
              "Idempotency-Key": key ?? crypto.randomUUID(),
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
  return {
    configuration: () =>
      request<{
        budget_profile: string;
        budget_limits: WorkView["budget"]["limits"];
      }>("/configuration"),
    threads: (cursor?: string) =>
      request<Page<{ thread_id: string; title: string; updated_at: string }>>(
        "/threads" + (cursor ? "?cursor=" + enc(cursor) : ""),
      ),
    works: (thread: string, cursor?: string) =>
      request<Page<WorkView>>(
        "/threads/" +
          enc(thread) +
          "/works" +
          (cursor ? "?cursor=" + enc(cursor) : ""),
      ),
    submit: (input: WorkInput, key: string) =>
      request<WorkView>("/works", input, key),
    work: (id: string) => request<WorkView>("/works/" + enc(id)),
    input: (id: string) =>
      request<InputContent & { input_revision: number }>(
        "/works/" + enc(id) + "/input",
      ),
    append: (id: string, input: AppendInput, key: string) =>
      request<WorkView>("/works/" + enc(id) + "/inputs", input, key),
    messages: (id: string, after = 0) =>
      request<{ items: Message[]; next_after: number }>(
        "/works/" + enc(id) + "/messages?after=" + after + "&limit=200",
      ),
    events: (id: string, after = 0) =>
      request<{ items: WorkEvent[]; next_after: number }>(
        "/works/" + enc(id) + "/events?after=" + after + "&limit=200",
      ),
    cancel: (id: string, key: string) =>
      request<WorkView>(
        "/works/" + enc(id) + "/cancel",
        { reason: "user stopped" },
        key,
      ),
    extend: (id: string, body: ExtendInput, key: string) =>
      request<WorkView>("/works/" + enc(id) + "/budget-extensions", body, key),
    material: (id: string) => request<MaterialView>("/materials/" + enc(id)),
    parse: (id: string, key: string) =>
      request<{ parse_id: string; state: string }>(
        "/materials/" + enc(id) + "/parse",
        {},
        key,
      ),
    knowledge: () =>
      request<{ release_id: string; items: ResourceItem[] }>("/knowledge"),
    method: (ref: ExactRef) =>
      request<{ ref: ExactRef; text: string }>(
        `/knowledge/${enc(ref.id)}/revisions/${enc(ref.revision)}?sha256=${enc(ref.sha256)}`,
      ),
    results: (query: {
      thread?: string;
      position?: string;
      cursor?: string;
    }) => {
      const params = new URLSearchParams(
        query.position
          ? { object_kind: "position", object_id: query.position }
          : { thread_id: query.thread ?? "" },
      );
      if (query.cursor) params.set("cursor", query.cursor);
      return request<Page<ResourceItem>>("/results?" + params);
    },
    result: (ref: ExactRef) =>
      request<SavedResult>(
        `/results/${enc(ref.id)}/revisions/${enc(ref.revision)}`,
      ),
    link: (ref: ExactRef, objects: WorkObject[], key: string) =>
      request(
        `/results/${enc(ref.id)}/links`,
        { expected_result_revision: ref.revision, objects },
        key,
      ),
    standard: (position: string) =>
      request<StandardView>(`/positions/${enc(position)}/standards/current`),
    confirm: (position: string, body: Confirmation, key: string) =>
      request<StandardView>(
        `/positions/${enc(position)}/standards/confirm`,
        body,
        key,
      ),
    async download(ref: ExactRef) {
      const response = await fetch(
        platformPath(
          `/api/hr/agent/results/${enc(ref.id)}/revisions/${enc(ref.revision)}/file`,
        ),
        { credentials: "include", cache: "no-store" },
      );
      if (!response.ok) {
        const e = await response.json().catch(() => ({}));
        throw new HrLoopError(response.status, e.code ?? "", e.details ?? {});
      }
      return response.blob();
    },
  };
}
export type HrLoopApi = ReturnType<typeof createHrLoopApi>;

// Cursor readers also stop when the caller changes work/account.
export async function readStream<T>(
  fetchPage: (after: number) => Promise<{ items: T[]; next_after: number }>,
  after = 0,
  current = () => true,
) {
  const items: T[] = [];
  let next_after = after;
  while (current()) {
    const page = await fetchPage(next_after);
    if (!current()) break;
    items.push(...page.items);
    if (page.next_after <= next_after) break;
    next_after = page.next_after;
    if (page.items.length < 200) break;
  }
  return { items, next_after };
}
export async function readPages<T>(
  fetchPage: (cursor?: string) => Promise<Page<T>>,
  current = () => true,
) {
  const items: T[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await fetchPage(cursor);
    if (!current()) break;
    items.push(...page.items);
    cursor = page.next_cursor ?? undefined;
    if (cursor && seen.has(cursor)) throw new Error("Repeated page");
    if (cursor) seen.add(cursor);
  } while (cursor && current());
  return items;
}
