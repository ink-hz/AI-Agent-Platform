import { platformPath } from './auth';

export interface ManufacturingAccess { allowed: boolean; available: boolean; can_edit: boolean; release?: string; protocol?: number; workflow_available?: boolean; legacy_access?: boolean; workflow_access?: boolean }
export class ManufacturingAccessError extends Error {
  constructor(public readonly status: number) { super('Manufacturing access unavailable'); }
}
export async function manufacturingAccess(signal?: AbortSignal): Promise<ManufacturingAccess> {
  let response = await fetch(platformPath('/api/v1/manufacturing/workflow/access'), {
    credentials: 'same-origin', headers: {Accept: 'application/json'}, signal,
  });
  if (response.status === 404) response = await fetch(platformPath('/api/v1/manufacturing/access'), { credentials: 'same-origin', headers: {Accept: 'application/json'}, signal });
  if (!response.ok) throw new ManufacturingAccessError(response.status);
  const value = await response.json() as ManufacturingAccess;
  if (typeof value.allowed !== 'boolean' || typeof value.available !== 'boolean') throw new Error('Invalid manufacturing access');
  return value;
}
