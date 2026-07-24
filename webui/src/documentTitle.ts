import { useEffect } from "react";

import type { Route } from "./router";


export const PLATFORM_TITLE = "Orbbec Agent Platform";


export function routeDocumentTitle(route: Route): string {
  switch (route.name) {
    case "agents": return `Agent · ${PLATFORM_TITLE}`;
    case "agent": return `Agent 详情 · ${PLATFORM_TITLE}`;
    case "agent-runtime": return `运行详情 · ${PLATFORM_TITLE}`;
    case "sessions": return `Session · ${PLATFORM_TITLE}`;
    case "session": return `Session 回放 · ${PLATFORM_TITLE}`;
    case "activity": return `运行记录 · ${PLATFORM_TITLE}`;
    default: return PLATFORM_TITLE;
  }
}


export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
