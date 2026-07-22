import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function rule(selector: string): string {
  const start = styles.indexOf(`${selector} {`);
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
    const declarations = [...styles.matchAll(/font-size:\s*([^;}]+)/g)].map((match) => match[1].trim());
    expect(declarations.length).toBeGreaterThan(0);
    for (const declaration of declarations) {
      expect(declaration).toMatch(/^(?:[\d.]+px|clamp\([\d.]+px,\s*[\d.]+vw,\s*[\d.]+px\))$/);
      const sizes = [...declaration.matchAll(/([\d.]+)px/g)].map((match) => Number(match[1]));
      expect(Math.min(...sizes)).toBeGreaterThanOrEqual(11.5);
    }
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

  it("gives percentage-height trend bars a definite containing block", () => {
    expect(rule(".trend-track")).toMatch(/(?:\{|;)\s*height:\s*132px\s*;/);
  });

  it("gives every Agent card substantial resting weight", () => {
    expect(rule(".fleet-agent-card")).toContain("padding: 24px");
    expect(rule(".fleet-agent-card")).toContain("border: 1px solid #c8d4e2");
    expect(rule(".fleet-agent-card")).toContain("box-shadow: 0 14px 34px rgba(20, 51, 89, .11)");
    expect(rule(".fleet-agent-card::before")).toContain("height: 6px");
    expect(styles).toContain(".fleet-avatar { width: 52px; height: 52px;");
  });

  it("keeps Agent names and detail rows readable", () => {
    expect(rule(".fleet-agent-identity h3")).toContain("font-size: 19px");
    expect(rule(".fleet-agent-identity h3")).toContain("white-space: normal");
    expect(rule(".fleet-agent-description")).toContain("font-size: 14px");
    expect(rule(".fleet-usage strong")).toContain("font-size: 34px");
    expect(rule(".fleet-agent-meta dd strong")).toContain("font-size: 14px");
    expect(rule(".fleet-agent-meta dd small")).toContain("font-size: 11.5px");
    expect(rule(".fleet-recent p")).toContain("font-size: 13px");
  });

  it("uses one-column summary and Agent layouts on small screens", () => {
    const mobile = block("@media (max-width: 720px)");
    expect(mobile).toContain(".fleet-summary-grid { grid-template-columns: 1fr; }");
    expect(mobile).toContain(".fleet-agent-grid { grid-template-columns: 1fr; }");
  });

  it("protects long Agent names beside wide status labels at 320px", () => {
    const mobile = block("@media (max-width: 720px)");
    expect(rule(".fleet-agent-identity h3")).toContain("overflow-wrap: anywhere");
    expect(mobile).toContain(".fleet-agent-head { display: grid; grid-template-columns: 52px minmax(0, 1fr);");
    expect(mobile).toContain(".fleet-state { grid-column: 2; grid-row: 2; justify-self: start; }");
  });

  it("gives the Daily Brief two equal, substantial desktop columns", () => {
    expect(rule(".daily-brief-grid")).toContain("grid-template-columns: repeat(2, minmax(0, 1fr))");
    expect(rule(".brief-panel")).toContain("min-height: 330px");
    expect(rule(".attention-panel")).toContain("border-top: 4px solid var(--down)");
  });

  it("uses rendered severity hooks for label, icon, border, and color treatments", () => {
    for (const tone of ["critical", "attention", "info", "recovery"]) {
      expect(styles).toContain(`.event-severity-${tone}`);
      expect(rule(`.operational-event-item.event-severity-${tone}`)).toContain("border-left: 4px solid");
      expect(rule(`.event-severity.event-severity-${tone}`)).toContain("color:");
      expect(rule(`.event-severity.event-severity-${tone} i`)).toContain("background:");
    }
    expect(rule(".operational-event-item.is-linked:hover")).not.toContain("border-color:");
  });

  it("distinguishes stale Briefs and quiet System Agent infrastructure", () => {
    expect(rule(".brief-freshness-stale")).toContain("color: var(--warn)");
    expect(rule(".operational-event-item.event-visibility-system")).toContain("box-shadow: none");
    expect(rule(".operational-event-item.event-visibility-system")).toContain("background: #f7f9fc");
  });

  it("finishes Activity groups, pagination, and Recent Activity cards", () => {
    expect(rule(".activity-history")).toContain("margin-top: 28px");
    expect(rule(".activity-group")).toContain("padding: 24px");
    expect(rule(".activity-load-more")).toContain("min-height: 44px");
    expect(rule(".agent-activity-section")).toContain("padding: 24px");
    expect(rule(".agent-activity-status")).toContain("min-height: 112px");
  });

  it("stacks the Brief and Activity controls at the approved Operations breakpoint", () => {
    const operationsMobile = block("@media (max-width: 760px)");
    expect(operationsMobile).toContain(".daily-brief-grid { grid-template-columns: 1fr; }");
    expect(operationsMobile).toContain(".attention-panel { order: -1; }");
    expect(operationsMobile).toContain(".activity-filter-bar { grid-template-columns: 1fr; }");
    expect(operationsMobile).toContain(".activity-group { padding: 18px; }");
    expect(operationsMobile).toContain(".agent-activity-section { padding: 18px; }");
  });
});
