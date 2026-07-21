import { useEffect, useState } from "react";


export type Route =
  | { name: "overview" }
  | { name: "agents" }
  | { name: "agent"; agentId: string }
  | { name: "sessions" }
  | { name: "session"; sessionKey: string }
  | { name: "flywheel" }
  | { name: "not-found" };


function decode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}


export function parseRoute(pathname: string): Route {
  const clean = pathname === "/" ? "/" : pathname.replace(/\/+$/, "");
  if (clean === "/") return { name: "overview" };
  if (clean === "/agents") return { name: "agents" };
  if (clean === "/sessions") return { name: "sessions" };
  if (clean === "/flywheel") return { name: "flywheel" };
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
    case "overview": return "/";
    case "agents": return "/agents";
    case "agent": return `/agents/${encodeURIComponent(route.agentId)}`;
    case "sessions": return "/sessions";
    case "session": return `/sessions/${encodeURIComponent(route.sessionKey)}`;
    case "flywheel": return "/flywheel";
    default: return "/404";
  }
}


export function routeSection(route: Route): "overview" | "agents" | "sessions" | "flywheel" | null {
  if (route.name === "agent") return "agents";
  if (route.name === "session") return "sessions";
  if (route.name === "not-found") return null;
  return route.name;
}


export function navigate(path: string): void {
  if (window.location.pathname === path) return;
  window.history.pushState({}, "", path);
  window.dispatchEvent(new Event("platform:navigate"));
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

