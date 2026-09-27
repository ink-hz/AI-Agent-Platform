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
    idea: "芯片、相机、软件与测量系统，留给客户完成的工作不同。值得探索的是：AI 能否帮助公司与客户明确各自的交付边界，把产品能力推进到场景可用。",
    direction: "机器人集成先理解系统与环境约束，工业测量先明确结果如何验收。AI 协助识别缺失条件、比较方案、提出下一次验证；不把不同场景套进同一套选型问答。",
    question: "回看一件建议未能落地的案例：产品选错、集成受阻，还是目标未说清？若只是确定性的接口与版本查询，先做好工具；若需要新产品能力，转入研发判断。",
    nodeIds: ["orbbec", "robotics", "scanning", "biometrics", "measurement", "aiot", "camera", "software", "integration"],
  },
  {
    id: "engineering", title: "产品研发",
    idea: "验证经验越容易复用，越需要知道什么时候失效。型号、固件、SDK 与主机改变后，旧结论能否沿用，比检索到相似案例更重要。",
    direction: "从一次产品变更追踪结论依赖的条件。例如 210/215 的光学与接口相同，固定方式却不同；AI 应协助找出变更触及的假设和需要补做的验证，保留迁移理由。",
    question: "AI 找出了专家遗漏的验证条件，还是只列了更多测试？若历史实验和版本记录不足，先形成可追溯记录；没有条件依据的经验迁移不应成为工程建议。",
    nodeIds: ["chip-product", "camera", "lidar", "smart-vision", "industry-systems", "software", "chip-tech", "optics", "depth-algorithm", "sdk-tech", "calibration", "development"],
  },
  {
    id: "market", title: "市场与技术",
    idea: "相似诉求可能指向新产品机会，也可能来自现有产品的使用门槛。AI 的价值在帮助双方辨别这两种情况，而不是把反馈频次变成产品优先级。",
    direction: "假设客户提出“换接口”：先追问系统接入目标，判断是否需要新型号，还是应改善现有集成方式。跨客户比较时保留场景差异，同时寻找不能被当前解释覆盖的案例。",
    question: "若现有方案加一次明确的集成指导就满足目标，还需要改产品吗？若多个独立场景都无法满足，再讨论共性能力；产品化、定制与服务三条路径由业务和技术共同取舍。",
    nodeIds: ["market-insight", "requirements", "product-planning", "marketing-sales", "customer-use", "research", "feasibility", "development"],
  },
  {
    id: "learning", title: "交付学习",
    idea: "客户暂时能继续使用、工程原因得到确认、产品完成修正，是不同的结果。FAE、VOC 与研发需要保留这些差别，避免一次临时解决被复用成通用结论。",
    direction: "跟随一个现场问题，保留客户目标、候选原因、已验证措施与尚未解决的部分。研发确认后的结论返回 FAE；VOC 保留客户是否达成目标，而不只记录故障关闭。",
    question: "假设绕开某功能后恢复使用：新版本发布时谁确认问题消失，旧措施何时停止推荐？若主要缺口是交接与反馈责任，先明确责任，不用新 Agent 替代。",
    nodeIds: ["customer-use", "integration", "requirements", "product-planning", "development", "quality", "digital"],
  },
  {
    id: "talent", title: "组织人才",
    idea: "业务受阻可能缺专业人才，也可能过度依赖少数人的跨专业判断。值得探索的是判断能力能否被传授和协作复用，不能只把需求翻译成新增岗位。",
    direction: "以光学、算法与软件联合验证为假设场景，记录各专业贡献与综合判断依据。AI 协助组织经授权的信息，HR 与业务判断培养、招聘或协作调整；不自动给人员定级。",
    question: "交接依据后，另一位参与者能否提出可复核方案，说明适用条件及必须请专家的边界？若只能复述材料，AI 整理尚未解除依赖；若缺决策权，先解决授权。",
    nodeIds: ["talent", "projects", "research", "development", "integration"],
  },
  {
    id: "quality", title: "供应与质量",
    idea: "内部验收通过与客户场景可用，需要核对是否采用了相同条件。质量探索应研究验证覆盖了什么、遗漏了什么，而不只是把异常描述归类。",
    direction: "假设更换光学料件后，原标定检查通过但现场出现异常：同时核对变更、批次、标定、软件与环境，比较候选解释并组织补充实验；不能直接认定物料变更就是原因。",
    question: "追溯记录是否支持同一对象与版本的比较？哪个实验能区分物料、软件和安装的解释？文字相似不能认定根因；记录不足时先补追溯，放行仍由质量角色负责。",
    nodeIds: ["procurement", "quality", "manufacturing", "calibration", "development"],
  },
  {
    id: "operations", title: "经营支撑",
    idea: "一个客户定制承诺，可能同时增加研发、验证、维护和交付责任。经营支撑值得探索的是在承诺前看清后续负担，避免各环节只判断自己眼前的工作。",
    direction: "沿一项候选定制需求，协助财务理解成本假设、项目识别资源依赖、法务核对许可与交付边界。把一次交付与长期维护分开，比较产品化和单客定制的后续责任。",
    question: "这项需求可以沉淀为共用能力，还是长期特殊维护？谁承担后续责任、哪些计划受影响？AI 整理取舍依据；若分歧来自授权和资源优先级，交由负责人决策。",
    nodeIds: ["finance", "projects", "legal"],
  },
  {
    id: "platform", title: "共用平台",
    idea: "共享一份文档不等于共享一个可靠结论。FAE、VOC 与研发可能需要共同维护工程判断的条件、证据和变化；HR 的人员判断则有不同的材料与责任边界。",
    direction: "先探索一个产品结论如何被多方引用、修订和停止使用。平台提供来源、版本、用途权限与回溯，业务角色确认结论；客户案例抽象后能否复用，也须审视保密边界。",
    question: "一个已经修正的结论，其他应用如何知道旧建议不再适用？先确认实际引用与更新需求；若各领域尚无共用对象，保留独立应用，不预建统一大脑或复杂编排。",
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
