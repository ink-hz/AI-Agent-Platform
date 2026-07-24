import type { MouseEvent, ReactNode } from "react";

import { UI_COPY } from "./copy";
import { navigate, routeSection, type Route } from "./router";


const NAVIGATION = [
  { label: "总览", path: "/", section: "overview" },
  { label: "Agent", path: "/agents", section: "agents" },
  { label: "Session", path: "/sessions", section: "sessions" },
  { label: "运行记录", path: "/activity", section: "activity" },
] as const;


function follow(event: MouseEvent<HTMLAnchorElement>, path: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(path);
}


export function AppShell({ route, children }: { route: Route; children: ReactNode }) {
  const current = routeSection(route);
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="/" onClick={(event) => follow(event, "/")}>
            <img className="brand-mark" src="/platform-logo.svg" alt="" aria-hidden="true" />
            <span className="brand-name"><strong>Orbbec</strong> Agent Platform</span>
          </a>
          <nav className="product-nav" aria-label={UI_COPY.navigationLabel}>
            {NAVIGATION.map((item) => (
              <a
                aria-current={current === item.section ? "page" : undefined}
                className={current === item.section ? "is-current" : undefined}
                href={item.path}
                key={item.path}
                onClick={(event) => follow(event, item.path)}
              >{item.label}</a>
            ))}
          </nav>
        </div>
      </header>
      <main className="page">{children}</main>
      <footer className="site-foot"><span>Orbbec Agent Platform</span></footer>
    </div>
  );
}
