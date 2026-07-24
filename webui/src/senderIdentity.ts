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
