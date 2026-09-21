import { Fragment, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { AiEngineeringDocumentSlug } from "../aiEngineeringApi";
import type { PanoramaActionId, PanoramaData, PanoramaGroup, PanoramaNode } from "../panoramaTypes";
import { routePanoramaEdge, type PanoramaRect } from "../panoramaRouting";

const ACTION_LABELS: Record<PanoramaActionId, string> = {
  brain: "AI 助手", agents: "Agent 目录", missions: "任务", sessions: "会话", operations: "运行概览",
  review: "复审", activity: "运行事件", identity: "账号与权限", governance: "审计日志", access: "访问记录",
  account: "账号", "agent-admin": "Agent 状态", notes: "工程笔记", hr: "人力资源 · 独立入口",
  office: "行政服务 · 独立入口", voc: "客户洞察 · 独立入口", fae: "技术支持 · 独立入口",
};
const EXTERNAL_ACTIONS = new Set<PanoramaActionId>(["hr", "office", "voc", "fae"]);

interface CanvasProps {
  organization?: ReactNode;
  data: PanoramaData;
  selectedId: string | null;
  matchIds: Set<string>;
  isOwner: boolean;
  onSelect: (id: string | null) => void;
  onAction: (actionId: PanoramaActionId) => void;
  onEvidence: (slug: AiEngineeringDocumentSlug) => void;
}

interface StructuralEdge {
  id: string;
  from: { type: "role" | "node"; id: string };
  to: { type: "role" | "node"; id: string };
  fromRole?: string;
  toRole?: string;
  fromNode?: string;
  toNode?: string;
  kind: "supply" | "supports" | "workflow" | "feedback";
  label: string;
  bidirectional?: boolean;
}

function relatedWithinTwoSteps(data: PanoramaData, selectedId: string | null): Set<string> {
  if (!selectedId) return new Set();
  const result = new Set([selectedId]); let frontier = [selectedId];
  for (let step = 0; step < 2; step += 1) {
    const next: string[] = [];
    for (const edge of data.edges) {
      if (frontier.includes(edge.from) && !result.has(edge.to)) { result.add(edge.to); next.push(edge.to); }
      if (frontier.includes(edge.to) && !result.has(edge.from)) { result.add(edge.from); next.push(edge.from); }
    }
    frontier = next;
  }
  result.delete(selectedId); return result;
}

function groupNodes(group: PanoramaGroup, byId: Map<string, PanoramaNode>) {
  return group.node_ids.map((id) => byId.get(id)).filter((node): node is PanoramaNode => !!node);
}

function NodeCard({ node, selected, related, match, company, onSelect }: {
  node: PanoramaNode; selected: boolean; related: boolean; match: boolean; company: boolean; onSelect: () => void;
}) {
  return <article
    className={`panorama-node${company ? " panorama-node--company" : ""}${selected ? " is-selected" : ""}${related ? " is-related" : ""}${match ? " is-search-match" : ""}`}
    data-node-id={node.id}
  >
    <button type="button" aria-pressed={selected} onClick={onSelect}>
      <strong>{node.title}</strong>{company && node.subtitle && <span>{node.subtitle}</span>}
    </button>
  </article>;
}

function ActionButton({ id, onAction }: { id: PanoramaActionId; onAction: CanvasProps["onAction"] }) {
  return <button type="button" className={`panorama-action${EXTERNAL_ACTIONS.has(id) ? " panorama-action--external" : ""}`} data-action-id={id} onClick={() => onAction(id)}>
    {ACTION_LABELS[id]}{EXTERNAL_ACTIONS.has(id) && <span aria-hidden="true"> ↗</span>}
  </button>;
}

function DetailPanel({ node, data, isOwner, onAction, onEvidence, onClose }: {
  node: PanoramaNode; data: PanoramaData; isOwner: boolean; onAction: CanvasProps["onAction"];
  onEvidence: CanvasProps["onEvidence"]; onClose: () => void;
}) {
  const actions = node.actions.filter((id) => id !== "access" || isOwner);
  const relationships = data.edges.filter((edge) => edge.from === node.id || edge.to === node.id);
  return <aside className="panorama-detail" aria-label={`${node.title}详情`}>
    <header><div><span>节点详情</span><h2>{node.title}</h2>{node.subtitle && <p>{node.subtitle}</p>}</div><button type="button" onClick={onClose}>关闭</button></header>
    {node.detail.length > 0 && <ul>{node.detail.map((item, index) => <li key={`${node.id}-detail-${index}`}>{item}</li>)}</ul>}
    {relationships.length > 0 && <dl className="panorama-detail__relations">{relationships.map((edge) => {
      const otherId = edge.from === node.id ? edge.to : edge.from; const other = data.nodes.find((item) => item.id === otherId);
      return <div key={edge.id}><dt>{edge.kind === "supply" ? "供给" : edge.kind === "supports" ? "技术支撑" : "业务反馈"}</dt><dd>{other?.title ?? otherId}{edge.label && ` · ${edge.label}`}</dd></div>;
    })}</dl>}
    {actions.length > 0 && <div className="panorama-detail__actions">{actions.map((id) => <ActionButton id={id} key={id} onAction={onAction} />)}</div>}
    {node.source_ids.length > 0 && <div className="panorama-detail__sources" aria-label="事实来源">{node.source_ids.map((id) => {
      const source = data.sources.find((item) => item.id === id);
      return source && <button type="button" data-source-id={id} key={id} onClick={() => onEvidence(source.document)}>{source.label}</button>;
    })}</div>}
  </aside>;
}

function relative(rect: DOMRect, root: DOMRect): PanoramaRect {
  return { left: rect.left - root.left, top: rect.top - root.top, right: rect.right - root.left, bottom: rect.bottom - root.top };
}

export function PanoramaCanvas({ data, selectedId, matchIds, isOwner, onSelect, onAction, onEvidence, organization }: CanvasProps) {
  const workflowIndex = data.layers.map(layer => layer.kind).lastIndexOf("workflow");
  const organizationAfter = workflowIndex >= 0 ? workflowIndex : data.layers.length - 1;
  const rootRef = useRef<HTMLDivElement>(null); const [paths, setPaths] = useState<Record<string, string>>({});
  const [structuralPaths, setStructuralPaths] = useState<Record<string, string>>({});
  const byId = useMemo(() => new Map(data.nodes.map((node) => [node.id, node])), [data.nodes]);
  const relatedIds = useMemo(() => relatedWithinTwoSteps(data, selectedId), [data, selectedId]);
  const selectedSet = useMemo(() => new Set(selectedId ? [selectedId, ...relatedIds] : []), [relatedIds, selectedId]);
  const visibleEdges = useMemo(() => selectedId ? data.edges.filter((edge) => selectedSet.has(edge.from) && selectedSet.has(edge.to)) : [], [data.edges, selectedId, selectedSet]);
  const selected = selectedId ? byId.get(selectedId) : undefined;
  const structuralEdges = useMemo(() => {
    const result: StructuralEdge[] = [
      { id: "industry-upstream-company", from: { type: "role", id: "upstream" }, to: { type: "role", id: "company" }, fromRole: "upstream", toRole: "company", kind: "supply", label: "上游供给" },
      { id: "industry-company-downstream", from: { type: "role", id: "company" }, to: { type: "role", id: "downstream" }, fromRole: "company", toRole: "downstream", kind: "supply", label: "行业供给" },
      { id: "portfolio-technology-products", from: { type: "role", id: "technology" }, to: { type: "role", id: "products" }, fromRole: "technology", toRole: "products", kind: "supports", label: "技术支撑" },
    ];
    const groupByRole = (role: PanoramaGroup["role"]) => data.layers.flatMap((layer) => layer.groups).find((group) => group.role === role);
    const marketing = groupByRole("marketing")?.node_ids ?? []; const delivery = groupByRole("delivery")?.node_ids ?? [];
    const sequence = (role: "marketing" | "delivery", ids: string[]) => ids.slice(0, -1).map((fromNode, index): StructuralEdge => ({
      id: `workflow-${role}-${index}`, from: { type: "node", id: fromNode }, to: { type: "node", id: ids[index + 1] },
      fromRole: role, toRole: role, fromNode, toNode: ids[index + 1], kind: "workflow", label: `${role} 双向协作`, bidirectional: true,
    }));
    result.push(...sequence("marketing", marketing), ...sequence("delivery", delivery));
    for (let index = 0; index < Math.min(marketing.length, delivery.length); index += 1) {
      result.push({
        id: `workflow-handoff-${index}`, from: { type: "node", id: marketing[index] }, to: { type: "node", id: delivery[index] },
        fromRole: "marketing", toRole: "delivery", fromNode: marketing[index], toNode: delivery[index], kind: "workflow", label: "Marketing 与 Technology 双向协作", bidirectional: true,
      });
    }
    if (delivery.length > 1) result.push({
      id: "workflow-feedback-loop", from: { type: "node", id: delivery[delivery.length - 1] }, to: { type: "node", id: delivery[0] },
      fromRole: "delivery", toRole: "delivery", fromNode: delivery[delivery.length - 1], toNode: delivery[0], kind: "feedback", label: "应用反馈 · 产品迭代",
    });
    return result;
  }, [data.layers]);

  useLayoutEffect(() => {
    const measure = () => {
      const root = rootRef.current; if (!root) return;
      const bounds = root.getBoundingClientRect(); const next: Record<string, string> = {};
      const obstacles = [...root.querySelectorAll<HTMLElement>("[data-node-id]")].map((node) => relative(node.getBoundingClientRect(), bounds));
      for (const edge of visibleEdges) {
        const from = root.querySelector<HTMLElement>(`[data-node-id="${edge.from}"]`);
        const to = root.querySelector<HTMLElement>(`[data-node-id="${edge.to}"]`);
        if (from && to) next[edge.id] = routePanoramaEdge(
          relative(from.getBoundingClientRect(), bounds), relative(to.getBoundingClientRect(), bounds), obstacles,
          { width: bounds.width, height: bounds.height },
        );
      }
      setPaths(next);
      const nextStructural: Record<string, string> = {};
      const endpoint = (value: StructuralEdge["from"]) => root.querySelector<HTMLElement>(value.type === "role" ? `[data-group-role="${value.id}"]` : `[data-node-id="${value.id}"]`);
      for (const edge of structuralEdges) {
        const from = endpoint(edge.from); const to = endpoint(edge.to);
        if (from && to) nextStructural[edge.id] = routePanoramaEdge(
          relative(from.getBoundingClientRect(), bounds), relative(to.getBoundingClientRect(), bounds), obstacles,
          { width: bounds.width, height: bounds.height },
        );
      }
      setStructuralPaths(nextStructural);
    };
    measure(); window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (rootRef.current) observer?.observe(rootRef.current);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, [data, structuralEdges, visibleEdges]);

  return <div className="panorama-canvas" ref={rootRef}>
    <svg className="panorama-structure" aria-label="全景结构关系" width="100%" height="100%">
      <defs><marker id="panorama-structure-arrow" markerHeight="7" markerWidth="7" orient="auto-start-reverse" refX="6" refY="3.5"><path d="M0 0L7 3.5L0 7Z" /></marker></defs>
      {structuralEdges.map((edge) => <g
        data-aggregate-edge={edge.id} data-edge-kind={edge.kind} data-from-node={edge.fromNode} data-from-role={edge.fromRole}
        data-to-node={edge.toNode} data-to-role={edge.toRole} key={edge.id}
      >
        {structuralPaths[edge.id] && <path d={structuralPaths[edge.id]} markerEnd="url(#panorama-structure-arrow)" markerStart={edge.bidirectional ? "url(#panorama-structure-arrow)" : undefined} />}
        <title>{edge.label}</title>
      </g>)}
    </svg>
    {selectedId && <svg className="panorama-relations" aria-label="选中节点关系" width="100%" height="100%">
      <defs><marker id="panorama-arrow" markerHeight="7" markerWidth="7" orient="auto" refX="6" refY="3.5"><path d="M0 0L7 3.5L0 7Z" /></marker></defs>
      {visibleEdges.map((edge) => <g data-edge-id={edge.id} data-edge-kind={edge.kind} key={edge.id}>
        {paths[edge.id] && <path d={paths[edge.id]} markerEnd="url(#panorama-arrow)" />}
        <title>{edge.label}</title>
      </g>)}
    </svg>}
    {data.layers.map((layer, index) => <Fragment key={layer.id}><section className={`panorama-layer panorama-layer--${layer.kind}`} data-layer-id={layer.id}>
      <h2>{layer.title}</h2>
      <div className="panorama-layer__groups">
        {layer.groups.map((group) => <section className={`panorama-group panorama-group--${group.role}`} data-group-id={group.id} data-group-role={group.role} key={group.id}>
          <h3>{group.title}</h3>
          <div className={`panorama-group__nodes panorama-columns-${group.columns}`}>
            {groupNodes(group, byId).map((node) => <NodeCard
              company={group.role === "company"} key={node.id} match={matchIds.has(node.id)} node={node}
              onSelect={() => onSelect(selectedId === node.id ? null : node.id)} related={relatedIds.has(node.id)} selected={selectedId === node.id}
            />)}
          </div>
        </section>)}
      </div>
    </section>{index === organizationAfter && organization}</Fragment>)}
    {data.layers.length === 0 && organization}
    {selected && <DetailPanel data={data} isOwner={isOwner} node={selected} onAction={onAction} onClose={() => onSelect(null)} onEvidence={onEvidence} />}
  </div>;
}
