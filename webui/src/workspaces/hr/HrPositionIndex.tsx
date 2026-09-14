import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, BriefcaseBusiness, Search } from "lucide-react";

import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import { startConversation, type ConversationSubmission } from "../../conversationApi";
import { createHrApi, type HrApi } from "../../hrApi";
import type { HrPosition, HrPositionDraft } from "../../hrTypes";
import { navigate } from "../../router";
import { completeMutationRequest, retainMutationRequest } from "./hrMutationRequest";
import "./hrPositionWorkflow.css";


type DraftStarter = (request: {
  draftId: string;
  text: string;
  csrfToken: string;
  requestId: string;
}) => Promise<{ conversationId: string }>;


function sourceLabel(position: HrPosition): string {
  return position.sourceKind === "official_site" ? "官网" : "内部";
}


function officialStatus(status: HrPosition["officialStatus"]): string {
  return ({
    active: "在招", stale: "信息较旧", suspected_inactive: "疑似下线", inactive: "已下线",
  } as const)[status ?? "active"];
}


async function loadEveryPosition(api: HrApi, signal: AbortSignal): Promise<HrPosition[]> {
  const found = new Map<string, HrPosition>();
  const seenCursors = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await api.listPositions(
      cursor ? { limit: 100, cursor } : { limit: 100 }, signal,
    );
    for (const item of page.items) found.set(item.positionId, item);
    if (!page.nextCursor) break;
    if (seenCursors.has(page.nextCursor)) throw new Error("repeated position cursor");
    seenCursors.add(page.nextCursor);
    cursor = page.nextCursor;
  } while (!signal.aborted);
  return [...found.values()];
}


export function HrPositionIndex({
  account,
  api: injectedApi,
  startDraftConversation,
  onSelect,
}: {
  account: Account;
  api?: HrApi;
  startDraftConversation?: DraftStarter;
  onSelect?: (position: HrPosition) => void;
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
  const [status, setStatus] = useState<"all" | HrPosition["internalStatus"]>("all");
  const [newOpen, setNewOpen] = useState(false);
  const [newRequest, setNewRequest] = useState("");
  const [working, setWorking] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mergeTargets, setMergeTargets] = useState<Record<string, string>>({});
  const newAttempt = useRef<{
    text: string;
    draftRequestId: string;
    conversationRequestId: string;
    submission?: ConversationSubmission;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    void Promise.all([
      loadEveryPosition(api, controller.signal),
      api.listDrafts("proposed", controller.signal),
    ]).then(([loadedPositions, pending]) => {
      if (controller.signal.aborted) return;
      setPositions(loadedPositions); setDrafts(pending); setState("ready");
    }).catch(() => {
      if (!controller.signal.aborted) setState("error");
    });
    return () => controller.abort();
  }, [api, attempt]);

  const visible = useMemo(() => {
    const selected = query.trim().toLocaleLowerCase();
    return positions.filter((position) =>
      (status === "all" || position.internalStatus === status)
      && (!selected || [
        position.title, position.officialJobId, position.department, ...position.locations,
      ].some((value) => value?.toLocaleLowerCase().includes(selected))),
    );
  }, [positions, query, status]);
  const official = visible.filter((position) => position.sourceKind === "official_site");
  const internal = visible.filter((position) => position.sourceKind === "manual");

  async function decide(
    draft: HrPositionDraft,
    action: "confirm" | "merge" | "dismiss",
  ) {
    setWorking(draft.draftId); setNotice(null);
    const payload = { action, rowVersion: draft.rowVersion, targetPositionId: mergeTargets[draft.draftId] ?? null };
    const operation = retainMutationRequest(`position-draft:${draft.draftId}:${action}`, payload);
    try {
      if (action === "confirm") await api.confirmDraft(draft.draftId, draft.rowVersion, operation.requestId);
      if (action === "merge") {
        const targetPositionId = mergeTargets[draft.draftId];
        if (!targetPositionId) { setNotice("请先明确选择要合并到的正式岗位。"); return; }
        await api.mergeDraft(draft.draftId, targetPositionId, draft.rowVersion, operation.requestId);
      }
      if (action === "dismiss") await api.dismissDraft(draft.draftId, draft.rowVersion, operation.requestId);
      completeMutationRequest(operation.key);
      setDrafts((current) => current.filter((item) => item.draftId !== draft.draftId));
    } catch { setNotice("操作未完成，请刷新后重试。"); }
    finally { setWorking(null); }
  }

  async function beginNewPosition() {
    const text = newRequest.trim();
    if (!text) return;
    setWorking("new"); setNotice(null);
    try {
      if (!newAttempt.current || newAttempt.current.text !== text) {
        const draftOperation = retainMutationRequest("position-new-draft", { text });
        const conversationOperation = retainMutationRequest("position-new-conversation", { text });
        newAttempt.current = {
          text,
          draftRequestId: draftOperation.requestId,
          conversationRequestId: conversationOperation.requestId,
        };
      }
      const attempt = newAttempt.current;
      const draft = await api.proposeDraft({
        sourceKind: "new_conversation", sourceKey: `request:${attempt.draftRequestId}`,
        sourceConversationId: null, title: text.slice(0, 500), proposal: { request: text },
        evidence: { source: "hr_position_index" }, discoveryRuleVersion: "interactive-v1",
      }, attempt.draftRequestId);
      let result: { conversationId: string };
      if (startDraftConversation) {
        result = await startDraftConversation({
          draftId: draft.draftId, text, csrfToken: account.csrf_token,
          requestId: attempt.conversationRequestId,
        });
      } else {
        attempt.submission ??= startConversation(
          text, account.csrf_token, "hr-bot", { positionDraftId: draft.draftId },
          attempt.conversationRequestId,
        );
        const created = await attempt.submission.send();
        result = { conversationId: created.conversation.conversation_id };
      }
      newAttempt.current = null;
      completeMutationRequest(retainMutationRequest("position-new-draft", { text }).key);
      completeMutationRequest(retainMutationRequest("position-new-conversation", { text }).key);
      navigate(`/hr/conversations/${encodeURIComponent(result.conversationId)}`);
    } catch { setNotice("岗位对话暂时没有创建成功，可以直接重试。"); }
    finally { setWorking(null); }
  }

  if (state === "loading") return <main className="hr-position-directory hr-position-state"><p>正在读取岗位…</p></main>;
  if (state === "error") return <main className="hr-position-directory hr-position-state" role="alert">
    <h1>岗位数据暂时不可用</h1><p>已有数据不会丢失，请稍后重试。</p>
    <button type="button" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
  </main>;

  return <main className="hr-position-directory"><div className="hr-position-page-inner">
    <header className="hr-pw-heading">
      <div><span>RECRUITMENT WORKSPACE</span>
        <h1>岗位</h1>
        <p>围绕一个岗位，查看要求、候选人、面试与主对话中保存的成果。</p>
      </div>
      <div className="hr-pw-heading-actions"><button className="hr-pw-primary" disabled={account.hard_stale_read_only} type="button" onClick={() => setNewOpen(true)}>用对话新建岗位</button><BriefcaseBusiness size={34} aria-hidden="true" /></div>
    </header>

    <div className="hr-pw-directory-tools">
      <label><Search size={18} aria-hidden="true" /><input aria-label="搜索岗位" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索岗位、编号、部门或地点" /></label>
      <select aria-label="岗位状态" value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
        <option value="all">全部岗位</option><option value="active">进行中</option><option value="draft">草案</option><option value="archived">已归档</option>
      </select>
      <span>{visible.length} 个岗位</span>
    </div>
    {account.hard_stale_read_only && <p className="hr-position-notice" role="status">账号目录信息已过期，岗位数据暂时只读。</p>}
    {notice && <p className="hr-position-notice" role="status">{notice}</p>}

    <PositionSection title="官网岗位" caption="来自官网同步，状态与内部工作状态分开显示。" positions={official} onSelect={onSelect} />
    <PositionSection title="内部岗位" caption="由招聘对话确认创建，不会写回官网注册表。" positions={internal} onSelect={onSelect} />

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

    </div>
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


function PositionSection({ title, caption, positions, onSelect }: { title: string; caption: string; positions: HrPosition[]; onSelect?: (position: HrPosition) => void }) {
  return <section className="hr-position-section">
    <div className="hr-position-section-heading"><div><h2>{title}</h2><p>{caption}</p></div><span>{positions.length}</span></div>
    {positions.length === 0 ? <div className="hr-position-empty">没有匹配的岗位。</div>
      : <div className="hr-pw-position-grid">{positions.map((position) => <article className="hr-pw-position-card" key={position.positionId}>
        <div><span>{position.officialJobId ?? '自建岗位'} · {sourceLabel(position)}</span><em>{position.internalStatus === "archived" ? "已归档" : position.internalStatus === "draft" ? "草案" : "进行中"}</em></div>
        <h2><PlatformLink href={`/hr/positions/${encodeURIComponent(position.positionId)}`}>{position.title}</PlatformLink></h2>
        <p>{[position.department, ...position.locations].filter(Boolean).join(" · ") || "部门与地点待补充"}</p>
        <div className="hr-pw-card-flow" aria-label="岗位工作流阶段"><span>JD / JR</span><span>候选人</span><span>面试</span><span>复盘</span></div>
        <div className="hr-pw-card-source">{position.officialStatus && <span>{officialStatus(position.officialStatus)}</span>}<span>{position.sourceVersion ? `官网版本 ${position.sourceVersion}` : "内部上下文"}</span></div>
        <footer><PlatformLink href={`/hr/positions/${encodeURIComponent(position.positionId)}`}>查看岗位工作流 <ArrowUpRight size={16} aria-hidden="true" /></PlatformLink>
          {onSelect ? <button type="button" disabled={position.internalStatus !== "active"} onClick={() => onSelect(position)}>在主对话中继续</button> : <PlatformLink href={`/hr/?position=${encodeURIComponent(position.positionId)}`}>与 Hannah 讨论岗位</PlatformLink>}
        </footer>
      </article>)}</div>}
  </section>;
}
