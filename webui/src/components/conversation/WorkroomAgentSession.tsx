import { useEffect, useState } from "react";

import type { ConversationTaskDetail } from "../../conversationTypes";
import type { WorkroomTask, WorkroomTimelineItem } from "../../workroomTypes";
import { WorkroomTimeline } from "./WorkroomTimeline";


function childEventLabel(kind: string): string {
  return ({
    thinking: "思考摘要",
    thinking_summary: "思考摘要",
    work: "工作进展",
    progress: "工作进展",
    message: "Agent 消息",
    artifact: "交付成果",
    result: "任务结果",
    question: "Agent 提问",
  } as Record<string, string>)[kind] ?? "Agent 更新";
}


export function WorkroomAgentSession({
  task,
  timeline,
  onClose,
  loadTaskDetail,
  turnId,
}: {
  task: WorkroomTask;
  timeline: WorkroomTimelineItem[];
  onClose: () => void;
  loadTaskDetail?: (turnId: string, taskId: string, signal: AbortSignal) => Promise<ConversationTaskDetail>;
  turnId: string;
}) {
  const [detail, setDetail] = useState<ConversationTaskDetail | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!loadTaskDetail) return;
    const controller = new AbortController();
    setDetail(null); setFailed(false);
    void loadTaskDetail(turnId, task.taskId, controller.signal).then((value) => {
      if (!controller.signal.aborted) setDetail(value);
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [loadTaskDetail, task.taskId, turnId]);
  return <aside className="workroom-session" aria-label={`${task.agentLabel} 子会话`}>
    <header>
      <div><strong>{task.agentLabel} 子会话</strong><span>只读记录</span></div>
      <button aria-label="关闭子会话" onClick={onClose} type="button">×</button>
    </header>
    <h4>{task.objective}</h4>
    <p>{task.publicReason}</p>
    {loadTaskDetail && !detail && !failed && <p className="workroom-session-state" role="status">正在读取真实子会话…</p>}
    {failed && <p className="workroom-session-state is-error" role="alert">子会话暂时无法读取，公开协作记录仍可查看。</p>}
    {detail && <ol className="workroom-session-messages" aria-label="子会话消息">
      {detail.messages.map((message) => <li className={`is-${message.sender}`} key={`${message.sender}-${message.seq}`}>
        <div><strong>{message.sender === "brain" ? "Agent 大脑" : message.sender === "agent" ? task.agentLabel : "Platform"}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time></div>
        <p>{message.text}</p>
      </li>)}
    </ol>}
    {detail && detail.events.length > 0 && <ol className="workroom-session-events" aria-label="子会话事件">
      {detail.events.map((event) => <li key={`${event.kind}-${event.seq}`}>
        <div><strong>{childEventLabel(event.kind)}</strong><time dateTime={event.created_at}>{new Date(event.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time></div>
        <p>{event.summary}</p>
      </li>)}
    </ol>}
    {(!loadTaskDetail || failed) && <WorkroomTimeline timeline={timeline.filter((item) => item.taskId === task.taskId)} />}
  </aside>;
}
