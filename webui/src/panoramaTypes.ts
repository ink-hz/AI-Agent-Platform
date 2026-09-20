import type { AiEngineeringDocumentSlug } from './aiEngineeringApi';
export type PanoramaActionId = 'brain' | 'agents' | 'missions' | 'sessions' | 'operations' | 'review' | 'activity' | 'identity' | 'governance' | 'access' | 'account' | 'agent-admin' | 'notes' | 'hr' | 'office' | 'voc' | 'fae';
export interface PanoramaSource { id: string; label: string; document: AiEngineeringDocumentSlug }
export interface PanoramaDomain { id: string; title: string; subtitle: string; items: string[]; detail: string[]; status: string; source_ids: string[]; related_ids: string[]; actions: PanoramaActionId[] }
export interface PanoramaData {
  version: string; updated_at: string; title: string;
  context: { period: string; metrics: { label: string; value: string }[]; observation: string; judgment: string; source_ids: string[] };
  revenue: { period: string; denominator_cents: number; segments: { id: string; label: string; amount_cents: number }[]; note: string; source_ids: string[] };
  domains: PanoramaDomain[]; support: PanoramaDomain[];
  shared: { title: string; actions: PanoramaActionId[]; status: string };
  asks: { owner: string; request: string }[];
  sources: PanoramaSource[];
}
