import type { Account } from "../../auth";
import { directConversationPath } from "../../platform/workspaces";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";
import { DirectAgentWorkspace } from "../direct/DirectAgentWorkspace";


function hrConversationPath(conversationId: string): string {
  return directConversationPath("hr-bot", conversationId)
    ?? `/hr/conversations/${encodeURIComponent(conversationId)}`;
}


export function HrWorkspacePage(props: { account: Account; conversationId?: string }) {
  return <WorkspaceErrorBoundary title="HR Agent">
    <DirectAgentWorkspace
      account={props.account}
      agentId="hr-bot"
      conversationId={props.conversationId}
      conversationPath={hrConversationPath}
      workspaceLabel="人才智能工作台"
      workspaceMark="HR"
    />
  </WorkspaceErrorBoundary>;
}
