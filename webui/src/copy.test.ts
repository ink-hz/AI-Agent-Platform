import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { UI_COPY } from "./copy";


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
  it("uses the approved source-language product vocabulary", () => {
    expect(UI_COPY.navigation).toEqual(["Overview", "Agents", "Sessions"]);
    expect(UI_COPY.navigationLabel).toBe("Product navigation");
    expect(UI_COPY.hero.title).toBe("Agent Overview");
    expect(UI_COPY.summary.metrics).toEqual([
      "Agents", "Online", "Total Conversations", "Last 7 Days",
    ]);
    expect(UI_COPY.agent.fields).toEqual([
      "Total Conversations", "Last 7 Days", "In Production", "Last Updated", "Recent",
    ]);
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
