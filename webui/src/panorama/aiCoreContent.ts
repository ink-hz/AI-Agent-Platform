import type { PanoramaData } from "../panoramaTypes";
import { availableExplorations, explorationNodes } from "./panoramaExplorations";

// Editorial hypotheses attached to existing nodes, not runtime Agent rules.
export interface AiCorePoint { topicId: string; label: string; }
export const AI_CORE_POINTS: Record<string, AiCorePoint> = {
  orbbec: { topicId: "mainline", label: "奥比要交付硬件，还是负责场景可用？" },
  requirements: { topicId: "market", label: "客户需要新产品，还是现有产品更好用？" },
  integration: { topicId: "customer", label: "现场任务能跑通吗？" },
  "customer-use": { topicId: "learning", label: "这次解决，能进入产品修正吗？" },
  talent: { topicId: "talent", label: "别人能独立做出判断吗？" },
  quality: { topicId: "quality", label: "验证覆盖了客户真实条件吗？" },
  finance: { topicId: "operations", label: "这项定制以后由谁维护？" },
  digital: { topicId: "platform", label: "旧结论变了，引用它的人知道吗？" },
};
export const AI_CORE_MAINLINE: AiCorePoint = { topicId: "mainline", label: "一次客户问题，何时能成为共用产品能力？" };
export const AI_PORTFOLIO_CORE: AiCorePoint = { topicId: "engineering", label: "这个结论在哪些型号和版本上成立？" };

export function portfolioCoreNodes(data: PanoramaData) {
  const topic = availableExplorations(data).find((item) => item.id === AI_PORTFOLIO_CORE.topicId);
  const placed = new Set(data.layers.filter((layer) => layer.kind === "portfolio").flatMap((layer) => layer.groups.flatMap((group) => group.node_ids)));
  return topic ? explorationNodes(topic, data).filter((node) => placed.has(node.id)) : [];
}

/** Topics kept by a custom layout but without their usual question anchor. */
export function unanchoredCorePoints(data: PanoramaData): AiCorePoint[] {
  const placed = new Set(data.layers.flatMap((layer) => layer.groups.flatMap((group) => group.node_ids)));
  const existing = new Set(data.nodes.map((node) => node.id));
  const anchored = new Set(Object.entries(AI_CORE_POINTS)
    .filter(([id]) => placed.has(id) && existing.has(id))
    .map(([, point]) => point.topicId));
  if (portfolioCoreNodes(data).length > 0) anchored.add(AI_PORTFOLIO_CORE.topicId);
  // The mainline question is always available in the graph header when its topic exists.
  anchored.add(AI_CORE_MAINLINE.topicId);
  const questions = [...Object.values(AI_CORE_POINTS), AI_PORTFOLIO_CORE];
  return availableExplorations(data).filter((topic) => !anchored.has(topic.id)).flatMap((topic) =>
    questions.find((question) => question.topicId === topic.id) ?? []);
}

export function aiCoreSearchMatches(data: PanoramaData, needle: string): Set<string> {
  const topics = availableExplorations(data);
  const matches = new Set<string>();
  const includes = (label: string) => label.toLocaleLowerCase().includes(needle);
  for (const topic of topics) {
    const nodes = explorationNodes(topic, data);
    if (topic.id === "mainline" && (includes(AI_CORE_MAINLINE.label) || includes("AI 工程判断"))) {
      nodes.forEach((node) => matches.add(node.id));
    }
  }
  const placed = new Set(data.layers.flatMap((layer) => layer.groups.flatMap((group) => group.node_ids)));
  const topicIds = new Set(topics.map((topic) => topic.id));
  for (const node of data.nodes) {
    const core = AI_CORE_POINTS[node.id];
    if (placed.has(node.id) && core && topicIds.has(core.topicId) && includes(core.label)) matches.add(node.id);
  }
  if (includes(AI_PORTFOLIO_CORE.label)) portfolioCoreNodes(data).forEach((node) => matches.add(node.id));
  return matches;
}
