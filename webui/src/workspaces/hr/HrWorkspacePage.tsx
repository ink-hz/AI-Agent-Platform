import { useRef } from "react";

import type { Account } from "../../auth";
import { directConversationPath } from "../../platform/workspaces";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";
import { DirectAgentWorkspace } from "../direct/DirectAgentWorkspace";
import { HrPositionIndex } from "./HrPositionIndex";
import { HrPositionWorkspace } from "./HrPositionWorkspace";
import { HrWorkspaceShell } from "./HrWorkspaceShell";
import type { HrPositionSection } from "../../hrR12Types";


function hrConversationPath(conversationId: string): string {
  return directConversationPath("hr-bot", conversationId)
    ?? `/hr/conversations/${encodeURIComponent(conversationId)}`;
}


export function HrWorkspacePage(props: { account: Account; conversationId?: string; positionId?: string; section?: HrPositionSection; freeChat?: boolean; positions?: boolean }) {
  const positionsActive = Boolean(props.positions || props.positionId);
  const lastChatConversationId = useRef<string | undefined>(undefined);
  if (!positionsActive) lastChatConversationId.current = props.conversationId;
  const chatConversationId = positionsActive
    ? lastChatConversationId.current
    : props.conversationId;
  const chatHref = chatConversationId
    ? hrConversationPath(chatConversationId)
    : "/hr/";

  return <HrWorkspaceShell
    account={props.account}
    chatHref={chatHref}
    current={positionsActive ? "positions" : "chat"}
  >
    <div className="hr-workspace-chat-panel" hidden={positionsActive}>
      <WorkspaceErrorBoundary title="HR 智能工作台">
        <DirectAgentWorkspace
          account={props.account}
          agentId="hr-bot"
          autoFocusComposer
          conversationId={chatConversationId}
          conversationPath={hrConversationPath}
          newConversationHeader={<section className="hr-conversation-welcome">
            <span>AI 招聘协作</span>
            <h1>今天想推进哪项招聘工作？</h1>
            <p>找岗位、做人才研究、筛简历、准备面试或整理招聘材料，直接告诉我。</p>
          </section>}
          showWorkspaceBackLink={false}
          workspaceLabel="HR 智能工作台"
          workspaceMark="HR"
          workspaceRootPath="/hr/"
        />
      </WorkspaceErrorBoundary>
    </div>
    {positionsActive && <div className="hr-workspace-position-panel">
      <WorkspaceErrorBoundary title="HR 智能工作台">
        {props.positionId
          ? <HrPositionWorkspace account={props.account} conversationId={props.conversationId} positionId={props.positionId} section={props.section} />
          : <HrPositionIndex account={props.account} />}
      </WorkspaceErrorBoundary>
    </div>}
  </HrWorkspaceShell>;
}
