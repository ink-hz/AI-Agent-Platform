export type FaeSectionStatus = "available" | "unavailable";
export type FaeFreshnessStatus = "fresh" | "stale" | "unavailable";

export interface FaeSectionState {
  status: FaeSectionStatus;
  as_of: string | null;
  error_code: string | null;
}

export interface FaeFreshness {
  status: FaeFreshnessStatus;
  data_as_of: string | null;
}

export interface FaeSummary {
  session_count: number;
  active_subject_count: number;
  negative_feedback_events: number;
  negative_turn_count: number;
  abnormal_session_count: number;
  open_issue_count: number | null;
  p50_duration_ms: number | null;
  p95_duration_ms: number | null;
}

export interface FaeSessionAttention {
  session_key: string;
  title: string | null;
  last_active_at: string;
  reason: "fallback" | "failed_outcome" | "empty_answer";
}

export interface FaeTrendPoint {
  day: string;
  sessions: number;
  negative_turns: number;
}

export interface FaeOverview {
  period_start: string;
  period_end: string;
  timezone: "Asia/Shanghai";
  freshness: FaeFreshness;
  summary: { state: FaeSectionState; data: FaeSummary | null };
  attention: { state: FaeSectionState; items: FaeSessionAttention[] };
  trends: { state: FaeSectionState; points: FaeTrendPoint[] };
  issues: { state: FaeSectionState; statuses: Record<string, number> };
  reports: { state: FaeSectionState; report_id: string | null; title: string | null; data_cutoff_at: string | null; currentness: "current" | "source_updated" | null };
}

export interface FaeSessionQuery {
  q?: string;
  channel?: string;
  sentiment?: "positive" | "negative" | "other";
  review_status?: string;
  outcome?: string;
  date_from?: string;
  date_to?: string;
  date_before?: string;
  subject_key?: string;
  has_subject?: string;
  abnormal?: string;
  has_latency?: string;
  limit?: number;
  offset?: number;
}

export class FaeWorkbenchContractError extends Error {
  constructor() {
    super("FAE workbench response contract invalid");
    this.name = "FaeWorkbenchContractError";
  }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new FaeWorkbenchContractError();
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new FaeWorkbenchContractError();
  }
}

function nonEmptyString(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new FaeWorkbenchContractError();
  return value;
}

function nullableString(value: unknown): string | null {
  return value === null ? null : nonEmptyString(value);
}

function integer(value: unknown, nullable = false): number | null {
  if (nullable && value === null) return null;
  if (!Number.isSafeInteger(value) || Number(value) < 0) throw new FaeWorkbenchContractError();
  return Number(value);
}

function calendarDate(value: string): boolean {
  const [year, month, dayOfMonth] = value.slice(0, 10).split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, dayOfMonth));
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === dayOfMonth;
}

function timestamp(value: unknown, nullable = false): string | null {
  if (nullable && value === null) return null;
  const selected = nonEmptyString(value);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(selected)
    || !calendarDate(selected) || !Number.isFinite(Date.parse(selected))) throw new FaeWorkbenchContractError();
  return selected;
}

function day(value: unknown): string {
  const selected = nonEmptyString(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(selected) || !calendarDate(selected)) {
    throw new FaeWorkbenchContractError();
  }
  return selected;
}

function state(value: unknown): FaeSectionState {
  const selected = record(value);
  exactKeys(selected, ["status", "as_of", "error_code"]);
  if (selected.status !== "available" && selected.status !== "unavailable") throw new FaeWorkbenchContractError();
  return {
    status: selected.status,
    as_of: timestamp(selected.as_of, true),
    error_code: nullableString(selected.error_code),
  };
}

function summary(value: unknown): FaeSummary {
  const selected = record(value);
  exactKeys(selected, [
    "session_count", "active_subject_count", "negative_feedback_events", "negative_turn_count",
    "abnormal_session_count", "open_issue_count", "p50_duration_ms", "p95_duration_ms",
  ]);
  return {
    session_count: integer(selected.session_count) as number,
    active_subject_count: integer(selected.active_subject_count) as number,
    negative_feedback_events: integer(selected.negative_feedback_events) as number,
    negative_turn_count: integer(selected.negative_turn_count) as number,
    abnormal_session_count: integer(selected.abnormal_session_count) as number,
    open_issue_count: integer(selected.open_issue_count, true),
    p50_duration_ms: integer(selected.p50_duration_ms, true),
    p95_duration_ms: integer(selected.p95_duration_ms, true),
  };
}

export function parseFaeOverview(value: unknown): FaeOverview {
  const selected = record(value);
  exactKeys(selected, ["period_start", "period_end", "timezone", "freshness", "summary", "attention", "trends", "issues", "reports"]);
  if (selected.timezone !== "Asia/Shanghai") throw new FaeWorkbenchContractError();

  const freshness = record(selected.freshness);
  exactKeys(freshness, ["status", "data_as_of"]);
  if (freshness.status !== "fresh" && freshness.status !== "stale" && freshness.status !== "unavailable") {
    throw new FaeWorkbenchContractError();
  }

  const summarySection = record(selected.summary);
  exactKeys(summarySection, ["state", "data"]);
  const attentionSection = record(selected.attention);
  exactKeys(attentionSection, ["state", "items"]);
  const trendsSection = record(selected.trends);
  exactKeys(trendsSection, ["state", "points"]);
  const issuesSection = record(selected.issues);
  exactKeys(issuesSection, ["state", "statuses"]);
  const reportsSection = record(selected.reports);
  exactKeys(reportsSection, ["state", "report_id", "title", "data_cutoff_at", "currentness"]);
  if (reportsSection.currentness !== null && reportsSection.currentness !== "current" && reportsSection.currentness !== "source_updated") throw new FaeWorkbenchContractError();
  if (!Array.isArray(attentionSection.items) || !Array.isArray(trendsSection.points)) throw new FaeWorkbenchContractError();
  const statuses = record(issuesSection.statuses);

  return {
    period_start: timestamp(selected.period_start) as string,
    period_end: timestamp(selected.period_end) as string,
    timezone: "Asia/Shanghai",
    freshness: { status: freshness.status, data_as_of: timestamp(freshness.data_as_of, true) },
    summary: { state: state(summarySection.state), data: summarySection.data === null ? null : summary(summarySection.data) },
    attention: {
      state: state(attentionSection.state),
      items: attentionSection.items.map((item) => {
        const attention = record(item);
        exactKeys(attention, ["session_key", "title", "last_active_at", "reason"]);
        if (attention.reason !== "fallback" && attention.reason !== "failed_outcome" && attention.reason !== "empty_answer") {
          throw new FaeWorkbenchContractError();
        }
        return {
          session_key: nonEmptyString(attention.session_key), title: nullableString(attention.title),
          last_active_at: timestamp(attention.last_active_at) as string, reason: attention.reason,
        };
      }),
    },
    trends: {
      state: state(trendsSection.state),
      points: trendsSection.points.map((item) => {
        const point = record(item);
        exactKeys(point, ["day", "sessions", "negative_turns"]);
        return { day: day(point.day), sessions: integer(point.sessions) as number, negative_turns: integer(point.negative_turns) as number };
      }),
    },
    issues: {
      state: state(issuesSection.state),
      statuses: Object.fromEntries(Object.entries(statuses).map(([key, count]) => [nonEmptyString(key), integer(count) as number])),
    },
    reports: {
      state: state(reportsSection.state),
      report_id: nullableString(reportsSection.report_id),
      title: nullableString(reportsSection.title),
      data_cutoff_at: timestamp(reportsSection.data_cutoff_at, true),
      currentness: reportsSection.currentness,
    },
  };
}
