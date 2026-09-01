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

function lastBlock(header: string): string {
  const start = styles.lastIndexOf(header);
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
  it("gives the VOC workspace a clear draft boundary and responsive layout", () => {
    expect(rule(".voc-layout")).toContain("grid-template-columns: minmax(0, 1.35fr) minmax(300px, .65fr)");
    expect(rule(".voc-draft-panel")).toContain("border: 1px solid #9fb9d5");
    expect(rule(".voc-draft-actions .is-submit")).toContain("background: var(--brand)");
    expect(block("@media (max-width: 820px)")).toContain(".voc-layout { grid-template-columns: 1fr; }");
  });
  it("uses a Chinese-first product typeface and removes template-era chrome", () => {
    expect(rule(":root")).toContain('font-family: "PingFang SC", "Microsoft YaHei", Inter, "Segoe UI", system-ui, sans-serif');
    expect(styles).not.toContain(".readonly-tag");
    expect(styles).not.toContain(".eyebrow");
  });

  it("presents runtime facts as one structured panel instead of floating cards", () => {
    expect(rule(".runtime-detail-grid")).toContain("gap: 1px");
    expect(rule(".runtime-detail-grid")).toContain("border: 1px solid var(--line)");
    expect(rule(".runtime-detail-grid")).toContain("background: var(--line-soft)");
    expect(rule(".runtime-fact")).toContain("border: 0");
    expect(rule(".runtime-fact")).toContain("border-radius: 0");
    expect(rule(".runtime-fact")).toContain("box-shadow: none");
  });

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

  it("keeps compact FAE workbench navigation in one keyboard-scrollable row", () => {
    const compact = block("@media (max-width: 900px)");
    expect(compact).toContain(".fae-workbench__sidebar nav { display: flex; flex-wrap: nowrap; overflow-x: auto; }");
  });

  it("turns the FAE outcome report into a responsive and printable reader", () => {
    const compactReport = block("@media (max-width: 1100px)");
    expect(compactReport).toContain(".fae-outcome-cover__facts, .fae-outcome-chapter__facts { grid-template-columns: repeat(2,minmax(0,1fr)); }");
    expect(compactReport).toContain(".fae-outcome-chapters { position: static;");
    const mobileReport = block("@media (max-width: 560px)");
    expect(mobileReport).toContain(".fae-outcome-cover__facts, .fae-outcome-chapter__facts, .fae-outcome-value-comparison { grid-template-columns: 1fr; }");
    expect(mobileReport).not.toContain(".fae-outcome-metric-group");
    const printReport = block("@media print");
    expect(printReport).toContain(".topbar, .admin-nav, .fae-workbench__sidebar");
    expect(printReport).toContain(".fae-outcome-chapters");
    expect(printReport).toContain("display: none");
    expect(block("@media (prefers-reduced-motion: reduce)")).toContain("scroll-behavior: auto");
  });

  it("bounds the FAE workspace and keeps its reading column legible", () => {
    expect(styles).toContain(".topbar-inner,\n.page,\n.site-foot { width: min(1240px, calc(100% - 48px));");
    expect(rule(".page.is-fae-workbench")).toContain("width: min(1440px, calc(100% - 48px))");
    expect(rule(".fae-workbench")).toContain("max-width: 1440px");
    expect(rule(".fae-workbench")).toContain("grid-template-columns: 216px minmax(0, 1fr)");
    expect(rule(".fae-workbench__content")).toContain("max-width: 1180px");
    expect(rule(".fae-overview")).toContain("font-size: 15px");
  });

  it("keeps available non-drillable metrics visually distinct from unavailable metrics", () => {
    expect(rule(".fae-summary-card.is-static")).toContain("background: var(--surface)");
    expect(rule(".fae-summary-card.is-static")).toContain("box-shadow: 0 9px 24px rgba(20,51,89,.07)");
  });

  it("stacks Session governance after the header and before Turns at 1040px", () => {
    expect(rule(".fae-session-detail-layout > div")).toContain("display: contents");
    const compact = lastBlock("@media (max-width: 1040px)");
    expect(compact).toContain(".fae-session-detail-layout { grid-template-columns: 1fr; }");
    expect(compact).toContain(".fae-session-detail-layout .session-detail-head { grid-column: 1; grid-row: 1; }");
    expect(compact).toContain(".fae-session-governance { position: static; grid-column: 1; grid-row: 2; }");
    expect(compact).toContain(".fae-session-detail-layout .turn-stack { grid-column: 1; grid-row: 3; }");
  });

  it("keeps overview cards single-column and actions visible without hover below 720px", () => {
    expect(rule(".fae-summary-card__action")).toContain("opacity: 1");
    const mobile = lastBlock("@media (max-width: 720px)");
    expect(mobile).toContain(".fae-overview__summary { grid-template-columns: 1fr; }");
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

  it("makes Professional Agent cards obvious links without changing the profile label", () => {
    expect(rule(".agent-use-card")).toContain("grid-template-rows: auto 1fr auto");
    expect(rule(".agent-use-card:hover")).toContain("transform: translateY(-2px)");
    expect(rule(".agent-use-card:focus-visible")).toContain("outline: 3px solid");
    expect(rule(".agent-use-card-action")).toContain("min-height: 42px");
    expect(rule('.agent-use-card[data-agent-kind="fae"]')).toContain("--agent-accent: #1f66c7");
    expect(rule('.agent-use-card[data-agent-kind="admin"]')).toContain("--agent-accent: #946300");
    expect(rule(".agent-use-profile > span")).toContain("color: var(--brand-bright)");
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

  it("contains all six Activity filter tracks through the intermediate desktop range", () => {
    const activityCompact = block("@media (max-width: 1080px)");
    expect(activityCompact).toContain(".activity-filter-bar { grid-template-columns: repeat(2, minmax(0, 1fr)); }");
    expect(activityCompact).toContain(".activity-filter-bar button { width: 100%; }");
  });

  it("stacks the Brief and Activity controls at the approved Operations breakpoint", () => {
    const operationsMobile = block("@media (max-width: 760px)");
    expect(operationsMobile).toContain(".daily-brief-grid { grid-template-columns: 1fr; }");
    expect(operationsMobile).toContain(".attention-panel { order: -1; }");
    expect(operationsMobile).toContain(".activity-filter-bar { grid-template-columns: 1fr; }");
    expect(operationsMobile).toContain(".activity-group { padding: 18px; }");
    expect(operationsMobile).toContain(".agent-activity-section { padding: 18px; }");
  });

  it("contains rich message content with message-scoped overflow rules", () => {
    expect(rule(".message-markdown")).toContain("min-width: 0");
    expect(rule(".message-markdown")).toContain("overflow-wrap: anywhere");
    expect(rule(".message-markdown pre,\n.message-markdown .table-scroll")).toContain("max-width: 100%");
    expect(rule(".message-markdown pre,\n.message-markdown .table-scroll")).toContain("overflow-x: auto");
    expect(rule(".message-markdown > :first-child")).toContain("margin-top: 0");
    expect(rule(".message-markdown > :last-child")).toContain("margin-bottom: 0");
  });

  it("scopes Markdown typography and tables to Session messages", () => {
    for (const selector of [
      ".message-markdown h1",
      ".message-markdown p",
      ".message-markdown ul",
      ".message-markdown blockquote",
      ".message-markdown table",
      ".message-markdown a",
      ".message-markdown code",
      ".message-markdown pre",
    ]) expect(styles).toContain(selector);
  });

  it("stacks messages while keeping labels and times readable on mobile", () => {
    const mobile = block("@media (max-width: 720px)");
    expect(mobile).toContain(".message-block { grid-template-columns: 1fr; gap: 8px; padding: 19px; }");
    expect(mobile).toContain(".message-label { align-items: center; flex-direction: row; justify-content: space-between; }");
  });

  it("keeps continuous Conversation content and composer usable on mobile", () => {
    expect(rule(".page.is-brain-workspace")).toContain("width: 100%");
    expect(rule(".app.is-brain-workspace-shell")).toContain("height: 100dvh");
    expect(rule(".brain-workspace")).toContain("grid-template-columns: 280px minmax(0,1fr)");
    expect(rule(".brain-workspace")).toContain("height: 100%");
    expect(rule(".brain-workspace")).toContain("overflow: hidden");
    expect(rule(".conversation-sidebar")).toContain("overflow-y: auto");
    expect(rule(".brain-workspace-main")).toContain("overflow-y: auto");
    expect(rule(".conversation-composer")).toContain("position: sticky");
    expect(rule(".conversation-message")).toContain("overflow-wrap: anywhere");
    const mobile = lastBlock("@media (max-width: 720px)");
    expect(mobile).toContain(".conversation-header { align-items: stretch; flex-direction: column; }");
    expect(mobile).toContain(".conversation-composer-actions { align-items: stretch; flex-direction: column; }");
    expect(mobile).toContain(".conversation-send { width: 100%; }");
    expect(mobile).toContain(".conversation-sidebar { position: fixed;");
    expect(mobile).toContain(".conversation-sidebar.is-open { transform: translateX(0); }");
    expect(mobile).toContain("env(safe-area-inset-bottom)");
  });

  it("keeps attachment names and cards readable on narrow screens", () => {
    expect(rule(".attachment-name")).toContain("overflow-wrap: anywhere");
    expect(rule(".attachment-card")).toContain("grid-template-columns: minmax(0, 1fr) auto");
    const mobile = block("@media (max-width: 720px)");
    expect(mobile).toContain(".attachment-card { grid-template-columns: 1fr; }");
    expect(mobile).toContain(".attachment-actions { justify-content: flex-start; }");
  });

  it("gives AI notes an independent two-column reading workspace", () => {
    expect(rule(".app.is-ai-notes-workspace-shell")).toContain("height: 100dvh");
    expect(rule(".page.is-ai-notes-workspace")).toContain("min-height: 0");
    expect(rule(".ai-notes-layout")).toContain("grid-template-columns: 19rem minmax(0, 1fr)");
    expect(rule(".ai-notes-layout")).toContain("min-height: 0");
    expect(rule(".ai-notes-sidebar")).toContain("overflow-y: auto");
    expect(rule(".ai-notes-reader")).toContain("overflow-y: auto");
    expect(rule(".ai-notes-reader-notice")).toContain("max-width: 1040px");
    expect(rule(".ai-note-article")).toContain("max-width: 1040px");
    expect(rule(".ai-note-article")).toContain("padding: 48px 48px 80px");
    expect(rule(".article-markdown")).toContain("font-size: 17px");
    expect(rule(".article-markdown")).toContain("line-height: 1.78");
    expect(rule(".article-markdown h2")).toContain("font-size: 25px");
    expect(rule(".article-markdown h3")).toContain("font-size: 20px");
    expect(styles).toContain(".article-markdown h4 { font-size: 17px; }");
    expect(rule(".article-markdown code")).toContain("font-size: 14px");
    expect(rule(".article-markdown table")).toContain("font-size: 14px");
    expect(rule(".ai-note-signature")).toContain("font-family: ui-monospace");
    expect(rule(".ai-note-author")).toContain("font-weight: 800");
    expect(rule(".ai-note-author")).toContain("color: var(--ink)");
    expect(rule(".ai-note-motto")).toContain("color: var(--ink-faint)");
    expect(rule(".article-table-scroll")).toContain("overflow-x: auto");
    expect(rule(".mermaid-diagram-trigger")).toContain("cursor: zoom-in");
    expect(rule(".mermaid-diagram-trigger")).toContain("display: block");
    expect(rule(".mermaid-diagram-trigger")).toContain("width: 100%");
    expect(rule(".mermaid-diagram-trigger > img")).toContain("width: 100%");
    expect(rule(".mermaid-diagram-trigger > img")).toContain("max-height: min(720px, 70vh)");
    expect(rule(".mermaid-diagram-trigger > img")).toContain("object-fit: contain");
    expect(rule(".mermaid-lightbox")).toContain("height: 100dvh");
    expect(rule(".mermaid-lightbox")).toContain("background: #fff");
    expect(rule(".mermaid-lightbox-canvas")).toContain("touch-action: none");
    expect(rule(".mermaid-lightbox-image")).toContain("transform-origin: center");
    expect(rule(".mermaid-lightbox-close")).toContain("position: absolute");
    expect(styles).not.toContain(".mermaid-lightbox-toolbar");
    expect(styles).not.toContain(".mermaid-diagram-zoom-hint");
    expect(rule(".mermaid-diagram-trigger:focus-visible")).toContain("outline: 3px solid");
    expect(rule(`.ai-notes-category-toggle:focus-visible,
.ai-notes-files button:focus-visible,
.ai-notes-mobile-menu:focus-visible,
.ai-notes-drawer-close:focus-visible`)).toContain("outline: 3px solid");
    const aiNotesMobile = block(
      "@media (max-width: 720px) {\n  .ai-notes-layout { grid-template-columns: minmax(0, 1fr); }\n  .ai-notes-sidebar { display: none; }\n  .ai-notes-mobile-menu { position: fixed",
    );
    expect(aiNotesMobile).toContain(".ai-notes-layout { grid-template-columns: minmax(0, 1fr); }");
    expect(aiNotesMobile).toContain(".ai-notes-sidebar { display: none; }");
    expect(aiNotesMobile).toContain(".ai-notes-mobile-menu { position: fixed");
    expect(aiNotesMobile).toContain(".ai-note-article { padding: 38px 18px 72px; }");
    expect(aiNotesMobile).toContain(".article-markdown { font-size: 16px; }");
    expect(aiNotesMobile).toContain(".mermaid-diagram-trigger > img { max-height: 68svh; }");
    expect(block("@media print")).toContain(".mermaid-diagram-trigger > img { max-height: none; }");
    expect(block("@media print")).toContain(".mermaid-lightbox { display: none !important; }");
  });

  it("keeps the AI notes home entry quiet and keyboard visible", () => {
    expect(rule(".brain-home-toolbar")).toContain("display: flex");
    expect(rule(".brain-home-toolbar")).toContain("justify-content: flex-end");
    expect(rule(".brain-home-focus")).toContain("width: min(760px, 100%)");
    expect(rule(".brain-home-focus")).toContain("margin:");
    expect(rule(".brain-ai-notes-entry")).toContain("display: inline-flex");
    expect(rule(".brain-ai-notes-entry")).toContain("min-height: 36px");
    expect(rule(".brain-ai-notes-entry")).not.toContain("position: fixed");
    expect(rule(".brain-ai-notes-entry:focus-visible")).toContain("outline: 3px solid");
    expect(rule(".brain-home-focus > h1")).toContain("font-size: clamp(32px, 5vw, 44px)");
    expect(rule(".brain-composer-actions")).toContain("justify-content: flex-end");
    const mobile = lastBlock("@media (max-width: 720px)");
    expect(mobile).toContain(".brain-home-toolbar");
    expect(mobile).toContain(".brain-home-focus");
    expect(mobile).toContain(".brain-home-focus .brain-composer { margin-top: 23px; }");
  });
});
