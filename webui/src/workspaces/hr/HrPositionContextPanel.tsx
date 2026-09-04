import { useEffect, useRef, useState } from "react";
import type { HrR12Api } from "../../hrR12Api";
import type { HrContextVersion } from "../../hrR12Types";
import { trapDialogFocus } from "./modalFocus";
import { completeMutationRequest, retainMutationRequest } from "./hrMutationRequest";

const MODULE_LABELS: Record<string, string> = {
  mission: "岗位使命", jd: "JD", jr: "JR", competencies: "能力要求",
  profile: "人才画像", talent_profile: "人才画像", sourcing: "搜寻策略",
  sourcing_strategy: "搜寻策略", interview_standard: "面试标准", unknowns: "未知项",
};
type ContextState = { current: HrContextVersion | null; drafts: HrContextVersion[]; history: HrContextVersion[] };

function moduleText(value: Record<string, unknown>): string {
  const preferred = [value.summary, value.text, value.title].find((item) => typeof item === "string");
  return typeof preferred === "string" ? preferred : JSON.stringify(value, null, 2);
}

export function HrPositionContextPanel({ api, positionId, onConfirmed, readOnly = false, refreshGeneration = 0, heading = "岗位上下文" }: {
  api: Pick<HrR12Api, "context" | "confirmContext" | "compareContext">; positionId: string;
  onConfirmed?: (context: HrContextVersion) => void; readOnly?: boolean;
  refreshGeneration?: number; heading?: string;
}) {
  const [data, setData] = useState<ContextState | null>(null);
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ draftId: string; changedModules: string[]; left: Record<string, unknown>; right: Record<string, unknown> } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const mutation = useRef<AbortController | null>(null);
  const historyClose = useRef<HTMLButtonElement>(null);
  const historyOpenButton = useRef<HTMLButtonElement>(null);
  const historyWasOpen = useRef(false);

  useEffect(() => { if (historyOpen) historyClose.current?.focus(); else if (historyWasOpen.current) historyOpenButton.current?.focus(); historyWasOpen.current = historyOpen; }, [historyOpen]);

  async function load(signal?: AbortSignal): Promise<ContextState> {
    const value = await api.context(positionId, signal);
    if (!signal?.aborted) setData(value);
    return value;
  }
  useEffect(() => { setData(null); setNotice(null); setConflict(null); }, [api, positionId]);
  useEffect(() => {
    const controller = new AbortController(); setNotice(null);
    void load(controller.signal).catch(() => { if (!controller.signal.aborted) setNotice("岗位理解暂时无法读取"); });
    return () => { controller.abort(); mutation.current?.abort(); };
  }, [api, positionId, refreshGeneration, loadAttempt]);

  async function confirm(draft: HrContextVersion, retry = false) {
    if (readOnly) return;
    const modules = selected[draft.contextVersionId] ?? [];
    if (modules.length === 0) return;
    mutation.current?.abort(); const controller = new AbortController(); mutation.current = controller;
    setNotice(null);
    try {
      const baseline = retry ? data?.current?.contextVersionId ?? null : draft.baseContextVersionId;
      const operation = retainMutationRequest(`position-context:${positionId}:${draft.contextVersionId}`, { baseline, modules, rowVersion: draft.rowVersion });
      const confirmed = await api.confirmContext(positionId, draft.contextVersionId, baseline, modules, draft.rowVersion, operation.requestId, controller.signal);
      if (!controller.signal.aborted) { completeMutationRequest(operation.key); setData((value) => value ? { ...value, current: confirmed } : value); onConfirmed?.(confirmed); setConflict(null); setNotice("已确认岗位上下文"); await load(controller.signal); }
    } catch (error) {
      if (controller.signal.aborted) return;
      if ((error as { status?: number }).status !== 409) { setNotice("确认未完成，请重试。"); return; }
      try {
        const fresh = await load(controller.signal);
        const refreshedDraft = fresh.drafts.find((item) => item.contextVersionId === draft.contextVersionId) ?? draft;
        let changedModules: string[] = [];
        if (refreshedDraft.baseContextVersionId && fresh.current && refreshedDraft.baseContextVersionId !== fresh.current.contextVersionId) {
          const comparison = await api.compareContext(positionId, refreshedDraft.baseContextVersionId, fresh.current.contextVersionId, controller.signal);
          changedModules = comparison.changedModules;
          if (!controller.signal.aborted) setConflict({ draftId: draft.contextVersionId, changedModules, left: comparison.left, right: comparison.right });
        }
        if (!controller.signal.aborted) { if (!refreshedDraft.baseContextVersionId || !fresh.current || refreshedDraft.baseContextVersionId === fresh.current.contextVersionId) setConflict({ draftId: draft.contextVersionId, changedModules, left: {}, right: {} }); setNotice("基线已变化，请比较差异后按新基线重试。"); }
      } catch { if (!controller.signal.aborted) setNotice("基线已变化，重新读取失败，请稍后重试。"); }
    }
  }

  return <section aria-label={heading} className="hr-r12-panel hr-context-panel">
    <header><div><span>POSITION KNOWLEDGE</span><h2>{heading}</h2></div>{data?.current && <strong>当前已确认 v{data.current.displayVersion}</strong>}</header>
    {!data && !notice && <p aria-live="polite">正在读取岗位理解…</p>}
    {!data && notice && <p role="alert">{notice}。<button type="button" onClick={() => setLoadAttempt((value) => value + 1)}>重试</button></p>}
    {data?.current && <article className="hr-context-current"><h3>当前已确认 v{data.current.displayVersion}</h3><p>{data.current.summary}</p></article>}
    {data && <section aria-label="待确认草稿"><h3>{data.drafts.length} 个待确认草稿</h3>
      {data.drafts.length === 0 && <p>当前没有待确认上下文草稿。</p>}
      {data.drafts.map((draft) => {
        const chosen = selected[draft.contextVersionId] ?? [];
        const thisConflict = conflict?.draftId === draft.contextVersionId ? conflict : null;
        return <article data-context-draft key={draft.contextVersionId}><h4>草稿 v{draft.displayVersion}</h4><p>{draft.summary}</p>
          <fieldset disabled={readOnly}><legend>选择要确认的模块</legend>{Object.entries(draft.modules).map(([key, value]) => <label key={key}><input type="checkbox" checked={chosen.includes(key)} onChange={() => setSelected((current) => ({ ...current, [draft.contextVersionId]: chosen.includes(key) ? chosen.filter((item) => item !== key) : [...chosen, key] }))} /><span><strong>{MODULE_LABELS[key] ?? key}</strong>{moduleText(value)}</span></label>)}</fieldset>
          <button disabled={readOnly || chosen.length === 0} type="button" onClick={() => void confirm(draft)}>确认选中模块</button>
          {thisConflict && <div className="hr-context-conflict" role="alert"><p>基线已变化{thisConflict.changedModules.length > 0 ? `：${thisConflict.changedModules.map((key) => MODULE_LABELS[key] ?? key).join("、")}` : "，请核对当前版本"}。</p>{thisConflict.changedModules.map((key) => <section key={key}><h5>{MODULE_LABELS[key] ?? key}</h5><p>确认前：{moduleText((thisConflict.left[key] as Record<string, unknown> | undefined) ?? {})}</p><p>当前基线：{moduleText((thisConflict.right[key] as Record<string, unknown> | undefined) ?? {})}</p></section>)}<button disabled={readOnly} type="button" onClick={() => void confirm(draft, true)}>按新基线重试</button></div>}
        </article>;
      })}
    </section>}
    {data && <button aria-expanded={historyOpen} ref={historyOpenButton} type="button" onClick={() => setHistoryOpen(true)}>历史版本（{data.history.length}）</button>}
    {historyOpen && data && <><button aria-label="关闭历史版本遮罩" className="hr-drawer-backdrop" type="button" onClick={() => setHistoryOpen(false)} /><aside aria-label="岗位上下文历史版本" aria-modal="true" className="hr-mobile-drawer" role="dialog" onKeyDown={(event) => trapDialogFocus(event, () => setHistoryOpen(false))}><header><h3>历史版本（{data.history.length}）</h3><button aria-label="关闭历史版本" ref={historyClose} type="button" onClick={() => setHistoryOpen(false)}>关闭</button></header><ol>{data.history.map((version) => <li key={version.contextVersionId}><strong>v{version.displayVersion}</strong><span>{version.status === "confirmed" ? "已确认" : version.status === "superseded" ? "历史版本" : "草稿"}</span><p>{version.summary}</p></li>)}</ol></aside></>}
    {notice && data && <p role="status">{notice}。<button type="button" onClick={() => setLoadAttempt((value) => value + 1)}>重试</button></p>}
  </section>;
}
