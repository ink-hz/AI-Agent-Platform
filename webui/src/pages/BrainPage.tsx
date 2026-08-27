import { useEffect, useRef, useState, type FormEvent } from "react";

import type { Account } from "../auth";
import { ConversationApiError, conversationInputTooLarge, startConversation, type ConversationSubmission } from "../conversationApi";
import type { Conversation } from "../conversationTypes";
import { navigate } from "../router";


const EXAMPLES = [
  "帮我细化一个视觉算法岗位的能力组合，并给出候选人搜寻与面试方案",
  "为深度相机异常整理一套分步排查方案，标出还需要补充的信息",
  "为灵巧手 OEM、代采与 DFM 服务制定目标客户与首轮触达方案",
] as const;

export interface BrainPageClient {
  createSubmission(text: string, csrfToken: string): ConversationSubmission;
}

const DEFAULT_CLIENT: BrainPageClient = { createSubmission: startConversation };

function isBrainUnavailable(error: unknown): boolean {
  if (!(error instanceof ConversationApiError) || error.status !== 503) return false;
  const detail = error.detail;
  return typeof detail === "object" && detail !== null
    && "detail" in detail && detail.detail === "Agent Brain unavailable";
}

export function BrainPage({
  account,
  client = DEFAULT_CLIENT,
  onConversationCreated,
  onOpenConversation = (path) => navigate(path),
}: {
  account: Account;
  client?: BrainPageClient;
  onConversationCreated?: (conversation: Conversation) => void;
  onOpenConversation?: (path: string) => void;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<"unavailable" | "other" | null>(null);
  const retained = useRef<{ text: string; submission: ConversationSubmission } | null>(null);
  const submitController = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const inputTooLarge = conversationInputTooLarge(text.trim());

  useEffect(() => () => submitController.current?.abort(), []);

  const send = async () => {
    const normalized = text.trim();
    if (!normalized || inputTooLarge || inFlight.current || account.hard_stale_read_only) return;
    let selected = retained.current;
    if (!selected || selected.text !== normalized) {
      selected = { text: normalized, submission: client.createSubmission(normalized, account.csrf_token) };
      retained.current = selected;
    }
    const controller = new AbortController();
    submitController.current?.abort();
    submitController.current = controller;
    inFlight.current = true;
    setPending(true);
    setFailure(null);
    try {
      const result = await selected.submission.send(controller.signal);
      retained.current = null;
      onConversationCreated?.(result.conversation);
      onOpenConversation(`/conversations/${encodeURIComponent(result.conversation.conversation_id)}`);
    } catch (error) {
      if (!controller.signal.aborted) {
        setFailure(isBrainUnavailable(error) ? "unavailable" : "other");
      }
    } finally {
      if (submitController.current === controller) {
        inFlight.current = false;
        if (!controller.signal.aborted) setPending(false);
      }
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void send();
  };

  return <div className="brain-page">
    <section className="brain-hero" aria-labelledby="brain-heading">
      <p>把原始需求直接交给它</p>
      <h1 id="brain-heading">Agent 大脑</h1>
      <span>它会判断是否需要专业 Agent，并把真实的分工、执行和结果交付给你。</span>
      <form className="brain-composer" onSubmit={submit}>
        <label htmlFor="brain-request">你想完成什么？</label>
        <textarea
          autoFocus
          disabled={account.hard_stale_read_only}
          id="brain-request"
          aria-label="你想完成什么？"
          maxLength={32 * 1024}
          onChange={(event) => {
            const next = event.target.value;
            setText(next);
            if (retained.current?.text !== next.trim()) retained.current = null;
            setFailure(null);
          }}
          placeholder="描述目标、背景和希望得到的结果…"
          rows={5}
          value={text}
        />
        <div className="brain-composer-actions">
          <span>首版支持纯文本任务；需要专业能力时最多调用一个已授权 Agent。</span>
          <button className="brain-submit" disabled={!text.trim() || inputTooLarge || pending || account.hard_stale_read_only} type="submit">
            {pending ? "正在创建…" : "开始对话"}
          </button>
        </div>
      </form>
      {inputTooLarge && <p className="mission-input-error" role="alert">输入超过 32 KiB，请精简后再提交。</p>}
      {failure && <div className="brain-submit-error" role="alert">
        <span>{failure === "unavailable"
          ? "Agent 大脑暂不可用。请稍后使用同一次请求重试。"
          : "对话暂未创建成功。网络恢复后可使用同一次请求安全重试。"}</span>
        <button className="brain-retry" disabled={pending} onClick={() => void send()} type="button">重新提交</button>
      </div>}
      <div className="brain-examples" aria-label="任务示例">
        {EXAMPLES.map((example) => <button className="brain-example" key={example} onClick={() => setText(example)} type="button">{example}</button>)}
      </div>
    </section>
  </div>;
}
