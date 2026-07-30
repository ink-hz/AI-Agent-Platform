import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MessageMarkdown } from "./components/MessageMarkdown";
import { TurnCard } from "./components/TurnCard";
import { formatMessageTime } from "./messageTime";
import type { TurnDetail } from "./types";


const turn: TurnDetail = {
  turn_key: "fae:turn-1", session_key: "fae:session-1", agent_id: "ai-fae-agent",
  source_kind: "fae", turn_index: 1, question: "## 用户问题",
  answer: "- 第一步\n- 第二步", created_at: "2026-07-21T09:00:00Z",
  question_at: "2026-07-29T07:28:32Z", answer_at: "2026-07-29T07:28:37Z",
  question_time_status: "exact", answer_time_status: "exact",
  trace_key: null, outcome: "resolved", fallback_used: false, duration_ms: 1250,
  sources: [], evidence: [], evidence_availability: "available", feedback: [],
  reviews: [], improvements: [], details: {}, sender_name: null,
  sender_department: null, sender_identity_status: "unavailable",
};


describe("Session message Markdown presentation", () => {
  it("renders headings, lists, tables, and fenced code", () => {
    const html = renderToStaticMarkup(
      <MessageMarkdown content={'## 标题\n\n- 项目一\n- 项目二\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```ts\nconst value = 1;\n```'} />,
    );

    expect(html).toContain("<h2>标题</h2>");
    expect(html).toContain("<ul>");
    expect(html).toContain('<div class="table-scroll"><table>');
    expect(html).toContain('<code class="language-ts">');
  });

  it("keeps generated links isolated from their opener", () => {
    const html = renderToStaticMarkup(
      <MessageMarkdown content="[文档](https://example.com/docs)" />,
    );

    expect(html).toContain('rel="noreferrer noopener"');
  });

  it("keeps raw script text non-executable", () => {
    const html = renderToStaticMarkup(
      <MessageMarkdown content={'<script>alert("x")</script>'} />,
    );

    expect(html).toContain("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("uses Markdown only for non-empty questions and answers", () => {
    const populated = renderToStaticMarkup(<TurnCard turn={turn} />);
    const empty = renderToStaticMarkup(<TurnCard turn={{ ...turn, question: "", answer: "" }} />);

    expect(populated).toContain('<div class="message-markdown"><h2>用户问题</h2></div>');
    expect(populated).toContain('<div class="message-markdown"><ul>');
    expect(empty).toContain("<p>未记录用户提问</p>");
    expect(empty).toContain("<p>未记录 Agent 回答</p>");
    expect(empty).not.toContain("message-markdown");
  });

  it("formats exact and estimated timestamps in Asia/Shanghai to the second", () => {
    expect(formatMessageTime("2026-07-29T07:28:32Z", "exact")).toEqual({
      label: "7月29日 15:28:32",
      dateTime: "2026-07-29T07:28:32Z",
    });
    expect(formatMessageTime("2026-07-29T07:28:32Z", "estimated")).toEqual({
      label: "约 7月29日 15:28:32",
      dateTime: "2026-07-29T07:28:32Z",
    });
  });

  it("reports unavailable and invalid timestamps without a datetime value", () => {
    expect(formatMessageTime(null, "unavailable")).toEqual({ label: "时间未记录" });
    expect(formatMessageTime("invalid", "exact")).toEqual({ label: "时间未记录" });
  });

  it("renders independent question and answer message times", () => {
    const html = renderToStaticMarkup(<TurnCard turn={turn} />);

    expect(html.match(/class="message-time"/g)).toHaveLength(2);
    expect(html).toContain('<time class="message-time" dateTime="2026-07-29T07:28:32Z">7月29日 15:28:32</time>');
    expect(html).toContain('<time class="message-time" dateTime="2026-07-29T07:28:37Z">7月29日 15:28:37</time>');
  });

  it("does not fall back to the Turn creation time", () => {
    const html = renderToStaticMarkup(<TurnCard turn={{
      ...turn,
      question_at: null,
      answer_at: null,
      question_time_status: "unavailable",
      answer_time_status: "unavailable",
    }} />);

    expect(html.match(/<time class="message-time">时间未记录<\/time>/g)).toHaveLength(2);
    expect(html).not.toContain('dateTime="2026-07-21T09:00:00Z"');
  });
});
