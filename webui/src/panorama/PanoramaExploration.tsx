import type { PanoramaData } from "../panoramaTypes";
import { explorationNodes, type PanoramaExploration } from "./panoramaExplorations";
import type { AiCorePoint } from "./aiCoreContent";

export function ExplorationPanel({ topic, data, onSelect, onClose, fallbackPoints, onExplore }: {
  topic: PanoramaExploration; data: PanoramaData; onSelect: (id: string) => void; onClose: () => void;
  fallbackPoints: AiCorePoint[]; onExplore?: (id: string) => void;
}) {
  return <aside className="panorama-detail panorama-exploration" aria-label={`${topic.title}探索`}>
    <header><div><span>探索假设</span><h2>{topic.title}</h2></div><button type="button" onClick={onClose}>关闭</button></header>
    <p className="panorama-exploration__idea">{topic.idea}</p>
    <section><h3>突破方向</h3><p>{topic.direction}</p></section>
    <div className="panorama-exploration__nodes" aria-label="涉及的业务节点">
      {explorationNodes(topic, data).map((node) => <button key={node.id} type="button" onClick={() => onSelect(node.id)}>{node.title}</button>)}
    </div>
    <section className="panorama-exploration__question"><h3>需要验证</h3><p>{topic.question}</p></section>
    {fallbackPoints.length > 0 && onExplore && <section className="panorama-exploration__fallback" aria-label="图上未显示的可看问题">
      <h3>图上未显示的问题</h3>
      {fallbackPoints.map((point) => <button type="button" key={point.topicId} onClick={() => onExplore(point.topicId)}>{point.label}</button>)}
    </section>}
  </aside>;
}
