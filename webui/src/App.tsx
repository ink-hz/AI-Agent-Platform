import { AppShell } from "./AppShell";
import { OverviewPage } from "./pages/OverviewPage";
import { AgentsPage } from "./pages/AgentsPage";
import { AgentDetailPage } from "./pages/AgentDetailPage";
import { SessionsPage } from "./pages/SessionsPage";
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
    case "session": page = <PendingPage title="Sessions" description="Conversation, answer, Evidence, and Trace inspection." />; break;
    case "flywheel": page = <PendingPage title="Flywheel" description="Feedback, Review, and improvement data." />; break;
    default: page = <PendingPage title="Page not found" description="Return to Agent Overview." />;
  }
  return <AppShell route={route}>{page}</AppShell>;
}
