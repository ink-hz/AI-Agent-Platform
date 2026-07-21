import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function rule(selector: string): string {
  const start = styles.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`missing CSS rule: ${selector}`);
  const end = styles.indexOf("}", start);
  return styles.slice(start, end + 1);
}


describe("Executive Operations visual contract", () => {
  it("uses the approved high-contrast foundation", () => {
    for (const token of [
      "--bg: #edf2f7",
      "--line: #cbd6e2",
      "--line-soft: #dde5ee",
      "--ink: #0f1b2d",
      "--ink-soft: #46566a",
      "--ink-faint: #68778a",
      "--brand: #123f78",
      "--brand-bright: #2468c5",
    ]) expect(styles).toContain(token);
  });

  it("never renders visible text below the approved minimum", () => {
    const sizes = [...styles.matchAll(/font-size:\s*([\d.]+)px/g)].map((match) => Number(match[1]));
    expect(sizes.length).toBeGreaterThan(0);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(11.5);
  });

  it("gives summary and insight cards visible resting weight", () => {
    expect(rule(".fleet-summary-card")).toContain("min-height: 150px");
    expect(rule(".fleet-summary-card")).toContain("padding: 24px");
    expect(rule(".fleet-summary-card")).toContain("box-shadow: 0 12px 30px rgba(21, 51, 88, .09)");
    expect(rule(".insight-card")).toContain("min-height: 330px");
    expect(rule(".insight-card")).toContain("padding: 26px");
    expect(rule(".insight-card")).toContain("box-shadow: 0 12px 30px rgba(21, 51, 88, .09)");
  });

  it("keeps chart values readable without hover", () => {
    expect(rule(".trend-value")).toContain("font-size: 12px");
    expect(rule(".trend-value")).toContain("opacity: 1");
    expect(rule(".trend-date")).toContain("font-size: 12px");
    expect(rule(".ranking-main strong")).toContain("font-size: 14px");
    expect(rule(".ranking-main > i")).toContain("height: 7px");
  });
});
