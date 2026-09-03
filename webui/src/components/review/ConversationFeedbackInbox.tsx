import { useEffect, useState } from "react";

import { platformPath } from "../../auth";
import type {
  AttachmentTicket,
  ConversationReviewAttachment,
  ConversationReviewFeedback,
  ConversationReviewFeedbackPage,
} from "../../api";


export interface ConversationFeedbackApi {
  feedback(signal?: AbortSignal): Promise<ConversationReviewFeedbackPage>;
  attachments(conversationId: string, signal?: AbortSignal): Promise<ConversationReviewAttachment[]>;
  triage(feedbackId: string, status: "triaged" | "dismissed", actor: string): Promise<ConversationReviewFeedback>;
  ticket(attachmentId: string, purpose: "preview" | "download", actor: string): Promise<AttachmentTicket>;
}


function reasonLabel(reason: string | null): string {
  return ({
    inaccurate: "内容不准确", incomplete: "回答不完整", unclear: "表达不清楚",
    unresolved: "没有解决问题", file_format: "文件格式不符合预期",
    source_timeliness: "来源或时效有问题", other: "其他问题",
  } as Record<string, string>)[reason ?? ""] ?? "未选择原因";
}


function unavailableLabel(reason: string | null): string | null {
  return ({
    quarantined: "已隔离，不可访问", rejected: "校验未通过，不可访问",
    deleted: "已删除，不可访问", deletion_pending: "正在删除，不可访问",
    retention_expired: "已到期，不可访问", unavailable: "历史文件不可用",
    uploading: "仍在上传", validating: "正在校验", scanning: "正在安全扫描",
  } as Record<string, string>)[reason ?? ""] ?? (reason ? "暂不可访问" : null);
}


function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}


export function ConversationFeedbackInbox({ api, actor }: { api: ConversationFeedbackApi; actor: string }) {
  const [items, setItems] = useState<ConversationReviewFeedback[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<ConversationReviewAttachment[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const selected = items.find((item) => item.feedback_id === selectedId) ?? null;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api.feedback(controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      setItems(page.items);
      setSelectedId((current) => current && page.items.some((item) => item.feedback_id === current)
        ? current : page.items[0]?.feedback_id ?? null);
      setLoading(false);
    }).catch(() => {
      if (!controller.signal.aborted) { setMessage("网页会话反馈暂时无法加载"); setLoading(false); }
    });
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    if (!selected) { setAttachments([]); return; }
    const controller = new AbortController();
    api.attachments(selected.conversation_id, controller.signal).then((next) => {
      if (!controller.signal.aborted) setAttachments(next);
    }).catch(() => {
      if (!controller.signal.aborted) setMessage("会话附件暂时无法加载");
    });
    return () => controller.abort();
  }, [api, selected?.conversation_id]);

  const openAttachment = async (attachment: ConversationReviewAttachment, purpose: "preview" | "download") => {
    setBusy(true); setMessage("");
    try {
      const ticket = await api.ticket(attachment.attachment_id, purpose, actor);
      window.open(platformPath(ticket.content_path), "_blank", "noopener,noreferrer");
    } catch {
      setMessage("文件当前不可访问，请刷新状态后重试");
    } finally { setBusy(false); }
  };

  const triage = async (status: "triaged" | "dismissed") => {
    if (!selected) return;
    setBusy(true); setMessage("");
    try {
      await api.triage(selected.feedback_id, status, actor);
      const remaining = items.filter((item) => item.feedback_id !== selected.feedback_id);
      setItems(remaining); setSelectedId(remaining[0]?.feedback_id ?? null);
    } catch { setMessage("分诊状态没有保存，请重试"); }
    finally { setBusy(false); }
  };

  return <section className="conversation-feedback-inbox" aria-label="网页会话反馈">
    <header><div><p>WEB CONVERSATION FEEDBACK</p><h2>网页会话待分诊</h2><span>点踩不会自动创建工程事项，由 Owner 查看上下文后明确分诊。</span></div><b>{items.length}</b></header>
    {message && <div className="review-message" role="status">{message}</div>}
    {loading ? <p className="conversation-feedback-state">正在加载网页会话反馈…</p>
      : items.length === 0 ? <p className="conversation-feedback-state">当前没有待分诊的网页会话反馈</p>
        : <div className="conversation-feedback-layout"><nav aria-label="待分诊网页会话">{items.map((item) => <button
          className={item.feedback_id === selectedId ? "is-selected" : ""}
          key={item.feedback_id} onClick={() => setSelectedId(item.feedback_id)} type="button"
        ><strong>{item.question || item.conversation_title}</strong><span>{item.agent_id} · {reasonLabel(item.reason)}</span><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("zh-CN")}</time></button>)}</nav>
        {selected && <article className="conversation-feedback-detail"><header><div><span>{selected.agent_id}</span><h3>{selected.conversation_title}</h3></div><time dateTime={selected.created_at}>{new Date(selected.created_at).toLocaleString("zh-CN")}</time></header>
          <section><b>用户问题</b><p>{selected.question}</p></section>
          <section><b>公开回答</b><p>{selected.answer}</p></section>
          <section><b>反馈内容</b><p>{reasonLabel(selected.reason)}{selected.comment ? `：${selected.comment}` : ""}</p></section>
          {selected.citations.length > 0 && <section><b>联网来源</b><ol>{selected.citations.map((citation) => <li key={citation.citation_key}><a href={citation.url} rel="noreferrer" target="_blank">{citation.title || citation.site}</a><small>检索于 {new Date(citation.retrieved_at).toLocaleString("zh-CN")}</small></li>)}</ol></section>}
          <section><b>会话附件</b>{attachments.length === 0 ? <p>本会话没有附件</p> : <div className="conversation-review-attachments">{attachments.map((attachment) => {
            const unavailable = unavailableLabel(attachment.availability_reason);
            return <article key={attachment.attachment_id}><div><strong>{attachment.display_name}</strong><span>{attachment.source === "user" ? "用户材料" : "Agent 结果"} · {formatBytes(attachment.size_bytes)}{attachment.version_no ? ` · V${attachment.version_no}${attachment.current ? " 当前版" : ""}` : ""}</span><small>保留至 {new Date(attachment.retained_until).toLocaleDateString("zh-CN")}</small></div>{unavailable ? <em>{unavailable}</em> : <div>{attachment.detected_mime === "application/pdf" || attachment.detected_mime?.startsWith("image/") ? <button disabled={busy} data-purpose="preview" onClick={() => void openAttachment(attachment, "preview")} type="button">预览</button> : null}<button disabled={busy} data-purpose="download" onClick={() => void openAttachment(attachment, "download")} type="button">下载</button></div>}</article>;
          })}</div>}</section>
          <footer><button disabled={busy} data-triage="dismissed" onClick={() => void triage("dismissed")} type="button">无需处理</button><button disabled={busy} data-triage="triaged" onClick={() => void triage("triaged")} type="button">完成分诊</button></footer>
        </article>}</div>}
  </section>;
}
