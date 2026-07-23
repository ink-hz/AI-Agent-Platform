export function formatSenderIdentity(
  name: string | null,
  department: string | null,
) {
  const safeName = name?.trim();
  const safeDepartment = department?.trim();
  if (!safeName) return "Feishu User";
  return safeDepartment
    ? `${safeName} · ${safeDepartment}`
    : `${safeName} · Department unavailable`;
}


export function additionalParticipantLabel(participantCount: number | null) {
  if (participantCount === null || participantCount <= 1) return null;
  const additional = participantCount - 1;
  return `+ ${additional} ${additional === 1 ? "person" : "people"}`;
}
