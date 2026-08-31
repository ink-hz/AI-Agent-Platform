/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ReviewPage } from "./ReviewPage";


const issue = {
  issue: { id: "issue-1", agent_id: "ai-fae-agent", title: "型号事实错误", priority: "P1", failure_layer: "capability_evidence", secondary_layers: [], root_cause: "结构化事实压过直接证据", impact_scope: "330 系列", owner: "fae:alice", disposition: "actionable", row_version: 2 },
  progress: { issue_id: "issue-1", status: "awaiting_review", missing_gates: ["semantic_review"], replay_passed_turns: 1, replay_required_turns: 1, reopened: false },
  links: [{ id: "link-1", active: true, link_role: "primary", agent_id: "ai-fae-agent", source_turn_key: "fae:turn-1", source_feedback_keys: ["fae:feedback-1"], source_question: "330 系列支持被动双目吗", source_answer: "旧的错误答案" }],
  evidence: [],
  replays: [{ id: "replay-1", issue_link_id: "link-1", attempt_no: 1, execution_status: "succeeded", runtime_gate: "passed", runtime_failure_reason: "", answer: "最新复测答案：335 系列存在主动与被动双目融合。", sources: [{ title: "官方产品页" }], trace_id: "trace-1", actual_version: "release-1", actual_git_sha: "a".repeat(40), configured_model: "claude-opus-4-8", actual_model: "claude-opus-4-8", semantic_verdict: "pending", review_method: null, reviewer: null, review_reason: "", started_at: "2026-08-03T00:00:00Z", completed_at: "2026-08-03T00:01:00Z" }],
  events: [],
};

const inboxItem = {
  agent_id: "ai-fae-agent",
  turn_key: "fae:turn-2",
  question: "另一条相同根因的负反馈",
  answer: "旧回答",
  feedback_keys: ["fae:feedback-2"],
  first_feedback_at: "2026-08-03T00:00:00Z",
};


function response(body: unknown): Response {
  return { ok: true, json: vi.fn().mockResolvedValue(body) } as unknown as Response;
}


let container: HTMLDivElement;
let root: Root;
let writeAvailable: boolean;

beforeEach(() => {
  writeAvailable = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  window.history.replaceState({}, "", "/admin/review?issue=issue-1");
  vi.stubGlobal("fetch", vi.fn((path: string) => {
    if (path.startsWith("/api/review/overview?")) return Promise.resolve(response({ feedback_rows: 79, negative_rows: 51, negative_turns: 50, positive_rows: 28, statuses: { awaiting_review: 1 }, write_available: writeAvailable }));
    if (path.startsWith("/api/review/inbox?")) return Promise.resolve(response([inboxItem]));
    if (path.startsWith("/api/review/issues?")) return Promise.resolve(response([{ ...issue.issue, progress: issue.progress }]));
    return Promise.resolve(response(issue));
  }));
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});


it("shows the original and latest replay answer without force close", async () => {
  await act(async () => root.render(<ReviewPage />));
  await act(async () => await Promise.resolve());

  expect(container.textContent).toContain("旧的错误答案");
  expect(container.textContent).toContain("最新复测答案");
  expect(container.textContent).toContain("移动回答归属");
  expect(container.textContent).toContain("待语义复审");
  expect(container.textContent).not.toContain("强制关闭");
  expect([...container.querySelectorAll("button")].some((button) => /关闭事项/.test(button.textContent || ""))).toBe(false);
  const paths = vi.mocked(fetch).mock.calls.map(([path]) => String(path));
  expect(paths.filter((path) => path.includes("/api/review/")).every((path) => path.includes("agent_id=ai-fae-agent") || path.includes("/issues/issue-1"))).toBe(true);
});


it("preserves the legacy actor field, issue query state, and generic Review API paths", async () => {
  await act(async () => root.render(<ReviewPage />));
  await act(async () => await Promise.resolve());

  expect(container.querySelector('input[placeholder="codex / fae:zhangsan"]')).not.toBeNull();
  expect(container.textContent).toContain("型号事实错误");
  expect(new URLSearchParams(window.location.search).get("issue")).toBe("issue-1");
  expect(vi.mocked(fetch).mock.calls.map(([path]) => String(path)).every((path) => path.startsWith("/api/review/"))).toBe(true);
});


it("keeps generic Review selection in the stable agent-scoped query URL", async () => {
  window.history.replaceState({}, "", "/admin/review?agent_id=ai-fae-agent&turn_key=fae%3Aturn-2");
  await act(async () => root.render(<ReviewPage />));
  await act(async () => container.querySelector<HTMLButtonElement>(".review-issue-list button")!.click());

  expect(window.location.pathname).toBe("/admin/review");
  expect(window.location.search).toBe("?agent_id=ai-fae-agent&issue=issue-1");
  expect(container.textContent).toContain("旧的错误答案");
});


it("can attach an inbox answer to an existing canonical issue", async () => {
  window.history.replaceState({}, "", "/admin/review?turn_key=fae%3Aturn-2");

  await act(async () => root.render(<ReviewPage />));
  await act(async () => await Promise.resolve());

  expect(container.textContent).toContain("关联到已有事项");
  const select = container.querySelector('select[aria-label="已有事项"]');
  expect(select).not.toBeNull();
  expect(select?.textContent).toContain("型号事实错误");
});


it("keeps the chosen generic issue selected after linking from a turn query", async () => {
  window.history.replaceState({}, "", "/admin/review?turn_key=fae%3Aturn-2");
  sessionStorage.setItem("reviewActor", "codex");

  await act(async () => root.render(<ReviewPage />));
  const select = container.querySelector<HTMLSelectElement>('select[aria-label="已有事项"]')!;
  await act(async () => {
    select.value = "issue-1";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const link = [...container.querySelectorAll("button")].find((button) => button.textContent === "关联到已有事项")!;
  await act(async () => link.click());

  expect(new URLSearchParams(window.location.search).get("issue")).toBe("issue-1");
  expect(container.textContent).toContain("旧的错误答案");
  expect(container.textContent).not.toContain("选择左侧事项");
});


it("keeps review data visible and disables mutations in read-only mode", async () => {
  writeAvailable = false;

  await act(async () => root.render(<ReviewPage />));
  await act(async () => await Promise.resolve());

  expect(container.textContent).toContain("只读模式");
  expect(container.textContent).toContain("旧的错误答案");
  expect(container.textContent).toContain("最新复测答案");
  const save = [...container.querySelectorAll("button")].find((button) => button.textContent === "保存归因");
  const replay = [...container.querySelectorAll("button")].find((button) => button.textContent?.startsWith("复跑 "));
  expect(save?.disabled).toBe(true);
  expect(replay?.disabled).toBe(true);
  expect(container.textContent).not.toContain("语义通过");
});
