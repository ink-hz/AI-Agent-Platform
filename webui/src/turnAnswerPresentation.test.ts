import { describe, expect, it } from "vitest";

import { turnAnswerPresentation } from "./turnAnswerPresentation";


describe("turn answer presentation", () => {
  it("keeps a non-empty answer authoritative", () => {
    expect(turnAnswerPresentation({
      answer: "完成",
      outcome: "failed",
      trace_key: "trace-1",
      details: { error_class: "provider_unavailable" },
    })).toEqual({ kind: "answer", content: "完成" });
  });

  it("labels an explicit empty failed turn with an allowlisted classification", () => {
    expect(turnAnswerPresentation({
      answer: "",
      outcome: "failed",
      trace_key: "trace-1",
      details: { error_class: "provider_unavailable", provider_payload: "private" },
    })).toEqual({
      kind: "failed",
      label: "本轮执行失败",
      classification: "服务暂时不可用",
    });
  });

  it("does not expose unknown failure detail values", () => {
    expect(turnAnswerPresentation({
      answer: "",
      outcome: "error",
      trace_key: "trace-2",
      details: { error_class: "secret-provider-payload" },
    })).toEqual({ kind: "failed", label: "本轮执行失败", classification: null });
  });

  it("preserves the legacy missing-answer state without failure evidence", () => {
    expect(turnAnswerPresentation({
      answer: "",
      outcome: null,
      trace_key: null,
      details: {},
    })).toEqual({ kind: "missing", label: "未记录 Agent 回答" });
  });
});
