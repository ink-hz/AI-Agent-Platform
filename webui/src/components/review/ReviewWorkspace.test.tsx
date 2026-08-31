/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FeedbackIssueDetail, FeedbackIssueSummary, ReviewInboxItem, ReviewOverview } from "../../types";
import { ReviewWorkspace, type ReviewApi } from "./ReviewWorkspace";


function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}

function issue(id: string, title: string, rootCause: string): FeedbackIssueDetail {
  return {
    issue: {
      id, agent_id: "ai-fae-agent", origin_turn_key: null, title, priority: "P2",
      failure_layer: "synthesis", secondary_layers: [], root_cause: rootCause,
      impact_scope: "FAE", owner: "fae:alice", disposition: "actionable", row_version: 1,
    },
    progress: {
      issue_id: id, status: "fixing", missing_gates: ["fix_ready"],
      replay_passed_turns: 0, replay_required_turns: 1, reopened: false,
    },
    links: [], evidence: [], replays: [], events: [],
  };
}

const issueA = issue("issue-a", "事项 A", "A 的根因");
const issueB = issue("issue-b", "事项 B", "B 的根因");
const issueNew = issue("issue-new", "新建事项", "新建事项根因");
const summaries: FeedbackIssueSummary[] = [issueA, issueB].map(({ issue: value, progress }) => ({ ...value, progress }));
const newSummary: FeedbackIssueSummary = { ...issueNew.issue, progress: issueNew.progress };
const inboxA: ReviewInboxItem = {
  agent_id: "ai-fae-agent", turn_key: "fae:turn-a", question: "Turn A 问题", answer: "Turn A 回答",
  feedback_keys: ["feedback-a"], first_feedback_at: "2026-08-31T00:00:00Z",
};
const inboxB: ReviewInboxItem = {
  agent_id: "ai-fae-agent", turn_key: "fae:turn-b", question: "Turn B 问题", answer: "Turn B 回答",
  feedback_keys: ["feedback-b"], first_feedback_at: "2026-08-31T00:01:00Z",
};
const overview: ReviewOverview = {
  feedback_rows: 2, negative_rows: 2, negative_turns: 2, positive_rows: 0,
  issue_total: 2, statuses: { fixing: 2 }, dispositions: { actionable: 2 }, write_available: true,
};

function apiWith(update: ReviewApi["update"]): ReviewApi {
  return {
    overview: vi.fn().mockResolvedValue(overview),
    inbox: vi.fn().mockResolvedValue([]),
    issues: vi.fn().mockResolvedValue(summaries),
    turnSummaries: vi.fn().mockResolvedValue([]),
    issue: vi.fn((id: string) => Promise.resolve(id === "issue-a" ? issueA : issueB)),
    create: vi.fn(), link: vi.fn(), update, move: vi.fn(), fixReady: vi.fn(), merge: vi.fn(),
    addEvidence: vi.fn(), verifyEvidence: vi.fn(), replay: vi.fn(), semanticReview: vi.fn(), disposition: vi.fn(),
  };
}

describe("ReviewWorkspace mutation refresh isolation", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    window.history.replaceState({}, "", "/admin/review?agent_id=ai-fae-agent&issue=issue-a");
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  const renderWorkspace = async (api: ReviewApi) => {
    await act(async () => root.render(<ReviewWorkspace
      api={api} agentId="ai-fae-agent" basePath="/admin/review" initialIssueId="issue-a"
      initialTurn={null} actor="codex" showActorField showAgentFilter
    />));
  };

  const renderInboxWorkspace = async (api: ReviewApi, basePath: "/admin/review" | "/admin/fae/issues") => {
    const query = basePath === "/admin/review"
      ? "?agent_id=ai-fae-agent&turn_key=fae%3Aturn-a"
      : "?session_key=fae%3Asession-a&turn_key=fae%3Aturn-a";
    window.history.replaceState({}, "", `${basePath}${query}`);
    await act(async () => root.render(<ReviewWorkspace
      api={api} agentId="ai-fae-agent" basePath={basePath} initialIssueId={null}
      initialTurn={inboxA} actor="codex" showActorField={basePath === "/admin/review"} showAgentFilter={false}
    />));
  };

  const chooseExistingIssue = async (id = "issue-a") => {
    const select = container.querySelector<HTMLSelectElement>('select[aria-label="已有事项"]')!;
    await act(async () => {
      select.value = id;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
  };

  const clickMutation = async (label: string) => {
    const button = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => candidate.textContent === label)!;
    await act(async () => button.click());
  };

  const chooseB = async () => {
    const row = [...container.querySelectorAll<HTMLButtonElement>(".review-issue-list button")]
      .find((button) => button.textContent?.includes("事项 B"))!;
    await act(async () => row.click());
    expect(container.textContent).toContain("B 的根因");
  };

  it("ignores a stale successful mutation refresh after selecting another issue", async () => {
    const pending = deferred<FeedbackIssueDetail>();
    const update = vi.fn((id: string) => id === "issue-a" ? pending.promise : Promise.resolve(issueB));
    const api = apiWith(update);
    await renderWorkspace(api);

    const saveA = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "保存归因")!;
    await act(async () => saveA.click());
    await chooseB();
    await act(async () => pending.resolve(issueA));

    expect(window.location.search).toBe("?agent_id=ai-fae-agent&issue=issue-b");
    expect(container.textContent).toContain("B 的根因");
    expect(container.textContent).not.toContain("A 的根因");
    const saveB = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "保存归因")!;
    await act(async () => saveB.click());
    expect(update.mock.calls[update.mock.calls.length - 1]?.[0]).toBe("issue-b");
  });

  it("ignores a stale conflict refresh after selecting another issue", async () => {
    const pending = deferred<FeedbackIssueDetail>();
    const update = vi.fn((id: string) => id === "issue-a" ? pending.promise : Promise.resolve(issueB));
    const api = apiWith(update);
    await renderWorkspace(api);

    const saveA = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "保存归因")!;
    await act(async () => saveA.click());
    await chooseB();
    await act(async () => pending.reject({ status: 409 }));

    expect(window.location.search).toBe("?agent_id=ai-fae-agent&issue=issue-b");
    expect(container.textContent).toContain("B 的根因");
    expect(container.textContent).not.toContain("A 的根因");
    expect(container.textContent).not.toContain("记录已被其他复审者更新");
    const saveB = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.textContent === "保存归因")!;
    await act(async () => saveB.click());
    expect(update.mock.calls[update.mock.calls.length - 1]?.[0]).toBe("issue-b");
  });

  it("navigates once and refreshes detail, list, overview, and success after FAE create", async () => {
    const api = apiWith(vi.fn());
    api.inbox = vi.fn().mockResolvedValue([inboxA, inboxB]);
    api.create = vi.fn().mockResolvedValue(issueNew);
    api.link = vi.fn().mockResolvedValue(issueNew);
    api.issue = vi.fn().mockResolvedValue(issueNew);
    api.issues = vi.fn()
      .mockResolvedValueOnce(summaries)
      .mockResolvedValueOnce([...summaries, newSummary]);
    api.overview = vi.fn()
      .mockResolvedValueOnce(overview)
      .mockResolvedValueOnce({ ...overview, feedback_rows: 3, issue_total: 3 });
    await renderInboxWorkspace(api, "/admin/fae/issues");
    const pushState = vi.spyOn(window.history, "pushState");

    await clickMutation("创建事项并纳管");

    expect(window.location.pathname).toBe("/admin/fae/issues/issue-new");
    expect(container.textContent).toContain("新建事项根因");
    expect(container.querySelector(".review-issue-list")?.textContent).toContain("新建事项");
    expect(container.textContent).toContain("负反馈回答已纳入闭环");
    expect(api.create).toHaveBeenCalledTimes(1);
    expect(api.link).toHaveBeenCalledTimes(1);
    expect(api.issue).toHaveBeenCalledTimes(1);
    expect(api.issue).toHaveBeenCalledWith("issue-new", expect.any(AbortSignal));
    expect(api.issues).toHaveBeenCalledTimes(2);
    expect(api.overview).toHaveBeenCalledTimes(2);
    expect(pushState).toHaveBeenCalledTimes(1);
  });

  it("navigates once and refreshes detail, list, overview, and success after FAE link", async () => {
    const linkedA = issue("issue-a", "事项 A（已关联）", "A 的根因");
    const api = apiWith(vi.fn());
    api.inbox = vi.fn().mockResolvedValue([inboxA, inboxB]);
    api.link = vi.fn().mockResolvedValue(linkedA);
    api.issue = vi.fn().mockResolvedValue(linkedA);
    api.issues = vi.fn()
      .mockResolvedValueOnce(summaries)
      .mockResolvedValueOnce([{ ...linkedA.issue, progress: linkedA.progress }, summaries[1]]);
    api.overview = vi.fn().mockResolvedValue(overview);
    await renderInboxWorkspace(api, "/admin/fae/issues");
    await chooseExistingIssue();
    const pushState = vi.spyOn(window.history, "pushState");

    await clickMutation("关联到已有事项");

    expect(window.location.pathname).toBe("/admin/fae/issues/issue-a");
    expect(container.textContent).toContain("事项 A（已关联）");
    expect(container.textContent).toContain("负反馈回答已关联到已有事项");
    expect(api.link).toHaveBeenCalledTimes(1);
    expect(api.issue).toHaveBeenCalledTimes(1);
    expect(api.issue).toHaveBeenCalledWith("issue-a", expect.any(AbortSignal));
    expect(api.issues).toHaveBeenCalledTimes(2);
    expect(api.overview).toHaveBeenCalledTimes(2);
    expect(pushState).toHaveBeenCalledTimes(1);
  });

  it("does not let deferred FAE create for Turn A override a newer Issue B selection", async () => {
    const pending = deferred<FeedbackIssueDetail>();
    const update = vi.fn().mockResolvedValue(issueB);
    const api = apiWith(update);
    api.inbox = vi.fn().mockResolvedValue([inboxA, inboxB]);
    api.create = vi.fn().mockReturnValue(pending.promise);
    api.link = vi.fn().mockResolvedValue(issueNew);
    await renderInboxWorkspace(api, "/admin/fae/issues");

    await clickMutation("创建事项并纳管");
    await chooseB();
    await act(async () => pending.resolve(issueNew));

    expect(window.location.pathname).toBe("/admin/fae/issues/issue-b");
    expect(container.textContent).toContain("B 的根因");
    expect(container.textContent).not.toContain("负反馈回答已纳入闭环");
    expect(api.link).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.issue).mock.calls.some(([id]) => id === "issue-new")).toBe(false);
    await clickMutation("保存归因");
    expect(update.mock.calls[update.mock.calls.length - 1]?.[0]).toBe("issue-b");
  });

  it("does not let deferred generic link for Turn A override a newer Turn B selection", async () => {
    const pending = deferred<FeedbackIssueDetail>();
    const update = vi.fn().mockResolvedValue(issueB);
    const api = apiWith(update);
    api.inbox = vi.fn().mockResolvedValue([inboxA, inboxB]);
    api.link = vi.fn().mockReturnValue(pending.promise);
    await renderInboxWorkspace(api, "/admin/review");
    await chooseExistingIssue();

    await clickMutation("关联到已有事项");
    const turnB = [...container.querySelectorAll<HTMLButtonElement>(".review-inbox button")]
      .find((button) => button.textContent?.includes("Turn B 问题"))!;
    await act(async () => turnB.click());
    await act(async () => pending.resolve(issueA));

    expect(window.location.search).toBe("?agent_id=ai-fae-agent&turn_key=fae%3Aturn-b");
    expect(container.textContent).toContain("Turn B 回答");
    expect(container.textContent).not.toContain("负反馈回答已关联到已有事项");
    expect(vi.mocked(api.issue).mock.calls.some(([id]) => id === "issue-a")).toBe(false);
    await chooseB();
    await clickMutation("保存归因");
    expect(update.mock.calls[update.mock.calls.length - 1]?.[0]).toBe("issue-b");
  });

  it("keeps generic create navigation and refresh coordinated by the workspace", async () => {
    const api = apiWith(vi.fn());
    api.inbox = vi.fn().mockResolvedValue([inboxA]);
    api.create = vi.fn().mockResolvedValue(issueNew);
    api.link = vi.fn().mockResolvedValue(issueNew);
    api.issue = vi.fn().mockResolvedValue(issueNew);
    api.issues = vi.fn()
      .mockResolvedValueOnce(summaries)
      .mockResolvedValueOnce([...summaries, newSummary]);
    await renderInboxWorkspace(api, "/admin/review");
    const replaceState = vi.spyOn(window.history, "replaceState");

    await clickMutation("创建事项并纳管");

    expect(window.location.pathname).toBe("/admin/review");
    expect(window.location.search).toBe("?agent_id=ai-fae-agent&issue=issue-new");
    expect(container.textContent).toContain("新建事项根因");
    expect(container.querySelector(".review-issue-list")?.textContent).toContain("新建事项");
    expect(container.textContent).toContain("负反馈回答已纳入闭环");
    expect(api.issue).toHaveBeenCalledTimes(1);
    expect(api.issues).toHaveBeenCalledTimes(2);
    expect(api.overview).toHaveBeenCalledTimes(2);
    expect(replaceState).toHaveBeenCalledTimes(1);
  });

  it("waits for the intended detail refresh before showing create success", async () => {
    const pendingDetail = deferred<FeedbackIssueDetail>();
    const api = apiWith(vi.fn());
    api.inbox = vi.fn().mockResolvedValue([inboxA]);
    api.create = vi.fn().mockResolvedValue(issueNew);
    api.link = vi.fn().mockResolvedValue(issueNew);
    api.issue = vi.fn().mockReturnValue(pendingDetail.promise);
    api.issues = vi.fn()
      .mockResolvedValueOnce(summaries)
      .mockResolvedValueOnce([...summaries, newSummary]);
    await renderInboxWorkspace(api, "/admin/review");

    await clickMutation("创建事项并纳管");

    expect(window.location.search).toBe("?agent_id=ai-fae-agent&issue=issue-new");
    expect(container.textContent).not.toContain("负反馈回答已纳入闭环");
    expect(container.textContent).not.toContain("新建事项根因");
    await act(async () => pendingDetail.resolve(issueNew));
    expect(container.textContent).toContain("新建事项根因");
    expect(container.textContent).toContain("负反馈回答已纳入闭环");
    expect(api.issue).toHaveBeenCalledTimes(1);
  });

  it("reports a list refresh failure against the intended post-create selection", async () => {
    const api = apiWith(vi.fn());
    api.inbox = vi.fn().mockResolvedValue([inboxA]);
    api.create = vi.fn().mockResolvedValue(issueNew);
    api.link = vi.fn().mockResolvedValue(issueNew);
    api.issue = vi.fn().mockResolvedValue(issueNew);
    api.issues = vi.fn()
      .mockResolvedValueOnce(summaries)
      .mockRejectedValueOnce(new Error("治理队列刷新失败"));
    await renderInboxWorkspace(api, "/admin/review");

    await clickMutation("创建事项并纳管");

    expect(window.location.search).toBe("?agent_id=ai-fae-agent&issue=issue-new");
    expect(container.textContent).toContain("新建事项根因");
    expect(container.textContent).toContain("治理队列刷新失败");
    expect(container.textContent).not.toContain("负反馈回答已纳入闭环");
  });

  it("does not leak an Issue A conflict message when selection changes during conflict refresh", async () => {
    const pendingConflictRefresh = deferred<FeedbackIssueDetail>();
    const update = vi.fn().mockRejectedValue({ status: 409 });
    const api = apiWith(update);
    api.issue = vi.fn()
      .mockResolvedValueOnce(issueA)
      .mockReturnValueOnce(pendingConflictRefresh.promise)
      .mockResolvedValueOnce(issueB);
    await renderWorkspace(api);

    await clickMutation("保存归因");
    await chooseB();
    await act(async () => pendingConflictRefresh.resolve(issueA));

    expect(window.location.search).toBe("?agent_id=ai-fae-agent&issue=issue-b");
    expect(container.textContent).toContain("B 的根因");
    expect(container.textContent).not.toContain("记录已被其他复审者更新");
  });

  it("reports a failed conflict refresh without claiming the record was refreshed", async () => {
    const update = vi.fn().mockRejectedValue({ status: 409 });
    const api = apiWith(update);
    api.issue = vi.fn()
      .mockResolvedValueOnce(issueA)
      .mockRejectedValueOnce(new Error("冲突状态刷新失败"));
    await renderWorkspace(api);

    await clickMutation("保存归因");

    expect(container.textContent).toContain("冲突状态刷新失败");
    expect(container.textContent).not.toContain("记录已被其他复审者更新");
  });
});
