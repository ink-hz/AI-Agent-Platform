import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  UI_COPY,
  channelStatusLabel,
  readinessLabel,
  readinessReasonLabel,
  runtimeFreshnessLabel,
} from "./copy";


function allStrings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (typeof value === "function") return [String(value(1))];
  if (Array.isArray(value)) return value.flatMap(allStrings);
  if (value && typeof value === "object") {
    return Object.values(value).flatMap(allStrings);
  }
  return [];
}


describe("reviewed UI copy", () => {
  it("uses Chinese system copy while preserving agreed technical nouns", () => {
    expect(UI_COPY.navigation).toEqual(["总览", "Agent", "Session", "运行记录"]);
    expect(UI_COPY.navigationLabel).toBe("主导航");
    expect(UI_COPY.hero.title).toBe("Agent 集群总览");
    expect(UI_COPY.summary.metrics).toEqual([
      "Agent 数量", "正常运行", "累计对话", "近 7 天对话",
    ]);
    expect(UI_COPY.agent.fields).toEqual([
      "累计对话", "近 7 天", "已上线", "最近更新", "最近工作",
    ]);
    expect(JSON.stringify(UI_COPY)).not.toMatch(/FLEET DIRECTORY|AGENT OPERATIONS|Read-only/);
    expect(JSON.stringify(UI_COPY)).toMatch(/Agent|Session|Trace|Backend/);
  });

  it("maps runtime enums for display without changing their source values", () => {
    expect(readinessLabel("Ready")).toBe("正常");
    expect(readinessLabel("Busy")).toBe("忙碌");
    expect(readinessLabel("Limited")).toBe("受限");
    expect(readinessLabel("Offline")).toBe("离线");
    expect(readinessLabel("Unknown")).toBe("未知");
    expect(readinessReasonLabel("Ready")).toBe("运行环境和主要 Channel 均正常");
    expect(channelStatusLabel("connected")).toBe("已连接");
    expect(runtimeFreshnessLabel("stale")).toBe("数据已过期");
  });

  it("contains no rejected translated or marketing-heavy labels", () => {
    const copy = allStrings(UI_COPY).join(" ");
    for (const rejected of ["助手", "AI 团队", "团队成员", "今天的 AI 团队", "数字员工"]) {
      expect(copy).not.toContain(rejected);
    }
  });

  it("uses the Orbbec product name as the static browser-title fallback", () => {
    const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");

    expect(html).toContain("<title>Orbbec Agent Platform</title>");
    expect(html).not.toContain("MetaBot Cluster Monitor");
  });
});
