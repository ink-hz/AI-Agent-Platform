import { AppShell } from "./AppShell";
import { OverviewPage } from "./pages/OverviewPage";
import { AgentsPage } from "./pages/AgentsPage";
import { AgentDetailPage } from "./pages/AgentDetailPage";
import { SessionsPage } from "./pages/SessionsPage";
import { SessionDetailPage } from "./pages/SessionDetailPage";
import { FlywheelPage } from "./pages/FlywheelPage";
import { ActivityPage } from "./pages/ActivityPage";
import { useRoute } from "./router";


function PendingPage({ title, description }: { title: string; description: string }) {
  return (
    <section className="empty-state">
      <span className="empty-pulse" aria-hidden="true" />
      <h2>{title}</h2>
      <p>{description}</p>
    </section>
  );
}


export default function App() {
  const route = useRoute();
  let page;
  switch (route.name) {
    case "overview": page = <OverviewPage />; break;
    case "agents": page = <AgentsPage />; break;
    case "agent": page = <AgentDetailPage agentId={route.agentId} />; break;
    case "sessions": page = <SessionsPage />; break;
    case "session": page = <SessionDetailPage sessionKey={route.sessionKey} />; break;
    case "flywheel": page = <FlywheelPage />; break;
    case "activity": page = <ActivityPage />; break;
    default: page = <PendingPage title="Page not found" description="Return to Agent Overview." />;
  }
  return <AppShell route={route}>{page}</AppShell>;
}
