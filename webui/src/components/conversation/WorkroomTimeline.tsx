import type { WorkroomTimelineItem } from "../../workroomTypes";


export function WorkroomTimeline({ timeline }: { timeline: WorkroomTimelineItem[] }) {
  return <ol className="workroom-timeline">
    {timeline.map((item) => <li className={`is-${item.sourceKind}`} key={item.eventId}>
      <div>
        <strong>{item.sourceLabel}</strong>
        <time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time>
      </div>
      <p>{item.text}</p>
      {item.interrupted && <small>本次思考中断</small>}
    </li>)}
  </ol>;
}
