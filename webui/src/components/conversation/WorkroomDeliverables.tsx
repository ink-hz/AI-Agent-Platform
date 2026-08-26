import { platformPath } from "../../auth";
import type { WorkroomDeliverable } from "../../workroomTypes";


export function WorkroomDeliverables({ deliverables }: { deliverables: WorkroomDeliverable[] }) {
  if (deliverables.length === 0) {
    return <p className="workroom-empty">专业 Agent 尚未提交附件或成果文件。</p>;
  }
  return <ul className="workroom-deliverables">
    {deliverables.map((item) => <li key={`${item.eventId}:${item.attachmentRef}`}>
      <a href={platformPath(`/api/v1/attachments/${encodeURIComponent(item.attachmentRef)}`)}>
        <span aria-hidden="true">↗</span>
        <strong>{item.label}</strong>
      </a>
    </li>)}
  </ul>;
}
