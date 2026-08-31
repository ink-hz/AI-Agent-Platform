export type SessionSource = "" | "metabot" | "fae" | "admin";
export type SessionSentiment = "" | "positive" | "negative" | "other";

export type SessionFilters = {
  agent_id: string;
  source_kind: SessionSource;
  q: string;
  channel: string;
  sentiment: SessionSentiment;
  review_status: string;
  outcome: string;
  date_from: string;
  date_to: string;
  page: number;
};


export const EMPTY_SESSION_FILTERS: SessionFilters = {
  agent_id: "",
  source_kind: "",
  q: "",
  channel: "",
  sentiment: "",
  review_status: "",
  outcome: "",
  date_from: "",
  date_to: "",
  page: 1,
};


const SOURCES = new Set<SessionSource>(["", "metabot", "fae", "admin"]);
const SENTIMENTS = new Set<SessionSentiment>(["", "positive", "negative", "other"]);


function clean(value: string | null): string {
  return (value ?? "").trim();
}


function cleanAgentId(value: string | null): string {
  const candidate = clean(value);
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(candidate) ? candidate : "";
}


export function normalizeFaeSessionDate(value: string | null): string {
  const candidate = clean(value);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(candidate);
  if (!match) return "";
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
  const offsetHour = match[7] === undefined ? null : Number(match[7]);
  const offsetMinute = match[8] === undefined ? null : Number(match[8]);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59
    || (offsetHour !== null && (offsetHour > 23 || offsetMinute === null || offsetMinute > 59))) return "";
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  calendar.setUTCHours(0, 0, 0, 0);
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day
    || !Number.isFinite(Date.parse(candidate))) return "";
  return candidate;
}

function cleanPage(params: URLSearchParams): number {
  const values = params.getAll("page");
  if (values.length !== 1 || !/^[1-9]\d*$/.test(values[0])) return 1;
  const page = Number(values[0]);
  return Number.isSafeInteger(page) ? page : 1;
}


export function sessionFiltersFromSearch(search: string): SessionFilters {
  const params = new URLSearchParams(search);
  const source = clean(params.get("source_kind"));
  const sentiment = clean(params.get("sentiment"));
  return {
    agent_id: cleanAgentId(params.get("agent_id")),
    source_kind: SOURCES.has(source as SessionSource) ? source as SessionSource : "",
    q: clean(params.get("q")),
    channel: clean(params.get("channel")),
    sentiment: SENTIMENTS.has(sentiment as SessionSentiment) ? sentiment as SessionSentiment : "",
    review_status: clean(params.get("review_status")),
    outcome: clean(params.get("outcome")),
    date_from: normalizeFaeSessionDate(params.get("date_from")),
    date_to: normalizeFaeSessionDate(params.get("date_to")),
    page: cleanPage(params),
  };
}


export function sessionsPath(filters: SessionFilters, basePath = "/admin/sessions"): string {
  const params = new URLSearchParams();
  if (filters.agent_id) params.set("agent_id", filters.agent_id);
  if (filters.source_kind) params.set("source_kind", filters.source_kind);
  if (filters.q) params.set("q", filters.q);
  if (basePath === "/admin/fae/sessions") {
    if (filters.channel) params.set("channel", filters.channel);
    if (filters.sentiment) params.set("sentiment", filters.sentiment);
    if (filters.review_status) params.set("review_status", filters.review_status);
    if (filters.outcome) params.set("outcome", filters.outcome);
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);
  }
  if (filters.page > 1) params.set("page", String(filters.page));
  const search = params.toString();
  return search ? `${basePath}?${search}` : basePath;
}
