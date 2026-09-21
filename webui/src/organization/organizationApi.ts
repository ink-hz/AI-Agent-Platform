import { platformPath } from '../auth';
import { AiEngineeringApiError } from '../aiEngineeringApi';

export interface Department { id: string; parent_id: string | null; name: string }
export interface OrganizationTree {
  generation_id: string; completed_at: string; freshness: 'fresh' | 'warning' | 'hard_stale';
  scope: 'visible_directory'; root_id: string; departments: Department[];
}
type DirectoryStatus = 'active' | 'inactive' | 'disabled';
export interface OrganizationMember { id: string; name: string; status: DirectoryStatus; departments: { id: string; name: string }[] }
export interface DepartmentDetail {
  generation_id: string; department_id: string; direct_count: number; total_count: number;
  status_counts: Record<DirectoryStatus, number>; position_available: boolean; members: OrganizationMember[]; next_cursor: string | null;
}
function invalid(): never { throw new Error('组织目录响应格式无效'); }
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); return value as Record<string, unknown>; }
function exact(value: Record<string, unknown>, keys: string[]) { if (Object.keys(value).length !== keys.length || keys.some(key => !(key in value))) invalid(); }
function text(value: unknown): string { if (typeof value !== 'string' || !value.trim()) return invalid(); return value; }
function uuid(value: unknown): string { const result = text(value); if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(result)) invalid(); return result; }
function count(value: unknown): number { if (!Number.isSafeInteger(value) || (value as number) < 0) return invalid(); return value as number; }
function array(value: unknown): unknown[] { if (!Array.isArray(value)) return invalid(); return value; }
function status(value: unknown): DirectoryStatus { if (value !== 'active' && value !== 'inactive' && value !== 'disabled') return invalid(); return value; }
export function parseOrganization(value: unknown): OrganizationTree {
  const item = object(value); exact(item, ['generation_id', 'completed_at', 'freshness', 'scope', 'root_id', 'departments']);
  const completed_at = text(item.completed_at);
  if (!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(completed_at) || !Number.isFinite(Date.parse(completed_at))) invalid();
  if (item.freshness !== 'fresh' && item.freshness !== 'warning' && item.freshness !== 'hard_stale') invalid();
  if (item.scope !== 'visible_directory') invalid();
  const root_id = uuid(item.root_id);
  const departments = array(item.departments).map(value => { const d = object(value); exact(d, ['id', 'parent_id', 'name']); return { id: uuid(d.id), parent_id: d.parent_id === null ? null : uuid(d.parent_id), name: text(d.name) }; });
  const byId = new Map(departments.map(d => [d.id, d]));
  if (byId.size !== departments.length || byId.get(root_id)?.parent_id !== null) invalid();
  const reached = new Set([root_id]); const children = new Map<string, string[]>();
  for (const d of departments) { if (d.id === root_id) continue; if (!d.parent_id || !byId.has(d.parent_id)) invalid(); children.set(d.parent_id, [...(children.get(d.parent_id) ?? []), d.id]); }
  const queue = [root_id]; for (let i = 0; i < queue.length; i++) for (const child of children.get(queue[i]) ?? []) { if (reached.has(child)) invalid(); reached.add(child); queue.push(child); }
  if (reached.size !== departments.length) invalid();
  return { generation_id: uuid(item.generation_id), completed_at, freshness: item.freshness as OrganizationTree['freshness'], scope: 'visible_directory', root_id, departments };
}
export function parseDepartment(value: unknown): DepartmentDetail {
  const item = object(value); exact(item, ['generation_id', 'department_id', 'direct_count', 'total_count', 'status_counts', 'position_available', 'members', 'next_cursor']);
  const counts = object(item.status_counts); exact(counts, ['active', 'inactive', 'disabled']);
  if (typeof item.position_available !== 'boolean') invalid();
  const direct_count = count(item.direct_count); const total_count = count(item.total_count);
  if (direct_count > total_count) invalid();
  const members = array(item.members).map(value => { const m = object(value); exact(m, ['id', 'name', 'status', 'departments']); return { id: uuid(m.id), name: text(m.name), status: status(m.status), departments: array(m.departments).map(value => { const d = object(value); exact(d, ['id', 'name']); return { id: uuid(d.id), name: text(d.name) }; }) }; });
  if (new Set(members.map(m => m.id)).size !== members.length || members.length > 100 || members.length > total_count) invalid();
  return { generation_id: uuid(item.generation_id), department_id: uuid(item.department_id), direct_count, total_count, status_counts: { active: count(counts.active), inactive: count(counts.inactive), disabled: count(counts.disabled) }, position_available: item.position_available as boolean, members, next_cursor: item.next_cursor === null ? null : uuid(item.next_cursor) };
}
async function read(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal });
  if (!response.ok) throw new AiEngineeringApiError(response.status);
  return response.json();
}
export async function fetchOrganization(signal?: AbortSignal) { return parseOrganization(await read('/api/v1/ai-engineering/organization', signal)); }
export async function fetchDepartment(departmentId: string, generationId: string, cursor?: string, signal?: AbortSignal) {
  const query = new URLSearchParams({ generation_id: generationId, limit: '50' }); if (cursor) query.set('cursor', cursor);
  const result = parseDepartment(await read(`/api/v1/ai-engineering/organization/departments/${encodeURIComponent(departmentId)}?${query}`, signal));
  if (result.generation_id !== generationId) throw new AiEngineeringApiError(409);
  if (result.department_id !== departmentId || (cursor && result.next_cursor === cursor)) invalid();
  return result;
}
