const pending = new Map<string, string>();
const STORAGE_PREFIX = "hr-r12:mutation:";

function fingerprint(value: string): string {
  const seeds = [2166136261, 2246822519, 3266489917, 668265263];
  return seeds.map((seed) => {
    let hash = seed >>> 0;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619) >>> 0;
    }
    return hash.toString(16).padStart(8, "0");
  }).join("");
}

function storage(): Storage | null {
  try { return window.sessionStorage; } catch { return null; }
}

export function retainMutationRequest(scope: string, payload: unknown): { key: string; requestId: string } {
  const key = `${STORAGE_PREFIX}${scope}:${fingerprint(JSON.stringify(payload))}`;
  const retained = pending.get(key) ?? storage()?.getItem(key);
  if (retained) { pending.set(key, retained); return { key, requestId: retained }; }
  const requestId = crypto.randomUUID();
  pending.set(key, requestId); storage()?.setItem(key, requestId);
  return { key, requestId };
}

export function completeMutationRequest(key: string): void {
  pending.delete(key); storage()?.removeItem(key);
}
