import type { ReactNode } from "react";

import { eventTargetPath, eventTimeLabel } from "../operations";
import type { EventSeverity, OperationalEvent } from "../types";
import { PlatformLink } from "./PlatformLink";


const SEVERITY: Record<EventSeverity, { icon: string; label: string }> = {
  critical: { icon: "!", label: "Critical" },
  attention: { icon: "△", label: "Attention" },
  info: { icon: "i", label: "Info" },
};

function sourceLabel(source: string): string {
  return source.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function EventContent({ event }: { event: OperationalEvent }) {
  const severity = SEVERITY[event.severity];
  return <>
    <div className="operational-event-head">
      <span className={`event-severity severity-${event.severity}`}>
        <i aria-hidden="true">{severity.icon}</i>{severity.label}
      </span>
      <time dateTime={event.occurred_at}>{eventTimeLabel(event)}</time>
    </div>
    <strong className="operational-event-title">{event.title}</strong>
    <p>{event.summary}</p>
    <div className="operational-event-meta">
      {event.agent_id && <span>Agent · {event.agent_id}</span>}
      <span>Source · {sourceLabel(event.source_kind)}</span>
    </div>
  </>;
}

export function OperationalEventItem({ event }: { event: OperationalEvent }) {
  const target = eventTargetPath(event);
  const content: ReactNode = <EventContent event={event} />;
  if (target) {
    return <PlatformLink className="operational-event-item is-linked" href={target}>{content}</PlatformLink>;
  }
  return <article className="operational-event-item">{content}</article>;
}
