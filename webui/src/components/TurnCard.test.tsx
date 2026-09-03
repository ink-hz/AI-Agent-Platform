/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import type { TurnDetail } from "../types";
import { TurnCard } from "./TurnCard";


const turn: TurnDetail = {
  turn_key: "fae:turn-1", session_key: "fae:session-1", agent_id: "ai-fae-agent",
  source_kind: "fae", turn_index: 1, question: "问题", answer: "旧答案",
  created_at: "2026-08-03T00:00:00Z", question_at: null, answer_at: null,
  question_time_status: "unavailable", answer_time_status: "unavailable",
  trace_key: null, outcome: "resolved", fallback_used: false, duration_ms: 1,
  sources: [], evidence: [], evidence_availability: "available",
  feedback: [{ feedback_key: "fae:feedback-1", sentiment: "negative", raw_rating: "bad", reason_code: null, comment: "错误", created_at: "2026-08-03T00:00:00Z", details: {} }],
  reviews: [], improvements: [], input_attachments: [], output_attachments: [],
  details: {}, sender_name: null,
  sender_department: null, sender_identity_status: "unavailable",
};


let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});


it("negative feedback turn links to review inbox", async () => {
  await act(async () => root.render(<TurnCard turn={turn} closureSummary={{
    turn_key: turn.turn_key,
    issue_id: "issue-1",
    status: "awaiting_replay",
    missing_gates: ["replay"],
    latest_valid_replay_id: null,
  }} />));

  const link = [...container.querySelectorAll("a")].find((item) => item.textContent === "查看修复闭环");

  expect(link?.getAttribute("href")).toBe("/admin/review?agent_id=ai-fae-agent&turn_key=fae%3Aturn-1");
  expect(container.textContent).toContain("等待复跑");
  expect(container.textContent).toContain("缺少：真实复跑");
});


it("uses an explicit governance link for an ordinary real Turn", async () => {
  await act(async () => root.render(<TurnCard
    turn={{ ...turn, feedback: [] }}
    governanceHref="/fae/manage/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1"
  />));

  const link = container.querySelector<HTMLAnchorElement>(".review-entry a");
  expect(link?.textContent).toBe("创建或查看问题");
  expect(link?.getAttribute("href")).toBe(
    "/fae/manage/issues?session_key=fae%3Asession-1&turn_key=fae%3Aturn-1",
  );
  expect(container.textContent).toContain("尚未纳管");
});


it("places input and output attachments immediately after their messages", async () => {
  const summary = {
    attachment_id: "attachment-1", display_name: "附件.pdf", mime_type: "application/pdf",
    size_bytes: 10, received_or_generated_at: "2026-08-03T00:00:00Z",
    archive_status: "pending" as const, delivery_status: "delivered" as const,
    expires_at: "2027-08-03T00:00:00Z",
  };
  await act(async () => root.render(<TurnCard turn={{
    ...turn,
    input_attachments: [{ ...summary, direction: "user_input" }],
    output_attachments: [{ ...summary, attachment_id: "attachment-2", direction: "agent_output" }],
  }} />));

  const children = [...container.querySelector(".turn-card")!.children];
  const question = children.findIndex((element) => element.classList.contains("question-block"));
  const answer = children.findIndex((element) => element.classList.contains("answer-block"));
  expect(children[question + 1].getAttribute("aria-label")).toBe("用户输入附件");
  expect(children[answer + 1].getAttribute("aria-label")).toBe("Agent 输出附件");
});


it("adds no attachment wrapper when the turn has no attachments", async () => {
  await act(async () => root.render(<TurnCard turn={turn} />));
  expect(container.querySelector(".attachment-list")).toBeNull();
});


it("renders projected signal summaries with explicit restricted-detail notices", async () => {
  await act(async () => root.render(<TurnCard turn={{
    ...turn,
    feedback: [],
    reviews: [],
    feedback_availability: "restricted",
    review_availability: "restricted",
    feedback_summary: { negative: 2, positive: 1 },
    review_status_summary: { pending: 1 },
  }} />));

  expect(container.textContent).toContain("负向反馈 × 2");
  expect(container.textContent).toContain("正向反馈 × 1");
  expect(container.textContent).toContain("复审状态 · pending × 1");
  expect(container.textContent).toContain("反馈详情：受限");
  expect(container.textContent).toContain("复审详情：受限");
  expect(container.textContent).not.toContain("尚未纳管");
});
