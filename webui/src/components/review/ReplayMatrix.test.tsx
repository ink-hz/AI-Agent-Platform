/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import type { IssueLink, ReplayRun } from "../../types";
import { ReplayMatrix, selectLatestValidReplay } from "./ReplayMatrix";


function replay(overrides: Partial<ReplayRun>): ReplayRun {
  return {
    id: `replay-${overrides.attempt_no}`,
    issue_link_id: "link-1",
    attempt_no: 1,
    actual_version: "release-1",
    actual_git_sha: "a".repeat(40),
    configured_model: "claude-opus-4-8",
    actual_model: "claude-opus-4-8",
    answer: "answer",
    sources: [],
    trace_id: "trace-1",
    execution_status: "succeeded",
    runtime_gate: "passed",
    runtime_failure_reason: "",
    semantic_verdict: "pending",
    review_method: null,
    reviewer: null,
    review_reason: "",
    started_at: "2026-08-11T04:00:00Z",
    completed_at: "2026-08-11T04:01:00Z",
    ...overrides,
  };
}


const attempts = [
  replay({ attempt_no: 3, completed_at: "2026-08-11T04:03:00Z", execution_status: "failed", runtime_gate: "failed", answer: "late failure" }),
  replay({ attempt_no: 2, completed_at: "2026-08-11T04:02:00Z", answer: "new valid" }),
  replay({ attempt_no: 1, completed_at: "2026-08-11T04:01:00Z", answer: "old valid" }),
];


it("selects the latest valid replay instead of a later failed attempt", () => {
  expect(selectLatestValidReplay(attempts)?.answer).toBe("new valid");
});


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


it("shows the latest valid answer and keeps the late failure in history", async () => {
  const links = [{ id: "link-1", active: true, link_role: "primary", source_turn_key: "fae:turn-1", source_question: "question", source_answer: "old" }] as IssueLink[];

  await act(async () => root.render(<ReplayMatrix links={links} replays={attempts} />));

  expect(container.querySelector(".replay-latest")?.textContent).toContain("new valid");
  expect(container.querySelector(".replay-latest")?.textContent).toContain("运行通过，待语义复审");
  expect(container.querySelector(".replay-latest")?.textContent).not.toContain("已修复");
  expect(container.querySelector(".replay-history")?.textContent).toContain("late failure");
  expect(container.querySelector(".replay-history")?.textContent).toContain("old valid");
});
