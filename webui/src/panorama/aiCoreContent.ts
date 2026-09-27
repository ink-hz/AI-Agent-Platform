import type { PanoramaData } from "../panoramaTypes";
import { availableExplorations, explorationNodes } from "./panoramaExplorations";

// Editorial hypotheses attached to existing nodes, not runtime Agent rules.
export interface AiCorePoint { topicId: string; label: string; }
export const AI_CORE_POINTS: Record<string, AiCorePoint> = {
  orbbec: { topicId: "mainline", label: "让产品能力走到场景可用" },
  requirements: { topicId: "market", label: "新产品机会，还是使用门槛" },
  integration: { topicId: "customer", label: "从方案建议走到场景验证" },
  "customer-use": { topicId: "learning", label: "临时解决 ≠ 产品修正" },
  talent: { topicId: "talent", label: "经验整理 ≠ 判断传授" },
  quality: { topicId: "quality", label: "核对场景验证是否充分" },
  finance: { topicId: "operations", label: "看清定制的长期责任" },
  digital: { topicId: "platform", label: "让修正传到所有引用处" },
};
export const AI_CORE_STEPS: readonly AiCorePoint[] = [
  { topicId: "customer", label: "场景可用" },
  { topicId: "engineering", label: "验证条件" },
  { topicId: "market", label: "产品取舍" },
  { topicId: "learning", label: "交付复用" },
];
export const AI_PORTFOLIO_CORE: AiCorePoint = { topicId: "engineering", label: "识别经验何时失效" };

export function portfolioCoreNodes(data: PanoramaData) {
  const topic = availableExplorations(data).find((item) => item.id === AI_PORTFOLIO_CORE.topicId);
  const placed = new Set(data.layers.filter((layer) => layer.kind === "portfolio").flatMap((layer) => layer.groups.flatMap((group) => group.node_ids)));
  return topic ? explorationNodes(topic, data).filter((node) => placed.has(node.id)) : [];
}

export function aiCoreSearchMatches(data: PanoramaData, needle: string): Set<string> {
  const topics = availableExplorations(data);
  const matches = new Set<string>();
  const includes = (label: string) => label.toLocaleLowerCase().includes(needle);
  for (const topic of topics) {
    const nodes = explorationNodes(topic, data);
    if (AI_CORE_STEPS.some((step) => step.topicId === topic.id && includes(step.label)) || (topic.id === "mainline" && includes("AI 工程方向"))) {
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
