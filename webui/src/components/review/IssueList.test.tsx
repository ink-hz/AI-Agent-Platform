/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FeedbackIssueSummary } from "../../types";
import { IssueList } from "./IssueList";


const issue: FeedbackIssueSummary = {
  id: "issue-1",
  agent_id: "ai-fae-agent",
  origin_turn_key: null,
  title: "这是一个用于验证双行截断且不能把整个工程字段平铺给业务使用者的长标题",
  priority: "P1",
  failure_layer: "coverage",
  secondary_layers: [],
  root_cause: "",
  impact_scope: "",
  owner: "codex",
  disposition: "actionable",
  row_version: null,
  progress: {
    issue_id: "issue-1",
    status: "awaiting_replay",
    missing_gates: ["semantic_review", "reviewer"],
    replay_passed_turns: 0,
    replay_required_turns: 1,
    reopened: false,
  },
};

describe("IssueList FAE governance presentation", () => {
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

  it("shows business queue tabs, hides repeated agent identity, and names only the next gate", async () => {
    await act(async () => root.render(<IssueList
      issues={[issue]}
      inbox={[]}
      selectedId={null}
      selectedTurnKey={null}
      onSelect={vi.fn()}
      onSelectInbox={vi.fn()}
      showAgentFilter={false}
      showAgentIdentity={false}
      presentation="fae-governance"
      statusFilter="open"
      onStatusFilterChange={vi.fn()}
      totalCount={87}
    />));

    expect(container.querySelector(".fae-governance-queues")?.textContent).toBe("需要行动待分诊待复跑已闭环全部");
    expect(container.querySelector(".review-list-heading")?.textContent).toContain("87");
    expect(container.querySelector(".review-issue-title")?.textContent).toContain("双行截断");
    expect(container.querySelector(".review-issue-list")?.textContent).not.toContain("ai-fae-agent");
    expect(container.querySelector(".review-issue-list")?.textContent).toContain("下一步：独立语义复审");
    expect(container.querySelector(".review-issue-list")?.textContent).not.toContain("复审人");
  });
});
