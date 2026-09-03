import type { Account } from "../../auth";
import { platformPath } from "../../auth";
import { FaeIssuesPage } from "../../pages/FaeIssuesPage";
import { FaeOverviewPage } from "../../pages/FaeOverviewPage";
import { FaeReportsPage } from "../../pages/FaeReportsPage";
import { FaeSessionDetailPage } from "../../pages/FaeSessionDetailPage";
import { FaeSessionsPage } from "../../pages/FaeSessionsPage";
import { FAE_DIRECT_PATH } from "../../platform/workspaces";
import type { Route } from "../../router";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";


export type FaeManagementRoute = Extract<Route, {
  name:
    | "fae-manage-overview"
    | "fae-manage-sessions"
    | "fae-manage-session"
    | "fae-manage-issues"
    | "fae-manage-issue"
    | "fae-manage-reports"
    | "fae-manage-report";
}>;


function hasManagementAccess(account: Account): boolean {
  return account.role === "platform_owner"
    || account.workspace_scopes.includes("fae_workbench");
}


function PermissionPage() {
  return <section className="permission-state fae-management-permission" data-status-code="403" role="alert">
    <h1>无权访问 FAE 管理</h1>
    <p>当前账号没有 FAE 工作台权限，请联系 Platform Owner 授权。</p>
    <a href={platformPath(FAE_DIRECT_PATH)}>返回 FAE Agent</a>
  </section>;
}


export function FaeManagementWorkspace({
  account,
  route,
}: {
  account: Account;
  route: FaeManagementRoute;
}) {
  if (!hasManagementAccess(account)) return <PermissionPage />;

  let page;
  switch (route.name) {
    case "fae-manage-overview": page = <FaeOverviewPage />; break;
    case "fae-manage-sessions": page = <FaeSessionsPage />; break;
    case "fae-manage-session": page = <FaeSessionDetailPage sessionKey={route.sessionKey} />; break;
    case "fae-manage-issues": page = <FaeIssuesPage account={account} />; break;
    case "fae-manage-issue": page = <FaeIssuesPage account={account} issueId={route.issueId} />; break;
    case "fae-manage-reports": page = <FaeReportsPage />; break;
    case "fae-manage-report": page = <FaeReportsPage reportId={route.reportId} />; break;
  }

  return <WorkspaceErrorBoundary title="FAE 工作台">{page}</WorkspaceErrorBoundary>;
}
