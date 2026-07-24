import { useEffect } from "react";

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
import { navigate, useRoute } from "./router";


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
  return <LoadingState label="Opening Sessions" />;
}


export default function App() {
  const route = useRoute();
  useDocumentTitle(routeDocumentTitle(route));
  let page;
  switch (route.name) {
    case "overview": page = <OverviewPage />; break;
    case "agents": page = <AgentsPage />; break;
    case "agent": page = <AgentDetailPage agentId={route.agentId} />; break;
    case "agent-runtime": page = <AgentRuntimePage agentId={route.agentId} />; break;
    case "sessions": page = <SessionsPage />; break;
    case "session": page = <SessionDetailPage sessionKey={route.sessionKey} />; break;
    case "flywheel": page = <LegacyFlywheelRedirect />; break;
    case "activity": page = <ActivityPage />; break;
    default: page = <PendingPage title="Page not found" description="Return to Agent Overview." />;
  }
  return <AppShell route={route}>{page}</AppShell>;
}
