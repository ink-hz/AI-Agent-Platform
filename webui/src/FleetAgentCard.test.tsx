import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FleetAgentCard } from "./FleetAgentCard";
import type { FleetAgent } from "./types";


const AGENT = {
  id: "hr-bot",
  name: "HR",
  domain: "HR",
  description: "处理招聘、人事与员工服务相关工作。",
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
      "处理招聘、人事与员工服务相关工作。",
      "Total Conversations",
      "826",
      "Last 7 Days",
      "214",
      "In Production",
      "33 days",
      "Since Jun 17, 2026",
      "Last Updated",
      "3 hours ago",
      "Jul 21, 2026",
      "Recent",
      "新员工入职需要准备哪些材料？",
      "Active",
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

    expect(html.match(/Not recorded/g)).toHaveLength(2);
    expect(html).not.toContain("Since Not recorded");
  });
});
