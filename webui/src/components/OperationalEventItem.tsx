import type { ReactNode } from "react";

import { eventTargetPath, eventTimeLabel } from "../operations";
import type { EventSeverity, OperationalEvent } from "../types";
import { PlatformLink } from "./PlatformLink";


type EventTone = EventSeverity | "recovery";

const SEVERITY: Record<EventTone, { icon: string; label: string }> = {
  critical: { icon: "!", label: "严重" },
  attention: { icon: "△", label: "需关注" },
  info: { icon: "i", label: "信息" },
  recovery: { icon: "✓", label: "已恢复" },
};

function EventContent({ event }: { event: OperationalEvent }) {
  const tone: EventTone = event.event_family === "recovery" ? "recovery" : event.severity;
  const severity = SEVERITY[tone];
  return <>
    <div className="operational-event-head">
      <span className={`event-severity event-severity-${tone}`}>
        <i aria-hidden="true">{severity.icon}</i>{severity.label}
      </span>
      <time dateTime={event.occurred_at}>{eventTimeLabel(event)}</time>
    </div>
    <strong className="operational-event-title">{event.title}</strong>
    <p>{event.summary}</p>
    <div className="operational-event-meta">
      {event.agent_id && <span>Agent · {event.agent_id}</span>}
      <span>来源 · {event.source_kind}</span>
    </div>
  </>;
}

export function OperationalEventItem({ event }: { event: OperationalEvent }) {
  const target = eventTargetPath(event);
  const tone: EventTone = event.event_family === "recovery" ? "recovery" : event.severity;
  const className = `operational-event-item event-severity-${tone} event-visibility-${event.agent_visibility}`;
  const content: ReactNode = <EventContent event={event} />;
  if (target) {
    return <PlatformLink className={`${className} is-linked`} href={target}>{content}</PlatformLink>;
  }
  return <article className={className}>{content}</article>;
}
