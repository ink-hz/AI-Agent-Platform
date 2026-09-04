/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentAttachmentLimits } from "../../brainTypes";
import type { ConversationAttachment } from "../../conversationTypes";
import { AttachmentApiError } from "../../attachmentApi";
import { AttachmentUploader, type AttachmentUploadClient, type UploadQueueItem } from "./AttachmentUploader";


const limits: AgentAttachmentLimits = {
  max_file_bytes: 50,
  max_files_per_message: 2,
  max_bytes_per_message: 80,
  max_files_per_conversation: 3,
  max_bytes_per_conversation: 120,
};

function attachment(file: File): ConversationAttachment {
  return {
    attachmentId: `attachment-${file.name}`,
    conversationId: "conversation-1",
    source: "user",
    displayName: file.name,
    detectedMime: file.type,
    sizeBytes: file.size,
    sha256: null,
    state: "ready",
    stateReason: null,
    createdAt: "2026-09-03T10:00:00Z",
    retainedUntil: "2027-09-03T10:00:00Z",
    preview: null,
    coverage: null,
  };
}

function client(): AttachmentUploadClient {
  return {
    begin: vi.fn().mockImplementation(async (_conversationId, file: File) => ({
      uploadId: `upload-${file.name}`,
      attachmentId: `attachment-${file.name}`,
      conversationId: "conversation-1",
      displayName: file.name,
      declaredMime: file.type,
      declaredSize: file.size,
      state: "uploading",
      uploadedBytes: 0,
      expiresAt: "2026-09-04T10:00:00Z",
    })),
    upload: vi.fn().mockImplementation(async (uploadId: string, file: File) => ({
      uploadId,
      attachmentId: `attachment-${file.name}`,
      conversationId: "conversation-1",
      displayName: file.name,
      declaredMime: file.type,
      declaredSize: file.size,
      state: "uploading",
      uploadedBytes: file.size,
      expiresAt: "2026-09-04T10:00:00Z",
    })),
    complete: vi.fn().mockImplementation(async (_uploadId: string, _csrf: string, _signal?: AbortSignal, file?: File) => attachment(file!)),
    cancel: vi.fn().mockResolvedValue(undefined),
  };
}

function filesEvent(type: "drop" | "paste", files: File[]): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  const transfer = {
    files,
    items: files.map((file) => ({ kind: "file", type: file.type, getAsFile: () => file })),
  } as unknown as DataTransfer;
  Object.defineProperty(event, type === "drop" ? "dataTransfer" : "clipboardData", {
    value: transfer,
  });
  return event;
}

describe("AttachmentUploader", () => {
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
    vi.restoreAllMocks();
  });

  it("accepts click, drop, and pasted images and reports the real processing states", async () => {
    const api = client();
    const snapshots: UploadQueueItem[][] = [];
    await act(async () => root.render(<AttachmentUploader
      acceptedInputTypes={["text", "image", "pdf", "office"]}
      client={api}
      conversationId="conversation-1"
      csrfToken="csrf"
      limits={limits}
      onChange={(items) => snapshots.push(items)}
    />));

    const picked = new File(["resume"], "resume.pdf", { type: "application/pdf" });
    const input = container.querySelector<HTMLInputElement>("input[type='file']")!;
    Object.defineProperty(input, "files", { configurable: true, value: [picked] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));

    expect(api.begin).toHaveBeenCalledWith("conversation-1", picked, "csrf", expect.any(AbortSignal));
    expect(snapshots.flat().map((item) => item.state)).toEqual(expect.arrayContaining([
      "queued", "uploading", "processing", "ready",
    ]));
    expect(container.textContent).toContain("已就绪");

    const dropped = new File(["photo"], "photo.png", { type: "image/png" });
    await act(async () => container.querySelector(".attachment-dropzone")
      ?.dispatchEvent(filesEvent("drop", [dropped])));
    expect(api.begin).toHaveBeenCalledWith("conversation-1", dropped, "csrf", expect.any(AbortSignal));

    const pasted = new File(["shot"], "clipboard.png", { type: "image/png" });
    await act(async () => container.querySelector(".attachment-uploader")
      ?.dispatchEvent(filesEvent("paste", [pasted])));
    expect(container.textContent).toContain("每条消息最多 2 个文件");
  });

  it("retries one failed item and removes it without deleting other files", async () => {
    const api = client();
    vi.mocked(api.upload)
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockImplementationOnce(async (uploadId: string, file: File) => ({
        uploadId, attachmentId: `attachment-${file.name}`, conversationId: "conversation-1",
        displayName: file.name, declaredMime: file.type, declaredSize: file.size,
        state: "uploading", uploadedBytes: file.size, expiresAt: "2026-09-04T10:00:00Z",
      }));
    await act(async () => root.render(<AttachmentUploader
      acceptedInputTypes={["text", "pdf"]}
      client={api}
      conversationId="conversation-1"
      csrfToken="csrf"
      limits={limits}
      onChange={vi.fn()}
    />));
    const file = new File(["resume"], "resume.pdf", { type: "application/pdf" });
    const input = container.querySelector<HTMLInputElement>("input[type='file']")!;
    Object.defineProperty(input, "files", { configurable: true, value: [file] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));

    expect(container.textContent).toContain("上传失败");
    await act(async () => [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "重试")?.click());
    expect(container.textContent).toContain("已就绪");
    expect(api.begin).toHaveBeenCalledTimes(2);

    await act(async () => [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "移除")?.click());
    expect(container.textContent).not.toContain("resume.pdf");
  });

  it("shows a safe retry message when content upload conflicts", async () => {
    const api = client();
    vi.mocked(api.upload).mockRejectedValueOnce(new AttachmentApiError(409, {
      detail: "declared and streamed content sizes differ",
    }));
    await act(async () => root.render(<AttachmentUploader
      acceptedInputTypes={["pdf"]}
      client={api}
      conversationId="conversation-1"
      csrfToken="csrf"
      limits={limits}
    />));
    const file = new File(["resume"], "resume.pdf", { type: "application/pdf" });
    const input = container.querySelector<HTMLInputElement>("input[type='file']")!;
    Object.defineProperty(input, "files", { configurable: true, value: [file] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));

    expect(container.textContent).toContain("文件传输失败，请重试");
    expect(container.textContent).not.toContain("Attachment API 409");
  });

  it("keeps the default uploader quota disclosure", async () => {
    await act(async () => root.render(<AttachmentUploader
      conversationId="conversation-1"
      csrfToken="csrf"
      limits={limits}
    />));

    expect(container.textContent).toContain("支持选择、拖放或粘贴；单条最多");
  });

  it("keeps an empty HR compact queue free of quota disclosure", async () => {
    await act(async () => root.render(<AttachmentUploader
      compact
      conversationId="conversation-1"
      csrfToken="csrf"
      limits={limits}
    />));

    expect(container.textContent).not.toContain("未选择任何文件");
    expect(container.textContent).not.toContain("支持选择、拖放或粘贴；单条最多");
  });

  it("restarts a restored pending upload instead of leaving a frozen queue item", async () => {
    const api = client();
    const file = new File(["resume"], "restored.pdf", { type: "application/pdf" });
    await act(async () => root.render(<AttachmentUploader
      acceptedInputTypes={["pdf"]}
      client={api}
      conversationId="conversation-1"
      csrfToken="csrf"
      initialItems={[{ localId: "restored-upload", file, progress: 35, state: "uploading", uploadId: "stale-upload" }]}
      limits={limits}
    />));

    expect(api.begin).toHaveBeenCalledWith("conversation-1", file, "csrf", expect.any(AbortSignal));
    expect(container.querySelector('.conversation-upload-chip[data-state="ready"]')?.textContent).toContain("restored.pdf");
  });

  it("rejects type, per-file, per-message, and Session quota overflow before upload", async () => {
    const api = client();
    await act(async () => root.render(<AttachmentUploader
      acceptedInputTypes={["text", "image", "pdf", "office"]}
      client={api}
      conversationBytes={100}
      conversationFileCount={2}
      conversationId="conversation-1"
      csrfToken="csrf"
      limits={limits}
      onChange={vi.fn()}
    />));
    const input = container.querySelector<HTMLInputElement>("input[type='file']")!;
    const tooLarge = new File(["x".repeat(51)], "large.pdf", { type: "application/pdf" });
    Object.defineProperty(input, "files", { configurable: true, value: [tooLarge] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));

    expect(container.textContent).toContain("单个文件不能超过");
    expect(api.begin).not.toHaveBeenCalled();

    const archive = new File(["zip"], "archive.zip", { type: "application/zip" });
    Object.defineProperty(input, "files", { configurable: true, value: [archive] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
    expect(container.textContent).toContain("不支持这种文件类型");
  });
});
