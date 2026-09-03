/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationAttachment } from "../../conversationTypes";
import { AttachmentCard } from "./AttachmentCard";


const attachment: ConversationAttachment = {
  attachmentId: "attachment-1", conversationId: "conversation-1", source: "user",
  displayName: "候选人简历.pdf", detectedMime: "application/pdf", sizeBytes: 2048,
  sha256: null, state: "ready", stateReason: null,
  createdAt: "2026-09-03T10:00:00Z", retainedUntil: "2027-09-03T10:00:00Z",
  preview: { attachmentId: "attachment-1", detectedMime: "application/pdf" },
  coverage: { pages: 1, sheets: null, slides: null, ocrComplete: null },
};

describe("AttachmentCard", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("shows useful file state and separates enable, preview, and destructive delete", async () => {
    const onActiveChange = vi.fn();
    const onPreview = vi.fn();
    const onDelete = vi.fn().mockResolvedValue(undefined);
    await act(async () => root.render(<AttachmentCard
      active
      attachment={attachment}
      onActiveChange={onActiveChange}
      onDelete={onDelete}
      onPreview={onPreview}
    />));

    expect(container.textContent).toContain("候选人简历.pdf");
    expect(container.textContent).toContain("2 KB");
    expect(container.textContent).toContain("可使用");
    expect(container.textContent).toContain("已读取 1 页");
    await act(async () => container.querySelector<HTMLInputElement>("input[type='checkbox']")?.click());
    expect(onActiveChange).toHaveBeenCalledWith(false);
    await act(async () => [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "预览")?.click());
    expect(onPreview).toHaveBeenCalledWith(attachment);

    await act(async () => [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "删除")?.click());
    expect(onDelete).not.toHaveBeenCalled();
    expect(container.textContent).toContain("确认删除");
    await act(async () => [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "确认删除")?.click());
    expect(onDelete).toHaveBeenCalledWith(attachment);
  });
});
