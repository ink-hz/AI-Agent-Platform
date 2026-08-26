import type { WorkroomTask } from "../../workroomTypes";


const STATUS_LABELS: Record<WorkroomTask["status"], string> = {
  queued: "等待开始",
  running: "正在工作",
  waiting: "等待信息",
  completed: "已完成",
  failed: "未完成",
  timed_out: "已超时",
  unavailable: "暂不可用",
  cancelled: "已停止",
};


export function WorkroomTeamView({
  tasks,
  onSelectTask,
}: {
  tasks: WorkroomTask[];
  onSelectTask: (taskId: string) => void;
}) {
  return <div className="workroom-team">
    {tasks.map((task) => <button
      aria-label={`查看 ${task.agentLabel} 子会话`}
      className={`workroom-agent-card is-${task.status}`}
      key={task.taskId}
      onClick={() => onSelectTask(task.taskId)}
      type="button"
    >
      <span className="workroom-agent-mark" aria-hidden="true">{task.agentLabel.slice(0, 1)}</span>
      <span className="workroom-agent-copy">
        <strong>{task.agentLabel}</strong>
        <small>{STATUS_LABELS[task.status]}</small>
        <b>{task.objective}</b>
        <span>{task.lastUpdate ?? task.publicReason}</span>
      </span>
      <span className="workroom-agent-arrow" aria-hidden="true">›</span>
    </button>)}
  </div>;
}
