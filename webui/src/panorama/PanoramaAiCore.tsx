import { AI_CORE_MAINLINE } from "./aiCoreContent";

export function PanoramaAiCore({ topicIds, onExplore, active }: { topicIds: Set<string>; onExplore: (id: string) => void; active: boolean }) {
  if (!topicIds.has(AI_CORE_MAINLINE.topicId)) return null;
  return <div className="panorama-ai-core" aria-label="AI 工程判断，待验证">
    <button type="button" className="panorama-ai-core__title" aria-pressed={active} onClick={() => onExplore(AI_CORE_MAINLINE.topicId)}>
      <span>AI 工程判断</span><strong>{AI_CORE_MAINLINE.label}</strong><small>待验证 · 点击看推演</small>
    </button>
  </div>;
}
