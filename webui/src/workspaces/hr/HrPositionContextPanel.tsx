import { useEffect, useState } from "react";
import type { HrR12Api } from "../../hrR12Api";
import type { HrContextVersion } from "../../hrR12Types";

export function HrPositionContextPanel({ api, positionId }: { api: Pick<HrR12Api, "context" | "confirmContext">; positionId: string }) {
  const [drafts, setDrafts] = useState<HrContextVersion[]>([]); const [selected, setSelected] = useState<string[]>([]); const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { const controller = new AbortController(); void api.context(positionId, controller.signal).then((value) => { if (!controller.signal.aborted) setDrafts(value.drafts); }).catch(() => { if (!controller.signal.aborted) setNotice("上下文暂时不可用"); }); return () => controller.abort(); }, [api, positionId]);
  const draft = drafts[0] ?? null;
  async function confirm() { if (!draft) return; try { await api.confirmContext(positionId, draft.contextVersionId, selected, draft.rowVersion, crypto.randomUUID()); setNotice("已确认岗位上下文"); } catch (error) { setNotice((error as { status?: number }).status === 409 ? "基线已变化，请先比较差异后重试。" : "确认未完成，请重试。"); } }
  if (!draft) return <section aria-label="岗位上下文"><h2>岗位上下文</h2><p>{notice ?? "当前没有待确认上下文草稿。"}</p></section>;
  return <section aria-label="岗位上下文"><h2>岗位上下文草稿 v{draft.displayVersion}</h2><p>{draft.summary}</p>{Object.entries(draft.modules).map(([key, value]) => <label key={key}><input type="checkbox" checked={selected.includes(key)} onChange={() => setSelected((current) => current.includes(key) ? current.filter((value) => value !== key) : [...current, key])} />{key}: {value}</label>)}<button type="button" onClick={() => void confirm()}>确认选中模块</button>{notice && <p role="status">{notice}</p>}</section>;
}
