import { useEffect, useState } from "react";

import { localPathname, platformPath } from "./auth";


export type Route =
  | { name: "login" }
  | { name: "account" }
  | { name: "brain" }
  | { name: "missions" }
  | { name: "mission"; missionId: string }
  | { name: "agents" }
  | { name: "agent"; agentId: string }
  | { name: "admin-overview" }
  | { name: "admin-agents" }
  | { name: "admin-agent"; agentId: string }
  | { name: "admin-agent-runtime"; agentId: string }
  | { name: "admin-sessions" }
  | { name: "admin-session"; sessionKey: string }
  | { name: "admin-review" }
  | { name: "admin-activity" }
  | { name: "admin-operations" }
  | { name: "admin-identity" }
  | { name: "admin-governance" }
  | { name: "legacy-redirect"; to: string }
  | { name: "not-found" };

export type RouteSection = "brain" | "agents" | "missions" | "account" | "admin";

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
    ? { name: "legacy-redirect", to: `${prefix}/${encodedValue}` }
    : { name: "not-found" };
}


export function parseRoute(pathname: string): Route {
  const local = localPathname(pathname);
  const clean = local === "/" ? "/" : local.replace(/\/+$/, "");
  if (clean === "/login") return { name: "login" };
  if (clean === "/account") return { name: "account" };
  if (clean === "/") return { name: "brain" };
  if (clean === "/missions") return { name: "missions" };
  if (clean === "/agents") return { name: "agents" };

  if (clean === "/admin" || clean === "/admin/overview") return { name: "admin-overview" };
  if (clean === "/admin/agents") return { name: "admin-agents" };
  if (clean === "/admin/sessions") return { name: "admin-sessions" };
  if (clean === "/admin/review") return { name: "admin-review" };
  if (clean === "/admin/activity") return { name: "admin-activity" };
  if (clean === "/admin/operations") return { name: "admin-operations" };
  if (clean === "/admin/identity") return { name: "admin-identity" };
  if (clean === "/admin/governance") return { name: "admin-governance" };

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
  const agent = /^\/agents\/([^/]+)$/.exec(clean);
  if (agent) {
    const agentId = decode(agent[1]);
    return agentId ? { name: "agent", agentId } : { name: "not-found" };
  }

  if (clean === "/review") return { name: "legacy-redirect", to: "/admin/review" };
  if (clean === "/activity") return { name: "legacy-redirect", to: "/admin/activity" };
  if (clean === "/flywheel") return { name: "legacy-redirect", to: "/admin/operations" };
  if (clean === "/identity") return { name: "legacy-redirect", to: "/admin/identity" };
  if (clean === "/governance") return { name: "legacy-redirect", to: "/admin/governance" };
  if (clean === "/sessions") return { name: "legacy-redirect", to: "/admin/sessions" };
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
    case "missions": return "/missions";
    case "mission": return `/missions/${encodeURIComponent(route.missionId)}`;
    case "agents": return "/agents";
    case "agent": return `/agents/${encodeURIComponent(route.agentId)}`;
    case "admin-overview": return "/admin";
    case "admin-agents": return "/admin/agents";
    case "admin-agent": return `/admin/agents/${encodeURIComponent(route.agentId)}`;
    case "admin-agent-runtime": return `/admin/agents/${encodeURIComponent(route.agentId)}/runtime`;
    case "admin-sessions": return "/admin/sessions";
    case "admin-session": return `/admin/sessions/${encodeURIComponent(route.sessionKey)}`;
    case "admin-review": return "/admin/review";
    case "admin-activity": return "/admin/activity";
    case "admin-operations": return "/admin/operations";
    case "admin-identity": return "/admin/identity";
    case "admin-governance": return "/admin/governance";
    case "legacy-redirect": return route.to;
    default: return "/404";
  }
}


export function routeSection(route: Route): RouteSection | null {
  if (route.name === "brain") return "brain";
  if (route.name === "agents" || route.name === "agent") return "agents";
  if (route.name === "missions" || route.name === "mission") return "missions";
  if (route.name === "account") return "account";
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
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.pathname));
  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.pathname));
    window.addEventListener("popstate", update);
    window.addEventListener("platform:navigate", update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener("platform:navigate", update);
    };
  }, []);
  return route;
}
