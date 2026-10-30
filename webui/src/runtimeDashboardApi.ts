import schema from '../../backend/tests/fixtures/runtime_dashboard_v1.schema.json';
import { platformPath } from './auth';
import type { DashboardDays, DashboardOverview } from './runtimeDashboardTypes';

type Schema = { $ref?: string; anyOf?: Schema[]; const?: unknown; enum?: unknown[]; type?: string; minimum?: number; format?: string; properties?: Record<string, Schema>; required?: string[]; items?: Schema; additionalProperties?: boolean | Schema };
// Validate the serialized DTO against the producer's checked-in schema. Defaults are
// serialized by the API; do not coerce missing metrics, permissions or evidence.
function matches(value: unknown, rule: Schema): boolean {
  if (rule.$ref) return matches(value, (schema.$defs as Record<string, Schema>)[rule.$ref.split('/').pop()!]);
  if (rule.anyOf) return rule.anyOf.some(option => matches(value, option));
  if ('const' in rule && value !== rule.const) return false;
  if (rule.enum && !rule.enum.includes(value)) return false;
  if (rule.type === 'null') return value === null;
  if (rule.type === 'integer') return Number.isSafeInteger(value) && (rule.minimum === undefined || Number(value) >= rule.minimum);
  if (rule.type === 'boolean') return typeof value === 'boolean';
  if (rule.type === 'string') return typeof value === 'string' && (rule.format !== 'date-time' || (/(Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value))));
  if (rule.type === 'array') return Array.isArray(value) && value.every(item => !rule.items || matches(item, rule.items));
  if (rule.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const record = value as Record<string, unknown>;
    const properties = rule.properties ?? {};
    const optional = new Set(['input_time_basis', 'missing_question_time_records']);
    if (Object.keys(properties).some(key => !(key in record) && !optional.has(key))) return false;
    return Object.entries(record).every(([key, item]) => Object.prototype.hasOwnProperty.call(properties, key) ? matches(item, properties[key])
      : typeof rule.additionalProperties === 'object' ? matches(item, rule.additionalProperties) : rule.additionalProperties !== false);
  }
  return true;
}

export async function fetchRuntimeDashboard(days: DashboardDays, signal?: AbortSignal): Promise<DashboardOverview> {
  const response = await fetch(platformPath(`/api/dashboard/overview?days=${days}`), {
    credentials: 'include', cache: 'no-store', headers: { Accept: 'application/json' }, signal,
  });
  if (!response.ok) throw new Error(`Dashboard HTTP ${response.status}`);
  const value: unknown = await response.json();
  if (!matches(value, schema as Schema) || (value as DashboardOverview).window.days !== days) throw new Error('Dashboard response invalid');
  return value as DashboardOverview;
}
