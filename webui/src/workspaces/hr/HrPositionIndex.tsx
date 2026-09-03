import { useEffect, useMemo, useState } from "react";

import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import { startConversation } from "../../conversationApi";
import { createHrApi, type HrApi } from "../../hrApi";
import type { HrPosition, HrPositionDraft } from "../../hrTypes";
import { navigate } from "../../router";


type DraftStarter = (request: {
  draftId: string;
  text: string;
  csrfToken: string;
}) => Promise<{ conversationId: string }>;


function requestId(): string {
  return crypto.randomUUID();
}


function sourceLabel(position: HrPosition): string {
  return position.sourceKind === "official_site" ? "官网" : "内部";
}


function officialStatus(status: HrPosition["officialStatus"]): string {
  return ({
    active: "在招", stale: "信息较旧", suspected_inactive: "疑似下线", inactive: "已下线",
  } as const)[status ?? "active"];
}


export function HrPositionIndex({
  account,
  api: injectedApi,
  startDraftConversation,
}: {
  account: Account;
  api?: HrApi;
  startDraftConversation?: DraftStarter;
}) {
  const api = useMemo(
    () => injectedApi ?? createHrApi(account.csrf_token),
    [account.csrf_token, injectedApi],
  );
  const [positions, setPositions] = useState<HrPosition[]>([]);
  const [drafts, setDrafts] = useState<HrPositionDraft[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [newOpen, setNewOpen] = useState(false);
  const [newRequest, setNewRequest] = useState("");
  const [working, setWorking] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mergeTargets, setMergeTargets] = useState<Record<string, string>>({});

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    void Promise.all([
      api.listPositions({ limit: 100 }, controller.signal),
      api.listDrafts("proposed", controller.signal),
    ]).then(([page, pending]) => {
      setPositions(page.items); setDrafts(pending); setState("ready");
    }).catch(() => {
      if (!controller.signal.aborted) setState("error");
    });
    return () => controller.abort();
  }, [api, attempt]);

  const visible = useMemo(() => {
    const selected = query.trim().toLocaleLowerCase();
    if (!selected) return positions;
    return positions.filter((position) => [
      position.title, position.officialJobId, position.department, ...position.locations,
    ].some((value) => value?.toLocaleLowerCase().includes(selected)));
  }, [positions, query]);
  const official = visible.filter((position) => position.sourceKind === "official_site");
  const internal = visible.filter((position) => position.sourceKind === "manual");

  async function decide(
    draft: HrPositionDraft,
    action: "confirm" | "merge" | "dismiss",
  ) {
    setWorking(draft.draftId); setNotice(null);
    try {
      if (action === "confirm") await api.confirmDraft(draft.draftId, draft.rowVersion, requestId());
      if (action === "merge") {
        const targetPositionId = mergeTargets[draft.draftId];
        if (!targetPositionId) { setNotice("请先明确选择要合并到的正式岗位。"); return; }
        await api.mergeDraft(draft.draftId, targetPositionId, draft.rowVersion, requestId());
      }
      if (action === "dismiss") await api.dismissDraft(draft.draftId, draft.rowVersion, requestId());
      setDrafts((current) => current.filter((item) => item.draftId !== draft.draftId));
    } catch { setNotice("操作未完成，请刷新后重试。"); }
    finally { setWorking(null); }
  }

  async function beginNewPosition() {
    const text = newRequest.trim();
    if (!text) return;
    setWorking("new"); setNotice(null);
    try {
      const idempotency = requestId();
      const draft = await api.proposeDraft({
        sourceKind: "new_conversation", sourceKey: `request:${idempotency}`,
        sourceConversationId: null, title: text.slice(0, 500), proposal: { request: text },
        evidence: { source: "hr_position_index" }, discoveryRuleVersion: "interactive-v1",
      }, idempotency);
      const starter = startDraftConversation ?? (async ({ draftId, text: prompt, csrfToken }) => {
        const result = await startConversation(
          prompt, csrfToken, "hr-bot", { positionDraftId: draftId },
        ).send();
        return { conversationId: result.conversation.conversation_id };
      });
      const result = await starter({ draftId: draft.draftId, text, csrfToken: account.csrf_token });
      navigate(`/hr/conversations/${encodeURIComponent(result.conversationId)}`);
    } catch { setNotice("岗位对话暂时没有创建成功，可以直接重试。"); }
    finally { setWorking(null); }
  }

  if (state === "loading") return <main className="hr-position-index hr-position-state"><p>正在读取岗位…</p></main>;
  if (state === "error") return <main className="hr-position-index hr-position-state" role="alert">
    <h1>岗位数据暂时不可用</h1><p>已有数据不会丢失，请稍后重试。</p>
    <button type="button" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
  </main>;

  return <main className="hr-position-index">
    <header className="hr-position-hero">
      <div><span className="hr-position-eyebrow">HR RECRUITING INTELLIGENCE</span>
        <h1>岗位智能工作台</h1>
        <p>把官网岗位、历史招聘对话和新需求放在同一个岗位上下文里。</p>
      </div>
      <button className="hr-position-primary" disabled={account.hard_stale_read_only} type="button" onClick={() => setNewOpen(true)}>用对话新建岗位</button>
    </header>

    <section className="hr-position-metrics" aria-label="岗位概览">
      <article><strong>{positions.length}</strong><span>正式岗位</span></article>
      <article><strong>{official.length}</strong><span>官网同步</span></article>
      <article><strong>{drafts.length}</strong><span>待你确认</span></article>
    </section>

    <div className="hr-position-toolbar">
      <label><span>搜索岗位</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="岗位名称、J 编号、部门或地点" /></label>
      <PlatformLink href="/hr/chat">自由对话</PlatformLink>
    </div>
    {account.hard_stale_read_only && <p className="hr-position-notice" role="status">账号目录信息已过期，岗位数据暂时只读。</p>}
    {notice && <p className="hr-position-notice" role="status">{notice}</p>}

    <PositionSection title="官网岗位" caption="来自官网同步，状态与内部工作状态分开显示。" positions={official} />
    <PositionSection title="内部岗位" caption="由招聘对话确认创建，不会写回官网注册表。" positions={internal} />

    <section className="hr-position-section" aria-labelledby="hr-drafts-title">
      <div className="hr-position-section-heading"><div><h2 id="hr-drafts-title">待确认</h2><p>识别结果仍是草稿，确认前不会成为正式岗位。</p></div><span>{drafts.length}</span></div>
      {drafts.length === 0 ? <div className="hr-position-empty">当前没有待确认的岗位草稿。</div>
        : <div className="hr-draft-list">{drafts.map((draft) => <article key={draft.draftId} className="hr-draft-card">
          <div><span className="hr-position-chip">{draft.sourceKind === "historical_conversation" ? "历史识别" : "新需求"}</span><h3>{draft.title}</h3><p>规则 {draft.discoveryRuleVersion} · 证据已保留</p></div>
          <div className="hr-draft-actions">
            <select aria-label={`选择 ${draft.title} 的合并目标`} disabled={account.hard_stale_read_only || working === draft.draftId || positions.length === 0} value={mergeTargets[draft.draftId] ?? ""} onChange={(event) => setMergeTargets((current) => ({ ...current, [draft.draftId]: event.target.value }))}>
              <option value="">选择合并目标…</option>
              {positions.map((position) => <option key={position.positionId} value={position.positionId}>{position.title} · {position.officialJobId ?? position.positionId}</option>)}
            </select>
            <button disabled={account.hard_stale_read_only || working === draft.draftId} type="button" onClick={() => void decide(draft, "confirm")}>确认新建</button>
            <button disabled={account.hard_stale_read_only || working === draft.draftId || !mergeTargets[draft.draftId]} type="button" onClick={() => void decide(draft, "merge")}>合并到岗位</button>
            <button disabled={account.hard_stale_read_only || working === draft.draftId} type="button" onClick={() => void decide(draft, "dismiss")}>忽略</button>
          </div>
        </article>)}</div>}
    </section>

    {newOpen && <div className="hr-position-dialog-backdrop" role="presentation">
      <section className="hr-position-dialog" role="dialog" aria-modal="true" aria-labelledby="new-position-title">
        <span className="hr-position-eyebrow">NEW POSITION</span><h2 id="new-position-title">先说清楚你要招什么人</h2>
        <p>可以像跟 HR 同事沟通一样描述。系统先建立草稿与对话，你确认后才生成正式岗位。</p>
        <textarea autoFocus value={newRequest} onChange={(event) => setNewRequest(event.target.value)} placeholder="例如：我要招聘一名 3D 打印机高级结构工程师，重点考察喷嘴和挤出工艺…" />
        <div><button type="button" onClick={() => setNewOpen(false)}>取消</button><button className="hr-position-primary" disabled={!newRequest.trim() || working === "new"} type="button" onClick={() => void beginNewPosition()}>{working === "new" ? "正在创建…" : "开始梳理"}</button></div>
      </section>
    </div>}
  </main>;
}


function PositionSection({ title, caption, positions }: { title: string; caption: string; positions: HrPosition[] }) {
  return <section className="hr-position-section">
    <div className="hr-position-section-heading"><div><h2>{title}</h2><p>{caption}</p></div><span>{positions.length}</span></div>
    {positions.length === 0 ? <div className="hr-position-empty">没有匹配的岗位。</div>
      : <div className="hr-position-grid">{positions.map((position) => <PlatformLink className="hr-position-card" href={`/hr/positions/${encodeURIComponent(position.positionId)}`} key={position.positionId}>
        <div><span className={`hr-position-chip hr-position-chip--${position.sourceKind}`}>{sourceLabel(position)}</span>{position.officialStatus && <span className={`hr-position-status hr-position-status--${position.officialStatus}`}>{officialStatus(position.officialStatus)}</span>}</div>
        <h3>{position.title}</h3><p>{[position.department, ...position.locations].filter(Boolean).join(" · ") || "岗位信息待完善"}</p>
        <footer><span>{position.officialJobId ?? "内部岗位"}</span><span>{position.sourceVersion ? `官网版本 ${position.sourceVersion}` : "内部上下文"}</span></footer>
      </PlatformLink>)}</div>}
  </section>;
}
