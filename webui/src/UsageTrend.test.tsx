import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageTrend } from "./UsageTrend";


describe("UsageTrend", () => {
  it("renders a seven-day conversation chart with accessible values", () => {
    const html = renderToStaticMarkup(
      <UsageTrend
        trend={[
          { date: "2026-07-15", conversations: 2 },
          { date: "2026-07-16", conversations: 5 },
          { date: "2026-07-17", conversations: 0 },
          { date: "2026-07-18", conversations: 8 },
          { date: "2026-07-19", conversations: 3 },
          { date: "2026-07-20", conversations: 12 },
          { date: "2026-07-21", conversations: 7 },
        ]}
      />,
    );

    expect(html).toContain("近 7 天对话趋势");
    expect(html).toContain("07/21");
    expect(html).toContain("12 次对话");
    expect(html).toContain("aria-label=\"2026-07-21，7 次对话\"");
  });

  it("renders a calm empty chart when every day is zero", () => {
    const html = renderToStaticMarkup(
      <UsageTrend trend={[{ date: "2026-07-21", conversations: 0 }]} />,
    );

    expect(html).toContain("等待新的真实对话数据");
  });
});
