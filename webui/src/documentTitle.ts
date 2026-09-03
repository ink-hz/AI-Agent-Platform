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
    case "brain": return `Agent 大脑 · ${PLATFORM_TITLE}`;
    case "conversations": return `Agent 大脑 · ${PLATFORM_TITLE}`;
    case "conversation": return `Agent 大脑 · ${PLATFORM_TITLE}`;
    case "missions": return `历史任务 · ${PLATFORM_TITLE}`;
    case "mission": return `任务 · ${PLATFORM_TITLE}`;
    case "agents": return `专业 Agent · ${PLATFORM_TITLE}`;
    case "agent": return `专业 Agent · ${PLATFORM_TITLE}`;
    case "voc-workspace": return `VOC 洞察助手 · ${PLATFORM_TITLE}`;
    case "hr": return `HR Agent · ${PLATFORM_TITLE}`;
    case "hr-conversation": return `HR Agent · ${PLATFORM_TITLE}`;
    case "marketing": return `${MARKETING_TITLE_BY_SLUG[route.agentSlug]} · ${PLATFORM_TITLE}`;
    case "marketing-conversation": return `${MARKETING_TITLE_BY_SLUG[route.agentSlug]} · ${PLATFORM_TITLE}`;
    case "fae-manage-overview": return `FAE 工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-sessions": return `FAE 工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-session": return `FAE 工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-issues": return `FAE 工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-issue": return `FAE 工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-reports": return `FAE 工作台 · ${PLATFORM_TITLE}`;
    case "fae-manage-report": return `FAE 工作台 · ${PLATFORM_TITLE}`;
    case "ai-notes": return `AI 工程笔记 · ${PLATFORM_TITLE}`;
    case "ai-note": return `AI 工程笔记 · ${PLATFORM_TITLE}`;
    case "admin-overview": return `管理中心 · ${PLATFORM_TITLE}`;
    case "admin-agents": return `Agent 管理 · ${PLATFORM_TITLE}`;
    case "admin-agent": return `Agent 详情 · ${PLATFORM_TITLE}`;
    case "admin-agent-runtime": return `运行详情 · ${PLATFORM_TITLE}`;
    case "admin-sessions": return `Session · ${PLATFORM_TITLE}`;
    case "admin-session": return `Session 回放 · ${PLATFORM_TITLE}`;
    case "admin-review": return `复审闭环 · ${PLATFORM_TITLE}`;
    case "admin-activity": return `运行记录 · ${PLATFORM_TITLE}`;
    case "login": return `登录 · ${PLATFORM_TITLE}`;
    case "account": return `企业账号 · ${PLATFORM_TITLE}`;
    case "admin-identity": return `身份管理 · ${PLATFORM_TITLE}`;
    case "admin-governance": return `治理审计 · ${PLATFORM_TITLE}`;
    case "admin-voc": return `VOC 管理 · ${PLATFORM_TITLE}`;
    default: return PLATFORM_TITLE;
  }
}


export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
