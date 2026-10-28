import { platformPath } from './auth';

export interface ManufacturingDataset {
  id: string;
  mode: 'production' | 'demo' | 'test';
  label: string;
}
interface AccessBase {
  allowed: boolean;
  available: boolean;
  can_edit: boolean;
  release?: string;
}
export interface LegacyManufacturingAccess extends AccessBase {
  protocol?: undefined;
  workflow_available?: undefined;
  workflow_access?: undefined;
  legacy_access?: undefined;
}
export interface ManufacturingBootstrap extends AccessBase {
  protocol: 1;
  release: string;
  workflow_available: boolean;
  workflow_access: boolean;
  legacy_access: boolean;
  configuration_admin: boolean;
  dataset: ManufacturingDataset | null;
  user: { id: string; display_name: string };
  project_ids: string[];
}
export type ManufacturingAccess = LegacyManufacturingAccess | ManufacturingBootstrap;
export class ManufacturingAccessError extends Error {
  constructor(public readonly status: number) { super('Manufacturing access unavailable'); }
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const boundedText = (value: unknown, max = 200): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= max;
const uuid = (value: unknown): value is string => typeof value === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
function validBase(value: unknown): value is Record<string, unknown> & AccessBase {
  return record(value) && ['allowed', 'available', 'can_edit'].every(key => typeof value[key] === 'boolean') &&
    (value.release === undefined ? !value.available : boundedText(value.release));
}
function validBootstrap(value: unknown): value is ManufacturingBootstrap {
  if (!validBase(value) || value.protocol !== 1 || !boundedText(value.release) ||
    !['workflow_available', 'workflow_access', 'legacy_access', 'configuration_admin'].every(key => typeof value[key] === 'boolean') ||
    !record(value.user) || !uuid(value.user.id) || typeof value.user.display_name !== 'string' || value.user.display_name.length > 200 ||
    !Array.isArray(value.project_ids) || !value.project_ids.every(uuid)) return false;
  if (!value.workflow_available) return value.dataset === null && value.workflow_access === false && value.project_ids.length === 0;
  return value.available === true && record(value.dataset) && boundedText(value.dataset.id) && boundedText(value.dataset.label) &&
    ['production', 'demo', 'test'].includes(value.dataset.mode as string);
}
export async function manufacturingAccess(signal?: AbortSignal): Promise<ManufacturingAccess> {
  const options: RequestInit = { credentials: 'same-origin', headers: {Accept: 'application/json'}, signal };
  let response = await fetch(platformPath('/api/v1/manufacturing/workflow/access'), options);
  const legacy = response.status === 404;
  if (legacy) response = await fetch(platformPath('/api/v1/manufacturing/access'), options);
  if (!response.ok) throw new ManufacturingAccessError(response.status);
  const value: unknown = await response.json();
  if (legacy) {
    if (!validBase(value) || value.protocol !== undefined) throw new Error('Invalid manufacturing access');
    return value as unknown as LegacyManufacturingAccess;
  }
  if (!validBootstrap(value)) throw new Error('Invalid manufacturing bootstrap');
  return value;
}
