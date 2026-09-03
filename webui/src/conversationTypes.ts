export type ConversationMode = "brain" | "direct_agent";
export type ConversationStatus = "active" | "archived";
export type ConversationMessageRole = "user" | "assistant" | "system";
export type ConversationDeliveryStatus = "accepted" | "streaming" | "completed" | "failed";
export type AttachmentState = "uploading" | "validating" | "scanning" |
  "ready" | "quarantined" | "rejected" | "deleted";

export interface ConversationAttachment {
  attachmentId: string;
  conversationId: string | null;
  source: "user" | "agent";
  displayName: string;
  detectedMime: string | null;
  sizeBytes: number;
  sha256: string | null;
  state: AttachmentState;
  stateReason: string | null;
  createdAt: string;
  retainedUntil: string;
  preview: { attachmentId: string; detectedMime: string } | null;
  coverage: {
    pages: number | null;
    sheets: number | null;
    slides: number | null;
    ocrComplete: boolean | null;
  } | null;
}

export interface ConversationCitation {
  citationKey: string;
  title: string;
  url: string;
  site: string;
  retrievedAt: string;
  supports: string[];
}

export interface ArtifactVersion {
  artifactKey: string;
  versionNo: number;
  producerVersionId: string;
  current: boolean;
  status: "processing" | "ready" | "failed";
  attachment: ConversationAttachment | null;
}

export interface ConversationReadState {
  conversationId: string;
  lastReadMessageSeq: number;
  lastReadAt: string;
}

export interface SearchRecovery {
  status: "unavailable" | "no_results" | "partial";
  attemptCount: number;
  lastAttemptAt: string;
  resumable: boolean;
  coverageNote: string | null;
}

export interface TurnSubmission {
  text: string;
  attachmentIds: string[];
  activeAttachmentIds: string[];
}
export type ConversationTurnStatus =
  | "accepted"
  | "running"
  | "waiting_agents"
  | "waiting_user"
  | "waiting_confirmation"
  | "completing"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface Conversation {
  conversation_id: string;
  mode: ConversationMode;
  direct_agent_id: string | null;
  title: string;
  status: ConversationStatus;
  summary_through_seq: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface ConversationMessage {
  message_id: string;
  conversation_id: string;
  seq: number;
  role: ConversationMessageRole;
  content: string;
  turn_id: string | null;
  delivery_status: ConversationDeliveryStatus;
  created_at: string;
  completed_at: string | null;
  input_attachments: ConversationAttachment[];
  output_attachments: ConversationAttachment[];
  active_attachment_ids: string[];
  search_recovery?: SearchRecovery;
}

export interface ConversationTurn {
  turn_id: string;
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string | null;
  retry_of_turn_id: string | null;
  status: ConversationTurnStatus;
  created_at: string;
  updated_at: string;
}

export interface ConversationEvent {
  event_id: string;
  conversation_id: string;
  seq: number;
  turn_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export type { WorkroomTurn } from "./workroomTypes";

export interface ConversationSubmissionResult {
  conversation: Conversation;
  message: ConversationMessage;
  turn: ConversationTurn;
}

export interface ConversationInterventionResult {
  intervention: {
    status: "pending" | "delivered";
    message_id: string;
  };
  message: ConversationMessage;
  turn: ConversationTurn;
}

export interface ConversationTaskDetail {
  task_id: string;
  child_session_id: string;
  agent_id: string;
  status: string;
  session_status: string;
  messages: Array<{
    seq: number;
    sender: "brain" | "agent" | "user" | "platform";
    kind: string;
    text: string;
    created_at: string;
  }>;
  events: Array<{
    seq: number;
    kind: string;
    source: string;
    source_ref: string;
    summary: string;
    status: string | null;
    evidence_refs: string[];
    artifact_refs: string[];
    created_at: string;
  }>;
}

export interface ConversationDetail {
  conversation: Conversation;
  current_turn: ConversationTurn | null;
}

export interface ConversationPage {
  items: Conversation[];
  next_cursor: string | null;
}

export interface ConversationCancelResult {
  conversation_id: string;
  turn_id: string;
  cancel_requested: true;
}

export type ConversationFeedbackRating = "helpful" | "unhelpful";
export type ConversationFeedbackReason = "inaccurate" | "incomplete" | "unclear" | "unresolved" | "other";

export interface ConversationFeedback {
  feedback_id: string;
  conversation_id: string;
  message_id: string;
  turn_id: string;
  rating: ConversationFeedbackRating;
  reason: ConversationFeedbackReason | null;
  created_at: string;
}

export const TERMINAL_CONVERSATION_TURN_STATUSES = new Set<ConversationTurnStatus>([
  "completed", "failed", "cancelled", "interrupted",
]);
