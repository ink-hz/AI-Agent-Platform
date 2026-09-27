import { AI_CORE_STEPS } from "./aiCoreContent";
export function PanoramaAiCore({ topicIds, onExplore }: { topicIds: Set<string>; onExplore: (id: string) => void }) {
  if (!topicIds.size) return null;
  return <div className="panorama-ai-core" aria-label="AI 工程方向，待验证">
    {topicIds.has("mainline") ? <button type="button" className="panorama-ai-core__title" onClick={() => onExplore("mainline")}>AI 工程方向</button> : <strong>AI 工程方向</strong>}
    <span className="panorama-ai-core__status">待验证</span>
    <div className="panorama-ai-core__steps">{AI_CORE_STEPS.filter((step) => topicIds.has(step.topicId)).map((step) =>
      <button type="button" key={step.topicId} onClick={() => onExplore(step.topicId)}>{step.label}</button>
    )}</div>
  </div>;
}
