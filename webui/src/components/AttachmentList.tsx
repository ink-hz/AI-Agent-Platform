import { useState } from "react";

import { createAttachmentTicket } from "../api";
import type { AttachmentSummary } from "../types";


export const ARCHIVE_LABEL: Record<AttachmentSummary["archive_status"], string> = {
  pending: "归档中",
  available: "可查看",
  failed: "归档失败",
  source_unavailable: "历史源文件不可用",
  expired: "已按一年保留策略清理",
};


function formatBytes(bytes: number | null): string {
  if (bytes === null) return "大小未知";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${Number(value.toFixed(1))} ${units[unit]}`;
}


function formatAttachmentTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hour12: false,
  }).format(date);
}


function canPreview(mimeType: string | null): boolean {
  return mimeType === "application/pdf" || Boolean(mimeType?.startsWith("image/"));
}


function AttachmentCard({ attachment }: { attachment: AttachmentSummary }) {
  const [activePurpose, setActivePurpose] = useState<"preview" | "download" | null>(null);
  const [error, setError] = useState(false);
  const name = attachment.display_name || "未命名附件";
  const openAttachment = async (purpose: "preview" | "download") => {
    setActivePurpose(purpose);
    setError(false);
    try {
      const ticket = await createAttachmentTicket(attachment.attachment_id, purpose);
      if (!ticket.content_path.startsWith("/api/attachments/content/")) throw new Error("invalid attachment content path");
      window.open(ticket.content_path, "_blank", "noopener,noreferrer");
    } catch {
      setError(true);
    } finally {
      setActivePurpose(null);
    }
  };
  const action = (purpose: "preview" | "download", label: string) => {
    const busy = activePurpose === purpose;
    return <button
      type="button"
      aria-busy={busy}
      aria-label={`${label} ${name}`}
      disabled={activePurpose !== null}
      onClick={() => void openAttachment(purpose)}
    >{busy ? "处理中…" : label}</button>;
  };

  return <article className="attachment-card">
    <div className="attachment-detail">
      <strong className="attachment-name">{name}</strong>
      <div className="attachment-meta">
        <span>{attachment.mime_type || "类型未知"}</span>
        <span>{attachment.size_bucket || formatBytes(attachment.size_bytes)}</span>
        <time dateTime={attachment.received_or_generated_at}>{formatAttachmentTime(attachment.received_or_generated_at)}</time>
      </div>
      <span className={`attachment-state attachment-state-${attachment.archive_status}`}>
        {attachment.content_available === false ? "仅保留脱敏元数据" : ARCHIVE_LABEL[attachment.archive_status]}
      </span>
      {error && <span className="attachment-error" role="alert">附件访问失败，请重试</span>}
    </div>
    {attachment.archive_status === "available" && attachment.content_available !== false && <div className="attachment-actions">
      {canPreview(attachment.mime_type) && action("preview", "查看")}
      {action("download", "下载")}
    </div>}
  </article>;
}


export function AttachmentList({ attachments, label }: {
  attachments: AttachmentSummary[];
  label: string;
}) {
  if (attachments.length === 0) return null;
  return <section className="attachment-list" aria-label={label}>
    {attachments.map((attachment) => <AttachmentCard attachment={attachment} key={attachment.attachment_id} />)}
  </section>;
}
