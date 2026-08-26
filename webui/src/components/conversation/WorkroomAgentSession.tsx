import type { WorkroomTask, WorkroomTimelineItem } from "../../workroomTypes";
import { WorkroomTimeline } from "./WorkroomTimeline";


export function WorkroomAgentSession({
  task,
  timeline,
  onClose,
}: {
  task: WorkroomTask;
  timeline: WorkroomTimelineItem[];
  onClose: () => void;
}) {
  return <aside className="workroom-session" aria-label={`${task.agentLabel} 子会话`}>
    <header>
      <div><strong>{task.agentLabel} 子会话</strong><span>只读记录</span></div>
      <button aria-label="关闭子会话" onClick={onClose} type="button">×</button>
    </header>
    <h4>{task.objective}</h4>
    <p>{task.publicReason}</p>
    <WorkroomTimeline timeline={timeline.filter((item) => item.taskId === task.taskId)} />
  </aside>;
}
