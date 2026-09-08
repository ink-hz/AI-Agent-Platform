import { useCallback, useEffect, useRef, useState } from "react";

import type { ConversationAttachment } from "../../conversationTypes";
import type { HrApi } from "../../hrApi";
import type { HrR12Api } from "../../hrR12Api";
import type { HrContextVersion, HrPositionMaterialItem, HrPositionTaskKind, HrTaskRecord } from "../../hrR12Types";
import type { HrPositionDetail } from "../../hrTypes";
import { completeMutationRequest, retainMutationRequest } from "./hrMutationRequest";
import { HrPositionTaskMenu } from "./HrPositionTaskMenu";
import { HrTaskReferences } from "./HrTaskReferences";

const taskLabels: Record<string, string> = { jd: "JD", jr: "JR", talent_profile: "人才画像", sourcing_strategy: "搜寻策略", position_interview_plan: "面试方案", candidate_match: "候选人匹配", candidate_interview_plan: "候选人面试题", candidate_comparison: "候选人比较" };
const statusLabels: Record<string, string> = { accepted: "已受理", running: "执行中", completed: "已完成", failed: "执行失败" };
const running = (task: HrTaskRecord) => task.status === "accepted" || task.status === "running";

// The chat host supplies only a selected new-conversation position or a validated route.
export function useHrChatPosition({ positionId, conversationId, api, r12, readOnly, onOpenResults }: {
  positionId?: string;
  conversationId?: string;
  api: HrApi;
  r12: HrR12Api;
  readOnly: boolean;
  onOpenResults(): void;
}) {
  const [loaded, setLoaded] = useState<{
    positionId: string; detail: HrPositionDetail; context: HrContextVersion | null; materials: HrPositionMaterialItem[];
  } | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([]);
  const [tasks, setTasks] = useState<HrTaskRecord[]>([]);
  const [taskError, setTaskError] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [refreshGeneration, setRefreshGeneration] = useState(0);
  const [taskAttempt, setTaskAttempt] = useState(0);
  const mutation = useRef<AbortController | null>(null);
  const scope = `${positionId ?? ""}:${conversationId ?? ""}`;
  const activeScope = useRef(scope);
  activeScope.current = scope;
  const data = loaded?.positionId === positionId ? loaded : null;
  const refresh = useCallback(() => setRefreshGeneration((value) => value + 1), []);

  useEffect(() => {
    setSelectedMaterialIds([]); setNotice(null); setWorking(false);
    return () => { mutation.current?.abort(); mutation.current = null; };
  }, [scope]);

  useEffect(() => {
    setLoadState("loading");
    if (!positionId) { setLoaded(null); return; }
    const controller = new AbortController();
    void Promise.all([
      api.position(positionId, controller.signal),
      r12.context(positionId, controller.signal),
      r12.resources(positionId, controller.signal),
    ]).then(([detail, context, resources]) => {
      if (controller.signal.aborted) return;
      const materials = resources.materials.filter((item) => item.state === "ready" && item.downloadAvailable);
      setLoaded({ positionId, detail, context: context.current, materials });
      setSelectedMaterialIds((ids) => ids.filter((id) => materials.some((item) => item.attachmentId === id)));
      setLoadState("ready");
    }).catch(() => { if (!controller.signal.aborted) setLoadState("error"); });
    return () => controller.abort();
  }, [api, positionId, r12, refreshGeneration]);

  useEffect(() => {
    setTasks([]); setTaskError(false);
    if (!positionId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    let previous: HrTaskRecord[] = [];
    const poll = async () => {
      try {
        const next = await r12.activeTasks(positionId, controller.signal);
        if (controller.signal.aborted) return;
        const nextRunning = new Set(next.filter(running).map((task) => task.taskId));
        if (previous.some((task) => running(task) && !nextRunning.has(task.taskId))
          || (previous.length === 0 && next.some((task) => !running(task)))) refresh();
        previous = next;
        setTasks(next); setTaskError(false);
        if (next.some(running)) timer = window.setTimeout(() => void poll(), 2_000);
      } catch { if (!controller.signal.aborted) setTaskError(true); }
    };
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [positionId, r12, refresh, taskAttempt]);

  async function startTask(kind: HrPositionTaskKind) {
    if (!positionId || !data || loadState !== "ready" || readOnly || mutation.current) return;
    const controller = new AbortController(); mutation.current = controller;
    setWorking(true); setNotice(null);
    const input = {
      ...(data.context ? { contextVersionId: data.context.contextVersionId } : {}),
      materialIds: selectedMaterialIds, conversationId,
    };
    const operation = retainMutationRequest(`position-task:${positionId}:${kind}`, input);
    try {
      const started = await r12.startTask(positionId, kind, operation.requestId, input, controller.signal);
      if (controller.signal.aborted || activeScope.current !== scope) return;
      completeMutationRequest(operation.key);
      if (started.status !== "failed") setSelectedMaterialIds([]);
      setTasks((items) => [started, ...items.filter((item) => item.taskId !== started.taskId)]);
      setTaskError(false);
      if (running(started)) setTaskAttempt((value) => value + 1);
      else refresh();
    } catch {
      if (!controller.signal.aborted && activeScope.current === scope) setNotice("岗位任务未启动，请重试。");
    } finally {
      if (mutation.current === controller) { mutation.current = null; setWorking(false); }
    }
  }

  async function changeMaterial(attachment: ConversationAttachment, active: boolean) {
    if (!positionId || readOnly || mutation.current) return;
    const controller = new AbortController(); mutation.current = controller;
    setWorking(true); setNotice(null);
    const operation = retainMutationRequest(`position-material:${positionId}:${active ? "promote" : "remove"}`, { attachmentId: attachment.attachmentId });
    try {
      if (active) await api.promoteMaterial(positionId, attachment.attachmentId, operation.requestId);
      else await api.removeMaterial(positionId, attachment.attachmentId, operation.requestId);
      completeMutationRequest(operation.key);
      if (!controller.signal.aborted && activeScope.current === scope) refresh();
    } catch {
      if (!controller.signal.aborted && activeScope.current === scope) setNotice("岗位材料操作未完成，请重试。");
    } finally {
      if (mutation.current === controller) { mutation.current = null; setWorking(false); }
    }
  }

  function confirmContext(context: HrContextVersion) {
    setLoaded((current) => current && current.positionId === positionId ? { ...current, context } : current);
    refresh();
  }

  const menu = (disabled: boolean) => positionId ? <HrPositionTaskMenu
    key={scope}
    disabled={disabled || readOnly || working || !data || loadState !== "ready"}
    materials={data?.materials ?? []}
    selectedMaterialIds={selectedMaterialIds}
    onSelectedMaterialIdsChange={setSelectedMaterialIds}
    onStart={(kind) => void startTask(kind)}
  /> : null;
  const status = positionId ? <>
    {(loadState === "error" || taskError || notice) && <section className="hr-position-task-status" role="status">
      {loadState === "error" ? <p>岗位资料或材料暂时无法读取，岗位任务已暂停。<button type="button" onClick={refresh}>重新读取</button></p> : null}
      {taskError && <p>任务状态暂时不可用。<button type="button" onClick={() => setTaskAttempt((value) => value + 1)}>刷新任务状态</button></p>}
      {notice && <p>{notice}</p>}
    </section>}
    {data && tasks.length > 0 && <section aria-label="岗位任务状态" className="hr-position-task-status" aria-live="polite"><ul>{tasks.map((task) => <li key={task.taskId}>
      <span>{taskLabels[task.taskKind] ?? task.taskKind}：{statusLabels[task.status] ?? task.status}{task.error ? ` · ${task.error}` : ""}</span>
      {task.status === "completed" && <button type="button" onClick={onOpenResults}>查看结果</button>}
      <HrTaskReferences references={task.references ?? []} />
    </li>)}</ul></section>}
  </> : null;

  return { detail: data?.detail, context: data?.context, working, menu, status, refreshGeneration, changeMaterial, confirmContext };
}
