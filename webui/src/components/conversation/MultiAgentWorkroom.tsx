import { useEffect, useRef, useState } from "react";

import type { WorkroomTurn } from "../../workroomTypes";
import type { ConversationTaskDetail } from "../../conversationTypes";
import { WorkroomAgentSession } from "./WorkroomAgentSession";
import { WorkroomDeliverables } from "./WorkroomDeliverables";
import { WorkroomTeamView } from "./WorkroomTeamView";
import { WorkroomTimeline } from "./WorkroomTimeline";


type WorkroomTab = "team" | "timeline" | "deliverables";


const STATUS_LABELS: Record<WorkroomTurn["status"], string> = {
  running: "团队正在协作",
  completed: "协作已完成",
  partially_completed: "已完成部分交付",
  failed: "协作未完成",
  cancelled: "协作已停止",
};


export function MultiAgentWorkroom({
  workroom,
  loadTaskDetail,
}: {
  workroom: WorkroomTurn;
  loadTaskDetail?: (turnId: string, taskId: string, signal: AbortSignal) => Promise<ConversationTaskDetail>;
}) {
  const [expanded, setExpanded] = useState(workroom.defaultExpanded);
  const [tab, setTab] = useState<WorkroomTab>("team");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const previousTurn = useRef(workroom.turnId);
  useEffect(() => {
    if (previousTurn.current !== workroom.turnId) {
      previousTurn.current = workroom.turnId;
      setExpanded(workroom.defaultExpanded);
      setTab("team");
      setSelectedTaskId(null);
    }
  }, [workroom.defaultExpanded, workroom.turnId]);
  const selectedTask = workroom.tasks.find((task) => task.taskId === selectedTaskId) ?? null;
  const idPrefix = `workroom-${workroom.turnId}`;
  const tabs: Array<{ id: WorkroomTab; label: string; count?: number }> = [
    { id: "team", label: "团队", count: workroom.tasks.length },
    { id: "timeline", label: "协作记录" },
    { id: "deliverables", label: "交付成果", count: workroom.deliverables.length },
  ];
  return <section className={`multi-agent-workroom is-${workroom.status}`}>
    <details open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>
        <span><strong>Agent 协作室</strong><small>{STATUS_LABELS[workroom.status]}</small></span>
        <b>{workroom.tasks.length} 位专业 Agent</b>
      </summary>
      <div className="workroom-body">
        <div className="workroom-tabs" role="tablist" aria-label="协作室视图">
          {tabs.map((item) => <button
            aria-label={item.label}
            aria-controls={`${idPrefix}-panel-${item.id}`}
            aria-selected={tab === item.id}
            id={`${idPrefix}-tab-${item.id}`}
            key={item.id}
            onClick={() => setTab(item.id)}
            role="tab"
            type="button"
          >{item.label}{item.count !== undefined && <span>{item.count}</span>}</button>)}
        </div>
        <div
          aria-labelledby={`${idPrefix}-tab-${tab}`}
          className="workroom-panel"
          id={`${idPrefix}-panel-${tab}`}
          role="tabpanel"
        >
          {tab === "team" && <WorkroomTeamView tasks={workroom.tasks} onSelectTask={setSelectedTaskId} />}
          {tab === "timeline" && <WorkroomTimeline timeline={workroom.timeline} />}
          {tab === "deliverables" && <WorkroomDeliverables deliverables={workroom.deliverables} />}
        </div>
        {selectedTask && <WorkroomAgentSession
          loadTaskDetail={loadTaskDetail}
          onClose={() => setSelectedTaskId(null)}
          task={selectedTask}
          timeline={workroom.timeline}
          turnId={workroom.turnId}
        />}
      </div>
    </details>
  </section>;
}
