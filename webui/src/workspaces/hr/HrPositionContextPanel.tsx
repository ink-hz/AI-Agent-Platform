import { useEffect, useRef, useState } from "react";
import type { HrR12Api } from "../../hrR12Api";
import type { HrContextVersion } from "../../hrR12Types";

const MODULE_LABELS: Record<string, string> = {
  mission: "岗位使命", jd: "JD", jr: "JR", competencies: "能力要求",
  profile: "人才画像", sourcing: "搜寻策略", interview_standard: "面试标准", unknowns: "未知项",
};
type ContextState = { current: HrContextVersion | null; drafts: HrContextVersion[]; history: HrContextVersion[] };

function moduleText(value: Record<string, unknown>): string {
  const preferred = [value.summary, value.text, value.title].find((item) => typeof item === "string");
  return typeof preferred === "string" ? preferred : JSON.stringify(value, null, 2);
}

export function HrPositionContextPanel({ api, positionId }: {
  api: Pick<HrR12Api, "context" | "confirmContext" | "compareContext">; positionId: string;
}) {
  const [data, setData] = useState<ContextState | null>(null);
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ draftId: string; changedModules: string[] } | null>(null);
  const mutation = useRef<AbortController | null>(null);

  async function load(signal?: AbortSignal): Promise<ContextState> {
    const value = await api.context(positionId, signal);
    if (!signal?.aborted) setData(value);
    return value;
  }
  useEffect(() => {
    const controller = new AbortController(); setData(null); setNotice(null); setConflict(null);
    void load(controller.signal).catch(() => { if (!controller.signal.aborted) setNotice("上下文暂时不可用"); });
    return () => { controller.abort(); mutation.current?.abort(); };
  }, [api, positionId]);

  async function confirm(draft: HrContextVersion, retry = false) {
    const modules = selected[draft.contextVersionId] ?? [];
    if (modules.length === 0) return;
    mutation.current?.abort(); const controller = new AbortController(); mutation.current = controller;
    setNotice(null);
    try {
      const baseline = retry ? data?.current?.contextVersionId ?? null : draft.baseContextVersionId;
      await api.confirmContext(positionId, draft.contextVersionId, baseline, modules, draft.rowVersion, crypto.randomUUID(), controller.signal);
      if (!controller.signal.aborted) { setConflict(null); setNotice("已确认岗位上下文"); await load(controller.signal); }
    } catch (error) {
      if (controller.signal.aborted) return;
      if ((error as { status?: number }).status !== 409) { setNotice("确认未完成，请重试。"); return; }
      try {
        const fresh = await load(controller.signal);
        const refreshedDraft = fresh.drafts.find((item) => item.contextVersionId === draft.contextVersionId) ?? draft;
        let changedModules: string[] = [];
        if (refreshedDraft.baseContextVersionId && fresh.current && refreshedDraft.baseContextVersionId !== fresh.current.contextVersionId) {
          changedModules = (await api.compareContext(positionId, refreshedDraft.baseContextVersionId, fresh.current.contextVersionId, controller.signal)).changedModules;
        }
        if (!controller.signal.aborted) { setConflict({ draftId: draft.contextVersionId, changedModules }); setNotice("基线已变化，请比较差异后按新基线重试。"); }
      } catch { if (!controller.signal.aborted) setNotice("基线已变化，重新读取失败，请稍后重试。"); }
    }
  }

  return <section aria-label="岗位上下文" className="hr-r12-panel hr-context-panel">
    <header><div><span>POSITION KNOWLEDGE</span><h2>岗位上下文</h2></div>{data?.current && <strong>当前已确认 v{data.current.displayVersion}</strong>}</header>
    {!data && <p aria-live="polite">{notice ?? "正在读取岗位上下文…"}</p>}
    {data?.current && <article className="hr-context-current"><h3>当前已确认 v{data.current.displayVersion}</h3><p>{data.current.summary}</p></article>}
    {data && <section aria-label="待确认草稿"><h3>{data.drafts.length} 个待确认草稿</h3>
      {data.drafts.length === 0 && <p>当前没有待确认上下文草稿。</p>}
      {data.drafts.map((draft) => {
        const chosen = selected[draft.contextVersionId] ?? [];
        const thisConflict = conflict?.draftId === draft.contextVersionId ? conflict : null;
        return <article data-context-draft key={draft.contextVersionId}><h4>草稿 v{draft.displayVersion}</h4><p>{draft.summary}</p>
          <fieldset><legend>选择要确认的模块</legend>{Object.entries(draft.modules).map(([key, value]) => <label key={key}><input type="checkbox" checked={chosen.includes(key)} onChange={() => setSelected((current) => ({ ...current, [draft.contextVersionId]: chosen.includes(key) ? chosen.filter((item) => item !== key) : [...chosen, key] }))} /><span><strong>{MODULE_LABELS[key] ?? key}</strong>{moduleText(value)}</span></label>)}</fieldset>
          <button disabled={chosen.length === 0} type="button" onClick={() => void confirm(draft)}>确认选中模块</button>
          {thisConflict && <div className="hr-context-conflict" role="alert"><p>基线已变化{thisConflict.changedModules.length > 0 ? `：${thisConflict.changedModules.map((key) => MODULE_LABELS[key] ?? key).join("、")}` : "，请核对当前版本"}。</p><button type="button" onClick={() => void confirm(draft, true)}>按新基线重试</button></div>}
        </article>;
      })}
    </section>}
    {data && <details><summary>历史版本（{data.history.length}）</summary><ol>{data.history.map((version) => <li key={version.contextVersionId}><strong>v{version.displayVersion}</strong><span>{version.status === "confirmed" ? "已确认" : version.status === "superseded" ? "历史版本" : "草稿"}</span><p>{version.summary}</p></li>)}</ol></details>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
