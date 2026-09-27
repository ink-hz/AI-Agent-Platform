import type { PanoramaData } from "../panoramaTypes";

/** Editorial hypotheses, independent of published graph facts and Agent execution. */
export interface PanoramaExploration {
  id: string;
  title: string;
  idea: string;
  direction: string;
  question: string;
  nodeIds: readonly string[];
}

export const PANORAMA_EXPLORATIONS: readonly PanoramaExploration[] = [
  {
    id: "customer", title: "客户场景",
    idea: "客户采用感知产品之后，还要跨过安装、适配与验证，才能获得可用的感知能力。AI 的机会可能在这段设计导入过程中。",
    direction: "从一件真实集成案例出发，理解任务与环境，识别缺失条件，辅助设计验证；根据实际结果修正建议。机器人、扫描与测量各自深入。",
    question: "专家在哪些地方必须追问或实测？AI 能帮助客户推进哪一步？兼容查询交给工具，现场结果仍需实际验证。",
    nodeIds: ["orbbec", "robotics", "scanning", "biometrics", "measurement", "aiot", "camera", "software", "integration"],
  },
  {
    id: "engineering", title: "产品研发",
    idea: "产品扩展带来适配与验证的复杂性。一次项目的验证经验若保留条件与判断依据，可能成为下一款产品的起点。",
    direction: "关联现象、环境与版本、排除过程和有效措施；新产品或版本变更时，帮助发现可以借鉴的经验及必须重新验证的假设。",
    question: "哪些经验能跨产品迁移，哪些只在原条件下有效？先跟随一次变更审查，确认现有记录能否支持判断；仿真和测试仍使用专业工具。",
    nodeIds: ["chip-product", "camera", "lidar", "smart-vision", "industry-systems", "software", "chip-tech", "optics", "depth-algorithm", "sdk-tech", "calibration", "development"],
  },
  {
    id: "market", title: "市场与技术",
    idea: "客户表达与工程条件之间、技术提升与应用价值之间，都需要共同判断。反馈相似，不一定意味着同一个产品机会。",
    direction: "把模糊诉求变成可验证问题，把技术能力变成具体场景假设；同时组织支持证据与反证，比较客户差异、可行性和交付代价。",
    question: "一项真实需求从表达走到产品定义时，误解和返工发生在哪里？AI 可以辅助澄清，产品取舍与资源决策仍由人负责。",
    nodeIds: ["market-insight", "requirements", "product-planning", "marketing-sales", "customer-use", "research", "feasibility", "development"],
  },
  {
    id: "learning", title: "交付学习",
    idea: "FAE 看到工程现象，VOC 保留客户诉求，研发理解产品机理。三种视角结合，才可能让一次解决影响后续产品和交付。",
    direction: "探索客户问题、诊断证据、产品判断与知识更新如何接续。有效结论带着适用条件返回服务端，客户诉求保留原意。",
    question: "追踪一个问题：信息在哪次交接丢失，处理结果是否返回？先确认真实流转，再决定工具连接；临时解决不等于产品问题已消除。",
    nodeIds: ["customer-use", "integration", "requirements", "product-planning", "development", "quality", "digital"],
  },
  {
    id: "talent", title: "组织人才",
    idea: "业务方向需要能力组合。组织树显示人员隶属，却不能说明光学、算法、软件与现场工程能力怎样协同、哪里需要补齐。",
    direction: "从一个产品方向出发，结合经授权的岗位要求与项目成果，辅助业务和 HR 判断招聘、培养、人员配置或外部合作的选择。",
    question: "缺的是专业能力、协作机制还是决策责任？能力判断由本人及业务负责人确认，不从部门、花名或活跃程度推断人员能力。",
    nodeIds: ["talent", "projects", "research", "development", "integration"],
  },
  {
    id: "quality", title: "供应与质量",
    idea: "设计、物料和工艺变化可能跨越多个环节。异常调查需要把现场现象与研发验证、标定、制造记录放在一起理解。",
    direction: "沿一次跨环节异常整理变更与证据，辅助提出排查线索、识别受影响场景，让质量经验能够返回研发和交付。",
    question: "现有记录能否追溯到同一对象与版本？关联条件尚需确认；文字相似不能认定根因，质量放行与统计分析保留专业方法。",
    nodeIds: ["procurement", "quality", "manufacturing", "calibration", "development"],
  },
  {
    id: "operations", title: "经营支撑",
    idea: "财务、项目、法务和行政面对的问题不同。应深入各自的决策与协作，判断 AI、系统互通或流程调整哪个更合适。",
    direction: "财务审视成本假设；项目识别依赖与变更；法务发现许可及保密风险；行政减少重复填写和事务往返。专业判断仍由相应角色承担。",
    question: "选一件正在处理的工作，找出真正的阻碍：信息不足、重复操作还是权责冲突？稳定流程无需再包装成自主 Agent。",
    nodeIds: ["finance", "projects", "legal", "office"],
  },
  {
    id: "platform", title: "共用平台",
    idea: "不同业务可能共用可信知识、授权工具和任务交接，但共用平台不意味着共用所有数据、判断方法与责任。",
    direction: "从实际协作归纳可复用的知识与工具，保留来源、版本、用途权限和回溯能力。HR、FAE 与 VOC 各自承担领域判断。",
    question: "什么能力确实被多个领域需要？客户经验如何安全复用？先验证共用需求，不预建包办所有任务的大脑或复杂编排。",
    nodeIds: ["digital", "talent", "integration", "customer-use", "sdk-tech", "software"],
  },
];

export function explorationNodes(topic: PanoramaExploration, data: PanoramaData) {
  const placed = new Set(data.layers.flatMap((layer) => layer.groups.flatMap((group) => group.node_ids)));
  const byId = new Map(data.nodes.map((node) => [node.id, node]));
  return topic.nodeIds.flatMap((id) => {
    const node = byId.get(id);
    return node && placed.has(id) ? [node] : [];
  });
}

export function availableExplorations(data: PanoramaData) {
  return PANORAMA_EXPLORATIONS.filter((topic) => explorationNodes(topic, data).length > 0);
}
