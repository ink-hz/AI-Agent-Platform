/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FeedbackIssueDetail, FeedbackIssueSummary, ReviewOverview } from "../../types";
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
const summaries: FeedbackIssueSummary[] = [issueA, issueB].map(({ issue: value, progress }) => ({ ...value, progress }));
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
  });

  const renderWorkspace = async (api: ReviewApi) => {
    await act(async () => root.render(<ReviewWorkspace
      api={api} agentId="ai-fae-agent" basePath="/admin/review" initialIssueId="issue-a"
      initialTurn={null} actor="codex" showActorField showAgentFilter
    />));
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
});
