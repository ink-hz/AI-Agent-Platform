import { useRef } from "react";

import type { PanoramaActionId, PanoramaData, PanoramaEdgeKind, PanoramaEditorState } from "../panoramaTypes";

const ACTIONS: PanoramaActionId[] = [
  "brain", "agents", "missions", "sessions", "operations", "review", "activity", "identity", "governance",
  "access", "account", "agent-admin", "notes", "hr", "office", "voc", "fae",
];
const ACTION_LABEL: Record<PanoramaActionId, string> = {
  brain: "大脑", agents: "Agent", missions: "任务", sessions: "会话", operations: "运行", review: "复审",
  activity: "记录", identity: "身份", governance: "治理", access: "访问", account: "账号", "agent-admin": "Agent 管理",
  notes: "笔记", hr: "HR", office: "行政", voc: "VOC", fae: "FAE",
};

interface Props {
  data: PanoramaData;
  state: PanoramaEditorState;
  busy: boolean;
  dirty: boolean;
  locked: boolean;
  preview: boolean;
  message: string;
  onChange: (data: PanoramaData) => void;
  onClose: () => void;
  onSave: () => void;
  onDiscard: () => void;
  onPublish: () => void;
  onRestore: () => void;
  onReconcile: () => void;
  onPreview: () => void;
}

function nextId(prefix: string, existing: Set<string>): string {
  for (let number = 1; number <= 999; number += 1) { const candidate = `${prefix}-${number}`; if (!existing.has(candidate)) return candidate; }
  return `${prefix}-${Date.now().toString(36)}`;
}
function moved<T>(values: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= values.length || to >= values.length) return values;
  const result = [...values]; const [item] = result.splice(from, 1); result.splice(to, 0, item); return result;
}

export function PanoramaEditor({ data, state, busy, dirty, locked, preview, message, onChange, onClose, onSave, onDiscard, onPublish, onRestore, onReconcile, onPreview }: Props) {
  const dragged = useRef<{ groupId: string; nodeId: string } | null>(null);
  const groups = data.layers.flatMap((layer) => layer.groups.map((group) => ({ ...group, layerId: layer.id })));
  const updateNode = (id: string, patch: Partial<PanoramaData["nodes"][number]>) => onChange({ ...data, nodes: data.nodes.map((node) => node.id === id ? { ...node, ...patch } : node) });
  const updateGroup = (layerId: string, groupId: string, patch: Partial<PanoramaData["layers"][number]["groups"][number]>) => onChange({
    ...data, layers: data.layers.map((layer) => layer.id === layerId ? { ...layer, groups: layer.groups.map((group) => group.id === groupId ? { ...group, ...patch } : group) } : layer),
  });
  const moveNode = (nodeId: string, fromGroupId: string, toGroupId: string, beforeId?: string) => {
    onChange({ ...data, layers: data.layers.map((layer) => ({ ...layer, groups: layer.groups.map((group) => {
      let ids = group.node_ids.filter((id) => id !== nodeId);
      if (group.id === toGroupId) { const target = beforeId ? ids.indexOf(beforeId) : -1; ids.splice(target < 0 ? ids.length : target, 0, nodeId); }
      return group.id === fromGroupId || group.id === toGroupId ? { ...group, node_ids: ids } : group;
    }) })) });
  };
  const deleteNode = (nodeId: string) => {
    if (!window.confirm("删除节点会同时移除相关关系。确定继续？")) return;
    onChange({
      ...data, nodes: data.nodes.filter((node) => node.id !== nodeId), edges: data.edges.filter((edge) => edge.from !== nodeId && edge.to !== nodeId),
      layers: data.layers.map((layer) => ({ ...layer, groups: layer.groups.map((group) => ({ ...group, node_ids: group.node_ids.filter((id) => id !== nodeId) })) })),
    });
  };
  const addNode = (groupId: string) => {
    if (data.nodes.length >= 80) return;
    const id = nextId("node", new Set(data.nodes.map((node) => node.id)));
    onChange({
      ...data, nodes: [...data.nodes, { id, title: "新节点", subtitle: "", detail: [], actions: [], source_ids: [] }],
      layers: data.layers.map((layer) => ({ ...layer, groups: layer.groups.map((group) => group.id === groupId ? { ...group, node_ids: [...group.node_ids, id] } : group) })),
    });
  };
  const addEdge = () => {
    if (data.nodes.length < 2 || data.edges.length >= 240) return;
    const id = nextId("edge", new Set(data.edges.map((edge) => edge.id)));
    onChange({ ...data, edges: [...data.edges, { id, from: data.nodes[0].id, to: data.nodes[1].id, kind: "supports", label: "" }] });
  };

  return <aside className={`panorama-editor${preview ? " panorama-editor--preview" : ""}`} aria-label="全景布局编辑器">
    <header className="panorama-editor__header">
      <div><span>共享草稿 · 修订 {state.revision}</span><h2>调整布局</h2></div>
      <button type="button" onClick={onClose}>退出调整</button>
    </header>
    <div className="panorama-editor__commands">
      <button type="button" onClick={onPreview}>{preview ? "返回编辑" : "预览草稿"}</button>
      <button type="button" disabled={busy || locked || !dirty} onClick={onSave}>保存草稿</button>
      <button type="button" disabled={busy || locked} onClick={onDiscard}>取消修改</button>
      <button type="button" disabled={busy || locked || dirty || !state.draft} onClick={onPublish}>发布</button>
      <button type="button" disabled={busy || locked || dirty || !state.previous} onClick={onRestore}>恢复上一版</button>
      {locked && <button type="button" disabled={busy} onClick={onReconcile}>核对服务器状态</button>}
    </div>
    {message && <p className="panorama-editor__message" role="status">{message}</p>}
    {!preview && <div className="panorama-editor__body">
      <label className="panorama-editor__field">全景标题<input name="panorama-title" maxLength={120} value={data.title} onInput={(event) => onChange({ ...data, title: event.currentTarget.value })} /></label>
      {data.layers.map((layer, layerIndex) => <section className="panorama-editor__layer" key={layer.id}>
        <header>
          <label>层标题<input maxLength={80} value={layer.title} onInput={(event) => onChange({ ...data, layers: data.layers.map((item) => item.id === layer.id ? { ...item, title: event.currentTarget.value } : item) })} /></label>
          <div><button type="button" disabled={layerIndex === 0} aria-label={`${layer.title}上移`} onClick={() => onChange({ ...data, layers: moved(data.layers, layerIndex, layerIndex - 1) })}>↑</button><button type="button" disabled={layerIndex === data.layers.length - 1} aria-label={`${layer.title}下移`} onClick={() => onChange({ ...data, layers: moved(data.layers, layerIndex, layerIndex + 1) })}>↓</button></div>
        </header>
        {layer.groups.map((group, groupIndex) => <section className="panorama-editor__group" key={group.id}>
          <header>
            <label>分组<input maxLength={80} value={group.title} onInput={(event) => updateGroup(layer.id, group.id, { title: event.currentTarget.value })} /></label>
            <label>列数<input type="number" min="1" max="8" value={group.columns} onInput={(event) => updateGroup(layer.id, group.id, { columns: Math.max(1, Math.min(8, Number(event.currentTarget.value) || 1)) })} /></label>
            <div><button type="button" disabled={groupIndex === 0} aria-label={`${group.title}上移`} onClick={() => onChange({ ...data, layers: data.layers.map((item) => item.id === layer.id ? { ...item, groups: moved(item.groups, groupIndex, groupIndex - 1) } : item) })}>↑</button><button type="button" disabled={groupIndex === layer.groups.length - 1} aria-label={`${group.title}下移`} onClick={() => onChange({ ...data, layers: data.layers.map((item) => item.id === layer.id ? { ...item, groups: moved(item.groups, groupIndex, groupIndex + 1) } : item) })}>↓</button></div>
          </header>
          <div className="panorama-editor__nodes" onDragOver={(event) => event.preventDefault()} onDrop={() => { const source = dragged.current; if (source) moveNode(source.nodeId, source.groupId, group.id); dragged.current = null; }}>
            {group.node_ids.map((nodeId, nodeIndex) => {
              const node = data.nodes.find((item) => item.id === nodeId); if (!node) return null;
              return <article draggable className="panorama-editor__node" data-editor-node-id={node.id} key={node.id}
                onDragStart={() => { dragged.current = { groupId: group.id, nodeId: node.id }; }}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => { event.stopPropagation(); const source = dragged.current; if (source) moveNode(source.nodeId, source.groupId, group.id, node.id); dragged.current = null; }}>
                <div className="panorama-editor__node-main">
                  <span aria-hidden="true">⋮⋮</span>
                  <label>名称<input maxLength={80} value={node.title} onInput={(event) => updateNode(node.id, { title: event.currentTarget.value })} /></label>
                  <label>副标题<input maxLength={160} value={node.subtitle} onInput={(event) => updateNode(node.id, { subtitle: event.currentTarget.value })} /></label>
                  <label>分组<select value={group.id} onChange={(event) => moveNode(node.id, group.id, event.currentTarget.value)}>{groups.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
                  <div className="panorama-editor__order"><button type="button" disabled={nodeIndex === 0} aria-label={`${node.title}上移`} onClick={() => updateGroup(layer.id, group.id, { node_ids: moved(group.node_ids, nodeIndex, nodeIndex - 1) })}>↑</button><button type="button" disabled={nodeIndex === group.node_ids.length - 1} aria-label={`${node.title}下移`} onClick={() => updateGroup(layer.id, group.id, { node_ids: moved(group.node_ids, nodeIndex, nodeIndex + 1) })}>↓</button><button type="button" onClick={() => deleteNode(node.id)}>删除</button></div>
                </div>
                <details><summary>详情与功能</summary>
                  <label>详情（每行一项）<textarea maxLength={2891} value={node.detail.join("\n")} onInput={(event) => updateNode(node.id, { detail: event.currentTarget.value.split("\n").filter(Boolean).slice(0, 12).map((item) => item.slice(0, 240)) })} /></label>
                  <fieldset><legend>绑定功能</legend>{ACTIONS.map((action) => <label key={action}><input type="checkbox" checked={node.actions.includes(action)} onChange={(event) => updateNode(node.id, { actions: event.currentTarget.checked ? [...node.actions, action] : node.actions.filter((item) => item !== action) })} />{ACTION_LABEL[action]}</label>)}</fieldset>
                </details>
              </article>;
            })}
          </div>
          <button type="button" disabled={data.nodes.length >= 80 || group.node_ids.length >= 40} onClick={() => addNode(group.id)}>添加节点</button>
        </section>)}
      </section>)}
      <section className="panorama-editor__edges"><header><h3>关系</h3><button type="button" disabled={data.nodes.length < 2 || data.edges.length >= 240} onClick={addEdge}>添加关系</button></header>
        {data.edges.map((edge) => <article key={edge.id}>
          <label>起点<select value={edge.from} onChange={(event) => onChange({ ...data, edges: data.edges.map((item) => item.id === edge.id ? { ...item, from: event.currentTarget.value } : item) })}>{data.nodes.map((node) => <option key={node.id} value={node.id}>{node.title}</option>)}</select></label>
          <label>终点<select value={edge.to} onChange={(event) => onChange({ ...data, edges: data.edges.map((item) => item.id === edge.id ? { ...item, to: event.currentTarget.value } : item) })}>{data.nodes.map((node) => <option key={node.id} value={node.id}>{node.title}</option>)}</select></label>
          <label>类型<select value={edge.kind} onChange={(event) => onChange({ ...data, edges: data.edges.map((item) => item.id === edge.id ? { ...item, kind: event.currentTarget.value as PanoramaEdgeKind } : item) })}><option value="supply">供给</option><option value="supports">技术支撑</option><option value="feedback">业务反馈</option></select></label>
          <label>说明<input maxLength={100} value={edge.label} onInput={(event) => onChange({ ...data, edges: data.edges.map((item) => item.id === edge.id ? { ...item, label: event.currentTarget.value } : item) })} /></label>
          <button type="button" onClick={() => onChange({ ...data, edges: data.edges.filter((item) => item.id !== edge.id) })}>删除关系</button>
        </article>)}
      </section>
    </div>}
  </aside>;
}
