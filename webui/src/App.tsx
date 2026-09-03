import { useEffect, useState } from "react";

import { AppShell } from "./AppShell";
import { routeDocumentTitle, useDocumentTitle } from "./documentTitle";
import { OverviewPage } from "./pages/OverviewPage";
import { AgentsPage } from "./pages/AgentsPage";
import { AgentDetailPage } from "./pages/AgentDetailPage";
import { AgentRuntimePage } from "./pages/AgentRuntimePage";
import { SessionsPage } from "./pages/SessionsPage";
import { SessionDetailPage } from "./pages/SessionDetailPage";
import { ActivityPage } from "./pages/ActivityPage";
import { ReviewPage } from "./pages/ReviewPage";
import { navigate, safeLegacyWorkspaceSearch, useRoute } from "./router";
import {
  AuthenticationRequired,
  DirectoryUnavailable,
  IdentityDisabled,
  PermissionDenied,
  loadAccount,
  logoutAccount,
  localPathname,
  platformPath,
  identityShellEnabled,
  type Account,
} from "./auth";
import { LoginPage } from "./pages/LoginPage";
import { AccountPage } from "./pages/AccountPage";
import { IdentityManagementPage } from "./pages/IdentityManagementPage";
import { GovernancePage } from "./pages/GovernancePage";
import { BrainWorkspacePage } from "./pages/BrainWorkspacePage";
import { MissionPage } from "./pages/MissionPage";
import { MissionsPage } from "./pages/MissionsPage";
import { AgentUseDirectoryPage } from "./pages/AgentUseDirectoryPage";
import { AgentUsePage } from "./pages/AgentUsePage";
import { AiNotesPage } from "./pages/AiNotesPage";
import { HrWorkspacePage } from "./workspaces/hr/HrWorkspacePage";
import { MarketingWorkspacePage } from "./workspaces/marketing/MarketingWorkspacePage";
import { FaeManagementWorkspace } from "./workspaces/fae/FaeManagementWorkspace";


function PendingPage({ title, description }: { title: string; description: string }) {
  return (
    <section className="empty-state">
      <span className="empty-pulse" aria-hidden="true" />
      <h2>{title}</h2>
      <p>{description}</p>
    </section>
  );
}


export function LegacyRedirect({
  to,
  navigation,
  location = window.location,
  navigateSpa = navigate,
}: {
  to: string;
  navigation: "spa" | "document";
  location?: Pick<Location, "replace">;
  navigateSpa?: typeof navigate;
}) {
  const target = `${to}${safeLegacyWorkspaceSearch(to, window.location.search)}`;
  useEffect(() => {
    if (navigation === "document") {
      location.replace(platformPath(target));
      return;
    }
    navigateSpa(target, { replace: true });
  }, [location, navigateSpa, navigation, target]);
  return <PendingPage title="正在打开工作区" description="正在进入对应的专业 Agent。" />;
}


function AccessState({
  title,
  description,
  onRetry,
}: {
  title: string;
  description: string;
  onRetry?: () => void;
}) {
  return <main className="access-shell"><section className="permission-state" role="alert"><h1>{title}</h1><p>{description}</p>{onRetry && <div className="access-actions"><button type="button" onClick={onRetry}>重新尝试</button><a href={platformPath("/login")}>重新登录</a></div>}</section></main>;
}


function viewerRouteAllowed(account: Account, route: ReturnType<typeof useRoute>): boolean {
  if (["brain", "conversations", "conversation", "missions", "mission", "agents", "agent", "agent-conversation", "voc-workspace", "hr", "hr-conversation", "marketing", "marketing-conversation", "ai-notes", "ai-note", "account"].includes(route.name)) return true;
  if (route.name === "admin-governance") return true;
  if (route.name === "admin-voc") return true;
  if (route.name === "admin-agent-runtime") return account.observation_agent_ids.includes(route.agentId);
  if (route.name === "admin-review" || route.name === "admin-activity") {
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
    case "brain": return account
      ? <BrainWorkspacePage account={account} />
      : <PendingPage title="Agent 大脑" description="请启用企业身份后使用。" />;
    case "conversations": return <LegacyRedirect to="/" navigation="spa" />;
    case "conversation": return account ? <BrainWorkspacePage account={account} conversationId={route.conversationId} /> : <PendingPage title="Agent 大脑" description="请启用企业身份后使用。" />;
    case "missions": return <MissionsPage />;
    case "mission": return account ? <MissionPage account={account} key={route.missionId} missionId={route.missionId} /> : <PendingPage title="历史任务" description="请启用企业身份后查看。" />;
    case "agents": return <AgentUseDirectoryPage />;
    case "agent": return account ? <AgentUsePage account={account} agentId={route.agentId} key={route.agentId} /> : <PendingPage title="专业 Agent" description="请启用企业身份后使用。" />;
    case "agent-conversation": return account ? <AgentUsePage account={account} agentId={route.agentId} conversationId={route.conversationId} key={route.agentId} /> : <PendingPage title="专业 Agent" description="请启用企业身份后使用。" />;
    case "voc-workspace": return <LegacyRedirect to="/voc/" navigation="document" />;
    case "hr": return account ? <HrWorkspacePage account={account} /> : <PendingPage title="HR Agent" description="请启用企业身份后使用。" />;
    case "hr-conversation": return account ? <HrWorkspacePage account={account} conversationId={route.conversationId} /> : <PendingPage title="HR Agent" description="请启用企业身份后使用。" />;
    case "marketing": return account ? <MarketingWorkspacePage account={account} agentSlug={route.agentSlug} /> : <PendingPage title="Marketing Agent" description="请启用企业身份后使用。" />;
    case "marketing-conversation": return account ? <MarketingWorkspacePage account={account} agentSlug={route.agentSlug} conversationId={route.conversationId} /> : <PendingPage title="Marketing Agent" description="请启用企业身份后使用。" />;
    case "ai-notes": return account ? <AiNotesPage /> : <PendingPage title="AI 工程笔记" description="请启用企业身份后阅读。" />;
    case "ai-note": return account
      ? <AiNotesPage categorySlug={route.categorySlug} articleSlug={route.articleSlug} />
      : <PendingPage title="AI 工程笔记" description="请启用企业身份后阅读。" />;
    case "admin-overview": return <OverviewPage />;
    case "admin-agents": return <AgentsPage />;
    case "admin-agent": return <AgentDetailPage agentId={route.agentId} />;
    case "admin-agent-runtime": return <AgentRuntimePage agentId={route.agentId} />;
    case "admin-sessions": return <SessionsPage />;
    case "admin-session": return <SessionDetailPage sessionKey={route.sessionKey} />;
    case "admin-review": return <ReviewPage />;
    case "admin-activity": return <ActivityPage />;
    case "admin-identity": return account ? <IdentityManagementPage account={account} /> : <PendingPage title="身份管理" description="身份模式未启用。" />;
    case "admin-governance": return <GovernancePage />;
    case "admin-voc": return <LegacyRedirect to="/voc/manage/" navigation="document" />;
    case "fae-manage-overview":
    case "fae-manage-sessions":
    case "fae-manage-session":
    case "fae-manage-issues":
    case "fae-manage-issue":
    case "fae-manage-reports":
    case "fae-manage-report": return account
      ? <FaeManagementWorkspace account={account} route={route} />
      : <PendingPage title="FAE 工作台" description="请启用企业身份后使用。" />;
    case "legacy-redirect": return <LegacyRedirect to={route.to} navigation={route.navigation} />;
    default: return <PendingPage title="页面不存在" description="请返回 Agent 大脑。" />;
  }
}


export default function App() {
  const route = useRoute();
  const identityMode = identityShellEnabled();
  const [account, setAccount] = useState<Account | null>(null);
  const [legacyMode, setLegacyMode] = useState(!identityMode);
  const [failure, setFailure] = useState<"permission" | "directory" | "unavailable" | null>(null);
  const [loading, setLoading] = useState(identityMode && route.name !== "login");
  const [accountAttempt, setAccountAttempt] = useState(0);
  const loginRoute = route.name === "login";
  useDocumentTitle(routeDocumentTitle(route));
  useEffect(() => {
    if (loginRoute) { setLoading(false); return; }
    if (!identityMode) { setLegacyMode(true); setLoading(false); return; }
    let current = true;
    setFailure(null); setLoading(true);
    void loadAccount().then((value) => {
      if (!current) return;
      setAccount(value); setLegacyMode(false); setFailure(null); setLoading(false);
    }).catch((error: unknown) => {
      if (!current) return;
      if (error instanceof AuthenticationRequired) {
        const returnPath = `${localPathname()}${window.location.search}`;
        navigate(`/login?return_path=${encodeURIComponent(returnPath)}`, { replace: true });
      } else if (error instanceof IdentityDisabled) {
        setLegacyMode(true); setFailure(null); setLoading(false);
      } else {
        setFailure(error instanceof PermissionDenied ? "permission" : error instanceof DirectoryUnavailable ? "directory" : "unavailable");
        setLoading(false);
      }
    });
    return () => { current = false; };
  }, [identityMode, loginRoute, accountAttempt]);

  if (loginRoute) return <LoginPage />;
  if (loading) return <AccessState title="正在进入 Agent Platform" description="正在确认您的企业账号，通常只需片刻。" />;
  if (failure === "permission") return <AccessState title="无权访问" description="当前账号没有该入口的访问权限。" />;
  if (failure === "directory") return <AccessState title="暂时无法确认企业账号" description="企业通讯录同步可能延迟，请稍后重试。" onRetry={() => setAccountAttempt((value) => value + 1)} />;
  if (failure) return <AccessState title="暂时无法进入平台" description="连接服务时遇到短暂问题，请重新尝试。" onRetry={() => setAccountAttempt((value) => value + 1)} />;
  if (route.name === "legacy-redirect") return productPage(route, account ?? undefined);
  if (!legacyMode && account) {
    const usageRoute = ["brain", "conversations", "conversation", "missions", "mission", "agents", "agent", "agent-conversation", "voc-workspace", "hr", "hr-conversation", "marketing", "marketing-conversation", "ai-notes", "ai-note", "account", "legacy-redirect"].includes(route.name);
    const faeManagementRoute = route.name.startsWith("fae-manage-");
    const allowed = usageRoute || faeManagementRoute || account.role === "platform_owner" || account.role === "platform_admin"
      || (account.role === "management_viewer" && viewerRouteAllowed(account, route));
    if (!allowed) {
      return <AppShell route={route} account={account}><section className="permission-state" role="alert"><h1>无权访问</h1><p>该页面不在你的后端授权范围内。</p></section></AppShell>;
    }
  }
  return <AppShell route={route} account={account}>{productPage(route, account ?? undefined)}</AppShell>;
}
