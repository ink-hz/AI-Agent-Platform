import type { FormEvent, ReactNode } from "react";

import { conversationInputTooLarge } from "../../conversationApi";
import { ComposerTextarea } from "./ComposerTextarea";


export function ConversationComposer({
  value,
  onChange,
  onSubmit,
  pending,
  disabled,
  disabledMessage,
  label = "继续对话",
  placeholder = "继续补充目标、背景或希望调整的方向…",
  attachmentControls,
  hasReadyAttachment = false,
  attachmentPending = false,
  tools,
  compact = false,
  navigation,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  pending: boolean;
  disabled: boolean;
  disabledMessage?: string;
  label?: string;
  placeholder?: string;
  attachmentControls?: ReactNode;
  hasReadyAttachment?: boolean;
  attachmentPending?: boolean;
  tools?: ReactNode;
  compact?: boolean;
  navigation?: ReactNode;
}) {
  const inputTooLarge = conversationInputTooLarge(value.trim());
  const submitDisabled = disabled || pending || attachmentPending
    || (!value.trim() && !hasReadyAttachment) || inputTooLarge;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };
  return <form className={`conversation-composer${compact ? " is-compact" : ""}`} onSubmit={submit}>
    {navigation}
    <label htmlFor="conversation-message">{label}</label>
    <ComposerTextarea
      autoSize={compact}
      aria-label={label}
      disabled={disabled}
      id="conversation-message"
      maxLength={32 * 1024}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" || event.shiftKey
          || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229
          || submitDisabled) return;
        event.preventDefault();
        onSubmit();
      }}
      placeholder={placeholder}
      rows={compact ? 1 : 4}
      value={value}
    />
    {attachmentControls && <div className="conversation-composer-attachments">
      {attachmentControls}
    </div>}
    <div className="conversation-composer-actions">
      {tools && <div className="conversation-composer-tools">{tools}</div>}
      <span hidden={compact}>{disabledMessage ?? (disabled
        ? "当前暂不可发送。"
        : "Enter 发送；Shift+Enter 换行。")}</span>
      <button
        className="conversation-send"
        disabled={submitDisabled}
        type="submit"
      >{pending ? "正在发送…" : attachmentPending ? "等待文件处理" : "✨ 发送"}</button>
    </div>
    {compact && (disabledMessage || disabled) && <p className="conversation-composer-status" role="status">{disabledMessage ?? "当前暂不可发送。"}</p>}
    {inputTooLarge && <p className="mission-input-error" role="alert">输入超过 32 KiB，请精简后再发送。</p>}
  </form>;
}
