import { useEffect } from "react";

import type { Route } from "./router";


export const PLATFORM_TITLE = "Orbbec Agent Platform";


export function routeDocumentTitle(route: Route): string {
  switch (route.name) {
    case "agents": return `Agents · ${PLATFORM_TITLE}`;
    case "agent": return `Agent · ${PLATFORM_TITLE}`;
    case "sessions": return `Sessions · ${PLATFORM_TITLE}`;
    case "session": return `Session Replay · ${PLATFORM_TITLE}`;
    case "activity": return `Activity History · ${PLATFORM_TITLE}`;
    default: return PLATFORM_TITLE;
  }
}


export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
