/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationAttachment } from "../conversationTypes";
import { ArtifactVersionList } from "../components/conversation/ArtifactVersionList";
import { ConversationComposer } from "../components/conversation/ConversationComposer";
import { MessageActions } from "../components/conversation/MessageActions";

const attachment = (id: string, name: string, mime = "application/pdf"): ConversationAttachment => ({
  attachmentId: id,
  conversationId: "hr-conversation-1",
  source: "agent",
  displayName: name,
  detectedMime: mime,
  sizeBytes: 2048,
  sha256: null,
  state: "ready",
  stateReason: null,
  createdAt: "2026-09-03T10:00:00Z",
  retainedUntil: "2027-09-03T10:00:00Z",
  preview: mime === "application/pdf" ? { attachmentId: id, detectedMime: mime } : null,
  coverage: null,
});

describe("HR workspace core acceptance", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("keeps the HR composer usable for text or ready attachments", async () => {
    const onSubmit = vi.fn();
    await act(async () => root.render(<ConversationComposer
      attachmentControls={<button type="button">上传附件</button>}
      disabled={false}
      hasReadyAttachment
      onChange={vi.fn()}
      onSubmit={onSubmit}
      pending={false}
      value=""
    />));

    expect(container.textContent).toContain("上传附件");
    const send = [...container.querySelectorAll("button")].find((button) => button.textContent === "✨ 发送")!;
    expect(send.disabled).toBe(false);
    await act(async () => send.click());
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("supports copying and captures a reason plus free-text details for downvotes", async () => {
    const onCopy = vi.fn().mockResolvedValue(true);
    const onFeedback = vi.fn();
    await act(async () => root.render(<MessageActions
      copyText={() => "候选人分析结果"}
      feedbackState={undefined}
      onCopy={onCopy}
      onFeedback={onFeedback}
    />));

    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "复制")?.click());
    expect(onCopy).toHaveBeenCalledWith("候选人分析结果");
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "需改进")?.click());
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "来源或时效有问题")?.click());
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "岗位信息已过期");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "提交反馈")?.click());
    expect(onFeedback).toHaveBeenCalledWith("unhelpful", "source_timeliness", "岗位信息已过期");
  });

  it("offers current-result downloads while retaining failed versions as evidence", async () => {
    const onOpen = vi.fn();
    const onDownloadAll = vi.fn();
    await act(async () => root.render(<ArtifactVersionList
      onDownloadAll={onDownloadAll}
      onOpen={onOpen}
      versions={[
        { artifactKey: "candidate-report", versionNo: 1, producerVersionId: "p1", current: false, status: "ready", attachment: attachment("a0", "候选人报告-v1.pdf") },
        { artifactKey: "candidate-report", versionNo: 2, producerVersionId: "p2", current: true, status: "ready", attachment: attachment("a1", "候选人报告-v2.pdf") },
        { artifactKey: "candidate-report", versionNo: 3, producerVersionId: "p3", current: false, status: "failed", attachment: null },
      ]}
    />));

    expect(container.textContent?.match(/当前版本/g)).toHaveLength(1);
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("所有版本"))?.click());
    expect(container.textContent).toContain("版本 3");
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "下载")?.click());
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ attachmentId: "a1" }), "download");
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "全部下载")?.click());
    expect(onDownloadAll).toHaveBeenCalledTimes(1);
  });
});
