import { useEffect, useState, type MouseEvent, type ReactNode } from "react";

import { fetchDeployment } from "./api";
import { UI_COPY } from "./copy";
import { navigate, routeSection, type Route } from "./router";
import type { DeploymentInfo } from "./types";


const NAVIGATION = [
  { label: "总览", path: "/", section: "overview" },
  { label: "Agent", path: "/agents", section: "agents" },
  { label: "Session", path: "/sessions", section: "sessions" },
  { label: "复审闭环", path: "/review", section: "review" },
  { label: "运行记录", path: "/activity", section: "activity" },
] as const;


function follow(event: MouseEvent<HTMLAnchorElement>, path: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(path);
}


export function AppShell({ route, children }: { route: Route; children: ReactNode }) {
  const current = routeSection(route);
  const [deployment, setDeployment] = useState<DeploymentInfo | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetchDeployment(controller.signal).then(setDeployment).catch(() => undefined);
    return () => controller.abort();
  }, []);
  const cloudReplica = deployment?.mode === "cloud-replica" && deployment.read_only;
  const navigation = cloudReplica
    ? NAVIGATION.filter((item) => item.section !== "review")
    : NAVIGATION;
  const freshnessLabel = deployment?.freshness === "current"
    ? "数据已同步"
    : deployment?.freshness === "stale"
      ? "数据已过期"
      : "等待首次同步";
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="/" onClick={(event) => follow(event, "/")}>
            <img className="brand-mark" src="/platform-logo.svg" alt="" aria-hidden="true" />
            <span className="brand-name"><strong>Orbbec</strong> Agent Platform</span>
          </a>
          <nav className="product-nav" aria-label={UI_COPY.navigationLabel}>
            {navigation.map((item) => (
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
      {cloudReplica && <aside
        className={`cloud-replica-banner is-${deployment.freshness}`}
        aria-label="云端副本状态"
      >
        <strong>云端脱敏只读副本</strong>
        <span>{freshnessLabel}</span>
        {deployment.last_success_at && <time dateTime={deployment.last_success_at}>
          最近同步 {new Intl.DateTimeFormat("zh-CN", {
            month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
          }).format(new Date(deployment.last_success_at))}
        </time>}
      </aside>}
      <main className="page">{children}</main>
      <footer className="site-foot"><span>Orbbec Agent Platform</span></footer>
    </div>
  );
}
