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
  await act(async () => root.render(<TurnCard turn={turn} />));

  const link = [...container.querySelectorAll("a")].find((item) => item.textContent === "进入修复闭环");

  expect(link?.getAttribute("href")).toContain("/review?turn_key=fae%3Aturn-1");
});
