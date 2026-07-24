import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FleetAgentCard } from "./FleetAgentCard";
import type { FleetAgent } from "./types";


const AGENT = {
  id: "hr-bot",
  name: "HR",
  domain: "HR",
  description: "Handles recruiting and employee services.",
  glyph: "HR",
  accent: "people",
  visibility: "business",
  state: "active",
  live_since: "2026-06-17T16:34:33+08:00",
  live_since_basis: "release_artifact",
  last_updated_at: "2026-07-20T23:00:00Z",
  last_updated_basis: "release_artifact",
  current_runtime_seconds: 3600,
  total_conversations: 826,
  conversations_last_7d: 214,
  last_activity_at: "2026-07-21T01:58:00Z",
  recent_summary: "新员工入职需要准备哪些材料？",
} as unknown as FleetAgent;


describe("FleetAgentCard", () => {
  it("renders identity, usage, durable lifecycle, and recent activity", () => {
    const html = renderToStaticMarkup(
      <FleetAgentCard agent={AGENT} now={new Date("2026-07-21T02:00:00Z")} />,
    );

    for (const text of [
      "HR",
      "Handles recruiting and employee services.",
      "累计对话",
      "826",
      "近 7 天",
      "214",
      "已上线",
      "已运行 33 天",
      "上线于 2026年6月17日",
      "最近更新",
      "3小时前",
      "2026年7月21日",
      "最近工作",
      "新员工入职需要准备哪些材料？",
      "活跃",
    ]) {
      expect(html).toContain(text);
    }
  });

  it("does not render technical diagnostics or controls", () => {
    const html = renderToStaticMarkup(
      <FleetAgentCard agent={AGENT} now={new Date("2026-07-21T02:00:00Z")} />,
    );

    for (const forbidden of ["Live Since", "Running Days", "Uptime", "Current Runtime", "端口", "延迟", "pm2", "<button", "href="]) {
      expect(html).not.toContain(forbidden);
    }
  });

  it("does not invent lifecycle dates when evidence is missing", () => {
    const html = renderToStaticMarkup(
      <FleetAgentCard
        agent={{ ...AGENT, live_since: null, last_updated_at: null }}
        now={new Date("2026-07-21T02:00:00Z")}
      />,
    );

    expect(html.match(/未记录/g)).toHaveLength(2);
    expect(html).not.toContain("上线于 未记录");
  });
});
