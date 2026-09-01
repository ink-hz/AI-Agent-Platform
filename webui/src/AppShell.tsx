import { useEffect, useState, type MouseEvent, type ReactNode } from "react";

import { fetchDeployment } from "./api";
import { UI_COPY } from "./copy";
import { navigate, routeSection, type Route } from "./router";
import type { DeploymentInfo } from "./types";
import { platformPath, type Account } from "./auth";
import { DeploymentProvider } from "./deploymentContext";


const USE_NAVIGATION = [
  { label: "Agent 大脑", path: "/", section: "brain" },
  { label: "专业 Agent", path: "/agents", section: "agents" },
] as const;

const ADMIN_NAVIGATION = [
  { label: "总览", path: "/admin", section: "admin" },
  { label: "Agent", path: "/admin/agents", section: "admin" },
  { label: "Session", path: "/admin/sessions", section: "admin" },
  { label: "FAE 工作台", path: "/admin/fae", section: "admin" },
  { label: "复审闭环", path: "/admin/review", section: "admin" },
  { label: "运行记录", path: "/admin/activity", section: "admin" },
  { label: "身份管理", path: "/admin/identity", section: "admin" },
  { label: "治理审计", path: "/admin/governance", section: "admin" },
  { label: "VOC 管理", path: "/admin/voc", section: "admin" },
] as const;


interface NavigationItem {
  label: string;
  path: string;
  section: "brain" | "conversations" | "agents" | "missions" | "ai-notes" | "account" | "admin";
}


function navigationFor(account?: Account | null): NavigationItem[] {
  const base: NavigationItem[] = [...USE_NAVIGATION];
  if (!account || account.role === "platform_owner" || account.role === "platform_admin") {
    base.push({ label: "管理中心", path: "/admin", section: "admin" });
  } else if (account.role === "management_viewer") {
    base.push({ label: "管理中心", path: "/admin/voc", section: "admin" });
  }
  return base;
}


function follow(event: MouseEvent<HTMLAnchorElement>, path: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(path);
}


export function AppShell({ route, children, account }: { route: Route; children: ReactNode; account?: Account | null }) {
  const current = routeSection(route);
  const brainWorkspace = route.name === "brain" || route.name === "conversation"
    || route.name === "agent" || route.name === "agent-conversation";
  const aiNotesWorkspace = route.name === "ai-notes" || route.name === "ai-note";
  const faeWorkspace = route.name.startsWith("admin-fae-");
  const faeGovernanceWorkspace = route.name === "admin-fae-issues" || route.name === "admin-fae-issue";
  const [deployment, setDeployment] = useState<DeploymentInfo | null>(null);
  const [deploymentResolved, setDeploymentResolved] = useState(current !== "admin");
  useEffect(() => {
    if (current !== "admin" || (account && account.role !== "platform_owner" && account.role !== "platform_admin")) {
      setDeployment(null);
      setDeploymentResolved(true);
      return;
    }
    const controller = new AbortController();
    setDeploymentResolved(false);
    void fetchDeployment(controller.signal).then((value) => {
      if (!controller.signal.aborted) setDeployment(value);
    }).catch(() => {
      if (!controller.signal.aborted) setDeployment(null);
    }).finally(() => {
      if (!controller.signal.aborted) setDeploymentResolved(true);
    });
    return () => controller.abort();
  }, [account, current]);
  const cloudReplica = deployment?.mode === "cloud-replica" && deployment.read_only;
  const roleNavigation = navigationFor(account);
  const navigation = roleNavigation;
  const managementNavigation = (!account || account.role === "platform_owner" || account.role === "platform_admin")
    ? (cloudReplica ? ADMIN_NAVIGATION.filter((item) => item.path !== "/admin/review") : ADMIN_NAVIGATION)
    : account?.role === "management_viewer"
      ? ADMIN_NAVIGATION.filter((item) => item.path === "/admin/voc")
      : [];
  const freshnessLabel = deployment?.freshness === "current"
    ? "数据已同步"
    : deployment?.freshness === "stale"
      ? "数据已过期"
      : "等待首次同步";
  return <DeploymentProvider deployment={deployment} resolved={deploymentResolved}>
    <div className={`app${brainWorkspace ? " is-brain-workspace-shell" : ""}${aiNotesWorkspace ? " is-ai-notes-workspace-shell" : ""}`}>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href={platformPath("/")} onClick={(event) => follow(event, "/")}>
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
          {account && <a
            aria-current={current === "account" ? "page" : undefined}
            aria-label={`查看 ${account.display_name} 的企业账号`}
            className={`account-chip${current === "account" ? " is-current" : ""}`}
            href={platformPath("/account")}
            onClick={(event) => follow(event, "/account")}
          >{account.display_name}</a>}
        </div>
      </header>
      {account?.hard_stale_read_only && <aside className="hard-stale-banner" role="status">
        <strong>通讯录已超过安全时限</strong><span>当前仅保留已授权管理账号的只读访问，变更功能已暂停。</span>
      </aside>}
      {current === "admin" && cloudReplica && !faeGovernanceWorkspace && <aside
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
      {current === "admin" && managementNavigation.length > 0 && <nav className="admin-nav" aria-label="管理中心">
        <div>{managementNavigation.map((item) => <a
          className={current === item.section && (window.location.pathname === platformPath(item.path)
            || (item.path !== "/admin" && window.location.pathname.startsWith(`${platformPath(item.path)}/`))) ? "is-current" : undefined}
          href={platformPath(item.path)} key={item.path} onClick={(event) => follow(event, item.path)}
        >{item.label}</a>)}</div>
      </nav>}
      <main className={`page${brainWorkspace ? " is-brain-workspace" : ""}${aiNotesWorkspace ? " is-ai-notes-workspace" : ""}${faeWorkspace ? " is-fae-workbench" : ""}`}>{children}</main>
      {!brainWorkspace && !aiNotesWorkspace && <footer className="site-foot"><span>Orbbec Agent Platform</span></footer>}
    </div>
  </DeploymentProvider>;
}
