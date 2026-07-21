import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function rule(selector: string): string {
  const start = styles.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`missing CSS rule: ${selector}`);
  const end = styles.indexOf("}", start);
  return styles.slice(start, end + 1);
}

function lastRule(selector: string): string {
  const start = styles.lastIndexOf(`${selector} {`);
  if (start < 0) throw new Error(`missing CSS rule: ${selector}`);
  const end = styles.indexOf("}", start);
  return styles.slice(start, end + 1);
}

function block(header: string): string {
  const start = styles.indexOf(header);
  if (start < 0) throw new Error(`missing CSS block: ${header}`);
  const openingBrace = styles.indexOf("{", start);
  let depth = 0;
  for (let index = openingBrace; index < styles.length; index += 1) {
    if (styles[index] === "{") depth += 1;
    if (styles[index] === "}") depth -= 1;
    if (depth === 0) return styles.slice(start, index + 1);
  }
  throw new Error(`unclosed CSS block: ${header}`);
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

  it("gives every Agent card substantial resting weight", () => {
    expect(rule(".fleet-agent-card")).toContain("padding: 24px");
    expect(rule(".fleet-agent-card")).toContain("border: 1px solid #c8d4e2");
    expect(rule(".fleet-agent-card")).toContain("box-shadow: 0 14px 34px rgba(20, 51, 89, .11)");
    expect(rule(".fleet-agent-card::before")).toContain("height: 6px");
    expect(lastRule(".fleet-avatar")).toContain("width: 52px");
    expect(lastRule(".fleet-avatar")).toContain("height: 52px");
  });

  it("keeps Agent names and detail rows readable", () => {
    expect(rule(".fleet-agent-identity h3")).toContain("font-size: 19px");
    expect(rule(".fleet-agent-identity h3")).toContain("white-space: normal");
    expect(rule(".fleet-agent-description")).toContain("font-size: 14px");
    expect(rule(".fleet-usage strong")).toContain("font-size: 34px");
    expect(rule(".fleet-agent-meta dd")).toContain("font-size: 13px");
    expect(rule(".fleet-recent p")).toContain("font-size: 13px");
  });

  it("uses one-column summary and Agent layouts on small screens", () => {
    const mobile = block("@media (max-width: 720px)");
    expect(mobile).toContain(".fleet-summary-grid { grid-template-columns: 1fr; }");
    expect(mobile).toContain(".fleet-agent-grid { grid-template-columns: 1fr; }");
  });
});
