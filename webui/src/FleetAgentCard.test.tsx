import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FleetAgentCard } from "./FleetAgentCard";
import type { FleetAgent } from "./types";


const AGENT: FleetAgent = {
  id: "hr-bot",
  name: "HR",
  domain: "HR",
  description: "处理招聘、人事与员工服务相关工作。",
  glyph: "HR",
  accent: "people",
  state: "active",
  uptime_seconds: 3600,
  total_conversations: 826,
  conversations_last_7d: 214,
  last_activity_at: "2026-07-21T01:58:00Z",
  recent_summary: "新员工入职需要准备哪些材料？",
};


describe("FleetAgentCard", () => {
  it("renders identity, usage, runtime, and recent activity", () => {
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
      "Uptime",
      "1小时 0分钟",
      "Last Activity",
      "2分钟前",
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

    for (const forbidden of ["端口", "延迟", "pm2", "<button", "href="]) {
      expect(html).not.toContain(forbidden);
    }
  });
});
