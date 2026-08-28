import { useEffect, useState } from "react";

import type { WorkroomAction } from "../../workroomTypes";


export type ActionMutation = (
  actionId: string,
  actionDigest: string,
) => Promise<WorkroomAction>;

export type ActionRejection = (actionId: string) => Promise<WorkroomAction>;


function statusCopy(action: WorkroomAction): { label: string; detail: string } {
  if (action.status === "rejected") return { label: "已拒绝", detail: "该操作不会执行。" };
  if (action.status === "expired") return { label: "已过期", detail: "确认期限已结束，该操作不会执行。" };
  if (action.status === "superseded") {
    return { label: "已失效", detail: "该操作已被更新，不能再执行。请以最新确认卡为准。" };
  }
  if (action.status === "pending") return { label: "需要确认", detail: "确认前不会执行。" };
  return ({
    not_started: { label: "已确认", detail: "平台已记录确认，正在准备执行。" },
    queued: { label: "等待执行", detail: "平台已记录确认，操作正在排队。" },
    running: { label: "正在执行", detail: "专业 Agent 正在执行已确认的操作。" },
    completed: { label: "已完成", detail: "已确认的操作执行完成。" },
    failed: { label: "执行失败", detail: "操作未完成，平台不会自动重复执行。" },
  } as const)[action.executionStatus];
}


function readableTime(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(date);
}


export function ActionCard({
  action,
  onConfirm,
  onReject,
}: {
  action: WorkroomAction;
  onConfirm?: ActionMutation;
  onReject?: ActionRejection;
}) {
  const [current, setCurrent] = useState(action);
  const [pending, setPending] = useState<"confirm" | "reject" | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setCurrent((selected) => {
      if (selected.actionId !== action.actionId || selected.actionDigest !== action.actionDigest) {
        return action;
      }
      // A successful mutation can arrive before the next projected SSE snapshot.
      // Never let that older pending snapshot visually undo an owner decision.
      if (selected.status !== "pending" && action.status === "pending") return selected;
      return action;
    });
    setFailed(false);
  }, [action]);
  const copy = statusCopy(current);
  const confirmedAt = readableTime(current.confirmedAt);
  const expiresAt = readableTime(current.expiresAt);

  const mutate = async (kind: "confirm" | "reject") => {
    const operation = kind === "confirm" ? onConfirm : onReject;
    if (!operation || pending) return;
    setPending(kind);
    setFailed(false);
    try {
      const updated = kind === "confirm"
        ? await (operation as ActionMutation)(current.actionId, current.actionDigest)
        : await (operation as ActionRejection)(current.actionId);
      setCurrent(updated);
    } catch {
      setFailed(true);
    } finally {
      setPending(null);
    }
  };

  return <article className={`workroom-action-card is-${current.status} is-execution-${current.executionStatus}`}>
    <header>
      <span aria-hidden="true" className="workroom-action-mark">✓</span>
      <div>
        <small>需要你决定的操作</small>
        <h4>{current.summary}</h4>
      </div>
      <strong>{copy.label}</strong>
    </header>
    <p>{current.impact}</p>
    <div className="workroom-action-state" role="status">
      <span>{copy.detail}</span>
      {current.status === "pending" && expiresAt && <small>确认有效期至 {expiresAt}</small>}
      {current.status === "confirmed" && current.confirmedBy && <small>{
        `已由${current.confirmedBy}确认${confirmedAt ? ` · ${confirmedAt}` : ""}`
      }</small>}
    </div>
    {current.status === "pending" && onConfirm && onReject && <div className="workroom-action-buttons">
      <button
        disabled={pending !== null}
        onClick={() => void mutate("reject")}
        type="button"
      >{pending === "reject" ? "正在拒绝" : "拒绝"}</button>
      <button
        className="is-primary"
        disabled={pending !== null}
        onClick={() => void mutate("confirm")}
        type="button"
      >{pending === "confirm" ? "正在确认" : "确认执行"}</button>
    </div>}
    {current.status === "pending" && (!onConfirm || !onReject) && <p className="workroom-action-readonly">
      当前为只读状态，暂时不能确认或拒绝。
    </p>}
    {failed && <p className="workroom-action-error" role="alert">操作暂未提交，请重试。</p>}
  </article>;
}
