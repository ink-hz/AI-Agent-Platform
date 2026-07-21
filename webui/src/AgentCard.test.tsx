import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentCard } from "./AgentCard";
import type { InstanceStatus } from "./types";


const HEALTHY_HR_BOT: InstanceStatus = {
  id: "hr-bot",
  name: "hr-bot",
  pm2_name: "metabot-hr",
  port: 9101,
  status: "healthy",
  uptime_seconds: 90_061,
  latency_ms: 6,
  checked_at: "2026-07-21T01:00:00Z",
  error: null,
};


describe("AgentCard", () => {
  it("renders Agent identity before read-only operational metrics", () => {
    const html = renderToStaticMarkup(
      <AgentCard
        instance={HEALTHY_HR_BOT}
        now={new Date("2026-07-21T01:00:05Z")}
      />,
    );

    expect(html).toContain("HR 助手");
    expect(html).toContain("人力资源");
    expect(html).toContain("支持招聘、人事与员工服务流程。");
    expect(html).toContain("运行时长");
    expect(html).toContain("响应延迟");
    expect(html).toContain("API 端口");
    expect(html).toContain("只读监控");
    expect(html).toContain("metabot-hr");
  });

  it("does not expose an entry link or control", () => {
    const html = renderToStaticMarkup(
      <AgentCard
        instance={HEALTHY_HR_BOT}
        now={new Date("2026-07-21T01:00:05Z")}
      />,
    );

    expect(html).not.toMatch(/<a(?:\s|>)/);
    expect(html).not.toContain("<button");
    expect(html).not.toContain("href=");
  });

  it("shows explicit freshness for a current snapshot", () => {
    const html = renderToStaticMarkup(
      <AgentCard
        instance={HEALTHY_HR_BOT}
        now={new Date("2026-07-21T01:00:05Z")}
      />,
    );

    expect(html).toContain("数据新鲜");
    expect(html).not.toContain("数据已过期");
  });

  it("shows stale data and the previous probe error independently", () => {
    const html = renderToStaticMarkup(
      <AgentCard
        instance={{
          ...HEALTHY_HR_BOT,
          status: "offline",
          error: "connection_failed",
        }}
        now={new Date("2026-07-21T01:00:31Z")}
      />,
    );

    expect(html).toContain("数据已过期");
    expect(html).toContain("连接失败");
  });
});
