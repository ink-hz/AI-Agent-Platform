import { MessageMarkdown } from "../MessageMarkdown";
import type { MissionEvent } from "../../brainTypes";


interface EventPresentation {
  title: string;
  actor: string;
  tone: "neutral" | "working" | "result" | "failure";
  markdown: boolean;
}

const PRESENTATIONS: Record<string, EventPresentation> = {
  "mission.started": { title: "需求已接收", actor: "你", tone: "neutral", markdown: false },
  "brain.responding": { title: "分析需求", actor: "Agent 大脑", tone: "working", markdown: false },
  "plan.created": { title: "任务规划", actor: "Agent 大脑", tone: "working", markdown: false },
  "plan.revised": { title: "计划已更新", actor: "Agent 大脑", tone: "working", markdown: false },
  "task.dispatched": { title: "已交付专业 Agent", actor: "Agent 大脑", tone: "working", markdown: false },
  "agent.accepted": { title: "专业 Agent 已接收", actor: "专业 Agent", tone: "working", markdown: false },
  "agent.progress": { title: "执行进度", actor: "专业 Agent", tone: "working", markdown: false },
  "agent.result": { title: "专业结果", actor: "专业 Agent", tone: "result", markdown: true },
  "task.reviewed": { title: "结果复核", actor: "Agent 大脑", tone: "result", markdown: true },
  "task.revision_requested": { title: "补充要求", actor: "Agent 大脑", tone: "working", markdown: false },
  "synthesis.started": { title: "整理交付", actor: "Agent 大脑", tone: "working", markdown: false },
  "mission.partially_completed": { title: "部分交付", actor: "Agent 大脑", tone: "failure", markdown: true },
  "mission.completed": { title: "最终交付", actor: "Agent 大脑", tone: "result", markdown: true },
  "mission.failed": { title: "任务未完成", actor: "Agent 大脑", tone: "failure", markdown: false },
  "mission.interrupted": { title: "执行已中断", actor: "Agent 大脑", tone: "failure", markdown: false },
  "mission.cancelled": { title: "任务已停止", actor: "Agent 大脑", tone: "failure", markdown: false },
};

function textValue(eventType: string, payload: Record<string, unknown>): string {
  const keys = eventType === "plan.created"
    ? ["objective", "text", "summary"]
    : eventType === "agent.result" || eventType.startsWith("mission.")
      ? ["result", "summary", "text", "partial_result"]
      : ["text", "summary", "result", "objective"];
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "状态已更新";
}

function progressValue(payload: Record<string, unknown>): number | null {
  const progress = payload.progress;
  if (typeof progress === "number" && Number.isFinite(progress) && progress >= 0 && progress <= 1) {
    return Math.round(progress * 100);
  }
  const current = payload.current;
  const total = payload.total;
  if (typeof current === "number" && typeof total === "number" && total > 0 && current >= 0 && current <= total) {
    return Math.round((current / total) * 100);
  }
  return null;
}

function formatEventTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

export function MissionTimeline({ events }: { events: MissionEvent[] }) {
  const ordered = [...new Map(events.map((event) => [event.seq, event])).values()]
    .sort((left, right) => left.seq - right.seq);
  if (!ordered.length) {
    return <section className="mission-timeline-empty" aria-live="polite">等待第一个任务事件…</section>;
  }
  return <ol className="mission-timeline" aria-label="任务协作过程" aria-live="polite" aria-relevant="additions text">
    {ordered.map((event) => {
      const presentation = PRESENTATIONS[event.event_type] ?? {
        title: "任务更新", actor: "Agent 大脑", tone: "neutral", markdown: false,
      };
      const text = textValue(event.event_type, event.payload);
      const agentId = typeof event.payload.agent_id === "string"
        ? event.payload.agent_id
        : typeof event.payload.selected_agent_id === "string" ? event.payload.selected_agent_id : null;
      const rationale = typeof event.payload.rationale_summary === "string" ? event.payload.rationale_summary : null;
      const progress = progressValue(event.payload);
      return <li className={`mission-event is-${presentation.tone}`} data-seq={event.seq} key={event.event_id}>
        <div className="mission-event-marker" aria-hidden="true" />
        <article>
          <header>
            <div><span>{presentation.actor}</span><h2 className="mission-event-title">{presentation.title}</h2></div>
            <time dateTime={event.created_at}>{formatEventTime(event.created_at)}</time>
          </header>
          {agentId && <p className="mission-event-agent">{agentId}</p>}
          {presentation.markdown ? <MessageMarkdown content={text} /> : <p className="mission-event-copy">{text}</p>}
          {rationale && <p className="mission-event-rationale">{rationale}</p>}
          {progress !== null && <div className="mission-progress" aria-label={`已完成 ${progress}%`} aria-valuemax={100} aria-valuemin={0} aria-valuenow={progress} role="progressbar">
            <div aria-hidden="true"><span style={{ width: `${progress}%` }} /></div><b>{progress}%</b>
          </div>}
        </article>
      </li>;
    })}
  </ol>;
}
