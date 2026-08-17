export type SessionSource = "" | "metabot" | "fae" | "admin";

export type SessionFilters = {
  agent_id: string;
  source_kind: SessionSource;
  q: string;
  page: number;
};


const SOURCES = new Set<SessionSource>(["", "metabot", "fae", "admin"]);


function clean(value: string | null): string {
  return (value ?? "").trim();
}


function cleanAgentId(value: string | null): string {
  const candidate = clean(value);
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(candidate) ? candidate : "";
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
  return {
    agent_id: cleanAgentId(params.get("agent_id")),
    source_kind: SOURCES.has(source as SessionSource) ? source as SessionSource : "",
    q: clean(params.get("q")),
    page: cleanPage(params),
  };
}


export function sessionsPath(filters: SessionFilters): string {
  const params = new URLSearchParams();
  if (filters.agent_id) params.set("agent_id", filters.agent_id);
  if (filters.source_kind) params.set("source_kind", filters.source_kind);
  if (filters.q) params.set("q", filters.q);
  if (filters.page > 1) params.set("page", String(filters.page));
  const search = params.toString();
  return search ? `/sessions?${search}` : "/sessions";
}
