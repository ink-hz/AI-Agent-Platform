import type { TurnDetail } from "./types";


type TurnAnswerInput = Pick<TurnDetail, "answer" | "outcome" | "trace_key" | "details">;

export type TurnAnswerPresentation =
  | { kind: "answer"; content: string }
  | { kind: "failed"; label: "本轮执行失败"; classification: string | null }
  | { kind: "missing"; label: "未记录 Agent 回答" };

const FAILED_OUTCOMES = new Set([
  "failed",
  "error",
  "cancelled",
  "interrupted",
  "timed_out",
  "timeout",
]);

const PUBLIC_CLASSIFICATIONS: Record<string, string> = {
  timeout: "执行超时",
  timed_out: "执行超时",
  provider_unavailable: "服务暂时不可用",
  cancelled: "执行已取消",
  execution_error: "执行异常",
};

export function turnAnswerPresentation(turn: TurnAnswerInput): TurnAnswerPresentation {
  if (turn.answer.trim()) return { kind: "answer", content: turn.answer };

  const outcome = turn.outcome?.trim().toLowerCase() ?? "";
  const traceStatus = typeof turn.details.trace_status === "string"
    ? turn.details.trace_status.trim().toLowerCase()
    : "";
  if (FAILED_OUTCOMES.has(outcome) || (turn.trace_key !== null && traceStatus === "failed")) {
    const errorClass = typeof turn.details.error_class === "string"
      ? turn.details.error_class.trim().toLowerCase()
      : "";
    return {
      kind: "failed",
      label: "本轮执行失败",
      classification: PUBLIC_CLASSIFICATIONS[errorClass] ?? null,
    };
  }

  return { kind: "missing", label: "未记录 Agent 回答" };
}
