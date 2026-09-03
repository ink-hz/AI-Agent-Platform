import { useMemo } from "react";

import {
  addFixEvidence,
  createReviewIssue,
  fetchReviewInbox,
  fetchReviewIssue,
  fetchReviewIssues,
  fetchReviewOverview,
  fetchConversationReviewFeedback,
  fetchReviewConversationAttachments,
  createReviewConversationAttachmentTicket,
  fetchReviewTurnSummaries,
  linkReviewTurn,
  markFixReady,
  mergeReviewIssue,
  moveReviewLink,
  reviewReplay,
  setIssueDisposition,
  startReplay,
  updateReviewIssue,
  updateConversationReviewFeedback,
  verifyFixEvidence,
} from "../api";
import { ReviewWorkspace, type ReviewApi } from "../components/review/ReviewWorkspace";


function initialActor(): string {
  try { return sessionStorage.getItem("reviewActor") || ""; } catch { return ""; }
}

const genericReviewApi = (agentId: string): ReviewApi => ({
  overview: (signal) => fetchReviewOverview(agentId, signal),
  inbox: (signal) => fetchReviewInbox(agentId, signal),
  issues: (signal) => fetchReviewIssues(agentId, signal),
  turnSummaries: fetchReviewTurnSummaries,
  issue: fetchReviewIssue,
  create: createReviewIssue,
  link: linkReviewTurn,
  update: updateReviewIssue,
  move: moveReviewLink,
  fixReady: markFixReady,
  merge: mergeReviewIssue,
  addEvidence: addFixEvidence,
  verifyEvidence: verifyFixEvidence,
  replay: startReplay,
  semanticReview: reviewReplay,
  disposition: setIssueDisposition,
  conversationFeedback: {
    feedback: (signal) => fetchConversationReviewFeedback("pending_triage", signal),
    attachments: fetchReviewConversationAttachments,
    triage: updateConversationReviewFeedback,
    ticket: createReviewConversationAttachmentTicket,
  },
});

export function ReviewPage() {
  const query = useMemo(() => new URLSearchParams(window.location.search), []);
  const agentId = query.get("agent_id") || "ai-fae-agent";
  const api = useMemo(() => genericReviewApi(agentId), [agentId]);
  return <ReviewWorkspace
    api={api}
    agentId={agentId}
    basePath="/admin/review"
    initialIssueId={query.get("issue")}
    initialTurn={null}
    actor={initialActor()}
    showActorField
    showAgentFilter
  />;
}
