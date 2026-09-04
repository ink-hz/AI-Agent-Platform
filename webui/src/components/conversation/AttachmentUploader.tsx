import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type DragEvent,
} from "react";

import {
  attachmentUploadErrorMessage,
  beginAttachmentUpload,
  cancelAttachmentUpload,
  completeAttachmentUpload,
  fetchConversationAttachment,
  uploadAttachmentContent,
  type AttachmentUploadStage,
  type AttachmentUpload,
} from "../../attachmentApi";
import type { AgentAttachmentLimits, AgentContentType } from "../../brainTypes";
import type { ConversationAttachment } from "../../conversationTypes";


export interface UploadQueueItem {
  localId: string;
  file: File;
  progress: number;
  state: "queued" | "uploading" | "processing" | "ready" | "failed";
  attachment?: ConversationAttachment;
  uploadId?: string;
  error?: unknown;
  errorMessage?: string;
}

export interface AttachmentUploadClient {
  begin(
    conversationId: string | null,
    file: File,
    csrfToken: string,
    signal?: AbortSignal,
  ): Promise<AttachmentUpload>;
  upload(
    uploadId: string,
    file: File,
    csrfToken: string,
    signal?: AbortSignal,
  ): Promise<AttachmentUpload>;
  complete(
    uploadId: string,
    csrfToken: string,
    signal?: AbortSignal,
    file?: File,
  ): Promise<ConversationAttachment>;
  status?(attachmentId: string, signal?: AbortSignal): Promise<ConversationAttachment>;
  cancel(uploadId: string, csrfToken: string, signal?: AbortSignal): Promise<void>;
}

export interface AttachmentUploaderHandle {
  addFiles(files: File[]): void;
}

const DEFAULT_CLIENT: AttachmentUploadClient = {
  begin: beginAttachmentUpload,
  upload: uploadAttachmentContent,
  complete: completeAttachmentUpload,
  status: fetchConversationAttachment,
  cancel: cancelAttachmentUpload,
};

const OFFICE_EXTENSIONS = new Set(["doc", "docx", "xls", "xlsx", "ppt", "pptx"]);

function extension(name: string): string {
  const index = name.lastIndexOf(".");
  return index < 0 ? "" : name.slice(index + 1).toLowerCase();
}

function contentType(file: File): AgentContentType | null {
  const suffix = extension(file.name);
  if (file.type.startsWith("image/") || ["png", "jpg", "jpeg", "webp"].includes(suffix)) return "image";
  if (file.type === "application/pdf" || suffix === "pdf") return "pdf";
  if (OFFICE_EXTENSIONS.has(suffix) || /officedocument|msword|ms-excel|ms-powerpoint/.test(file.type)) return "office";
  if (file.type.startsWith("text/") || ["txt", "md", "csv"].includes(suffix)) return "text";
  return null;
}

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  const mb = bytes / 1024 / 1024;
  return `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB`;
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

export const AttachmentUploader = forwardRef<AttachmentUploaderHandle, {
  acceptedInputTypes?: AgentContentType[];
  client?: AttachmentUploadClient;
  conversationBytes?: number;
  conversationFileCount?: number;
  conversationId: string | null;
  compact?: boolean;
  csrfToken: string;
  disabled?: boolean;
  limits?: AgentAttachmentLimits;
  initialItems?: UploadQueueItem[];
  onChange?: (items: UploadQueueItem[]) => void;
  onError?: (message: string | null) => void;
  onReady?: (attachment: ConversationAttachment) => void;
  onRemoveReady?: (attachment: ConversationAttachment) => void;
  onQueueChange?: (items: UploadQueueItem[]) => void;
}>(function AttachmentUploader({
  acceptedInputTypes = ["text", "image", "pdf", "office"],
  client = DEFAULT_CLIENT,
  conversationBytes = 0,
  conversationFileCount = 0,
  conversationId,
  compact = false,
  csrfToken,
  disabled = false,
  limits = {
    max_file_bytes: 50 * 1024 * 1024,
    max_files_per_message: 5,
    max_bytes_per_message: 50 * 1024 * 1024,
    max_files_per_conversation: 50,
    max_bytes_per_conversation: 500 * 1024 * 1024,
  },
  initialItems = [],
  onChange,
  onError,
  onReady,
  onRemoveReady,
  onQueueChange,
}, forwardedRef) {
  const [items, setItems] = useState<UploadQueueItem[]>(() => initialItems.map((item) => ({ ...item })));
  const [validationError, setValidationError] = useState<string | null>(null);
  const itemsRef = useRef<UploadQueueItem[]>(items);
  const controllers = useRef(new Map<string, AbortController>());

  useEffect(() => () => {
    for (const controller of controllers.current.values()) controller.abort();
  }, []);

  const publish = (next: UploadQueueItem[]) => {
    itemsRef.current = next;
    setItems(next);
    onChange?.(next.map((item) => ({ ...item })));
    onQueueChange?.(next.map((item) => ({ ...item })));
  };
  const update = (localId: string, values: Partial<UploadQueueItem>) => {
    publish(itemsRef.current.map((item) => item.localId === localId ? { ...item, ...values } : item));
  };
  const showError = (message: string | null) => {
    setValidationError(message);
    onError?.(message);
  };

  const process = async (localId: string) => {
    const item = itemsRef.current.find((candidate) => candidate.localId === localId);
    if (!item) return;
    const controller = new AbortController();
    controllers.current.set(localId, controller);
    let stage: AttachmentUploadStage = "begin";
    try {
      if (item.uploadId) {
        try {
          await client.cancel(item.uploadId, csrfToken, controller.signal);
        } catch {
          if (controller.signal.aborted) return;
          // Cancellation is best effort: an expired upload must not block a clean restart.
        }
      }
      if (controller.signal.aborted) return;
      update(localId, {
        error: undefined, errorMessage: undefined, progress: 10, state: "uploading", uploadId: undefined,
      });
      const begun = await client.begin(conversationId, item.file, csrfToken, controller.signal);
      update(localId, { progress: 35, uploadId: begun.uploadId });
      stage = "content";
      await client.upload(begun.uploadId, item.file, csrfToken, controller.signal);
      update(localId, { progress: 70, state: "processing" });
      stage = "complete";
      let attachment = await client.complete(begun.uploadId, csrfToken, controller.signal, item.file);
      update(localId, { attachment, progress: attachment.state === "ready" ? 100 : 85 });
      stage = "processing";
      for (let attempt = 0; ["validating", "scanning"].includes(attachment.state); attempt += 1) {
        if (!client.status || attempt >= 300) throw new Error("文件处理超时，请稍后重试");
        await wait(1000, controller.signal);
        attachment = await client.status(attachment.attachmentId, controller.signal);
        update(localId, { attachment, progress: attachment.state === "ready" ? 100 : 90 });
      }
      if (attachment.state !== "ready") {
        throw new Error(attachment.stateReason || "文件未通过安全处理");
      }
      update(localId, { attachment, error: undefined, errorMessage: undefined, progress: 100, state: "ready" });
      onReady?.(attachment);
      showError(null);
    } catch (error) {
      if (!controller.signal.aborted) {
        const message = attachmentUploadErrorMessage(error, stage);
        update(localId, { error, errorMessage: message, state: "failed" });
        showError(message);
      }
    } finally {
      if (controllers.current.get(localId) === controller) controllers.current.delete(localId);
    }
  };

  useEffect(() => {
    for (const item of itemsRef.current) {
      if (["queued", "uploading", "processing"].includes(item.state)) void process(item.localId);
    }
    // Restored queue items are resumed only once when this uploader mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const validate = (selected: File[]): string | null => {
    const existingQueue = itemsRef.current.filter((item) => item.state !== "failed");
    const unsupported = selected.find((file) => {
      const detected = contentType(file);
      return detected === null || !acceptedInputTypes.includes(detected);
    });
    if (unsupported) return `不支持这种文件类型：${unsupported.name}`;
    const oversized = selected.find((file) => file.size <= 0 || file.size > limits.max_file_bytes);
    if (oversized) return `单个文件不能超过 ${sizeLabel(limits.max_file_bytes)}`;
    if (existingQueue.length + selected.length > limits.max_files_per_message) {
      return `每条消息最多 ${limits.max_files_per_message} 个文件`;
    }
    const selectedBytes = selected.reduce((sum, file) => sum + file.size, 0);
    const queuedBytes = existingQueue.reduce((sum, item) => sum + item.file.size, 0);
    if (queuedBytes + selectedBytes > limits.max_bytes_per_message) {
      return `每条消息附件合计不能超过 ${sizeLabel(limits.max_bytes_per_message)}`;
    }
    if (conversationFileCount + existingQueue.length + selected.length > limits.max_files_per_conversation) {
      return `本次会话最多保留 ${limits.max_files_per_conversation} 个用户文件`;
    }
    if (conversationBytes + queuedBytes + selectedBytes > limits.max_bytes_per_conversation) {
      return `本次会话用户文件合计不能超过 ${sizeLabel(limits.max_bytes_per_conversation)}`;
    }
    return null;
  };

  const addFiles = (selected: File[]) => {
    if (disabled || selected.length === 0) return;
    const error = validate(selected);
    if (error) {
      showError(error);
      return;
    }
    showError(null);
    const created = selected.map((file) => ({
      localId: crypto.randomUUID(),
      file,
      progress: 0,
      state: "queued" as const,
    }));
    publish([...itemsRef.current, ...created]);
    for (const item of created) void process(item.localId);
  };
  useImperativeHandle(forwardedRef, () => ({ addFiles }));

  const retry = (item: UploadQueueItem) => {
    void process(item.localId);
  };
  const remove = async (item: UploadQueueItem) => {
    controllers.current.get(item.localId)?.abort();
    if (item.state !== "ready" && item.uploadId) {
      try { await client.cancel(item.uploadId, csrfToken); } catch { /* removal stays local */ }
    }
    if (item.state === "ready" && item.attachment) onRemoveReady?.(item.attachment);
    publish(itemsRef.current.filter((candidate) => candidate.localId !== item.localId));
  };
  const change = (event: ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  };
  const drop = (event: DragEvent) => {
    event.preventDefault();
    addFiles(Array.from(event.dataTransfer.files));
  };
  const paste = (event: ClipboardEvent) => {
    const files = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    if (files.length > 0) {
      event.preventDefault();
      addFiles(files);
    }
  };

  return <section
    className={`attachment-uploader attachment-dropzone conversation-uploader${compact ? " is-compact" : ""}`}
    onDragOver={(event) => event.preventDefault()}
    onDrop={drop}
    onPaste={paste}
  >
    <div className="conversation-upload-toolbar">
      <label className="conversation-upload-button">
        <input
          accept="image/*,.pdf,.txt,.md,.csv,.doc,.docx,.xls,.xlsx,.ppt,.pptx"
          disabled={disabled}
          multiple
          onChange={change}
          type="file"
        />
        <span aria-hidden="true">＋</span> 添加文件或图片
      </label>
    </div>
    {!compact && <small>支持选择、拖放或粘贴；单条最多 {limits.max_files_per_message} 个、合计 {sizeLabel(limits.max_bytes_per_message)}</small>}
    {validationError && <p className="conversation-upload-error" role="alert">{validationError}</p>}
    {items.length > 0 && <ul className="conversation-upload-queue">{items.map((item) => <li className="conversation-upload-chip" key={item.localId} data-state={item.state}>
      <div><strong>{item.file.name}</strong><span>{item.state === "failed"
        ? `上传失败：${item.errorMessage ?? "请重试"}`
        : item.state === "ready" ? "已就绪"
          : item.state === "processing" ? "正在安全处理" : "正在上传"}</span></div>
      <progress max={100} value={item.progress} />
      <div className="conversation-upload-actions">
        {item.state === "failed" && <button onClick={() => void retry(item)} type="button">重试</button>}
        {(item.state === "failed" || item.state === "ready") && <button onClick={() => void remove(item)} type="button">移除</button>}
      </div>
    </li>)}</ul>}
  </section>;
});
