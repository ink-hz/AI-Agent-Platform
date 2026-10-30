import type { SessionSummary } from "./types";

export function hasFaeSubjectIdentity(item: Pick<SessionSummary, "source_kind" | "agent_id">) {
  return item.source_kind === "fae" && ["ai-fae-agent", "ai-daq-fae-agent"].includes(item.agent_id);
}

export function formatFaeSender(session: Pick<SessionSummary, "verified_subject" | "primary_sender_name">) {
  const ref = session.verified_subject;
  if (!ref) return "提问人未知";
  const kind = ref.subject_type === "enterprise_member" ? "内部用户" : "外部用户";
  return session.primary_sender_name?.trim()
    ? `${session.primary_sender_name.trim()} · ${kind}`
    : `${kind} · 姓名暂不可用 · ${ref.subject_id}`;
}

export function formatSenderIdentity(
  name: string | null,
  department: string | null,
) {
  const safeName = name?.trim();
  const safeDepartment = department?.trim();
  if (!safeName) return "Feishu 用户";
  return safeDepartment
    ? `${safeName} · ${safeDepartment}`
    : `${safeName} · 部门未记录`;
}


export function additionalParticipantLabel(participantCount: number | null) {
  if (participantCount === null || participantCount <= 1) return null;
  const additional = participantCount - 1;
  return `另有 ${additional} 人`;
}
