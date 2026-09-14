import { navigate, routePath } from '../../router';

export type HrWorkPanel = 'candidate-materials' | 'candidate-work';
type Draft = { ownerId: string; positionId?: string; text: string; notice: string; panel?: HrWorkPanel };
let pending: Draft | undefined;

// Only an unsent draft crosses the page boundary. Never store private text in URLs or storage.
export function openHrWork(ownerId: string, positionId?: string | null, text = '', notice = '', panel?: HrWorkPanel) {
  pending = { ownerId, positionId: positionId ?? undefined, text, notice, panel };
  navigate(routePath({ name: 'hr', positionId: positionId ?? undefined }));
}

export function takeHrWorkDraft(ownerId: string, positionId?: string) {
  const draft = pending;
  pending = undefined;
  return draft?.ownerId === ownerId && draft.positionId === positionId
    ? { text: draft.text, notice: draft.notice, ...(draft.panel ? {panel:draft.panel} : {}) }
    : undefined;
}
