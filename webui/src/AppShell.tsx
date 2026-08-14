import { useEffect, useState, type MouseEvent, type ReactNode } from "react";

import { fetchDeployment } from "./api";
import { UI_COPY } from "./copy";
import { navigate, routeSection, type Route } from "./router";
import type { DeploymentInfo } from "./types";
import { platformPath, type Account } from "./auth";


const NAVIGATION = [
  { label: "总览", path: "/", section: "overview" },
  { label: "Agent", path: "/agents", section: "agents" },
  { label: "Session", path: "/sessions", section: "sessions" },
  { label: "复审闭环", path: "/review", section: "review" },
  { label: "运行记录", path: "/activity", section: "activity" },
] as const;


interface NavigationItem {
  label: string;
  path: string;
  section: "overview" | "agents" | "sessions" | "review" | "activity" | "account" | "identity" | "governance";
}


function navigationFor(account?: Account | null): NavigationItem[] {
  if (!account) return [...NAVIGATION];
  if (account.role === "member") {
    return [{ label: "企业账号", path: "/account", section: "account" }];
  }
  if (account.role === "management_viewer") {
    return [
      { label: "企业账号", path: "/account", section: "account" },
      ...account.observation_agent_ids.flatMap((agentId): NavigationItem[] => [
        { label: `${agentId} 运行`, path: `/agents/${encodeURIComponent(agentId)}/runtime`, section: "agents" },
        { label: `${agentId} 复审`, path: `/review?agent_id=${encodeURIComponent(agentId)}`, section: "review" },
        { label: `${agentId} 记录`, path: `/activity?agent_id=${encodeURIComponent(agentId)}`, section: "activity" },
      ]),
      { label: "治理审计", path: "/governance", section: "governance" },
    ];
  }
  return [
    ...NAVIGATION,
    { label: "身份管理", path: "/identity", section: "identity" },
    { label: "企业账号", path: "/account", section: "account" },
  ];
}


function follow(event: MouseEvent<HTMLAnchorElement>, path: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(path);
}


export function AppShell({ route, children, account }: { route: Route; children: ReactNode; account?: Account | null }) {
  const current = routeSection(route);
  const [deployment, setDeployment] = useState<DeploymentInfo | null>(null);
  useEffect(() => {
    if (account && account.role !== "platform_owner") return;
    const controller = new AbortController();
    void fetchDeployment(controller.signal).then(setDeployment).catch(() => undefined);
    return () => controller.abort();
  }, [account]);
  const cloudReplica = deployment?.mode === "cloud-replica" && deployment.read_only;
  const roleNavigation = navigationFor(account);
  const navigation = cloudReplica
    ? roleNavigation.filter((item) => item.section !== "review")
    : roleNavigation;
  const freshnessLabel = deployment?.freshness === "current"
    ? "数据已同步"
    : deployment?.freshness === "stale"
      ? "数据已过期"
      : "等待首次同步";
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href={platformPath(account?.role === "member" ? "/account" : "/")} onClick={(event) => follow(event, account?.role === "member" ? "/account" : "/")}>
            <img className="brand-mark" src={platformPath("/favicon.ico")} alt="" aria-hidden="true" />
            <span className="brand-name"><strong>Orbbec</strong> Agent Platform</span>
          </a>
          <nav className="product-nav" aria-label={UI_COPY.navigationLabel}>
            {navigation.map((item) => (
              <a
                aria-current={current === item.section ? "page" : undefined}
                className={current === item.section ? "is-current" : undefined}
                href={platformPath(item.path)}
                key={item.path}
                onClick={(event) => follow(event, item.path)}
              >{item.label}</a>
            ))}
          </nav>
          {account && <span className="account-chip">{account.display_name}</span>}
        </div>
      </header>
      {account?.hard_stale_read_only && <aside className="hard-stale-banner" role="status">
        <strong>通讯录已超过安全时限</strong><span>当前仅保留已授权管理账号的只读访问，变更功能已暂停。</span>
      </aside>}
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
