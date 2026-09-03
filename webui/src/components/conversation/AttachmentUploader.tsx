import { useEffect, useRef, useState, type ChangeEvent, type ClipboardEvent, type DragEvent } from "react";
import { beginAttachmentUpload, cancelAttachmentUpload, completeAttachmentUpload, fetchConversationAttachment, uploadAttachmentContent, type AttachmentUpload } from "../../attachmentApi";
import type { AgentAttachmentLimits, AgentContentType } from "../../brainTypes";
import type { ConversationAttachment } from "../../conversationTypes";

export interface UploadQueueItem {
  localId: string; file: File; progress: number;
  state: "queued" | "uploading" | "processing" | "ready" | "failed";
  attachment?: ConversationAttachment; uploadId?: string; error?: string;
}
export interface AttachmentUploadClient {
  begin(conversationId: string | null, file: File, csrfToken: string, signal?: AbortSignal): Promise<AttachmentUpload>;
  upload(uploadId: string, file: File, csrfToken: string, signal?: AbortSignal): Promise<AttachmentUpload>;
  complete(uploadId: string, csrfToken: string, signal?: AbortSignal, file?: File): Promise<ConversationAttachment>;
  cancel(uploadId: string, csrfToken: string, signal?: AbortSignal): Promise<void>;
  status?(attachmentId: string, signal?: AbortSignal): Promise<ConversationAttachment>;
}
const DEFAULT_CLIENT: AttachmentUploadClient = { begin: beginAttachmentUpload, upload: uploadAttachmentContent, complete: completeAttachmentUpload, cancel: cancelAttachmentUpload, status: fetchConversationAttachment };
const DEFAULT_LIMITS: AgentAttachmentLimits = {
  max_file_bytes: 50 * 1024 * 1024, max_files_per_message: 5, max_bytes_per_message: 50 * 1024 * 1024,
  max_files_per_conversation: 50, max_bytes_per_conversation: 500 * 1024 * 1024,
};

function accepted(file: File, types: AgentContentType[]): boolean {
  const name = file.name.toLowerCase();
  if (types.includes("image") && file.type.startsWith("image/")) return true;
  if (types.includes("pdf") && (file.type === "application/pdf" || name.endsWith(".pdf"))) return true;
  if (types.includes("text") && (file.type.startsWith("text/") || name.endsWith(".txt"))) return true;
  return types.includes("office") && (/(\.docx?|\.xlsx?|\.pptx?)$/.test(name) || /officedocument|msword|ms-excel|ms-powerpoint/.test(file.type));
}
function delay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => { const timer = window.setTimeout(resolve, 1000); signal.addEventListener("abort", () => { window.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true }); });
}

export function AttachmentUploader({ conversationId, csrfToken, disabled = false,
  acceptedInputTypes = ["text", "image", "pdf", "office"], limits = DEFAULT_LIMITS,
  conversationBytes = 0, conversationFileCount = 0, onChange, onReady, onError, onQueueChange,
  client = DEFAULT_CLIENT }: {
  conversationId: string | null; csrfToken: string; disabled?: boolean;
  acceptedInputTypes?: AgentContentType[]; limits?: AgentAttachmentLimits;
  conversationBytes?: number; conversationFileCount?: number;
  onChange?: (items: UploadQueueItem[]) => void; onReady?: (attachment: ConversationAttachment) => void;
  onError?: (message: string) => void; onQueueChange?: (items: UploadQueueItem[]) => void;
  client?: AttachmentUploadClient;
}) {
  const [items, setItems] = useState<UploadQueueItem[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const controllers = useRef(new Map<string, AbortController>());
  useEffect(() => () => controllers.current.forEach((controller) => controller.abort()), []);
  const publish = (next: UploadQueueItem[]) => { onChange?.(next.map((item) => ({ ...item }))); onQueueChange?.(next); return next; };
  const update = (localId: string, values: Partial<UploadQueueItem>) => setItems((current) => publish(current.map((item) => item.localId === localId ? { ...item, ...values } : item)));
  const process = async (item: UploadQueueItem) => {
    const controller = new AbortController(); controllers.current.set(item.localId, controller);
    try {
      update(item.localId, { state: "uploading", progress: 10, error: undefined });
      const begun = await client.begin(conversationId, item.file, csrfToken, controller.signal);
      update(item.localId, { uploadId: begun.uploadId, progress: 35 });
      await client.upload(begun.uploadId, item.file, csrfToken, controller.signal);
      update(item.localId, { state: "processing", progress: 70 });
      let attachment = await client.complete(begun.uploadId, csrfToken, controller.signal, item.file);
      update(item.localId, { attachment, state: attachment.state === "ready" ? "ready" : "processing", progress: attachment.state === "ready" ? 100 : 85 });
      for (let attempt = 0; client.status && attachment.state !== "ready" && ["validating", "scanning"].includes(attachment.state) && attempt < 300; attempt += 1) {
        await delay(controller.signal); attachment = await client.status(attachment.attachmentId, controller.signal);
        update(item.localId, { attachment, state: attachment.state === "ready" ? "ready" : "processing", progress: attachment.state === "ready" ? 100 : 90 });
      }
      if (attachment.state !== "ready") throw new Error("文件处理未完成");
      onReady?.(attachment); setMessage(null);
    } catch (error) {
      if (!controller.signal.aborted) { const detail = error instanceof Error ? error.message : "上传失败"; update(item.localId, { state: "failed", error: detail }); setMessage("上传失败，请重试"); onError?.("上传失败，请重试"); }
    } finally { controllers.current.delete(item.localId); }
  };
  const addFiles = (selected: File[]) => {
    if (disabled || selected.length === 0) return;
    const live = items.filter((item) => item.state !== "failed");
    const totalBytes = live.reduce((sum, item) => sum + item.file.size, 0) + selected.reduce((sum, file) => sum + file.size, 0);
    let error: string | null = null;
    if (selected.some((file) => file.size > limits.max_file_bytes)) error = `单个文件不能超过 ${limits.max_file_bytes} 字节`;
    else if (selected.some((file) => !accepted(file, acceptedInputTypes))) error = "不支持这种文件类型";
    else if (live.length + selected.length > limits.max_files_per_message) error = `每条消息最多 ${limits.max_files_per_message} 个文件`;
    else if (totalBytes > limits.max_bytes_per_message) error = "本条消息附件总大小超过限制";
    else if (conversationFileCount + live.length + selected.length > limits.max_files_per_conversation || conversationBytes + totalBytes > limits.max_bytes_per_conversation) error = "当前会话的文件容量已达到上限";
    if (error) { setMessage(error); onError?.(error); return; }
    const created = selected.map((file) => ({ localId: crypto.randomUUID(), file, progress: 0, state: "queued" as const }));
    setItems((current) => publish([...current, ...created])); setMessage(null); created.forEach((item) => void process(item));
  };
  const remove = (localId: string) => setItems((current) => publish(current.filter((item) => item.localId !== localId)));
  const retry = (item: UploadQueueItem) => { update(item.localId, { state: "queued", progress: 0, error: undefined, uploadId: undefined }); void process(item); };
  const change = (event: ChangeEvent<HTMLInputElement>) => { addFiles([...event.target.files ?? []]); event.target.value = ""; };
  const drop = (event: DragEvent) => { event.preventDefault(); addFiles([...event.dataTransfer.files]); };
  const paste = (event: ClipboardEvent) => { const files = [...event.clipboardData.files].filter((file) => file.type.startsWith("image/")); if (files.length) { event.preventDefault(); addFiles(files); } };
  return <section className="attachment-uploader conversation-uploader" onPaste={paste}>
    <div className="attachment-dropzone" onDragOver={(event) => event.preventDefault()} onDrop={drop}><label className="conversation-upload-button"><input disabled={disabled} multiple onChange={change} type="file" /><span aria-hidden="true">＋</span> 添加文件或图片</label><small>支持选择、拖放或粘贴</small></div>
    {message && <p className="attachment-upload-message" role="alert">{message}</p>}
    {items.length > 0 && <ul className="conversation-upload-queue">{items.map((item) => <li key={item.localId} data-state={item.state}><div><strong>{item.file.name}</strong><span>{item.state === "failed" ? "上传失败" : item.state === "ready" ? "已就绪" : item.state === "processing" ? "正在安全处理" : item.state === "queued" ? "等待上传" : "正在上传"}</span></div><progress max={100} value={item.progress} />{item.state === "failed" && <button onClick={() => retry(item)} type="button">重试</button>}{(item.state === "failed" || item.state === "ready") && <button onClick={() => remove(item.localId)} type="button">移除</button>}</li>)}</ul>}
  </section>;
}
