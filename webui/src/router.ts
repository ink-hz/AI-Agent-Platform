import { useEffect, useState } from "react";

import { localPathname, platformPath } from "./auth";
import { STATUS_LABELS } from "./components/review/IssueList";
import { MARKETING_AGENT_ID_BY_SLUG } from "./platform/workspaces";
import { sessionFiltersFromSearch } from "./sessionNavigation";


export type MarketingAgentSlug = keyof typeof MARKETING_AGENT_ID_BY_SLUG;


export type Route =
  | { name: "login" }
  | { name: "account" }
  | { name: "brain" }
  | { name: "conversations" }
  | { name: "conversation"; conversationId: string }
  | { name: "missions" }
  | { name: "mission"; missionId: string }
  | { name: "agents" }
  | { name: "voc-workspace" }
  | { name: "hr" }
  | { name: "hr-conversation"; conversationId: string }
  | { name: "marketing"; agentSlug: MarketingAgentSlug }
  | { name: "marketing-conversation"; agentSlug: MarketingAgentSlug; conversationId: string }
  | { name: "fae-manage-overview" }
  | { name: "fae-manage-sessions" }
  | { name: "fae-manage-session"; sessionKey: string }
  | { name: "fae-manage-issues" }
  | { name: "fae-manage-issue"; issueId: string }
  | { name: "fae-manage-reports" }
  | { name: "fae-manage-report"; reportId: string }
  | { name: "ai-notes" }
  | { name: "ai-note"; categorySlug: string; articleSlug: string }
  | { name: "admin-overview" }
  | { name: "admin-agents" }
  | { name: "admin-agent"; agentId: string }
  | { name: "admin-agent-runtime"; agentId: string }
  | { name: "admin-sessions" }
  | { name: "admin-session"; sessionKey: string }
  | { name: "admin-review" }
  | { name: "admin-activity" }
  | { name: "admin-identity" }
  | { name: "admin-governance" }
  | { name: "admin-voc" }
  | { name: "legacy-redirect"; to: string; navigation: "spa" | "document" }
  | { name: "not-found" };

export type RouteSection = "brain" | "conversations" | "agents" | "missions" | "ai-notes" | "account" | "fae" | "admin";

export type NavigateOptions = {
  replace?: boolean;
  state?: unknown;
};


function decode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}


function encodedRedirect(prefix: string, encodedValue: string): Route {
  return decode(encodedValue)
    ? { name: "legacy-redirect", to: `${prefix}/${encodedValue}`, navigation: "spa" }
    : { name: "not-found" };
}


const MARKETING_SLUGS = new Set(Object.keys(MARKETING_AGENT_ID_BY_SLUG));
const SAFE_WORKSPACE_ID = /^[A-Za-z0-9:._-]+$/;


function marketingSlug(value: string): value is MarketingAgentSlug {
  return MARKETING_SLUGS.has(value);
}


function safeDecodedValue(encodedValue: string, pattern = SAFE_WORKSPACE_ID): string | null {
  const value = decode(encodedValue);
  return value && pattern.test(value) ? value : null;
}


function singleSearchValue(
  query: URLSearchParams,
  key: string,
  valid: (value: string) => boolean = (value) => value.length > 0,
): string | null {
  const values = query.getAll(key);
  return values.length === 1 && valid(values[0]) ? values[0] : null;
}


const LOCAL_LIFECYCLE_STATUSES = new Set([
  "open",
  ...Object.keys(STATUS_LABELS).filter((status) => status !== "unknown"),
]);
const CLOUD_DISPOSITIONS = new Set(["actionable", "duplicate", "not_actionable", "wont_fix"]);
const ISSUE_PRIORITIES = new Set(["P0", "P1", "P2", "P3"]);


export function issueFilterFromSearch(search: string, cloudReplica: boolean): { value: string; valid: boolean; kind: "status" | "disposition" | "all" } {
  const query = new URLSearchParams(search);
  const statuses = query.getAll("status");
  const dispositions = query.getAll("disposition");
  if (statuses.length === 0 && dispositions.length === 0) return { value: "open", valid: true, kind: "status" };
  if (statuses.length === 1 && statuses[0] === "all" && dispositions.length === 0) {
    return { value: "all", valid: true, kind: "all" };
  }
  if (cloudReplica) {
    if (statuses.length === 1 && LOCAL_LIFECYCLE_STATUSES.has(statuses[0]) && dispositions.length === 0) {
      return { value: statuses[0], valid: true, kind: "status" };
    }
    if (statuses.length === 0 && dispositions.length === 1 && CLOUD_DISPOSITIONS.has(dispositions[0])) {
      return { value: dispositions[0], valid: true, kind: "disposition" };
    }
  } else if (statuses.length === 1 && dispositions.length === 0 && LOCAL_LIFECYCLE_STATUSES.has(statuses[0])) {
    return { value: statuses[0], valid: true, kind: "status" };
  }
  return { value: "open", valid: false, kind: "status" };
}


export function safeIssueCollectionParams(search: string, cloudReplica: boolean): URLSearchParams {
  const raw = new URLSearchParams(search);
  const safe = new URLSearchParams();
  const status = issueFilterFromSearch(search, cloudReplica);
  if (status.valid && status.kind === "all") safe.set("status", "all");
  else if (status.valid && status.value) safe.set(status.kind, status.value);
  const priority = singleSearchValue(raw, "priority", (value) => ISSUE_PRIORITIES.has(value));
  const failureLayer = singleSearchValue(raw, "failure_layer", (value) => /^[a-z][a-z0-9_]{0,63}$/.test(value));
  const owner = singleSearchValue(raw, "owner", (value) => value.length <= 160 && value.trim() === value && value.length > 0);
  const query = singleSearchValue(raw, "q", (value) => value.length <= 240 && value.trim() === value && value.length > 0);
  const createdAfter = singleSearchValue(raw, "created_after", (value) => /^\d{4}-\d{2}-\d{2}T00:00:00\+08:00$/.test(value));
  if (priority) safe.set("priority", priority);
  if (failureLayer) safe.set("failure_layer", failureLayer);
  if (owner) safe.set("owner", owner);
  if (query) safe.set("q", query);
  if (createdAfter) safe.set("created_after", createdAfter);
  const page = singleSearchValue(raw, "page", (value) => /^\d+$/.test(value));
  if (page) {
    const parsed = Number(page);
    if (Number.isSafeInteger(parsed) && parsed > 1) safe.set("page", String(parsed));
  }
  return safe;
}


export function selectedReportVersion(search: string): number | undefined | null {
  const values = new URLSearchParams(search).getAll("version");
  if (values.length === 0) return undefined;
  if (values.length !== 1 || !/^[1-9]\d*$/.test(values[0])) return null;
  const version = Number(values[0]);
  return Number.isSafeInteger(version) ? version : null;
}


export function safeLegacyWorkspaceSearch(targetPath: string, sourceSearch: string): string {
  const raw = new URLSearchParams(sourceSearch);
  const safe = new URLSearchParams();

  if (/^\/fae\/manage\/sessions(?:\/[^/]+)?$/.test(targetPath)) {
    const parsed = sessionFiltersFromSearch(sourceSearch);
    const filterKeys = [
      "q", "channel", "sentiment", "review_status", "outcome", "date_from", "date_to",
      "date_before", "subject_key", "has_subject", "abnormal", "has_latency",
    ] as const;
    for (const key of filterKeys) {
      if (raw.getAll(key).length === 1 && parsed[key]) safe.set(key, parsed[key]);
    }
    const page = singleSearchValue(raw, "page", (value) => /^[1-9]\d*$/.test(value));
    if (page) {
      const parsedPage = Number(page);
      if (Number.isSafeInteger(parsedPage)) safe.set("page", String(parsedPage));
    }
  } else if (/^\/fae\/manage\/issues(?:\/[^/]+)?$/.test(targetPath)) {
    const issueParams = safeIssueCollectionParams(sourceSearch, true);
    if (raw.getAll("status").length === 0 && raw.getAll("disposition").length === 0) {
      issueParams.delete("status");
    }
    issueParams.forEach((value, key) => safe.set(key, value));
    const sessionKey = singleSearchValue(raw, "session_key", (value) => SAFE_WORKSPACE_ID.test(value));
    const turnKey = singleSearchValue(raw, "turn_key", (value) => SAFE_WORKSPACE_ID.test(value));
    if (sessionKey && turnKey) {
      safe.set("session_key", sessionKey);
      safe.set("turn_key", turnKey);
    }
  } else if (/^\/fae\/manage\/reports\/[^/]+$/.test(targetPath)) {
    const version = selectedReportVersion(sourceSearch);
    if (version !== undefined && version !== null) safe.set("version", String(version));
  }

  const search = safe.toString();
  return search ? `?${search}` : "";
}


export function parseRoute(pathname: string, search = ""): Route {
  const local = localPathname(pathname);
  const clean = local === "/" ? "/" : local.replace(/\/+$/, "");
  if (clean === "/login") return { name: "login" };
  if (clean === "/account") return { name: "account" };
  if (clean === "/") return { name: "brain" };
  if (clean === "/conversations") return { name: "conversations" };
  if (clean === "/missions") return { name: "missions" };
  if (clean === "/agents") return { name: "agents" };
  if (clean === "/agents/voc" || clean === "/agents/voc/workspace") {
    return { name: "legacy-redirect", to: "/voc/", navigation: "document" };
  }
  if (clean === "/agents/ai-fae-agent") {
    return { name: "legacy-redirect", to: "/fae/", navigation: "document" };
  }
  if (clean === "/agents/ai-admin-agent") {
    return { name: "legacy-redirect", to: "/office/?view=services", navigation: "document" };
  }
  if (clean === "/agents/hr-bot") {
    return { name: "legacy-redirect", to: "/hr/", navigation: "spa" };
  }
  const legacyMarketing = Object.entries(MARKETING_AGENT_ID_BY_SLUG).find(([, id]) => clean === `/agents/${id}`);
  if (legacyMarketing) {
    return { name: "legacy-redirect", to: `/marketing/${legacyMarketing[0]}`, navigation: "spa" };
  }
  if (clean === "/ai-notes") return { name: "ai-notes" };

  const legacyAgentConversation = /^\/agents\/([^/]+)\/conversations\/([^/]+)$/.exec(clean);
  if (legacyAgentConversation) {
    const agentId = safeDecodedValue(legacyAgentConversation[1]);
    const conversationId = safeDecodedValue(legacyAgentConversation[2]);
    if (!agentId || !conversationId) return { name: "not-found" };
    if (agentId === "hr-bot") {
      return { name: "legacy-redirect", to: `/hr/conversations/${legacyAgentConversation[2]}`, navigation: "spa" };
    }
    if (agentId === "ai-fae-agent") {
      return { name: "legacy-redirect", to: `/fae/conversations/${legacyAgentConversation[2]}`, navigation: "document" };
    }
    const marketingEntry = Object.entries(MARKETING_AGENT_ID_BY_SLUG).find(([, id]) => id === agentId);
    if (marketingEntry) {
      return { name: "legacy-redirect", to: `/marketing/${marketingEntry[0]}/conversations/${legacyAgentConversation[2]}`, navigation: "spa" };
    }
  }

  const hrConversation = /^\/hr\/conversations\/([^/]+)$/.exec(clean);
  if (hrConversation) {
    const conversationId = safeDecodedValue(hrConversation[1]);
    return conversationId ? { name: "hr-conversation", conversationId } : { name: "not-found" };
  }
  if (local === "/hr") return { name: "legacy-redirect", to: "/hr/", navigation: "spa" };
  if (clean === "/hr") return { name: "hr" };

  const marketingConversation = /^\/marketing\/([^/]+)\/conversations\/([^/]+)$/.exec(clean);
  if (marketingConversation) {
    const agentSlug = marketingConversation[1];
    const conversationId = safeDecodedValue(marketingConversation[2]);
    return marketingSlug(agentSlug) && conversationId
      ? { name: "marketing-conversation", agentSlug, conversationId }
      : { name: "not-found" };
  }
  const marketing = /^\/marketing\/([^/]+)$/.exec(clean);
  if (marketing) {
    return marketingSlug(marketing[1])
      ? { name: "marketing", agentSlug: marketing[1] }
      : { name: "not-found" };
  }
  if (clean === "/marketing") {
    return { name: "legacy-redirect", to: "/marketing/prospecting", navigation: "spa" };
  }

  if (clean === "/voc") {
    const views = new URLSearchParams(search).getAll("view");
    if (views.length === 1 && views[0] === "management") {
      return { name: "legacy-redirect", to: "/voc/manage/", navigation: "document" };
    }
  }

  const faeManageSession = /^\/fae\/manage\/sessions\/([^/]+)$/.exec(clean);
  if (faeManageSession) {
    const sessionKey = safeDecodedValue(faeManageSession[1]);
    return sessionKey ? { name: "fae-manage-session", sessionKey } : { name: "not-found" };
  }
  const faeManageIssue = /^\/fae\/manage\/issues\/([^/]+)$/.exec(clean);
  if (faeManageIssue) {
    const issueId = safeDecodedValue(faeManageIssue[1], /^[0-9a-fA-F-]{36}$/);
    return issueId ? { name: "fae-manage-issue", issueId } : { name: "not-found" };
  }
  const faeManageReport = /^\/fae\/manage\/reports\/([^/]+)$/.exec(clean);
  if (faeManageReport) {
    const reportId = safeDecodedValue(faeManageReport[1]);
    return reportId ? { name: "fae-manage-report", reportId } : { name: "not-found" };
  }
  if (clean === "/fae/manage") return { name: "fae-manage-overview" };
  if (clean === "/fae/manage/sessions") return { name: "fae-manage-sessions" };
  if (clean === "/fae/manage/issues") return { name: "fae-manage-issues" };
  if (clean === "/fae/manage/reports") return { name: "fae-manage-reports" };

  const aiNote = /^\/ai-notes\/([a-z0-9][a-z0-9-]{0,63})\/([a-z0-9][a-z0-9-]{0,127})$/.exec(clean);
  if (aiNote) return { name: "ai-note", categorySlug: aiNote[1], articleSlug: aiNote[2] };

  if (clean === "/admin" || clean === "/admin/overview") return { name: "admin-overview" };
  if (clean === "/admin/agents") return { name: "admin-agents" };
  if (clean === "/admin/sessions") return { name: "admin-sessions" };
  if (clean === "/admin/review") return { name: "admin-review" };
  if (clean === "/admin/activity") return { name: "admin-activity" };
  if (clean === "/admin/operations") return { name: "legacy-redirect", to: "/admin", navigation: "spa" };
  if (clean === "/admin/identity") return { name: "admin-identity" };
  if (clean === "/admin/governance") return { name: "admin-governance" };
  if (clean === "/admin/voc") return { name: "legacy-redirect", to: "/voc/manage/", navigation: "document" };

  const faeSession = /^\/admin\/fae\/sessions\/([^/]+)$/.exec(clean);
  if (faeSession) {
    const sessionKey = decode(faeSession[1]);
    return sessionKey && /^[A-Za-z0-9:._-]+$/.test(sessionKey)
      ? { name: "legacy-redirect", to: `/fae/manage/sessions/${faeSession[1]}`, navigation: "spa" }
      : { name: "not-found" };
  }
  const faeIssue = /^\/admin\/fae\/issues\/([^/]+)$/.exec(clean);
  if (faeIssue) {
    const issueId = decode(faeIssue[1]);
    return issueId && /^[0-9a-fA-F-]{36}$/.test(issueId)
      ? { name: "legacy-redirect", to: `/fae/manage/issues/${faeIssue[1]}`, navigation: "spa" }
      : { name: "not-found" };
  }
  const faeReport = /^\/admin\/fae\/reports\/([^/]+)$/.exec(clean);
  if (faeReport) {
    const reportId = decode(faeReport[1]);
    return reportId && /^[A-Za-z0-9._:-]+$/.test(reportId)
      ? { name: "legacy-redirect", to: `/fae/manage/reports/${faeReport[1]}`, navigation: "spa" }
      : { name: "not-found" };
  }
  if (clean === "/admin/fae") return { name: "legacy-redirect", to: "/fae/manage/", navigation: "spa" };
  if (clean === "/admin/fae/sessions") return { name: "legacy-redirect", to: "/fae/manage/sessions", navigation: "spa" };
  if (clean === "/admin/fae/issues") return { name: "legacy-redirect", to: "/fae/manage/issues", navigation: "spa" };
  if (clean === "/admin/fae/reports") return { name: "legacy-redirect", to: "/fae/manage/reports", navigation: "spa" };

  const adminAgentRuntime = /^\/admin\/agents\/([^/]+)\/runtime$/.exec(clean);
  if (adminAgentRuntime) {
    const agentId = decode(adminAgentRuntime[1]);
    return agentId ? { name: "admin-agent-runtime", agentId } : { name: "not-found" };
  }
  const adminAgent = /^\/admin\/agents\/([^/]+)$/.exec(clean);
  if (adminAgent) {
    const agentId = decode(adminAgent[1]);
    return agentId ? { name: "admin-agent", agentId } : { name: "not-found" };
  }
  const adminSession = /^\/admin\/sessions\/([^/]+)$/.exec(clean);
  if (adminSession) {
    const sessionKey = decode(adminSession[1]);
    return sessionKey ? { name: "admin-session", sessionKey } : { name: "not-found" };
  }

  const mission = /^\/missions\/([^/]+)$/.exec(clean);
  if (mission) {
    const missionId = decode(mission[1]);
    return missionId ? { name: "mission", missionId } : { name: "not-found" };
  }
  const conversation = /^\/conversations\/([^/]+)$/.exec(clean);
  if (conversation) {
    const conversationId = decode(conversation[1]);
    return conversationId ? { name: "conversation", conversationId } : { name: "not-found" };
  }
  if (clean === "/review") return { name: "legacy-redirect", to: "/admin/review", navigation: "spa" };
  if (clean === "/activity") return { name: "legacy-redirect", to: "/admin/activity", navigation: "spa" };
  if (clean === "/flywheel") return { name: "legacy-redirect", to: "/admin", navigation: "spa" };
  if (clean === "/identity") return { name: "legacy-redirect", to: "/admin/identity", navigation: "spa" };
  if (clean === "/governance") return { name: "legacy-redirect", to: "/admin/governance", navigation: "spa" };
  if (clean === "/sessions") return { name: "legacy-redirect", to: "/admin/sessions", navigation: "spa" };
  const legacySession = /^\/sessions\/([^/]+)$/.exec(clean);
  if (legacySession) return encodedRedirect("/admin/sessions", legacySession[1]);
  const legacyRuntime = /^\/agents\/([^/]+)\/runtime$/.exec(clean);
  if (legacyRuntime) return encodedRedirect("/admin/agents", `${legacyRuntime[1]}/runtime`);

  return { name: "not-found" };
}


export function routePath(route: Route): string {
  switch (route.name) {
    case "login": return "/login";
    case "account": return "/account";
    case "brain": return "/";
    case "conversations": return "/conversations";
    case "conversation": return `/conversations/${encodeURIComponent(route.conversationId)}`;
    case "missions": return "/missions";
    case "mission": return `/missions/${encodeURIComponent(route.missionId)}`;
    case "agents": return "/agents";
    case "voc-workspace": return "/agents/voc/workspace";
    case "hr": return "/hr/";
    case "hr-conversation": return `/hr/conversations/${encodeURIComponent(route.conversationId)}`;
    case "marketing": return `/marketing/${route.agentSlug}`;
    case "marketing-conversation": return `/marketing/${route.agentSlug}/conversations/${encodeURIComponent(route.conversationId)}`;
    case "fae-manage-overview": return "/fae/manage/";
    case "fae-manage-sessions": return "/fae/manage/sessions";
    case "fae-manage-session": return `/fae/manage/sessions/${encodeURIComponent(route.sessionKey)}`;
    case "fae-manage-issues": return "/fae/manage/issues";
    case "fae-manage-issue": return `/fae/manage/issues/${encodeURIComponent(route.issueId)}`;
    case "fae-manage-reports": return "/fae/manage/reports";
    case "fae-manage-report": return `/fae/manage/reports/${encodeURIComponent(route.reportId)}`;
    case "ai-notes": return "/ai-notes";
    case "ai-note": return `/ai-notes/${encodeURIComponent(route.categorySlug)}/${encodeURIComponent(route.articleSlug)}`;
    case "admin-overview": return "/admin";
    case "admin-agents": return "/admin/agents";
    case "admin-agent": return `/admin/agents/${encodeURIComponent(route.agentId)}`;
    case "admin-agent-runtime": return `/admin/agents/${encodeURIComponent(route.agentId)}/runtime`;
    case "admin-sessions": return "/admin/sessions";
    case "admin-session": return `/admin/sessions/${encodeURIComponent(route.sessionKey)}`;
    case "admin-review": return "/admin/review";
    case "admin-activity": return "/admin/activity";
    case "admin-identity": return "/admin/identity";
    case "admin-governance": return "/admin/governance";
    case "admin-voc": return "/admin/voc";
    case "legacy-redirect": return route.to;
    default: return "/404";
  }
}


export function routeSection(route: Route): RouteSection | null {
  if (route.name === "brain") return "brain";
  if (route.name === "conversations" || route.name === "conversation") return "brain";
  if (route.name === "agents" || route.name === "voc-workspace"
    || route.name === "hr" || route.name === "hr-conversation" || route.name === "marketing" || route.name === "marketing-conversation") return "agents";
  if (route.name === "missions" || route.name === "mission") return "missions";
  if (route.name === "ai-notes" || route.name === "ai-note") return "ai-notes";
  if (route.name === "account") return "account";
  if (route.name.startsWith("fae-manage-")) return "fae";
  if (route.name.startsWith("admin-")) return "admin";
  return null;
}


export function currentLocationPath(): string {
  return `${localPathname()}${window.location.search}`;
}


export function navigate(path: string, options: NavigateOptions = {}): void {
  if (currentLocationPath() === path) return;
  const target = platformPath(path);
  if (options.replace) {
    window.history.replaceState(options.state ?? {}, "", target);
  } else {
    window.history.pushState(options.state ?? {}, "", target);
  }
  window.dispatchEvent(new Event("platform:navigate"));
  if (!options.replace) window.requestAnimationFrame(() => window.scrollTo(0, 0));
}


export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.pathname, window.location.search));
  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.pathname, window.location.search));
    window.addEventListener("popstate", update);
    window.addEventListener("platform:navigate", update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener("platform:navigate", update);
    };
  }, []);
  return route;
}
