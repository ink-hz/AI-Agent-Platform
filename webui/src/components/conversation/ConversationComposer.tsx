import type { FormEvent, ReactNode } from "react";

import { conversationInputTooLarge } from "../../conversationApi";


export function ConversationComposer({
  value,
  onChange,
  onSubmit,
  pending,
  disabled,
  label = "继续对话",
  placeholder = "继续补充目标、背景或希望调整的方向…",
  attachmentControls,
  hasReadyAttachment = false,
  attachmentPending = false,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  pending: boolean;
  disabled: boolean;
  label?: string;
  placeholder?: string;
  attachmentControls?: ReactNode;
  hasReadyAttachment?: boolean;
  attachmentPending?: boolean;
}) {
  const inputTooLarge = conversationInputTooLarge(value.trim());
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };
  return <form className="conversation-composer" onSubmit={submit}>
    {attachmentControls}
    <label htmlFor="conversation-message">{label}</label>
    <textarea
      aria-label={label}
      disabled={disabled}
      id="conversation-message"
      maxLength={32 * 1024}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      rows={4}
      value={value}
    />
    <div className="conversation-composer-actions">
      <span>{disabled ? "当前对话正在执行或账号处于只读状态。" : "Enter 换行；点击发送继续同一个对话。"}</span>
      <button
        className="conversation-send"
        disabled={disabled || pending || attachmentPending || (!value.trim() && !hasReadyAttachment) || inputTooLarge}
        type="submit"
      >{pending ? "正在发送…" : attachmentPending ? "等待文件处理" : "发送"}</button>
    </div>
    {inputTooLarge && <p className="mission-input-error" role="alert">输入超过 32 KiB，请精简后再发送。</p>}
  </form>;
}
