/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationReviewAttachment, ConversationReviewFeedback } from "../../api";
import { ConversationFeedbackInbox } from "./ConversationFeedbackInbox";


const feedback: ConversationReviewFeedback = {
  feedback_id: "feedback-1", conversation_id: "conversation-1", message_id: "message-2",
  turn_id: "turn-1", mission_id: null, agent_id: "hr-bot", conversation_title: "岗位搜索",
  question: "搜索研发岗位", answer: "以下是核验后的岗位。", rating: "unhelpful",
  reason: "source_timeliness", comment: "来源已经过期", triage_status: "pending_triage",
  triaged_by_internal_user_id: null, triaged_at: null, created_at: "2026-09-03T12:00:00Z",
  citations: [{ citation_key: "source-1", title: "招聘官网", url: "https://example.com/jobs", site: "example.com", retrieved_at: "2026-09-03T11:59:00Z", supports: ["岗位清单"] }],
};

const attachments: ConversationReviewAttachment[] = [{
  attachment_id: "attachment-1", conversation_id: "conversation-1", source: "agent",
  display_name: "岗位清单.xlsx", detected_mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  size_bytes: 4096, state: "ready", created_at: "2026-09-03T12:00:00Z",
  retained_until: "2027-09-03T12:00:00Z", processing_coverage: null,
  availability_reason: null, artifact_key: "jobs", version_no: 2, current: true,
}, {
  attachment_id: "attachment-2", conversation_id: "conversation-1", source: "user",
  display_name: "候选人.pdf", detected_mime: "application/pdf", size_bytes: 2048,
  state: "quarantined", created_at: "2026-09-03T11:00:00Z", retained_until: "2027-09-03T11:00:00Z",
  processing_coverage: null, availability_reason: "quarantined", artifact_key: null,
  version_no: null, current: false,
}];


describe("ConversationFeedbackInbox", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("shows context, citations and attachment availability, then records explicit triage", async () => {
    const triage = vi.fn().mockResolvedValue({ ...feedback, triage_status: "triaged" });
    const ticket = vi.fn().mockResolvedValue({ ticket: "opaque", expires_at: "2026-09-03T12:05:00Z", content_path: "/api/v1/attachments/content/opaque" });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    await act(async () => root.render(<ConversationFeedbackInbox actor="corp:owner" api={{
      feedback: vi.fn().mockResolvedValue({ items: [feedback], total: 1, limit: 100, offset: 0 }),
      attachments: vi.fn().mockResolvedValue(attachments), triage, ticket,
    }} />));

    expect(container.textContent).toContain("搜索研发岗位");
    expect(container.textContent).toContain("以下是核验后的岗位");
    expect(container.textContent).toContain("来源已经过期");
    expect(container.querySelector('a[href="https://example.com/jobs"]')).not.toBeNull();
    expect(container.textContent).toContain("岗位清单.xlsx");
    expect(container.textContent).toContain("已隔离，不可访问");

    const download = container.querySelector<HTMLButtonElement>('button[data-purpose="download"]');
    await act(async () => download?.click());
    expect(ticket).toHaveBeenCalledWith("attachment-1", "download", "corp:owner");
    expect(open).toHaveBeenCalledWith("/api/v1/attachments/content/opaque", "_blank", "noopener,noreferrer");

    await act(async () => container.querySelector<HTMLButtonElement>('button[data-triage="triaged"]')?.click());
    expect(triage).toHaveBeenCalledWith("feedback-1", "triaged", "corp:owner");
    expect(container.textContent).toContain("当前没有待分诊的网页会话反馈");
  });
});
