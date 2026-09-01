import { useMemo, useState } from "react";

import type { FeedbackIssueSummary, IssueStatus, ReviewInboxItem } from "../../types";
import type { ReviewIssueFilters } from "./ReviewWorkspace";


export const STATUS_LABELS: Record<IssueStatus, string> = {
  unknown: "生命周期状态暂不可用",
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

export interface IssueFilterOption {
  value: string;
  label: string;
}

const DISPOSITION_LABELS: Record<FeedbackIssueSummary["disposition"], string> = {
  actionable: "需处理",
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

const FAILURE_LAYER_OPTIONS = [
  "channel", "context", "guardrail", "schema", "planner",
  "capability_evidence", "coverage", "synthesis", "outcome", "trace_eval",
];


export function IssueList({
  issues,
  inbox,
  selectedId,
  selectedTurnKey,
  onSelect,
  onSelectInbox,
  showAgentFilter = true,
  statusFilter,
  onStatusFilterChange,
  statusOptions,
  statusPresentation = "lifecycle",
  serverFilters,
  onServerFiltersChange,
  presentation = "default",
  showAgentIdentity = true,
  totalCount,
}: {
  issues: FeedbackIssueSummary[];
  inbox: ReviewInboxItem[];
  selectedId: string | null;
  selectedTurnKey: string | null;
  onSelect: (id: string) => void;
  onSelectInbox: (turnKey: string) => void;
  showAgentFilter?: boolean;
  statusFilter?: string;
  onStatusFilterChange?: (status: string) => void;
  statusOptions?: IssueFilterOption[];
  statusPresentation?: "lifecycle" | "disposition";
  serverFilters?: ReviewIssueFilters;
  onServerFiltersChange?: (filters: ReviewIssueFilters) => void;
  presentation?: "default" | "fae-governance";
  showAgentIdentity?: boolean;
  totalCount?: number;
}) {
  const [agent, setAgent] = useState("");
  const [layer, setLayer] = useState("");
  const [priority, setPriority] = useState("");
  const [owner, setOwner] = useState("");
  const [localStatus, setLocalStatus] = useState("");
  const status = statusFilter ?? localStatus;
  const [createdAfter, setCreatedAfter] = useState("");
  const server = Boolean(onServerFiltersChange);
  const selectedLayer = server ? serverFilters?.failure_layer ?? "" : layer;
  const selectedPriority = server ? serverFilters?.priority ?? "" : priority;
  const selectedOwner = server ? serverFilters?.owner ?? "" : owner;
  const selectedCreatedAfter = server ? serverFilters?.created_after?.slice(0, 10) ?? "" : createdAfter;
  const selectedQuery = server ? serverFilters?.query ?? "" : "";
  const updateServer = (updates: ReviewIssueFilters) => onServerFiltersChange?.({ ...serverFilters, ...updates, offset: 0 });
  const filtered = useMemo(() => server ? issues : issues.filter((item) => (
    (!agent || item.agent_id === agent)
    && (!layer || item.failure_layer === layer)
    && (!priority || item.priority === priority)
    && (!owner || (item.owner || "").includes(owner))
    && (onStatusFilterChange || !status || (status === "open"
      ? !["closed", "duplicate", "not_actionable", "wont_fix"].includes(item.progress.status)
      : item.progress.status === status))
    && (!createdAfter || !item.created_at || item.created_at >= `${createdAfter}T00:00:00`)
  )), [agent, createdAfter, issues, layer, owner, priority, server, status]);
  const agents = [...new Set(issues.map((item) => item.agent_id))];
  const layers = [...new Set(issues.map((item) => item.failure_layer).filter(Boolean))] as string[];
  const serverLayers = selectedLayer && !FAILURE_LAYER_OPTIONS.includes(selectedLayer)
    ? [selectedLayer, ...FAILURE_LAYER_OPTIONS]
    : FAILURE_LAYER_OPTIONS;
  const faeGovernance = presentation === "fae-governance";
  const queueOptions = [
    { value: "open", label: "需要行动" },
    { value: "pending_triage", label: "待分诊" },
    { value: "awaiting_replay", label: "待复跑" },
    { value: "closed", label: "已闭环" },
    { value: "", label: "全部" },
  ];
  const updateStatus = (value: string) => onStatusFilterChange
    ? onStatusFilterChange(value)
    : setLocalStatus(value);
  const queueLabel = queueOptions.find((option) => option.value === status)?.label ?? "反馈事项";
  const effectiveStatusOptions = statusOptions ?? Object.entries(STATUS_LABELS)
    .filter(([value]) => !faeGovernance || value !== "unknown")
    .map(([value, label]) => ({ value, label }));

  return <aside className="review-list-panel">
    <div className="review-list-heading"><div><p>治理队列</p><h2>{faeGovernance ? queueLabel : "反馈事项"}</h2></div><span>{totalCount ?? filtered.length}</span></div>
    {faeGovernance && <nav className="fae-governance-queues" aria-label="治理队列视图">{queueOptions.map((option) => <button type="button" aria-pressed={status === option.value} className={status === option.value ? "is-current" : undefined} key={option.value || "all"} onClick={() => updateStatus(option.value)}>{option.label}</button>)}</nav>}
    {faeGovernance && <select className="fae-governance-status-select" aria-label="状态" value={status} onChange={(event) => updateStatus(event.target.value)}><option value="">全部状态</option>{effectiveStatusOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select>}
    <div className="review-filters" aria-label="事项筛选">
      {showAgentFilter && <select aria-label="Agent" value={agent} onChange={(event) => setAgent(event.target.value)}><option value="">全部 Agent</option>{agents.map((value) => <option key={value}>{value}</option>)}</select>}
      {server && <input aria-label="事项搜索" placeholder="搜索标题" value={selectedQuery} onChange={(event) => updateServer({ query: event.target.value || undefined })} />}
      <select aria-label="失败层" value={selectedLayer} onChange={(event) => server ? updateServer({ failure_layer: event.target.value || undefined }) : setLayer(event.target.value)}><option value="">全部失败层</option>{(server ? serverLayers : layers).map((value) => <option key={value}>{value}</option>)}</select>
      <select aria-label="优先级" value={selectedPriority} onChange={(event) => server ? updateServer({ priority: event.target.value || undefined }) : setPriority(event.target.value)}><option value="">全部优先级</option>{["P0", "P1", "P2", "P3"].map((value) => <option key={value}>{value}</option>)}</select>
      {!faeGovernance && <select aria-label="状态" value={status} onChange={(event) => updateStatus(event.target.value)}><option value="">全部状态</option>{effectiveStatusOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select>}
      <input aria-label="负责人" placeholder="负责人" value={selectedOwner} onChange={(event) => server ? updateServer({ owner: event.target.value || undefined }) : setOwner(event.target.value)} />
      <input aria-label="创建日期起" type="date" value={selectedCreatedAfter} onChange={(event) => server ? updateServer({ created_after: event.target.value ? `${event.target.value}T00:00:00+08:00` : undefined }) : setCreatedAfter(event.target.value)} />
    </div>
    {inbox.length > 0 && <section className="review-inbox"><h3>待纳管回答 <span>{inbox.length}</span></h3>{inbox.map((item) => <button className={selectedTurnKey === item.turn_key ? "is-selected" : ""} key={item.turn_key} onClick={() => onSelectInbox(item.turn_key)}><strong>{item.question || "未记录问题"}</strong><small>{showAgentIdentity ? `${item.agent_id} · ` : ""}{item.feedback_count ?? item.feedback_keys.length} 条负反馈</small></button>)}</section>}
    <div className="review-issue-list">{filtered.map((item) => <button className={selectedId === item.id ? "is-selected" : ""} key={item.id} onClick={() => onSelect(item.id)}><span><b>{item.priority}</b>{statusPresentation === "disposition" ? DISPOSITION_LABELS[item.disposition] : STATUS_LABELS[item.progress.status]}</span><strong className="review-issue-title">{item.title}</strong><small>{showAgentIdentity ? `${item.agent_id} · ` : ""}{item.failure_layer || "待归因"} · {item.owner || "未分配"}</small>{item.progress.missing_gates === null ? <em>下一步暂不可用</em> : item.progress.missing_gates.length > 0 && <em>{faeGovernance ? "下一步：" : "缺："}{faeGovernance ? (GATE_LABELS[item.progress.missing_gates[0]] || item.progress.missing_gates[0]) : item.progress.missing_gates.map((gate) => GATE_LABELS[gate] || gate).join("、")}</em>}</button>)}</div>
  </aside>;
}
