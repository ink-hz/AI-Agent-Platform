import type { PanoramaData } from "../panoramaTypes";
import { explorationNodes, type PanoramaExploration } from "./panoramaExplorations";

export function ExplorationPanel({ topic, data, onSelect, onClose, topics, onExplore }: {
  topic: PanoramaExploration; data: PanoramaData; onSelect: (id: string) => void; onClose: () => void;
  topics: readonly PanoramaExploration[]; onExplore?: (id: string) => void;
}) {
  return <aside className="panorama-detail panorama-exploration" aria-label={`${topic.title}探索`}>
    <header><div><span>探索假设</span><h2>{topic.title}</h2></div><button type="button" onClick={onClose}>关闭</button></header>
    {topics.length > 1 && onExplore && <label className="panorama-exploration__topics">探索领域
      <select aria-label="探索领域" value={topic.id} onChange={(event) => onExplore(event.currentTarget.value)}>
        {topics.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select>
    </label>}
    <p className="panorama-exploration__idea">{topic.idea}</p>
    <section><h3>突破方向</h3><p>{topic.direction}</p></section>
    <div className="panorama-exploration__nodes" aria-label="涉及的业务节点">
      {explorationNodes(topic, data).map((node) => <button key={node.id} type="button" onClick={() => onSelect(node.id)}>{node.title}</button>)}
    </div>
    <section className="panorama-exploration__question"><h3>需要验证</h3><p>{topic.question}</p></section>
  </aside>;
}
