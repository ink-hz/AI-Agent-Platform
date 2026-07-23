import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TraceTimeline } from "./components/TraceTimeline";
import { TurnCard } from "./components/TurnCard";
import type { TraceDetail, TurnDetail } from "./types";


const trace: TraceDetail = {
  trace_key: "fae:trace-1", turn_key: "fae:turn-1", agent_id: "ai-fae-agent",
  source_kind: "fae", status: "completed", started_at: "2026-07-21T09:00:00Z",
  completed_at: "2026-07-21T09:00:01Z", duration_ms: 1250, engine: "loop",
  backend: "anthropic", model: "Claude Opus 4.8", input_tokens: 1200,
  output_tokens: 540, cost_usd: null, error_class: null, error_message: null,
  detail_availability: "available", source_synced_at: "2026-07-21T09:10:00Z",
  details: {}, steps: [{ step_key: "fae:stage:1", trace_key: "fae:trace-1",
    kind: "stage", name: "Evidence Retrieval", status: "completed", parent_step_key: null,
    seq: 1, started_at: "2026-07-21T09:00:00Z", duration_ms: 320,
    input_summary: { query_count: 2 }, output_summary: { source_count: 3 },
    safe_metadata: {}, error_summary: null }],
};


const turn: TurnDetail = {
  turn_key: "fae:turn-1", session_key: "fae:session-1", agent_id: "ai-fae-agent",
  source_kind: "fae", turn_index: 1, question: "如何排查设备连接？",
  answer: "请先确认 USB 枚举状态。", created_at: "2026-07-21T09:00:00Z",
  trace_key: "fae:trace-1", outcome: "resolved", fallback_used: false,
  duration_ms: 1250, sources: [], evidence: [], evidence_availability: "available",
  feedback: [], reviews: [], improvements: [], details: {}, sender_name: null,
  sender_department: null, sender_identity_status: "unavailable",
};


describe("Session Trace presentation", () => {
  it("separates Trace summary from ordered execution steps", () => {
    const html = renderToStaticMarkup(<TraceTimeline trace={trace} />);
    expect(html).toContain("Claude Opus 4.8");
    expect(html).toContain("1.25s");
    expect(html).toContain("Evidence Retrieval");
    expect(html).toContain("1,200 input");
  });

  it("preserves original question and answer text", () => {
    const html = renderToStaticMarkup(<TurnCard turn={turn} />);
    expect(html).toContain("如何排查设备连接？");
    expect(html).toContain("请先确认 USB 枚举状态。");
    expect(html).toContain("View Trace");
  });

  it("explains when engineering Trace detail is unavailable", () => {
    const html = renderToStaticMarkup(<TraceTimeline trace={{ ...trace, detail_availability: "unavailable" }} />);
    expect(html).toContain("Engineering Trace is not captured by this source");
    expect(html).toContain("Evidence Retrieval");
  });
});
