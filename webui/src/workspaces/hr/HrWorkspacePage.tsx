import type { Account } from "../../auth";
import { directConversationPath } from "../../platform/workspaces";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";
import { DirectAgentWorkspace } from "../direct/DirectAgentWorkspace";
import { HrPositionIndex } from "./HrPositionIndex";
import { HrPositionWorkspace } from "./HrPositionWorkspace";
import type { HrPositionSection } from "../../hrR12Types";


function hrConversationPath(conversationId: string): string {
  return directConversationPath("hr-bot", conversationId)
    ?? `/hr/conversations/${encodeURIComponent(conversationId)}`;
}


export function HrWorkspacePage(props: { account: Account; conversationId?: string; positionId?: string; section?: HrPositionSection; freeChat?: boolean }) {
  if (!props.conversationId && !props.positionId && !props.freeChat) {
    return <WorkspaceErrorBoundary title="HR Agent">
      <HrPositionIndex account={props.account} />
    </WorkspaceErrorBoundary>;
  }
  if (props.positionId) {
    return <WorkspaceErrorBoundary title="HR Agent">
      <HrPositionWorkspace account={props.account} conversationId={props.conversationId} positionId={props.positionId} section={props.section} />
    </WorkspaceErrorBoundary>;
  }
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
