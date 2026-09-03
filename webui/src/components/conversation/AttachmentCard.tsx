import { useState } from "react";
import type { ConversationAttachment } from "../../conversationTypes";

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) { const value = bytes / 1024; return `${Number.isInteger(value) ? value : value.toFixed(1)} KB`; }
  const value = bytes / 1024 / 1024; return `${Number.isInteger(value) ? value : value.toFixed(1)} MB`;
}
function stateLabel(state: ConversationAttachment["state"]): string {
  return ({ ready: "可使用", uploading: "上传中", validating: "校验中", scanning: "安全扫描中", quarantined: "已隔离", rejected: "文件不可用", deleted: "已删除" })[state];
}
function coverageLabel(attachment: ConversationAttachment): string | null {
  if (attachment.coverage?.pages) return `已读取 ${attachment.coverage.pages} 页`;
  if (attachment.coverage?.sheets) return `已读取 ${attachment.coverage.sheets} 个工作表`;
  if (attachment.coverage?.slides) return `已读取 ${attachment.coverage.slides} 页幻灯片`;
  if (attachment.coverage?.ocrComplete) return "OCR 已完成";
  return null;
}

export function AttachmentCard({ attachment, active = false, compact = false, selectable,
  onActiveChange, onToggle, onPreview, onDownload, onOpen, onDelete }: {
  attachment: ConversationAttachment; active?: boolean; compact?: boolean; selectable?: boolean;
  onActiveChange?: (active: boolean) => void;
  onToggle?: (attachmentId: string, active: boolean) => void;
  onPreview?: (attachment: ConversationAttachment) => void;
  onDownload?: (attachment: ConversationAttachment) => void;
  onOpen?: (attachment: ConversationAttachment, purpose: "preview" | "download") => void;
  onDelete?: (attachment: ConversationAttachment) => void | Promise<void>;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const ready = attachment.state === "ready";
  const canSelect = selectable ?? Boolean(onActiveChange || onToggle);
  const expiry = new Date(attachment.retainedUntil);
  const expiryLabel = Number.isNaN(expiry.valueOf()) ? "" : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(expiry);
  const coverage = coverageLabel(attachment);
  return <article className={`conversation-attachment-card${compact ? " is-compact" : ""}`} data-state={attachment.state}>
    {canSelect && <label className="conversation-attachment-select"><input checked={active} disabled={!ready} onChange={(event) => {
      onActiveChange?.(event.target.checked); onToggle?.(attachment.attachmentId, event.target.checked);
    }} type="checkbox" /><span>本轮使用</span></label>}
    <div className="conversation-attachment-main"><span aria-hidden="true" className="conversation-attachment-icon">{attachment.detectedMime?.startsWith("image/") ? "▧" : attachment.detectedMime === "application/pdf" ? "PDF" : "DOC"}</span>
      <div><strong title={attachment.displayName}>{attachment.displayName}</strong><p>{sizeLabel(attachment.sizeBytes)} · {stateLabel(attachment.state)}{coverage ? ` · ${coverage}` : ""}{expiryLabel ? ` · 保留至 ${expiryLabel}` : ""}</p>{attachment.stateReason && <small>{attachment.stateReason}</small>}</div></div>
    <div className="conversation-attachment-actions">
      {ready && attachment.preview && <button onClick={() => { onPreview?.(attachment); onOpen?.(attachment, "preview"); }} type="button">预览</button>}
      {ready && (onDownload || onOpen) && <button onClick={() => { onDownload?.(attachment); onOpen?.(attachment, "download"); }} type="button">下载</button>}
      {onDelete && attachment.source === "user" && attachment.state !== "deleted" && (confirmingDelete
        ? <><button className="is-danger" onClick={() => { void onDelete(attachment); setConfirmingDelete(false); }} type="button">确认删除</button><button onClick={() => setConfirmingDelete(false)} type="button">取消</button></>
        : <button className="is-danger" onClick={() => setConfirmingDelete(true)} type="button">删除</button>)}
    </div>
  </article>;
}
