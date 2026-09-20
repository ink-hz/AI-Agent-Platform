import { useEffect } from "react";

import type { Route } from "./router";


export const PLATFORM_TITLE = "Orbbec Agent Platform";

const MARKETING_TITLE_BY_SLUG = {
  prospecting: "Marketing Prospecting",
  inbound: "Marketing Inbound",
  voice: "Marketing Voice",
  intelligence: "Marketing Intelligence",
  gtm: "Marketing GTM",
} as const;


export function routeDocumentTitle(route: Route): string {
  switch (route.name) {
    case "home": return PLATFORM_TITLE;
    case "brain": return `AI 助手 · ${PLATFORM_TITLE}`;
    case "ai-engineering": return `AI 工程全景 · ${PLATFORM_TITLE}`;
    case "conversations": return `AI 助手 · ${PLATFORM_TITLE}`;
    case "conversation": return `AI 助手 · ${PLATFORM_TITLE}`;
    case "missions": return `历史任务 · ${PLATFORM_TITLE}`;
    case "mission": return `任务 · ${PLATFORM_TITLE}`;
    case "agents": return `Agent 目录 · ${PLATFORM_TITLE}`;
    case "voc-workspace": return `VOC 洞察助手 · ${PLATFORM_TITLE}`;
    case "hr": return `HR 智能工作台 · ${PLATFORM_TITLE}`;
    case "hr-agent": return "Hannah · HR 智能工作台";
    case "hr-chat": return `HR 智能工作台 · ${PLATFORM_TITLE}`;
    case "hr-positions": return "岗位 · HR 智能工作台";
    case "hr-position": return "岗位 · HR 智能工作台";
    case "hr-position-section": return "岗位 · HR 智能工作台";
    case "hr-panorama": return "HR 情报 · HR 智能工作台";
    case "marketing": return `${MARKETING_TITLE_BY_SLUG[route.agentSlug]} · ${PLATFORM_TITLE}`;
    case "marketing-conversation": return `${MARKETING_TITLE_BY_SLUG[route.agentSlug]} · ${PLATFORM_TITLE}`;
    case "fae-manage-overview": return `技术支持工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-sessions": return `技术支持工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-session": return `技术支持工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-issues": return `技术支持工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-issue": return `技术支持工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-reports": return `技术支持工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-report": return `技术支持工作台 · ${PLATFORM_TITLE}`;
    case "ai-notes": return `AI 工程笔记 · ${PLATFORM_TITLE}`;
    case "ai-note": return `AI 工程笔记 · ${PLATFORM_TITLE}`;
    case "admin-overview": return `运行概览 · ${PLATFORM_TITLE}`;
    case "admin-agents": return `Agent 状态 · ${PLATFORM_TITLE}`;
    case "admin-agent": return `Agent 详情 · ${PLATFORM_TITLE}`;
    case "admin-agent-runtime": return `运行详情 · ${PLATFORM_TITLE}`;
    case "admin-sessions": return `会话记录 · ${PLATFORM_TITLE}`;
    case "admin-session": return `会话回放 · ${PLATFORM_TITLE}`;
    case "admin-review": return `任务复审 · ${PLATFORM_TITLE}`;
    case "admin-activity": return `运行事件 · ${PLATFORM_TITLE}`;
    case "login": return `登录 · ${PLATFORM_TITLE}`;
    case "account": return `企业账号 · ${PLATFORM_TITLE}`;
    case "admin-identity": return `账号与权限 · ${PLATFORM_TITLE}`;
    case "admin-governance": return `审计日志 · ${PLATFORM_TITLE}`;
    case "admin-access": return `访问记录 · ${PLATFORM_TITLE}`;
    case "admin-voc": return `客户洞察工作台 · ${PLATFORM_TITLE}`;
    default: return PLATFORM_TITLE;
  }
}


export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
