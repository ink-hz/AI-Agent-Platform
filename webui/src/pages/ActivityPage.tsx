import { useEffect, useMemo, useState } from "react";

import { agentsForSelector } from "../agentVisibility";
import { fetchAgents, fetchOperationalEvents, type OperationsEventQuery } from "../api";
import { EmptyState, LoadingState } from "../components/DataState";
import { OperationalEventItem } from "../components/OperationalEventItem";
import type { AgentSummary, EventSeverity, OperationalEvent } from "../types";


const PAGE_SIZE = 50;
const ACTIVITY_TIME_ZONE = "Asia/Shanghai";

interface ActivityFilters {
  agent_id: string;
  event_type: string;
  severity: "" | EventSeverity;
  date_from: string;
  date_to: string;
}

interface AppliedRequest {
  filters: ActivityFilters;
  offset: number;
  revision: number;
}

function shanghaiControlValue(date: Date): string | null {
  if (Number.isNaN(date.getTime())) return null;
  const values = new Map(
    new Intl.DateTimeFormat("en-GB", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone: ACTIVITY_TIME_ZONE,
    }).formatToParts(date).map((part) => [part.type, part.value]),
  );
  return `${values.get("year")}-${values.get("month")}-${values.get("day")}T${values.get("hour")}:${values.get("minute")}`;
}

function controlTimestamp(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null;
  const timestamp = `${value}:00+08:00`;
  return shanghaiControlValue(new Date(timestamp)) === value ? timestamp : null;
}

function explicitTimestampIsValid(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (match === null) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText = "0"] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12
    && day >= 1 && day <= days[month - 1]
    && hour >= 0 && hour <= 23
    && minute >= 0 && minute <= 59
    && second >= 0 && second <= 59
    && !Number.isNaN(new Date(value).getTime());
}

function timestampControl(value: string | null): string {
  if (value === null || value === "") return "";
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) {
    return controlTimestamp(value) === null ? "" : value;
  }
  if (!explicitTimestampIsValid(value)) return "";
  return shanghaiControlValue(new Date(value)) || "";
}

function filtersFromSearch(search: string): ActivityFilters {
  const params = new URLSearchParams(search);
  const severity = params.get("severity") || "";
  return {
    agent_id: (params.get("agent_id") || "").trim(),
    event_type: (params.get("event_type") || "").trim(),
    severity: severity === "info" || severity === "attention" || severity === "critical" ? severity : "",
    date_from: timestampControl(params.get("date_from")),
    date_to: timestampControl(params.get("date_to")),
  };
}

function cleanFilters(filters: ActivityFilters): ActivityFilters {
  return {
    agent_id: filters.agent_id.trim(),
    event_type: filters.event_type.trim(),
    severity: filters.severity,
    date_from: controlTimestamp(filters.date_from) === null ? "" : filters.date_from,
    date_to: controlTimestamp(filters.date_to) === null ? "" : filters.date_to,
  };
}

function filterParams(filters: ActivityFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.agent_id) params.set("agent_id", filters.agent_id);
  if (filters.event_type) params.set("event_type", filters.event_type);
  if (filters.severity) params.set("severity", filters.severity);
  const dateFrom = controlTimestamp(filters.date_from);
  const dateTo = controlTimestamp(filters.date_to);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  return params;
}

function canonicalActivityPath(filters: ActivityFilters): string {
  const params = filterParams(filters);
  return params.size ? `/activity?${params}` : "/activity";
}

function localDateKey(date: Date): string | null {
  if (Number.isNaN(date.getTime())) return null;
  const values = new Map(
    new Intl.DateTimeFormat("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      timeZone: ACTIVITY_TIME_ZONE,
    }).formatToParts(date).map((part) => [part.type, part.value]),
  );
  return `${values.get("year")}-${values.get("month")}-${values.get("day")}`;
}

function activityDateHeading(value: string, now: Date): string {
  const date = new Date(value);
  const key = localDateKey(date);
  if (key === null) return "Date unavailable";
  if (key === localDateKey(now)) return "Today";
  if (key === localDateKey(new Date(now.getTime() - 86_400_000))) return "Yesterday";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: ACTIVITY_TIME_ZONE,
  }).format(date);
}

function appendUnique(current: OperationalEvent[], next: OperationalEvent[]): OperationalEvent[] {
  const seen = new Set(current.map((event) => event.event_id));
  return [...current, ...next.filter((event) => {
    if (seen.has(event.event_id)) return false;
    seen.add(event.event_id);
    return true;
  })];
}

function groupedEvents(events: OperationalEvent[], now: Date) {
  const groups = new Map<string, OperationalEvent[]>();
  events.forEach((event) => {
    const heading = activityDateHeading(event.occurred_at, now);
    groups.set(heading, [...(groups.get(heading) || []), event]);
  });
  return Array.from(groups, ([heading, items]) => ({ heading, items }));
}

function requestQuery(filters: ActivityFilters, offset: number): OperationsEventQuery {
  return {
    agent_id: filters.agent_id,
    event_type: filters.event_type,
    severity: filters.severity || undefined,
    date_from: controlTimestamp(filters.date_from) || undefined,
    date_to: controlTimestamp(filters.date_to) || undefined,
    limit: PAGE_SIZE,
    offset,
  };
}

export function ActivityPage() {
  const initial = useMemo(() => filtersFromSearch(window.location.search), []);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [draft, setDraft] = useState<ActivityFilters>(initial);
  const [applied, setApplied] = useState<AppliedRequest>({ filters: initial, offset: 0, revision: 0 });
  const [events, setEvents] = useState<OperationalEvent[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [consumed, setConsumed] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [now, setNow] = useState(() => new Date());

  const applyFilters = (next: ActivityFilters) => {
    setDraft(next);
    setApplied((current) => ({ filters: next, offset: 0, revision: current.revision + 1 }));
  };

  useEffect(() => {
    const canonical = canonicalActivityPath(initial);
    if (`${window.location.pathname}${window.location.search}` !== canonical) {
      window.history.replaceState({}, "", canonical);
    }
    const restore = () => {
      if (window.location.pathname !== "/activity") return;
      const next = filtersFromSearch(window.location.search);
      const nextPath = canonicalActivityPath(next);
      if (`${window.location.pathname}${window.location.search}` !== nextPath) {
        window.history.replaceState({}, "", nextPath);
      }
      applyFilters(next);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [initial]);

  useEffect(() => {
    const key = localDateKey(now);
    if (key === null) return undefined;
    const nextMidnight = Date.parse(`${key}T00:00:00+08:00`) + 86_400_000;
    const timer = window.setTimeout(() => setNow(new Date()), Math.max(1, nextMidnight - Date.now()));
    return () => window.clearTimeout(timer);
  }, [now]);

  useEffect(() => {
    const controller = new AbortController();
    fetchAgents(controller.signal)
      .then((nextAgents) => { if (!controller.signal.aborted) setAgents(nextAgents); })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    setUnavailable(false);
    if (applied.offset === 0) {
      setEvents([]);
      setTotal(null);
      setConsumed(0);
      setLoading(true);
    } else {
      setLoadingMore(true);
    }
    fetchOperationalEvents(requestQuery(applied.filters, applied.offset), controller.signal)
      .then((page) => {
        if (disposed) return;
        setEvents((current) => applied.offset === 0 ? appendUnique([], page.items) : appendUnique(current, page.items));
        setConsumed(page.offset + page.items.length);
        setTotal(page.total);
        setLoading(false);
        setLoadingMore(false);
      })
      .catch(() => {
        if (disposed) return;
        setUnavailable(true);
        setLoading(false);
        setLoadingMore(false);
      });
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [applied]);

  const selectableAgents = agentsForSelector(agents, draft.agent_id);
  const needsSelectedPlaceholder = draft.agent_id !== ""
    && !selectableAgents.some((agent) => agent.id === draft.agent_id);
  const groups = groupedEvents(events, now);
  const hasMore = total !== null && consumed < total;

  return <>
    <section className="page-intro">
      <div>
        <p className="eyebrow">OPERATIONAL RECORD</p>
        <h1>Activity History</h1>
        <p>Review evidence-backed changes across Business Agents and explicitly selected System Agents.</p>
      </div>
    </section>
    <form className="filter-bar" onSubmit={(event) => {
      event.preventDefault();
      const next = cleanFilters(draft);
      const path = canonicalActivityPath(next);
      if (`${window.location.pathname}${window.location.search}` !== path) {
        window.history.pushState({}, "", path);
      }
      applyFilters(next);
    }}>
      <label><span>Agent</span><select name="agent_id" value={draft.agent_id} onChange={(event) => setDraft((current) => ({ ...current, agent_id: event.target.value }))}><option value="">All Business Agents</option>{selectableAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}{needsSelectedPlaceholder && <option value={draft.agent_id}>{draft.agent_id}</option>}</select></label>
      <label><span>Event type</span><input name="event_type" value={draft.event_type} onChange={(event) => setDraft((current) => ({ ...current, event_type: event.target.value }))} placeholder="e.g. runtime_offline" /></label>
      <label><span>Severity</span><select name="severity" value={draft.severity} onChange={(event) => setDraft((current) => ({ ...current, severity: event.target.value as ActivityFilters["severity"] }))}><option value="">All severities</option><option value="critical">Critical</option><option value="attention">Attention</option><option value="info">Info</option></select></label>
      <label><span>From</span><input name="date_from" type="datetime-local" value={draft.date_from} onChange={(event) => setDraft((current) => ({ ...current, date_from: event.target.value }))} /></label>
      <label><span>To</span><input name="date_to" type="datetime-local" value={draft.date_to} onChange={(event) => setDraft((current) => ({ ...current, date_to: event.target.value }))} /></label>
      <button type="submit">Apply filters</button>
    </form>
    {unavailable && <section className="data-state data-error" role="alert"><strong>Activity unavailable</strong><p>The Platform could not read operational history. Existing Agent services are not affected.</p></section>}
    {loading ? <LoadingState label="Loading Activity" />
      : events.length === 0 && !unavailable ? <EmptyState title="No matching activity" description="Adjust the filters or wait for new evidence-backed operational changes." />
      : <div className="activity-history">{groups.map((group) => <section className="activity-group" key={group.heading}><h2>{group.heading}</h2><div className="operational-event-list">{group.items.map((event) => <OperationalEventItem event={event} key={event.event_id} />)}</div></section>)}</div>}
    {hasMore && <button type="button" disabled={loadingMore} onClick={() => {
      if (loadingMore) return;
      setApplied((current) => ({ ...current, offset: consumed, revision: current.revision + 1 }));
    }}>{loadingMore ? "Loading more" : "Load more"}</button>}
  </>;
}
