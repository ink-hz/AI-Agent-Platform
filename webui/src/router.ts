import { useEffect, useState } from "react";

import { localPathname, platformPath } from "./auth";


export type Route =
  | { name: "login" }
  | { name: "account" }
  | { name: "identity" }
  | { name: "governance" }
  | { name: "overview" }
  | { name: "agents" }
  | { name: "agent"; agentId: string }
  | { name: "agent-runtime"; agentId: string }
  | { name: "sessions" }
  | { name: "session"; sessionKey: string }
  | { name: "flywheel" }
  | { name: "review" }
  | { name: "activity" }
  | { name: "not-found" };

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


export function parseRoute(pathname: string): Route {
  const local = localPathname(pathname);
  const clean = local === "/" ? "/" : local.replace(/\/+$/, "");
  if (clean === "/login") return { name: "login" };
  if (clean === "/account") return { name: "account" };
  if (clean === "/identity") return { name: "identity" };
  if (clean === "/governance") return { name: "governance" };
  if (clean === "/") return { name: "overview" };
  if (clean === "/agents") return { name: "agents" };
  if (clean === "/sessions") return { name: "sessions" };
  if (clean === "/flywheel") return { name: "flywheel" };
  if (clean === "/review") return { name: "review" };
  if (clean === "/activity") return { name: "activity" };
  const agentRuntime = /^\/agents\/([^/]+)\/runtime$/.exec(clean);
  if (agentRuntime) {
    const agentId = decode(agentRuntime[1]);
    return agentId ? { name: "agent-runtime", agentId } : { name: "not-found" };
  }
  const agent = /^\/agents\/([^/]+)$/.exec(clean);
  if (agent) {
    const agentId = decode(agent[1]);
    return agentId ? { name: "agent", agentId } : { name: "not-found" };
  }
  const session = /^\/sessions\/([^/]+)$/.exec(clean);
  if (session) {
    const sessionKey = decode(session[1]);
    return sessionKey ? { name: "session", sessionKey } : { name: "not-found" };
  }
  return { name: "not-found" };
}


export function routePath(route: Route): string {
  switch (route.name) {
    case "login": return "/login";
    case "account": return "/account";
    case "identity": return "/identity";
    case "governance": return "/governance";
    case "overview": return "/";
    case "agents": return "/agents";
    case "agent": return `/agents/${encodeURIComponent(route.agentId)}`;
    case "agent-runtime": return `/agents/${encodeURIComponent(route.agentId)}/runtime`;
    case "sessions": return "/sessions";
    case "session": return `/sessions/${encodeURIComponent(route.sessionKey)}`;
    case "flywheel": return "/flywheel";
    case "review": return "/review";
    case "activity": return "/activity";
    default: return "/404";
  }
}


export function routeSection(route: Route): "overview" | "agents" | "sessions" | "review" | "activity" | "account" | "identity" | "governance" | null {
  if (route.name === "agent" || route.name === "agent-runtime") return "agents";
  if (route.name === "session") return "sessions";
  if (route.name === "flywheel" || route.name === "not-found" || route.name === "login") return null;
  return route.name;
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
