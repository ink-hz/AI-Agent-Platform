import { useEffect, useState } from "react";

import { AppShell } from "./AppShell";
import { LoadingState } from "./components/DataState";
import { routeDocumentTitle, useDocumentTitle } from "./documentTitle";
import { OverviewPage } from "./pages/OverviewPage";
import { AgentsPage } from "./pages/AgentsPage";
import { AgentDetailPage } from "./pages/AgentDetailPage";
import { AgentRuntimePage } from "./pages/AgentRuntimePage";
import { SessionsPage } from "./pages/SessionsPage";
import { SessionDetailPage } from "./pages/SessionDetailPage";
import { ActivityPage } from "./pages/ActivityPage";
import { ReviewPage } from "./pages/ReviewPage";
import { navigate, useRoute } from "./router";
import {
  AuthenticationRequired,
  DirectoryUnavailable,
  IdentityDisabled,
  PermissionDenied,
  loadAccount,
  logoutAccount,
  platformPath,
  identityShellEnabled,
  type Account,
} from "./auth";
import { LoginPage } from "./pages/LoginPage";
import { AccountPage } from "./pages/AccountPage";
import { IdentityManagementPage } from "./pages/IdentityManagementPage";
import { GovernancePage } from "./pages/GovernancePage";


function PendingPage({ title, description }: { title: string; description: string }) {
  return (
    <section className="empty-state">
      <span className="empty-pulse" aria-hidden="true" />
      <h2>{title}</h2>
      <p>{description}</p>
    </section>
  );
}


function LegacyFlywheelRedirect() {
  useEffect(() => navigate("/sessions", { replace: true }), []);
  return <LoadingState label="正在打开 Session" />;
}


function AccessState({ title, description }: { title: string; description: string }) {
  return <main className="access-shell"><section className="permission-state" role="alert"><h1>{title}</h1><p>{description}</p></section></main>;
}


function viewerRouteAllowed(account: Account, route: ReturnType<typeof useRoute>): boolean {
  if (route.name === "account" || route.name === "governance") return true;
  if (route.name === "agent-runtime") return account.observation_agent_ids.includes(route.agentId);
  if (route.name === "review" || route.name === "activity") {
    const selected = new URLSearchParams(window.location.search).getAll("agent_id");
    return selected.length === 1 && account.observation_agent_ids.includes(selected[0]);
  }
  return false;
}


function productPage(route: ReturnType<typeof useRoute>, account?: Account) {
  switch (route.name) {
    case "account": return account ? <AccountPage account={account} onLogout={async (csrf) => {
      await logoutAccount(csrf);
      window.location.replace(platformPath("/login"));
    }} /> : <PendingPage title="企业账号" description="身份模式未启用。" />;
    case "identity": return account ? <IdentityManagementPage account={account} /> : <PendingPage title="身份管理" description="身份模式未启用。" />;
    case "governance": return <GovernancePage />;
    case "overview": return <OverviewPage />;
    case "agents": return <AgentsPage />;
    case "agent": return <AgentDetailPage agentId={route.agentId} />;
    case "agent-runtime": return <AgentRuntimePage agentId={route.agentId} />;
    case "sessions": return <SessionsPage />;
    case "session": return <SessionDetailPage sessionKey={route.sessionKey} />;
    case "flywheel": return <LegacyFlywheelRedirect />;
    case "activity": return <ActivityPage />;
    case "review": return <ReviewPage />;
    default: return <PendingPage title="页面不存在" description="请返回 Agent 集群总览。" />;
  }
}


export default function App() {
  const route = useRoute();
  const identityMode = identityShellEnabled();
  const [account, setAccount] = useState<Account | null>(null);
  const [legacyMode, setLegacyMode] = useState(!identityMode);
  const [failure, setFailure] = useState<"permission" | "directory" | "unavailable" | null>(null);
  const [loading, setLoading] = useState(identityMode && route.name !== "login");
  useDocumentTitle(routeDocumentTitle(route));
  useEffect(() => {
    if (route.name === "login") { setLoading(false); return; }
    if (!identityMode) { setLegacyMode(true); setLoading(false); return; }
    let current = true;
    setLoading(true);
    void loadAccount().then((value) => {
      if (!current) return;
      setAccount(value); setLegacyMode(false); setFailure(null); setLoading(false);
    }).catch((error: unknown) => {
      if (!current) return;
      if (error instanceof AuthenticationRequired) {
        navigate("/login", { replace: true });
      } else if (error instanceof IdentityDisabled) {
        setLegacyMode(true); setFailure(null); setLoading(false);
      } else {
        setFailure(error instanceof PermissionDenied ? "permission" : error instanceof DirectoryUnavailable ? "directory" : "unavailable");
        setLoading(false);
      }
    });
    return () => { current = false; };
  }, [identityMode, route.name]);

  if (route.name === "login") return <LoginPage />;
  if (loading) return <AccessState title="正在验证企业身份" description="正在读取账号与授权范围。" />;
  if (failure === "permission") return <AccessState title="无权访问" description="当前账号没有该入口的访问权限。" />;
  if (failure === "directory") return <AccessState title="企业通讯录暂不可用" description="平台无法确认当前成员状态，请稍后重试。" />;
  if (failure) return <AccessState title="平台暂不可用" description="无法读取账号状态，请稍后重试。" />;
  if (!legacyMode && account) {
    if (account.role === "member" && route.name === "overview") {
      navigate("/account", { replace: true });
      return <AccessState title="正在打开企业账号" description="" />;
    }
    const allowed = account.role === "platform_owner"
      || (account.role === "member" ? route.name === "account" : viewerRouteAllowed(account, route));
    if (!allowed) {
      return <AppShell route={route} account={account}><section className="permission-state" role="alert"><h1>无权访问</h1><p>该页面不在你的后端授权范围内。</p></section></AppShell>;
    }
  }
  return <AppShell route={route} account={account}>{productPage(route, account ?? undefined)}</AppShell>;
}
