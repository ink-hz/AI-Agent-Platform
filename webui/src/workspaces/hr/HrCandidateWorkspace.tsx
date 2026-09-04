import { useEffect, useState } from "react";
import type { HrR12Api } from "../../hrR12Api";
import type { HrCandidateDraft } from "../../hrR12Types";

export function HrCandidateWorkspace({ api, positionId }: { api: Pick<HrR12Api, "candidateDrafts" | "retryDraft" | "confirmDraft" | "startTask">; positionId: string }) {
  const [drafts, setDrafts] = useState<HrCandidateDraft[]>([]); const [feedback, setFeedback] = useState(""); const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { const controller = new AbortController(); void api.candidateDrafts(positionId, controller.signal).then((value) => { if (!controller.signal.aborted) setDrafts(value); }).catch(() => setNotice("候选人材料暂时不可用")); return () => controller.abort(); }, [api, positionId]);
  async function retry(draft: HrCandidateDraft) { try { const next = await api.retryDraft(positionId, draft.draftId, crypto.randomUUID()); setDrafts((items) => items.map((item) => item.draftId === draft.draftId ? { ...item, ...next } : item)); } catch { setNotice("重试解析未完成，请重试。"); } }
  return <section aria-label="候选人"><h2>候选人</h2><button type="button">批量上传简历</button>{drafts.map((draft) => <article key={draft.draftId}><strong>{draft.filename}</strong><p>{draft.state === "failed" ? `解析失败：${draft.error ?? "未知错误"}` : "待确认"}</p>{draft.state === "failed" ? <button type="button" onClick={() => void retry(draft)}>重试解析</button> : <button type="button" onClick={() => void api.confirmDraft(positionId, draft.draftId, crypto.randomUUID())}>确认候选人</button>}</article>)}<label>人工纠正<textarea value={feedback} onChange={(event) => setFeedback(event.target.value)} /></label><button type="button" onClick={() => void api.startTask(positionId, "candidate_comparison", crypto.randomUUID(), { feedback })}>比较候选人</button>{notice && <p role="status">{notice}</p>}</section>;
}
