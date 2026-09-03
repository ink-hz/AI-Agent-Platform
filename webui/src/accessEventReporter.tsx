import { useEffect } from "react";

import { platformPath, type Account } from "./auth";
import { MARKETING_AGENT_ID_BY_SLUG } from "./platform/workspaces";
import type { Route } from "./router";

export interface PageAccessEvent {
  workspace_key: string;
  page_key: string;
  agent_id?: string;
}

export function accessEventForRoute(route: Route): PageAccessEvent | null {
  switch (route.name) {
    case "brain": return { workspace_key: "platform", page_key: "platform.brain" };
    case "conversations": return { workspace_key: "platform", page_key: "platform.conversations" };
    case "conversation": return { workspace_key: "platform", page_key: "platform.conversation" };
    case "agents": return { workspace_key: "platform", page_key: "platform.agent_directory" };
    case "missions": return { workspace_key: "platform", page_key: "platform.missions" };
    case "mission": return { workspace_key: "platform", page_key: "platform.mission_detail" };
    case "account": return { workspace_key: "platform", page_key: "platform.account" };
    case "ai-notes": return { workspace_key: "platform", page_key: "platform.ai_notes" };
    case "ai-note": return { workspace_key: "platform", page_key: "platform.ai_note" };
    case "hr": return { workspace_key: "hr", page_key: "hr.workspace" };
    case "hr-conversation": return { workspace_key: "hr", page_key: "hr.conversation" };
    case "marketing": return { workspace_key: "marketing", page_key: "marketing.workspace", agent_id: MARKETING_AGENT_ID_BY_SLUG[route.agentSlug] };
    case "marketing-conversation": return { workspace_key: "marketing", page_key: "marketing.conversation", agent_id: MARKETING_AGENT_ID_BY_SLUG[route.agentSlug] };
    case "fae-manage-overview": return { workspace_key: "fae", page_key: "fae.manage.overview" };
    case "fae-manage-sessions": return { workspace_key: "fae", page_key: "fae.manage.sessions" };
    case "fae-manage-session": return { workspace_key: "fae", page_key: "fae.manage.session_detail" };
    case "fae-manage-issues": return { workspace_key: "fae", page_key: "fae.manage.issues" };
    case "fae-manage-issue": return { workspace_key: "fae", page_key: "fae.manage.issue_detail" };
    case "fae-manage-reports": return { workspace_key: "fae", page_key: "fae.manage.reports" };
    case "fae-manage-report": return { workspace_key: "fae", page_key: "fae.manage.report_detail" };
    case "admin-overview": return { workspace_key: "admin", page_key: "admin.overview" };
    case "admin-agents": return { workspace_key: "admin", page_key: "admin.agents" };
    case "admin-agent": return { workspace_key: "admin", page_key: "admin.agent_detail" };
    case "admin-agent-runtime": return { workspace_key: "admin", page_key: "admin.agent_runtime" };
    case "admin-sessions": return { workspace_key: "admin", page_key: "admin.sessions" };
    case "admin-session": return { workspace_key: "admin", page_key: "admin.session_detail" };
    case "admin-review": return { workspace_key: "admin", page_key: "admin.review" };
    case "admin-activity": return { workspace_key: "admin", page_key: "admin.activity" };
    case "admin-identity": return { workspace_key: "admin", page_key: "admin.identity" };
    case "admin-governance": return { workspace_key: "admin", page_key: "admin.governance" };
    case "admin-access": return { workspace_key: "admin", page_key: "admin.access_history" };
    default: return null;
  }
}

export function AccessEventReporter({ account, route }: { account: Account; route: Route }) {
  const descriptor = accessEventForRoute(route);
  const workspaceKey = descriptor?.workspace_key;
  const pageKey = descriptor?.page_key;
  const agentId = descriptor?.agent_id;

  useEffect(() => {
    if (!workspaceKey || !pageKey) return;
    const body = {
      access_event_id: crypto.randomUUID(),
      workspace_key: workspaceKey,
      page_key: pageKey,
      ...(agentId ? { agent_id: agentId } : {}),
    };
    void Promise.resolve().then(() => fetch(platformPath("/api/v1/access-events/page-view"), {
      method: "POST",
      credentials: "include",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })).catch(() => undefined);
  }, [account.internal_user_id, agentId, pageKey, workspaceKey]);
  return null;
}
