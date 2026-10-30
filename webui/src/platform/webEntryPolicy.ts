import type { AgentCapabilityCard } from '../brainTypes';

const WEB_PATHS = new Set(['/fae/', '/daq/', '/hr/', '/voc/', '/office/?view=services']);
const DISPLAY_ORDER = ['ai-fae-agent', 'ai-daq-fae-agent', 'hr-bot', 'voc', 'ai-admin-agent'];

export function validWebEntry(value: unknown): value is NonNullable<AgentCapabilityCard['web_entry']> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const entry = value as Record<string, unknown>;
  return entry.kind === 'independent_service' && typeof entry.path === 'string' && WEB_PATHS.has(entry.path);
}

export function webEntryPath(card: AgentCapabilityCard): string | null {
  return card.can_use !== false && validWebEntry(card.web_entry) ? card.web_entry.path : null;
}

export function webInventoryApplications(cards: AgentCapabilityCard[]): AgentCapabilityCard[] {
  const rank = (id: string) => { const index = DISPLAY_ORDER.indexOf(id); return index < 0 ? DISPLAY_ORDER.length : index; };
  return cards.filter(card => validWebEntry(card.web_entry)).sort((a, b) => rank(a.agent_id) - rank(b.agent_id)
    || a.display_name.localeCompare(b.display_name, 'zh-CN'));
}

export function webApplications(cards: AgentCapabilityCard[]): AgentCapabilityCard[] {
  return webInventoryApplications(cards).filter(card => card.can_use !== false);
}

const CONTEXTUAL_AGENT: Readonly<Record<string, string>> = {hr: 'hr-bot', fae: 'ai-fae-agent', voc: 'voc', office: 'ai-admin-agent'};
export function contextualWebPath(action: string, cards: AgentCapabilityCard[]): string | null {
  const id = CONTEXTUAL_AGENT[action];
  if (!id) return null;
  const card = cards.find(candidate => candidate.agent_id === id);
  return card ? webEntryPath(card) : null;
}

/** Prefer the current Web projection of an agent over retained channel adapters. */
export function catalogInventory(cards: AgentCapabilityCard[]): AgentCapabilityCard[] {
  const unique = new Map<string, AgentCapabilityCard>();
  for (const card of cards) {
    const previous = unique.get(card.agent_id);
    if (!previous || (!validWebEntry(previous.web_entry) && validWebEntry(card.web_entry))) unique.set(card.agent_id, card);
  }
  return [...unique.values()];
}
