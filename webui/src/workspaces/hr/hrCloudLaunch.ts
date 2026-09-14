import { navigate, routePath } from '../../router';

type Draft = { ownerId: string; positionId?: string; text: string; notice: string };
let pending: Draft | undefined;

// Only an unsent draft crosses the page boundary. Never store private text in URLs or storage.
export function openHrWork(ownerId: string, positionId?: string | null, text = '', notice = '') {
  pending = { ownerId, positionId: positionId ?? undefined, text, notice };
  navigate(routePath({ name: 'hr', positionId: positionId ?? undefined }));
}

export function takeHrWorkDraft(ownerId: string, positionId?: string) {
  const draft = pending;
  pending = undefined;
  return draft?.ownerId === ownerId && draft.positionId === positionId
    ? { text: draft.text, notice: draft.notice }
    : undefined;
}
