import { useEffect, useState, type FormEvent } from "react";

import type { PlanBatch, PlanRow, SdkApi, SdkRole } from "./sdkApi";

type Props = {
  client: SdkApi;
  csrfToken: string;
  role: SdkRole;
  readOnly: boolean;
  onSelect: (row: PlanRow | null) => void;
};

export function PlanIntake({ client, csrfToken, role, readOnly, onSelect }: Props) {
  const [batches, setBatches] = useState<PlanBatch[]>([]);
  const [batchPage, setBatchPage] = useState(1);
  const [batchTotal, setBatchTotal] = useState(0);
  const [batchId, setBatchId] = useState<number | null>(null);
  const [rows, setRows] = useState<PlanRow[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [module, setModule] = useState("");
  const [priority, setPriority] = useState("");
  const [owner, setOwner] = useState("");
  const [selected, setSelected] = useState<PlanRow | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let live = true;
    void client.planImports(batchPage).then(result => {
      if (!live) return;
      setBatches(result.items);
      setBatchTotal(result.total);
      setBatchId(current => result.items.some(item => item.id === current) ? current : result.items[0]?.id ?? null);
    }).catch(() => { if (live) setError("无法读取年度计划批次。"); });
    return () => { live = false; };
  }, [client, batchPage]);

  useEffect(() => {
    if (batchId === null) { setRows([]); setTotal(0); return; }
    let live = true;
    void client.planRows(batchId, page, { module: module.trim() || undefined, priority: priority.trim() || undefined, owner: owner.trim() || undefined })
      .then(result => { if (live) { setRows(result.items); setTotal(result.total); } })
      .catch(() => { if (live) setError("无法读取年度计划来源行。"); });
    return () => { live = false; };
  }, [client, batchId, page, module, priority, owner]);

  function clearSelection() { setSelected(null); onSelect(null); }

  async function upload(event: FormEvent) {
    event.preventDefault(); setError(""); setNotice("");
    if (!file) { setError("请先选择 CSV 文件。"); return; }
    if (!file.name.toLowerCase().endsWith(".csv")) { setError("请选择 CSV 文件。"); return; }
    try {
      const imported = await client.uploadPlan(file, csrfToken);
      const updated = await client.planImports(1);
      setBatchPage(1); setBatchTotal(updated.total);
      setBatches(updated.items.some(item => item.id === imported.id) ? updated.items : [imported, ...updated.items]);
      setBatchId(imported.id); setPage(1); clearSelection();
      setNotice(`已读取 ${imported.row_count} 条来源记录，其中 ${imported.task_count} 条任务。`);
    } catch { setError("导入失败，请核对文件格式、权限和大小。"); }
  }

  async function select(row: PlanRow) {
    setError("");
    try { const detail = await client.planRow(row.id); setSelected(detail); onSelect(detail); }
    catch { setError("无法读取来源行。"); }
  }

  const batch = batches.find(item => item.id === batchId);
  return <section className="sdk-panel sdk-plan">
    <h2>2026 SDK 年度计划</h2>
    <p>原始任务计划，用于梳理需求；来源状态与责任人按文件原文展示。</p>
    {error && <p role="alert" className="sdk-error">{error}</p>}
    {notice && <p role="status" className="sdk-notice">{notice}</p>}
    {role === "contributor" && !readOnly && <form onSubmit={upload} className="sdk-plan-upload">
      <label>选择年度计划 CSV <input type="file" accept=".csv,text/csv" onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
      <button type="submit">导入 CSV</button>
    </form>}
    {batches.length > 0 && <>
      <label className="sdk-plan-batch">来源批次 <select value={batchId ?? ""} onChange={event => { setBatchId(Number(event.target.value)); setPage(1); clearSelection(); }}>
        {batches.map(item => <option key={item.id} value={item.id}>{item.filename} · {item.created_at.slice(0, 10)}</option>)}
      </select></label>
      <div className="sdk-pagination"><button type="button" disabled={batchPage <= 1} onClick={() => { clearSelection(); setBatchPage(batchPage - 1); }}>上一批次页</button><span>批次第 {batchPage} 页，共 {batchTotal} 批</span><button type="button" disabled={batchPage * 20 >= batchTotal} onClick={() => { clearSelection(); setBatchPage(batchPage + 1); }}>下一批次页</button></div>
      {batch && <p className="sdk-id">{batch.row_count} 条来源记录 · {batch.task_count} 条任务 · {batch.module_count} 个栏目 · SHA-256 {batch.sha256}</p>}
      <div className="sdk-plan-filters">
        <input aria-label="按栏目筛选" placeholder="栏目" value={module} onChange={event => { setModule(event.target.value); setPage(1); clearSelection(); }} />
        <input aria-label="按优先级筛选" placeholder="优先级" value={priority} onChange={event => { setPriority(event.target.value); setPage(1); clearSelection(); }} />
        <input aria-label="按原责任人筛选" placeholder="原责任人" value={owner} onChange={event => { setOwner(event.target.value); setPage(1); clearSelection(); }} />
      </div>
      <ul className="sdk-plan-rows">{rows.map(row => <li key={row.id}>
        <button type="button" onClick={() => { void select(row); }}>{row.kind === "heading" ? "栏目：" : "任务："}{row.cells[1] || row.module}</button>
        <span>{row.module} · {row.cells[6] || "未填优先级"} · 原文件第 {row.physical_line} 行{row.story_id ? ` · 需求 #${row.story_id}` : ""}</span>
      </li>)}</ul>
      {rows.length === 0 && <p>当前筛选没有来源行。</p>}
      <div className="sdk-pagination"><button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页，共 {total} 条</span><button type="button" disabled={page * 50 >= total} onClick={() => setPage(page + 1)}>下一页</button></div>
      {selected && <div className="sdk-plan-detail"><h3>来源详情</h3>
        <p>逻辑记录 {selected.record_number} · 原文件第 {selected.physical_line} 行 · {selected.module}</p>
        <dl>{["平台", "任务", "责任人", "版本", "开始时间", "结束时间", "优先级", "风险点", "完成情况", "备注"].map((name, index) => <div key={name}><dt>{name}</dt><dd className="sdk-pre">{selected.cells[index] || "未填写"}</dd></div>)}</dl>
        <p>{selected.story_id ? `已关联正式需求 #${selected.story_id}` : selected.conversion_state ? `转需求状态：${selected.conversion_state}` : "尚未形成正式需求"}</p>
      </div>}
    </>}
    {batches.length === 0 && <p>尚未导入年度计划。</p>}
  </section>;
}
