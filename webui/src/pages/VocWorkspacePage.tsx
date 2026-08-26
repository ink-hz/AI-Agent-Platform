import { useEffect, useRef, useState } from "react";

import {
  VocApiError,
  vocApi,
  type EvidenceBasis,
  type VocApi,
  type VocDetail,
  type VocDraft,
  type VocDraftContent,
  type VocSummary,
} from "../vocApi";

const BASIS_LABELS: Record<EvidenceBasis, string> = {
  customer_quote: "客户原话",
  employee_observation: "员工观察",
  employee_relay: "员工转述",
  unknown: "暂不确定",
};

function message(error: unknown): string {
  if (error instanceof VocApiError) {
    if (error.status === 409) return "草稿已在其他页面更新，请重新载入后继续。";
    if (error.status === 404) return "没有找到这条 VOC，或你没有查看权限。";
    if (error.status === 503) return "VOC 服务暂时忙，请稍后重试；你输入的内容仍保留在这里。";
  }
  return "暂时无法完成操作，请稍后重试。";
}

function editable(draft: VocDraft, changes: Partial<VocDraftContent>): VocDraft {
  return { ...draft, content: { ...draft.content, ...changes } };
}

export function VocWorkspacePage({ csrfToken, api = vocApi }: { csrfToken: string; api?: VocApi }) {
  const [source, setSource] = useState("");
  const [draft, setDraft] = useState<VocDraft | null>(null);
  const [draftDirty, setDraftDirty] = useState(false);
  const [items, setItems] = useState<VocSummary[]>([]);
  const [detail, setDetail] = useState<VocDetail | null>(null);
  const [supplement, setSupplement] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createdVocNo, setCreatedVocNo] = useState<string | null>(null);
  const createRequest = useRef<string | null>(null);
  const editRequest = useRef<string | null>(null);
  const cancelRequest = useRef<string | null>(null);
  const submitRequest = useRef<string | null>(null);
  const supplementRequest = useRef<string | null>(null);

  const reloadList = async () => setItems(await api.listVocs());
  const reloadDraft = async () => {
    setDraft(await api.activeDraft());
    setDraftDirty(false);
  };
  const changeDraft = (changes: Partial<VocDraftContent>) => {
    editRequest.current = null;
    submitRequest.current = null;
    setDraftDirty(true);
    setDraft((current) => current ? editable(current, changes) : current);
  };

  useEffect(() => {
    let current = true;
    void Promise.all([api.activeDraft(), api.listVocs()]).then(([active, vocs]) => {
      if (!current) return;
      setDraft(active); setDraftDirty(false); setItems(vocs);
      if (active) setSource(active.source_text);
    }).catch((failure) => { if (current) setError(message(failure)); });
    return () => { current = false; };
  }, [api]);

  const organize = async () => {
    if (!source.trim() || busy) return;
    setBusy("organize"); setError(null); setCreatedVocNo(null);
    createRequest.current ??= crypto.randomUUID();
    try {
      const value = await api.createDraft(createRequest.current, source.trim(), csrfToken);
      createRequest.current = null; editRequest.current = null; cancelRequest.current = null; submitRequest.current = null; setDraft(value); setDraftDirty(false);
    } catch (failure) {
      setError(message(failure));
      if (failure instanceof VocApiError && failure.status === 409) void reloadDraft();
    }
    finally { setBusy(null); }
  };

  const save = async () => {
    if (!draft || busy) return;
    setBusy("save"); setError(null); editRequest.current ??= crypto.randomUUID();
    try {
      const value = await api.updateDraft(draft.draft_id, editRequest.current, draft.version, draft.content, csrfToken);
      editRequest.current = null; setDraft(value); setDraftDirty(false);
    } catch (failure) {
      setError(message(failure));
      if (failure instanceof VocApiError && failure.status === 409) void reloadDraft();
    } finally { setBusy(null); }
  };

  const cancel = async () => {
    if (!draft || busy) return;
    setBusy("cancel"); setError(null); cancelRequest.current ??= crypto.randomUUID();
    try {
      await api.cancelDraft(draft.draft_id, cancelRequest.current, draft.version, csrfToken);
      cancelRequest.current = null; editRequest.current = null; submitRequest.current = null; setDraft(null); setDraftDirty(false); setSource("");
    } catch (failure) {
      setError(message(failure));
      if (failure instanceof VocApiError && failure.status === 409) void reloadDraft();
    }
    finally { setBusy(null); }
  };

  const submit = async () => {
    if (!draft || busy) return;
    setBusy("submit"); setError(null);
    try {
      let persisted = draft;
      if (draftDirty) {
        editRequest.current ??= crypto.randomUUID();
        persisted = await api.updateDraft(draft.draft_id, editRequest.current, draft.version, draft.content, csrfToken);
        editRequest.current = null;
        setDraft(persisted);
        setDraftDirty(false);
      }
      submitRequest.current ??= crypto.randomUUID();
      const value = await api.submitDraft(persisted.draft_id, submitRequest.current, persisted.version, csrfToken);
      submitRequest.current = null; editRequest.current = null; cancelRequest.current = null; setCreatedVocNo(value.voc_no); setDraft(null); setDraftDirty(false); setSource("");
      await reloadList();
    } catch (failure) {
      setError(message(failure));
      if (failure instanceof VocApiError && failure.status === 409) void reloadDraft();
    }
    finally { setBusy(null); }
  };

  const openDetail = async (vocNo: string) => {
    setBusy("detail"); setError(null);
    try { setDetail(await api.getVoc(vocNo)); }
    catch (failure) { setError(message(failure)); }
    finally { setBusy(null); }
  };

  const addSupplement = async () => {
    if (!detail || !supplement.trim() || busy) return;
    setBusy("supplement"); setError(null); supplementRequest.current ??= crypto.randomUUID();
    try {
      await api.supplementVoc(detail.voc_no, supplementRequest.current, supplement.trim(), csrfToken);
      supplementRequest.current = null; setSupplement(""); setDetail(await api.getVoc(detail.voc_no)); await reloadList();
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(null); }
  };

  return <section className="voc-workspace">
    <header className="voc-hero">
      <p>VOC 洞察助手</p>
      <h1>把客户声音，整理成可行动的记录</h1>
      <span>先形成草稿，由你检查和修改；只有点击确认后才会正式入库。</span>
    </header>

    <div className="voc-layout">
      <section className="voc-compose-panel">
        <label htmlFor="voc-source">客户反馈</label>
        <textarea id="voc-source" aria-label="客户反馈" maxLength={4000} value={source} onChange={(event) => { createRequest.current = null; setSource(event.target.value); }} placeholder="例如：客户反馈设备连续运行两小时后发热，并出现自动关机…" />
        <div className="voc-compose-actions"><span>{source.length}/4000</span><button type="button" disabled={!source.trim() || Boolean(busy)} onClick={() => void organize()}>{error && createRequest.current ? "重新整理" : busy === "organize" ? "正在整理…" : "整理成草稿"}</button></div>
      </section>

      {error && <p className="voc-message is-error" role="alert">{error}</p>}
      {createdVocNo && <p className="voc-message is-success" role="status">已正式提交 <strong>{createdVocNo}</strong></p>}

      {draft && <section className="voc-draft-panel" aria-label="VOC 草稿">
        <header><div><p>待确认草稿</p><h2>请检查整理结果</h2></div><span>版本 {draft.version}</span></header>
        <div className="voc-form-grid">
          <label>客户<input aria-label="客户" value={draft.content.customer ?? ""} onChange={(event) => changeDraft({ customer: event.target.value || null })} /></label>
          <label>产品或场景<input aria-label="产品或场景" value={draft.content.product_or_scenario ?? ""} onChange={(event) => changeDraft({ product_or_scenario: event.target.value || null })} /></label>
          <label className="is-wide">反馈内容<input aria-label="反馈内容" required value={draft.content.feedback} onChange={(event) => changeDraft({ feedback: event.target.value })} /></label>
          <label className="is-wide">影响<input aria-label="影响" value={draft.content.impact ?? ""} onChange={(event) => changeDraft({ impact: event.target.value || null })} /></label>
          <label>信息来源<select aria-label="信息来源" value={draft.content.evidence_basis} onChange={(event) => changeDraft({ evidence_basis: event.target.value as EvidenceBasis })}>{Object.entries(BASIS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label className="is-wide">待补信息<textarea aria-label="待补信息" value={draft.content.gaps.join("\n")} onChange={(event) => changeDraft({ gaps: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean).slice(0, 8) })} /></label>
        </div>
        <div className="voc-draft-actions"><button type="button" disabled={Boolean(busy)} onClick={() => void cancel()}>取消草稿</button><button type="button" disabled={Boolean(busy) || !draft.content.feedback.trim()} onClick={() => void save()}>保存修改</button><button className="is-submit" type="button" disabled={Boolean(busy) || !draft.content.feedback.trim()} onClick={() => void submit()}>确认提交 VOC</button></div>
      </section>}

      <section className="voc-records">
        <header><div><p>我的 VOC</p><h2>已提交的客户声音</h2></div><button type="button" disabled={Boolean(busy)} onClick={() => void reloadList()}>刷新</button></header>
        {items.length === 0 ? <p className="voc-empty">还没有正式 VOC。先在上方描述一条客户反馈吧。</p> : <ol>{items.map((item) => <li key={item.voc_no}><button type="button" onClick={() => void openDetail(item.voc_no)}><strong>{item.voc_no}</strong><span>{item.latest_content}</span><small>版本 {item.revision}</small></button></li>)}</ol>}
      </section>
    </div>

    {detail && <aside className="voc-detail" aria-label="VOC 详情"><header><div><p>VOC 详情</p><h2>{detail.voc_no}</h2></div><button type="button" aria-label="关闭 VOC 详情" onClick={() => setDetail(null)}>×</button></header><ol>{detail.entries.map((entry) => <li key={entry.revision}><span>版本 {entry.revision}</span><p>{entry.content}</p></li>)}</ol><label>补充信息<textarea aria-label="补充信息" maxLength={4000} value={supplement} onChange={(event) => { supplementRequest.current = null; setSupplement(event.target.value); }} /></label><button className="voc-supplement-submit" type="button" disabled={!supplement.trim() || Boolean(busy)} onClick={() => void addSupplement()}>提交补充</button></aside>}
  </section>;
}
