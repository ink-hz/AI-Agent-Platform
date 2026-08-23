import { useEffect, useRef, useState } from "react";

import type { Account } from "../auth";
import {
  BrainApiError, cancelMission, fetchMission, reconnectDelay, streamMissionEvents,
  type MissionStreamOptions,
} from "../brainApi";
import { TERMINAL_MISSION_STATUSES, type Mission, type MissionEvent, type MissionStatus } from "../brainTypes";
import { MissionTimeline } from "../components/mission/MissionTimeline";
import { PlatformLink } from "../components/PlatformLink";
import { platformPath } from "../auth";


export interface MissionPageClient {
  fetchMission(missionId: string, signal?: AbortSignal): Promise<Mission>;
  cancelMission(missionId: string, csrfToken: string, signal?: AbortSignal): Promise<Mission>;
  streamMissionEvents(missionId: string, options: MissionStreamOptions): Promise<void>;
  reconnectDelay(signal: AbortSignal): Promise<void>;
}

const DEFAULT_CLIENT: MissionPageClient = { fetchMission, cancelMission, streamMissionEvents, reconnectDelay };

const TERMINAL_EVENT_STATUSES: Readonly<Record<string, readonly MissionStatus[]>> = {
  "mission.completed": ["completed"],
  "mission.partially_completed": ["partially_completed"],
  "mission.failed": ["failed", "partially_completed"],
  "mission.cancelled": ["cancelled"],
  "mission.interrupted": ["interrupted", "partially_completed"],
};

function mergeEvent(events: MissionEvent[], next: MissionEvent): MissionEvent[] {
  const existing = events.findIndex((event) => event.seq === next.seq);
  if (existing >= 0) {
    if (events[existing].event_id === next.event_id) return events;
    return events;
  }
  return [...events, next].sort((left, right) => left.seq - right.seq);
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    planning: "Agent 大脑正在分析", delegated: "专业 Agent 正在执行", synthesizing: "Agent 大脑正在整理",
    completed: "任务已完成", partially_completed: "任务部分完成", failed: "任务未完成",
    cancelled: "任务已停止", interrupted: "任务已中断",
  };
  return labels[status] ?? "任务处理中";
}

export function MissionPage({ missionId, account, client = DEFAULT_CLIENT }: {
  missionId: string;
  account: Account;
  client?: MissionPageClient;
}) {
  const [mission, setMission] = useState<Mission | null>(null);
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [connection, setConnection] = useState<"connecting" | "live" | "offline">("connecting");
  const [loadFailure, setLoadFailure] = useState(false);
  const [cancelFailure, setCancelFailure] = useState(false);
  const [authenticationExpired, setAuthenticationExpired] = useState(false);
  const cancelController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let cursor = 0;
    let terminalEventStatuses: readonly MissionStatus[] = [];
    setMission(null);
    setEvents([]);
    setConnection("connecting");
    setLoadFailure(false);
    setCancelFailure(false);
    setAuthenticationExpired(false);
    const run = async () => {
      while (!controller.signal.aborted) {
        let snapshot: Mission;
        try {
          snapshot = await client.fetchMission(missionId, controller.signal);
          if (controller.signal.aborted) return;
          setMission(snapshot);
          setLoadFailure(false);
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof BrainApiError && error.status === 401) {
            setAuthenticationExpired(true);
            setConnection("offline");
            return;
          }
          setLoadFailure(true);
          setConnection("offline");
          await client.reconnectDelay(controller.signal);
          continue;
        }
        setConnection(cursor === 0 ? "connecting" : "live");
        try {
          await client.streamMissionEvents(missionId, {
            after: cursor,
            signal: controller.signal,
            onEvent: (event) => {
              if (controller.signal.aborted || event.mission_id !== missionId || event.seq <= cursor) return;
              cursor = event.seq;
              terminalEventStatuses = TERMINAL_EVENT_STATUSES[event.event_type] ?? terminalEventStatuses;
              setEvents((current) => mergeEvent(current, event));
              setConnection("live");
            },
          });
          const verified = await client.fetchMission(missionId, controller.signal);
          if (controller.signal.aborted) return;
          setMission(verified);
          setLoadFailure(false);
          if (TERMINAL_MISSION_STATUSES.has(verified.status) && terminalEventStatuses.includes(verified.status)) {
            setConnection("live");
            return;
          }
          setConnection("offline");
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof BrainApiError && error.status === 401) {
            setAuthenticationExpired(true);
            setConnection("offline");
            return;
          }
          setConnection("offline");
        }
        if (!controller.signal.aborted) await client.reconnectDelay(controller.signal);
      }
    };
    void run();
    return () => {
      controller.abort();
      cancelController.current?.abort();
    };
  }, [client, missionId]);

  const stop = async () => {
    setCancelFailure(false);
    const controller = new AbortController();
    cancelController.current?.abort();
    cancelController.current = controller;
    try {
      setMission(await client.cancelMission(missionId, account.csrf_token, controller.signal));
    } catch {
      if (!controller.signal.aborted) setCancelFailure(true);
    } finally {
      if (cancelController.current === controller) cancelController.current = null;
    }
  };

  const missionPath = `/missions/${encodeURIComponent(missionId)}`;
  const loginPath = `/login?return_path=${encodeURIComponent(missionPath)}`;

  if (!mission && authenticationExpired) return <section className="mission-load-state" role="alert"><h1>企业登录已失效</h1><p>重新登录后可继续读取这项已保存的任务。</p><a href={platformPath(loginPath)}>重新登录</a></section>;
  if (!mission && loadFailure) return <section className="mission-load-state" role="alert"><h1>暂时无法读取任务</h1><p>任务内容仍安全保存在平台，请稍后刷新。</p></section>;
  if (!mission) return <section className="mission-load-state" aria-live="polite"><h1>正在打开任务</h1><p>正在读取已保存的任务与协作事件。</p></section>;
  const terminal = TERMINAL_MISSION_STATUSES.has(mission.status);
  return <div className="mission-page">
    <PlatformLink className="back-link" href="/">← 返回 Agent 大脑</PlatformLink>
    <header className="mission-header">
      <div><p>{mission.mode === "direct_agent" ? "专业 Agent 任务" : "Agent 大脑任务"}</p><h1>{mission.prompt}</h1><span>{statusLabel(mission.status)}</span></div>
      {!terminal && <button className="mission-cancel" disabled={mission.cancel_requested || account.hard_stale_read_only} onClick={() => void stop()} type="button">
        {mission.cancel_requested ? "正在停止" : "停止任务"}
      </button>}
    </header>
    {connection === "offline" && <aside className="mission-connection is-offline" role="status">
      <strong>连接暂时中断</strong><span>任务仍会保留，页面正在从最后一个已接收事件恢复。</span>
    </aside>}
    {connection === "connecting" && <aside className="mission-connection" role="status">正在连接任务进度…</aside>}
    {authenticationExpired && <aside className="mission-connection is-offline" role="alert"><strong>企业登录已失效</strong><a href={platformPath(loginPath)}>重新登录以继续读取任务</a></aside>}
    {cancelFailure && <aside className="mission-connection is-offline" role="alert">停止请求暂未送达，请稍后重试。</aside>}
    <section className="mission-request"><span>你的需求</span><p>{mission.prompt}</p></section>
    <MissionTimeline
      directAgentId={mission.direct_agent_id}
      events={events}
      missionMode={mission.mode}
    />
    <footer className="mission-foot">
      <a className="mission-permalink" href={platformPath(`/missions/${encodeURIComponent(mission.mission_id)}`)}>此任务的固定链接</a>
      <span>页面断线不会创建新任务。</span>
    </footer>
  </div>;
}
