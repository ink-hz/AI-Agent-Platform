import { useEffect, useState, type MouseEvent, type ReactNode } from "react";

import { fetchDeployment } from "./api";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { PlatformSidebar } from "./platform/PlatformSidebar";
import "./platform/platformShell.css";
import { navigate, routeSection, type Route } from "./router";
import type { DeploymentInfo } from "./types";
import { platformPath, type Account } from "./auth";
import { DeploymentProvider } from "./deploymentContext";


const NAVIGATION_PREFERENCE = "platform.navigation.collapsed";
function initialNavigationCollapsed(): boolean {
  if (window.matchMedia?.("(max-width: 900px)").matches) return true;
  try { return window.localStorage?.getItem(NAVIGATION_PREFERENCE) === "true"; } catch { return false; }
}

function follow(event: MouseEvent<HTMLAnchorElement>, path: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(path);
}


export function AppShell({ route, children, account, panorama = false }: { route: Route; children: ReactNode; account?: Account | null; panorama?: boolean }) {
  const current = routeSection(route);
  const brainWorkspace = !panorama && (route.name === "home" || route.name === "brain" || route.name === "conversation"
    || route.name === "marketing" || route.name === "marketing-conversation");
  const hrWorkspace = route.name === "hr" || route.name === "hr-chat" || route.name === "hr-agent" || route.name === "hr-positions"
    || route.name === "hr-position" || route.name === "hr-position-section"
    || route.name === "hr-panorama";
  const aiNotesWorkspace = !panorama && (route.name === "ai-notes" || route.name === "ai-note");
  const agentDesignWorkspace = route.name === "admin-agent-designs";
  const faeWorkspace = route.name.startsWith("fae-manage-");
  const accountCanReadDeployment = account?.role === "platform_owner" || account?.role === "platform_admin";
  const shouldLoadDeployment = accountCanReadDeployment || (current === "admin" && !account);
  const [deployment, setDeployment] = useState<DeploymentInfo | null>(null);
  const [deploymentResolved, setDeploymentResolved] = useState(!shouldLoadDeployment);
  useEffect(() => {
    if (!shouldLoadDeployment) {
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
  }, [shouldLoadDeployment]);
  const cloudReplica = deployment?.mode === "cloud-replica" && deployment.read_only;
  const [navigationCollapsed, setNavigationCollapsed] = useState(initialNavigationCollapsed);
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 900px)");
    const update = () => setNavigationCollapsed(initialNavigationCollapsed());
    media?.addEventListener("change", update);
    return () => media?.removeEventListener("change", update);
  }, []);
  const toggleNavigation = () => {
    setNavigationCollapsed(value => {
      const next = !value;
      if (!window.matchMedia?.("(max-width: 900px)").matches) {
        try { window.localStorage?.setItem(NAVIGATION_PREFERENCE, String(next)); } catch { /* Optional preference. */ }
      }
      return next;
    });
  };
  return <DeploymentProvider deployment={deployment} resolved={deploymentResolved}>
    <div className={`app platform-shell${panorama ? " is-panorama-shell" : ""}${brainWorkspace ? " is-brain-workspace-shell" : ""}${hrWorkspace ? " is-hr-workspace-shell" : ""}${aiNotesWorkspace ? " is-ai-notes-workspace-shell" : ""}`}>
      <header className="topbar">
        <div className="topbar-inner">
          {!hrWorkspace && <button type="button" className="platform-nav-toggle" aria-controls="platform-navigation"
            aria-expanded={!navigationCollapsed} aria-label={navigationCollapsed ? "展开导航" : "收起导航"} onClick={toggleNavigation}>
            {navigationCollapsed ? <PanelLeftOpen size={20} aria-hidden="true"/> : <PanelLeftClose size={20} aria-hidden="true"/>}
          </button>}
          <a className="brand" href={platformPath("/")} onClick={(event) => follow(event, "/")}>
            <img className="brand-mark" src={platformPath("/favicon.ico")} alt="" aria-hidden="true" />
            <span className="brand-name"><strong>Orbbec</strong> Agent Platform</span>
          </a>
          {account && <a
            aria-current={current === "account" ? "page" : undefined}
            aria-label={`查看 ${account.display_name} 的企业账号`}
            className={`account-chip${current === "account" ? " is-current" : ""}`}
            href={platformPath("/account")}
            onClick={(event) => follow(event, "/account")}
          >{account.display_name}</a>}
        </div>
      </header>
      <div className="platform-body">
        {!hrWorkspace && <PlatformSidebar route={route} account={account} readOnly={!deploymentResolved || !deployment || Boolean(cloudReplica)} collapsed={navigationCollapsed}/>}
        <div className="platform-content">
      <main className={`page${panorama ? " is-panorama-page" : ""}${brainWorkspace ? " is-brain-workspace" : ""}${hrWorkspace ? " is-hr-workspace" : ""}${aiNotesWorkspace ? " is-ai-notes-workspace" : ""}${faeWorkspace ? " is-fae-workbench" : ""}${agentDesignWorkspace ? " is-agent-design-workspace" : ""}`}>
      {account?.hard_stale_read_only && !faeWorkspace && !hrWorkspace && <aside className="hard-stale-banner" role="status">
        <strong>通讯录已超过安全时限</strong><span>当前仅保留已授权管理账号的只读访问，变更功能已暂停。</span>
      </aside>}
        {children}</main>
      {!panorama && !brainWorkspace && !hrWorkspace && !aiNotesWorkspace && <footer className="site-foot"><span>Orbbec Agent Platform</span></footer>}
        </div>
      </div>
    </div>
  </DeploymentProvider>;
}
