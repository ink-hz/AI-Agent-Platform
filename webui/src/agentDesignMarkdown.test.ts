import { expect, it } from "vitest";
import { prepareDesignMarkdown } from "./agentDesignMarkdown";
it("preserves source anchors without enabling raw HTML and ignores code headings", () => {
  const result = prepareDesignMarkdown('# Design\n\n<a id="overview"></a>\n\n## 总览\n\n```text\n# ignored\n```\n\n## 总览');
  expect(result.headings.map(h => h.id)).toEqual(['design', 'overview', '总览']);
  expect(result.headingIds[5]).toBe('overview');
  expect(result.markdown).not.toContain('<a id=');
  expect(result.markdown).toContain('# ignored');
});
it("makes repeated headings unique", () => {
  expect(prepareDesignMarkdown('## Same\n## Same').headings.map(h => h.id)).toEqual(['same', 'same-2']);
});
