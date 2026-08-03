import { useMemo, useState } from "react";

import type { FeedbackIssueSummary, IssueStatus, ReviewInboxItem } from "../../types";


export const STATUS_LABELS: Record<IssueStatus, string> = {
  pending_triage: "待归因",
  fixing: "修复中",
  awaiting_merge: "待合并",
  awaiting_deploy: "待部署",
  awaiting_replay: "待复跑",
  awaiting_review: "待语义复审",
  closed: "已闭环",
  duplicate: "重复事项",
  not_actionable: "无需处理",
  wont_fix: "暂不修复",
};

export const GATE_LABELS: Record<string, string> = {
  failure_layer: "失败层归因",
  root_cause: "根因",
  owner: "负责人",
  linked_turn: "关联回答",
  fix_ready: "修复完成声明",
  verified_merge: "已验证合并",
  verified_deployment: "已验证部署",
  replay_runtime: "真实复跑",
  semantic_review: "独立语义复审",
  review_method: "复审方式",
  reviewer: "复审人",
  review_reason: "复审理由",
  build_identity_mismatch: "部署版本一致性",
  model_echo_unavailable: "provider 模型回显",
  actual_model_mismatch: "实际模型一致性",
};


export function IssueList({
  issues,
  inbox,
  selectedId,
  selectedTurnKey,
  onSelect,
  onSelectInbox,
}: {
  issues: FeedbackIssueSummary[];
  inbox: ReviewInboxItem[];
  selectedId: string | null;
  selectedTurnKey: string | null;
  onSelect: (id: string) => void;
  onSelectInbox: (turnKey: string) => void;
}) {
  const [agent, setAgent] = useState("");
  const [layer, setLayer] = useState("");
  const [priority, setPriority] = useState("");
  const [owner, setOwner] = useState("");
  const [status, setStatus] = useState("");
  const [createdAfter, setCreatedAfter] = useState("");
  const filtered = useMemo(() => issues.filter((item) => (
    (!agent || item.agent_id === agent)
    && (!layer || item.failure_layer === layer)
    && (!priority || item.priority === priority)
    && (!owner || (item.owner || "").includes(owner))
    && (!status || item.progress.status === status)
    && (!createdAfter || !item.created_at || item.created_at >= `${createdAfter}T00:00:00`)
  )), [agent, createdAfter, issues, layer, owner, priority, status]);
  const agents = [...new Set(issues.map((item) => item.agent_id))];
  const layers = [...new Set(issues.map((item) => item.failure_layer).filter(Boolean))] as string[];

  return <aside className="review-list-panel">
    <div className="review-list-heading"><div><p>治理队列</p><h2>反馈事项</h2></div><span>{filtered.length}/{issues.length}</span></div>
    <div className="review-filters" aria-label="事项筛选">
      <select aria-label="Agent" value={agent} onChange={(event) => setAgent(event.target.value)}><option value="">全部 Agent</option>{agents.map((value) => <option key={value}>{value}</option>)}</select>
      <select aria-label="失败层" value={layer} onChange={(event) => setLayer(event.target.value)}><option value="">全部失败层</option>{layers.map((value) => <option key={value}>{value}</option>)}</select>
      <select aria-label="优先级" value={priority} onChange={(event) => setPriority(event.target.value)}><option value="">全部优先级</option>{["P0", "P1", "P2", "P3"].map((value) => <option key={value}>{value}</option>)}</select>
      <select aria-label="状态" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
      <input aria-label="负责人" placeholder="负责人" value={owner} onChange={(event) => setOwner(event.target.value)} />
      <input aria-label="创建日期起" type="date" value={createdAfter} onChange={(event) => setCreatedAfter(event.target.value)} />
    </div>
    {inbox.length > 0 && <section className="review-inbox"><h3>待纳管回答 <span>{inbox.length}</span></h3>{inbox.map((item) => <button className={selectedTurnKey === item.turn_key ? "is-selected" : ""} key={item.turn_key} onClick={() => onSelectInbox(item.turn_key)}><strong>{item.question || "未记录问题"}</strong><small>{item.agent_id} · {item.feedback_keys.length} 条负反馈</small></button>)}</section>}
    <div className="review-issue-list">{filtered.map((item) => <button className={selectedId === item.id ? "is-selected" : ""} key={item.id} onClick={() => onSelect(item.id)}><span><b>{item.priority}</b>{STATUS_LABELS[item.progress.status]}</span><strong>{item.title}</strong><small>{item.agent_id} · {item.failure_layer || "待归因"} · {item.owner || "未分配"}</small>{item.progress.missing_gates.length > 0 && <em>缺：{item.progress.missing_gates.map((gate) => GATE_LABELS[gate] || gate).join("、")}</em>}</button>)}</div>
  </aside>;
}
