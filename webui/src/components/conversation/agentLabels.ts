export function professionalAgentLabel(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  const known: Record<string, string> = {
    "hr-bot": "HR Agent",
    "fae": "FAE Agent",
    "fae-bot": "FAE Agent",
    "admin": "AI ADMIN Agent",
    "admin-bot": "AI ADMIN Agent",
  };
  return known[value] ?? `专业 Agent · ${value}`;
}
