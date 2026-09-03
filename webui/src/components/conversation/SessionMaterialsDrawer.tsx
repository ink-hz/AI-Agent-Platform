import { useState } from "react";
import type { AgentAttachmentLimits } from "../../brainTypes";
import type { ConversationAttachment } from "../../conversationTypes";
import { AttachmentCard } from "./AttachmentCard";

const STORAGE_KEY = "platform.session-materials.open";
function initialOpen(): boolean { try { return window.localStorage?.getItem(STORAGE_KEY) !== "false"; } catch { return true; } }

export function SessionMaterialsDrawer({ attachments, activeAttachmentIds, activeIds, limits,
  onActiveIdsChange, onToggle, onPreview, onDownload, onOpen, onDelete }: {
  attachments: ConversationAttachment[]; activeAttachmentIds?: string[]; activeIds?: string[]; limits?: AgentAttachmentLimits;
  onActiveIdsChange?: (ids: string[]) => void; onToggle?: (attachmentId: string, active: boolean) => void;
  onPreview?: (attachment: ConversationAttachment) => void; onDownload?: (attachment: ConversationAttachment) => void;
  onOpen?: (attachment: ConversationAttachment, purpose: "preview" | "download") => void;
  onDelete?: (attachment: ConversationAttachment) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(initialOpen);
  const selectedIds = activeAttachmentIds ?? activeIds ?? [];
  const user = attachments.filter((item) => item.source === "user");
  const output = attachments.filter((item) => item.source === "agent");
  const active = user.filter((item) => selectedIds.includes(item.attachmentId));
  const toggle = (attachmentId: string, enabled: boolean) => {
    onToggle?.(attachmentId, enabled);
    onActiveIdsChange?.(enabled ? selectedIds.includes(attachmentId) ? selectedIds : [...selectedIds, attachmentId] : selectedIds.filter((id) => id !== attachmentId));
  };
  const group = (title: string, items: ConversationAttachment[], selectable: boolean) => <section className="session-materials-group"><header><h3>{title}</h3><span>{items.length}</span></header>{items.length === 0 ? <p>暂无</p> : items.map((attachment) => <AttachmentCard
    active={selectedIds.includes(attachment.attachmentId)} attachment={attachment} compact key={attachment.attachmentId}
    onActiveChange={selectable ? (enabled) => toggle(attachment.attachmentId, enabled) : undefined}
    onDelete={onDelete} onDownload={onDownload} onOpen={onOpen} onPreview={onPreview}
  />)}</section>;
  const setExpanded = () => setOpen((current) => { const next = !current; try { window.localStorage?.setItem(STORAGE_KEY, String(next)); } catch { /* optional */ } return next; });
  return <aside aria-label="会话材料" className="session-materials-drawer"><header><div><span>SESSION MATERIALS</span><h2>会话材料</h2></div><button aria-expanded={open} className="session-materials-toggle" onClick={setExpanded} type="button">{open ? "收起" : "展开"}</button></header>{open && <div className="session-materials-panel"><p className="session-materials-usage">{user.length} / {limits?.max_files_per_conversation ?? 50} 个 · 默认保留 1 年</p>{group("本轮启用", active, true)}{group("已上传材料", user, true)}{group("生成结果", output, false)}</div>}</aside>;
}
