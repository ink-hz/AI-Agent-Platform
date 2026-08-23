import { useEffect, useRef, useState, type FormEvent } from "react";

import type { Account } from "../auth";
import { conversationInputTooLarge, listConversations, startConversation, type ConversationSubmission } from "../conversationApi";
import type { ConversationPage } from "../conversationTypes";
import { PlatformLink } from "../components/PlatformLink";
import { navigate } from "../router";


const EXAMPLES = [
  "帮我细化一个视觉算法岗位的能力组合，并给出候选人搜寻与面试方案",
  "为深度相机异常整理一套分步排查方案，标出还需要补充的信息",
  "为灵巧手 OEM、代采与 DFM 服务制定目标客户与首轮触达方案",
] as const;

export interface BrainPageClient {
  listConversations(signal?: AbortSignal): Promise<ConversationPage>;
  createSubmission(text: string, csrfToken: string): ConversationSubmission;
}

const DEFAULT_CLIENT: BrainPageClient = { listConversations, createSubmission: startConversation };

export function BrainPage({
  account,
  client = DEFAULT_CLIENT,
  onOpenConversation = (path) => navigate(path),
}: {
  account: Account;
  client?: BrainPageClient;
  onOpenConversation?: (path: string) => void;
}) {
  const [text, setText] = useState("");
  const [recent, setRecent] = useState<ConversationPage | null>(null);
  const [recentUnavailable, setRecentUnavailable] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState(false);
  const retained = useRef<{ text: string; submission: ConversationSubmission } | null>(null);
  const submitController = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const inputTooLarge = conversationInputTooLarge(text.trim());

  useEffect(() => {
    const controller = new AbortController();
    client.listConversations(controller.signal).then(setRecent).catch(() => {
      if (!controller.signal.aborted) setRecentUnavailable(true);
    });
    return () => {
      controller.abort();
      submitController.current?.abort();
    };
  }, [client]);

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
    setFailure(false);
    try {
      const result = await selected.submission.send(controller.signal);
      retained.current = null;
      onOpenConversation(`/conversations/${encodeURIComponent(result.conversation.conversation_id)}`);
    } catch {
      if (!controller.signal.aborted) setFailure(true);
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
          maxLength={32 * 1024}
          onChange={(event) => {
            const next = event.target.value;
            setText(next);
            if (retained.current?.text !== next.trim()) retained.current = null;
            setFailure(false);
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
        <span>对话暂未创建成功。网络恢复后可使用同一次请求安全重试。</span>
        <button className="brain-retry" disabled={pending} onClick={() => void send()} type="button">重新提交</button>
      </div>}
      <div className="brain-examples" aria-label="任务示例">
        {EXAMPLES.map((example) => <button className="brain-example" key={example} onClick={() => setText(example)} type="button">{example}</button>)}
      </div>
    </section>
    <section className="brain-recent" aria-labelledby="recent-conversations-heading">
      <header><div><p>YOUR WORK</p><h2 id="recent-conversations-heading">最近对话</h2></div><PlatformLink href="/conversations">查看全部</PlatformLink></header>
      {recentUnavailable ? <p className="brain-recent-state" role="status">最近对话暂时无法读取，不影响创建新对话。</p>
        : recent === null ? <p className="brain-recent-state" role="status">正在读取最近对话…</p>
        : recent.items.length === 0 ? <p className="brain-recent-state">还没有对话，从上面的输入框开始。</p>
        : <div className="brain-recent-list">{recent.items.slice(0, 5).map((conversation) => <PlatformLink href={`/conversations/${encodeURIComponent(conversation.conversation_id)}`} key={conversation.conversation_id}>
          <span>{conversation.title}</span><b>{conversation.status === "archived" ? "已归档" : "可继续"}</b>
        </PlatformLink>)}</div>}
      <PlatformLink className="brain-agent-link" href="/agents">也可以直接使用专业 Agent →</PlatformLink>
    </section>
  </div>;
}
