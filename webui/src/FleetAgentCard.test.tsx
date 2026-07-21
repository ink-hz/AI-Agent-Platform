import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FleetAgentCard } from "./FleetAgentCard";
import type { FleetAgent } from "./types";


const AGENT: FleetAgent = {
  id: "hr-bot",
  name: "HR 助手",
  domain: "人力资源",
  description: "支持招聘、人事与员工服务流程。",
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
      "HR 助手",
      "人力资源",
      "支持招聘、人事与员工服务流程。",
      "累计对话",
      "826",
      "近 7 天",
      "214",
      "运行时长",
      "1小时 0分钟",
      "2分钟前",
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

    for (const forbidden of ["端口", "延迟", "pm2", "<button", "href="]) {
      expect(html).not.toContain(forbidden);
    }
  });
});
