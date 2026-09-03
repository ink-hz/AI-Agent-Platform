/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationAttachment } from "../../conversationTypes";
import { ArtifactVersionList } from "./ArtifactVersionList";

const attachment = (id: string, name: string, mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"): ConversationAttachment => ({
  attachmentId: id, conversationId: "conversation-1", source: "agent", displayName: name,
  detectedMime: mime, sizeBytes: 2048, sha256: null, state: "ready", stateReason: null,
  createdAt: "2026-09-03T10:00:00Z", retainedUntil: "2027-09-03T10:00:00Z",
  preview: mime === "application/pdf" ? { attachmentId: id, detectedMime: mime } : null,
  coverage: null,
});

describe("ArtifactVersionList", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("shows prominent downloads, safe previews, all versions, and bulk download", async () => {
    const onOpen = vi.fn(); const onDownloadAll = vi.fn();
    await act(async () => root.render(<ArtifactVersionList
      onDownloadAll={onDownloadAll} onOpen={onOpen}
      versions={[
        { artifactKey: "report", versionNo: 1, producerVersionId: "p1", current: false, status: "ready", attachment: attachment("a1", "人才报告-v1.pptx") },
        { artifactKey: "report", versionNo: 2, producerVersionId: "p2", current: true, status: "ready", attachment: attachment("a2", "人才报告-v2.pdf", "application/pdf") },
        { artifactKey: "report", versionNo: 3, producerVersionId: "p3", current: false, status: "failed", attachment: null },
      ]}
    />));
    expect(container.textContent).toContain("全部下载");
    expect(container.textContent).toContain("当前版本");
    expect(container.textContent).not.toContain("版本 1");
    await act(async () => [...container.querySelectorAll("button")].find((item) => item.textContent?.includes("所有版本"))?.click());
    expect(container.textContent).toContain("版本 1");
    expect(container.textContent).toContain("版本 3");
    expect(container.textContent?.match(/当前版本/g)).toHaveLength(1);
    expect([...container.querySelectorAll("button")].filter((item) => item.textContent === "预览")).toHaveLength(1);
    await act(async () => [...container.querySelectorAll("button")].find((item) => item.textContent === "下载")?.click());
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ attachmentId: "a2" }), "download");
    await act(async () => [...container.querySelectorAll("button")].find((item) => item.textContent === "全部下载")?.click());
    expect(onDownloadAll).toHaveBeenCalledTimes(1);
  });
});
