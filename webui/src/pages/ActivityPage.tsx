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

function initialFilters(): ActivityFilters {
  const params = new URLSearchParams(window.location.search);
  const severity = params.get("severity") || "";
  return {
    agent_id: params.get("agent_id") || "",
    event_type: params.get("event_type") || "",
    severity: severity === "info" || severity === "attention" || severity === "critical" ? severity : "",
    date_from: params.get("date_from") || "",
    date_to: params.get("date_to") || "",
  };
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
    date_from: filters.date_from,
    date_to: filters.date_to,
    limit: PAGE_SIZE,
    offset,
  };
}

export function ActivityPage() {
  const initial = useMemo(initialFilters, []);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [draft, setDraft] = useState<ActivityFilters>(initial);
  const [filters, setFilters] = useState<ActivityFilters>(initial);
  const [events, setEvents] = useState<OperationalEvent[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const now = useMemo(() => new Date(), []);

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
    if (offset === 0) {
      setEvents([]);
      setTotal(null);
      setLoading(true);
    } else {
      setLoadingMore(true);
    }
    fetchOperationalEvents(requestQuery(filters, offset), controller.signal)
      .then((page) => {
        if (disposed) return;
        setEvents((current) => offset === 0 ? appendUnique([], page.items) : appendUnique(current, page.items));
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
  }, [filters, offset]);

  const selectableAgents = agentsForSelector(agents, draft.agent_id);
  const groups = groupedEvents(events, now);
  const hasMore = total !== null && events.length < total;

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
      setOffset(0);
      setFilters({ ...draft, event_type: draft.event_type.trim() });
    }}>
      <label><span>Agent</span><select name="agent_id" value={draft.agent_id} onChange={(event) => setDraft((current) => ({ ...current, agent_id: event.target.value }))}><option value="">All Business Agents</option>{selectableAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
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
    {hasMore && <button type="button" disabled={loadingMore} onClick={() => setOffset(events.length)}>{loadingMore ? "Loading more" : "Load more"}</button>}
  </>;
}
