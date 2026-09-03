export type WorkspaceId = "office" | "fae" | "voc" | "hr" | "marketing";
export type RouteOwner = "platform" | "ai-admin" | "ai-fae" | "voc";
export type ManagementScope = "fae_workbench" | "voc_management";

export const FAE_DIRECT_PATH = "/fae/";
export const FAE_MANAGEMENT_PATH = "/fae/manage";
export const FAE_WORKBENCH_API_PATH = "/api/fae";

export interface WorkspaceRoute {
  workspaceId: WorkspaceId;
  basePath: string;
  routeOwner: RouteOwner;
  directAgentIds: readonly string[];
  agentSlugById: Readonly<Record<string, string>>;
  availability: "catalog";
  management: {
    basePath: string;
    routeOwner: RouteOwner;
    scope: ManagementScope;
  } | null;
}

export const MARKETING_AGENT_ID_BY_SLUG = Object.freeze({
  prospecting: "marketing-prospecting-bot",
  inbound: "marketing-inbound-bot",
  voice: "marketing-voice-bot",
  intelligence: "marketing-intelligence-bot",
  gtm: "marketing-gtm-bot",
} as const);

export const WORKSPACES: readonly WorkspaceRoute[] = Object.freeze([
  { workspaceId: "office", basePath: "/office/", routeOwner: "ai-admin", directAgentIds: ["ai-admin-agent"], agentSlugById: {}, availability: "catalog", management: null },
  { workspaceId: "fae", basePath: FAE_DIRECT_PATH, routeOwner: "ai-fae", directAgentIds: ["ai-fae-agent"], agentSlugById: {}, availability: "catalog", management: { basePath: `${FAE_MANAGEMENT_PATH}/`, routeOwner: "platform", scope: "fae_workbench" } },
  { workspaceId: "voc", basePath: "/voc/", routeOwner: "voc", directAgentIds: ["voc"], agentSlugById: {}, availability: "catalog", management: { basePath: "/voc/manage/", routeOwner: "voc", scope: "voc_management" } },
  { workspaceId: "hr", basePath: "/hr/", routeOwner: "platform", directAgentIds: ["hr-bot"], agentSlugById: {}, availability: "catalog", management: null },
  { workspaceId: "marketing", basePath: "/marketing/", routeOwner: "platform", directAgentIds: Object.values(MARKETING_AGENT_ID_BY_SLUG), agentSlugById: Object.fromEntries(Object.entries(MARKETING_AGENT_ID_BY_SLUG).map(([slug, id]) => [id, slug])), availability: "catalog", management: null },
]);

export function workspaceForAgent(agentId: string): WorkspaceRoute | null {
  return WORKSPACES.find((workspace) => workspace.directAgentIds.includes(agentId)) ?? null;
}

export function workspaceLaunchPath(agentId: string): string | null {
  const workspace = workspaceForAgent(agentId);
  if (!workspace) return null;
  if (workspace.workspaceId === "office") return `${workspace.basePath}?view=services`;
  if (workspace.workspaceId === "marketing") {
    const slug = workspace.agentSlugById[agentId];
    return slug ? `${workspace.basePath}${slug}` : null;
  }
  return workspace.basePath;
}

export function directConversationPath(agentId: string, conversationId: string): string | null {
  const workspace = workspaceForAgent(agentId);
  if (!workspace || workspace.routeOwner === "voc" || workspace.routeOwner === "ai-admin") return null;
  const launchPath = workspaceLaunchPath(agentId);
  if (!launchPath) return null;
  return `${launchPath.replace(/\/$/, "")}/conversations/${encodeURIComponent(conversationId)}`;
}
