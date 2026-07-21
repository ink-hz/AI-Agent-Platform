export type AgentAccent =
  | "collaboration"
  | "people"
  | "prospecting"
  | "inbound"
  | "voice"
  | "support"
  | "testing"
  | "strategy"
  | "intelligence"
  | "default";


export interface AgentIdentity {
  name: string;
  domain: string;
  description: string;
  glyph: string;
  accent: AgentAccent;
}


const AGENT_IDENTITIES: Record<string, AgentIdentity> = {
  "feishu-default": {
    name: "飞书默认助手",
    domain: "通用协作",
    description: "承接飞书默认会话与日常协作任务。",
    glyph: "飞",
    accent: "collaboration",
  },
  "hr-bot": {
    name: "HR 助手",
    domain: "人力资源",
    description: "支持招聘、人事与员工服务流程。",
    glyph: "HR",
    accent: "people",
  },
  "marketing-prospecting-bot": {
    name: "营销拓客助手",
    domain: "Marketing · Prospecting",
    description: "发现、筛选并跟进潜在客户线索。",
    glyph: "拓",
    accent: "prospecting",
  },
  "marketing-inbound-bot": {
    name: "营销获客助手",
    domain: "Marketing · Inbound",
    description: "承接入站线索、内容触达与客户咨询。",
    glyph: "入",
    accent: "inbound",
  },
  "marketing-voice-bot": {
    name: "营销语音助手",
    domain: "Marketing · Voice",
    description: "支持语音触达、通话沟通与结果整理。",
    glyph: "声",
    accent: "voice",
  },
  "fae-bot": {
    name: "FAE 技术助手",
    domain: "技术支持",
    description: "支持产品咨询、问题诊断与现场应用。",
    glyph: "FAE",
    accent: "support",
  },
  "test-bot": {
    name: "测试助手",
    domain: "开发验证",
    description: "用于接口联调、集成测试与运行验证。",
    glyph: "测",
    accent: "testing",
  },
  "marketing-gtm-bot": {
    name: "GTM 策略助手",
    domain: "Marketing · GTM",
    description: "支持市场进入策略、节奏规划与执行协同。",
    glyph: "GTM",
    accent: "strategy",
  },
  "marketing-intelligence-bot": {
    name: "市场情报助手",
    domain: "Marketing · Intelligence",
    description: "收集并整理市场动态与竞争情报。",
    glyph: "情",
    accent: "intelligence",
  },
};


export function agentIdentity(id: string, fallbackName: string): AgentIdentity {
  return AGENT_IDENTITIES[id] ?? {
    name: fallbackName,
    domain: "MetaBot 实例",
    description: "由运行契约动态发现的 Agent Bot 实例。",
    glyph: "AI",
    accent: "default",
  };
}
