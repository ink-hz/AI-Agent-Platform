import type { PanoramaData } from "../panoramaTypes";
import { explorationNodes, type PanoramaExploration } from "./panoramaExplorations";

export function ExplorationNavigation({ topics, selectedId, onChange }: {
  topics: readonly PanoramaExploration[]; selectedId: string; onChange: (id: string) => void;
}) {
  return <nav className="panorama-exploration-nav" aria-label="AI 探索主题">
    <span className="panorama-exploration-legend">紫色描边 · 探索关注</span>
    {topics.map((topic) => <button type="button" data-topic-id={topic.id} aria-pressed={topic.id === selectedId}
      key={topic.id} onClick={() => onChange(topic.id)}>{topic.title}</button>)}
  </nav>;
}

export function ExplorationPanel({ topic, data, onSelect, onClose }: {
  topic: PanoramaExploration; data: PanoramaData; onSelect: (id: string) => void; onClose: () => void;
}) {
  return <aside className="panorama-detail panorama-exploration" aria-label={`${topic.title}探索`}>
    <header><div><span>探索假设</span><h2>{topic.title}</h2></div><button type="button" onClick={onClose}>关闭</button></header>
    <p className="panorama-exploration__idea">{topic.idea}</p>
    <section><h3>突破方向</h3><p>{topic.direction}</p></section>
    <div className="panorama-exploration__nodes" aria-label="涉及的业务节点">
      {explorationNodes(topic, data).map((node) => <button key={node.id} type="button" onClick={() => onSelect(node.id)}>{node.title}</button>)}
    </div>
    <section className="panorama-exploration__question"><h3>需要验证</h3><p>{topic.question}</p></section>
  </aside>;
}
