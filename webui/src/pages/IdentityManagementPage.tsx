import { useEffect, useState } from "react";

import {
  DirectoryUnavailable,
  ManagementMutationIndeterminate,
  PermissionDenied,
  PlatformApiError,
  changeAdministrator,
  createAdministratorMutation,
  type Account,
  type AdministratorMutation,
  type ManagedUser,
} from "../auth";
import {
  clearPendingAdministrator,
  loadPendingAdministrator,
  storeAdministratorIntegrityFailure,
  storeConfirmedAdministratorRefresh,
  storeInflightAdministrator,
  storePendingAdministratorReplay,
  type PendingAdministratorState,
} from "../pendingAdministrator";
import { listAdministratorUsers, type AdministratorUser } from "../administratorDirectory";
import { AdministratorSearch } from "../components/AdministratorSearch";
import { platformPath } from "../auth";


function failureMessage(error: unknown): string {
  if (error instanceof DirectoryUnavailable || (error instanceof Error && error.message.includes("503"))) {
    return "审计或目录服务暂不可用，未执行任何变更。";
  }
  if (error instanceof PermissionDenied) return "当前账号无权管理身份。";
  if (error instanceof Error && error.message.includes("409")) return "用户状态已变化，请刷新后重试。";
  return "暂时无法读取身份数据。";
}


function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


function provesAdministratorMutationNotApplied(error: unknown): boolean {
  if (error instanceof PlatformApiError && error.status >= 400 && error.status < 500) {
    return true;
  }
  if (!(error instanceof DirectoryUnavailable) || !isObject(error.detail)) return false;
  const detail = error.detail.detail;
  return detail === "fresh directory required" || detail === "required audit unavailable";
}


function leavesAdministratorMutationOutcomeUncertain(error: unknown): boolean {
  return error instanceof ManagementMutationIndeterminate
    || error instanceof TypeError
    || (error instanceof PlatformApiError && error.status >= 500);
}


export function IdentityManagementPage({ account }: { account: Account }) {
  if (account.role !== "platform_owner" && account.role !== "platform_admin") {
    return <section className="permission-state" role="alert"><h1>无权访问</h1><p>请联系苍渊。</p></section>;
  }
  return <AdministratorManagement key={`${account.internal_user_id}:${account.role}`} account={account} />;
}

function AdministratorManagement({ account }: { account: Account }) {
  const [users, setUsers] = useState<AdministratorUser[]>([]);
  const [adding, setAdding] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingAdministratorState, setPendingAdministratorState] = useState<PendingAdministratorState>(() => (
    account.role === "platform_owner"
      ? loadPendingAdministrator(account.internal_user_id)
      : { kind: "none" }
  ));
  const pendingAdministrator = pendingAdministratorState.kind === "pending_replay"
    ? pendingAdministratorState.operation
    : null;
  const inflightAdministrator = pendingAdministratorState.kind === "inflight_no_replay"
    ? pendingAdministratorState.operation
    : null;
  const confirmedAdministrator = pendingAdministratorState.kind === "confirmed_needs_refresh"
    ? pendingAdministratorState.operation
    : null;
  const administratorMutationBlocked = pendingAdministratorState.kind !== "none";
  const refreshUsers = async () => {
    const refreshed = await listAdministratorUsers();
    setUsers(refreshed);
    setLoaded(true);
    return refreshed;
  };
  const load = async () => {
    try {
      const refreshed = await refreshUsers();
      if (pendingAdministratorState.kind === "pending_replay") {
        setMessage("管理员变更结果仍未知；已刷新当前角色，请使用同一请求重试确认。");
      } else if (pendingAdministratorState.kind === "inflight_no_replay") {
        setMessage("管理员变更处于不可重放状态；当前角色仅供参考，无法证明该请求已终态完成，请人工核查治理审计。");
      } else if (pendingAdministratorState.kind === "confirmed_needs_refresh") {
        if (matchesAdministratorOutcome(refreshed, pendingAdministratorState.operation)) {
          if (clearAdministratorState(pendingAdministratorState)) {
            setMessage("变更已确认，当前角色已刷新。");
          } else {
            setMessage("管理员变更已由服务端确认，但本地待处理状态无法清除；请手动核查。");
          }
        } else {
          setMessage("管理员变更已由服务端确认，但刷新后的角色与预期不一致；请手动核查。");
        }
      } else if (pendingAdministratorState.kind === "integrity_failure") {
        setMessage("无法验证待处理的管理员操作；已停止新的管理员变更，请手动核查。");
      }
    } catch (error) {
      if (pendingAdministratorState.kind === "pending_replay") {
        setMessage("管理员变更结果仍未知；当前角色刷新失败，请使用同一请求重试确认。");
      } else if (pendingAdministratorState.kind === "inflight_no_replay") {
        setMessage("管理员变更处于不可重放状态；当前角色刷新失败，请人工核查治理审计。");
      } else if (pendingAdministratorState.kind === "confirmed_needs_refresh") {
        setMessage("管理员变更已由服务端确认，但当前角色刷新失败。");
      } else if (pendingAdministratorState.kind === "integrity_failure") {
        setMessage("无法验证待处理的管理员操作；已停止新的管理员变更，请手动核查。");
      } else {
        setMessage(failureMessage(error));
      }
    }
  };
  useEffect(() => { void load(); }, []);
  const expectedAdministratorRole = (operation: AdministratorMutation) => (
    operation.revoke ? "member" : "platform_admin"
  );
  const matchesAdministratorOutcome = (
    refreshed: ManagedUser[], operation: AdministratorMutation,
  ) => operation.revoke
    ? !refreshed.some(user => user.internal_user_id === operation.targetInternalUserId)
    : refreshed.some(user => user.internal_user_id === operation.targetInternalUserId
      && user.role === expectedAdministratorRole(operation));
  const unknownAdministratorMessage = (refreshed: boolean) => refreshed
    ? "管理员变更结果仍未知；已刷新当前角色，请使用同一请求重试确认。"
    : "管理员变更结果仍未知；当前角色刷新失败，请使用同一请求重试确认。";
  const refreshUnknownAdministrator = async () => {
    try {
      await refreshUsers();
      return true;
    } catch {
      return false;
    }
  };
  const clearAdministratorState = (retainedState: PendingAdministratorState) => {
    if (!clearPendingAdministrator(account.internal_user_id)) {
      setPendingAdministratorState(retainedState);
      return false;
    }
    setPendingAdministratorState({ kind: "none" });
    return true;
  };
  const beginInflightAdministrator = (operation: AdministratorMutation) => {
    if (!storeInflightAdministrator(account.internal_user_id, operation)) {
      setPendingAdministratorState({ kind: "integrity_failure" });
      setMessage("无法保存待处理的管理员操作；已停止新的管理员变更，请手动核查。");
      return false;
    }
    setPendingAdministratorState({ kind: "inflight_no_replay", operation });
    return true;
  };
  const retainPendingAdministratorReplay = (operation: AdministratorMutation) => {
    if (!storePendingAdministratorReplay(account.internal_user_id, operation)) {
      setPendingAdministratorState({ kind: "inflight_no_replay", operation });
      setMessage("无法保存可重试的管理员操作；操作保持不可重放，请手动核查。");
      return false;
    }
    setPendingAdministratorState({ kind: "pending_replay", operation });
    return true;
  };
  const failAdministratorIntegrity = (operation: AdministratorMutation, message: string) => {
    if (storeAdministratorIntegrityFailure(account.internal_user_id)) {
      setPendingAdministratorState({ kind: "integrity_failure" });
    } else {
      setPendingAdministratorState({ kind: "inflight_no_replay", operation });
    }
    setMessage(message);
  };
  const retainConfirmedAdministrator = (operation: AdministratorMutation) => {
    if (!storeConfirmedAdministratorRefresh(account.internal_user_id, operation)) {
      setPendingAdministratorState({ kind: "inflight_no_replay", operation });
      setMessage("管理员变更已由服务端确认，但本地确认状态无法保存；操作保持不可重放，请手动核查。");
      return false;
    }
    setPendingAdministratorState({ kind: "confirmed_needs_refresh", operation });
    return true;
  };
  const finishConfirmedAdministrator = async (
    operation: AdministratorMutation,
    reconciled: boolean,
  ) => {
    if (!retainConfirmedAdministrator(operation)) return;
    try {
      const refreshed = await refreshUsers();
      if (!matchesAdministratorOutcome(refreshed, operation)) {
        setMessage("管理员变更已由服务端确认，但刷新后的角色与预期不一致；请手动核查。");
        return;
      }
      if (!clearAdministratorState({ kind: "confirmed_needs_refresh", operation })) {
        setMessage("管理员变更已由服务端确认，但本地待处理状态无法清除；请手动核查。");
        return;
      }
      setMessage(reconciled
        ? "变更结果曾无法确认；已使用同一请求重试并刷新确认生效。"
        : "变更成功，服务端已记录审计事件。");
    } catch {
      setMessage("管理员变更已由服务端确认，但当前角色刷新失败。");
    }
  };
  async function dispatchInflightAdministrator(
    operation: AdministratorMutation,
    reconciled: boolean,
    replayMatchingIndeterminate: boolean,
  ) {
    try {
      await changeAdministrator(account, operation);
    } catch (error) {
      if (
        error instanceof ManagementMutationIndeterminate
        && error.requestId !== operation.requestId
      ) {
        failAdministratorIntegrity(
          operation,
          "管理员变更响应校验失败；已停止新的管理员变更，请手动核查。",
        );
        return;
      }
      if (provesAdministratorMutationNotApplied(error)) {
        if (clearAdministratorState({ kind: "inflight_no_replay", operation })) {
          setMessage(failureMessage(error));
        } else {
          setMessage("服务端确认未执行管理员变更，但本地锁定状态无法清除；请手动核查。");
        }
        return;
      }
      if (!leavesAdministratorMutationOutcomeUncertain(error)) {
        setPendingAdministratorState({ kind: "inflight_no_replay", operation });
        setMessage("管理员变更遇到无法分类的客户端错误；操作保持不可重放，请手动核查。");
        return;
      }
      const refreshed = await refreshUnknownAdministrator();
      if (!retainPendingAdministratorReplay(operation)) return;
      if (error instanceof ManagementMutationIndeterminate && replayMatchingIndeterminate) {
        await replayAdministrator(operation);
        return;
      }
      setMessage(unknownAdministratorMessage(refreshed));
      return;
    }
    await finishConfirmedAdministrator(operation, reconciled);
  }
  async function replayAdministrator(operation: AdministratorMutation) {
    if (!beginInflightAdministrator(operation)) return;
    await dispatchInflightAdministrator(operation, true, false);
  }
  const mutateAdministrator = async (user: ManagedUser, revoke: boolean) => {
    if (account.role !== "platform_owner" || account.hard_stale_read_only || busy || administratorMutationBlocked) return;
    const operation = createAdministratorMutation(user, revoke);
    setBusy(true);
    setMessage("");
    if (!beginInflightAdministrator(operation)) {
      setBusy(false);
      return;
    }
    await dispatchInflightAdministrator(operation, false, true);
    setBusy(false);
  };
  const retryAdministrator = async () => {
    if (!pendingAdministrator) return;
    setBusy(true);
    setMessage("");
    try { await replayAdministrator(pendingAdministrator); } finally { setBusy(false); }
  };
  const refreshNonReplayAdministrator = async () => {
    const operation = confirmedAdministrator || inflightAdministrator;
    if (!operation) return;
    const retainedState: PendingAdministratorState = confirmedAdministrator
      ? { kind: "confirmed_needs_refresh", operation }
      : { kind: "inflight_no_replay", operation };
    setBusy(true);
    try {
      const refreshed = await refreshUsers();
      if (inflightAdministrator) {
        setMessage("管理员变更处于不可重放状态；当前角色仅供参考，无法证明该请求已终态完成，请人工核查治理审计。");
      } else if (!matchesAdministratorOutcome(refreshed, operation)) {
        setMessage(confirmedAdministrator
          ? "管理员变更已由服务端确认，但刷新后的角色与预期不一致；请手动核查。"
          : "管理员变更可能已提交但无法安全重放；已刷新当前角色，请手动核查。");
      } else if (clearAdministratorState(retainedState)) {
        setMessage("变更已确认，当前角色已刷新。");
      } else {
        setMessage(confirmedAdministrator
          ? "管理员变更已由服务端确认，但本地待处理状态无法清除；请手动核查。"
          : "管理员变更无法安全重放，且本地锁定状态无法清除；请手动核查。");
      }
    } catch {
      setMessage(confirmedAdministrator
        ? "管理员变更已由服务端确认，但当前角色刷新失败。"
        : "管理员变更处于不可重放状态；当前角色刷新失败，请人工核查治理审计。");
    } finally { setBusy(false); }
  };
  const canManage = account.role === "platform_owner";
  const blocked = busy || administratorMutationBlocked || account.hard_stale_read_only;
  return <section className="identity-page administrator-page">
    <header className="administrator-heading">
      <h1>账号与权限</h1>
      {canManage && <button type="button" disabled={blocked || !loaded} onClick={() => setAdding(true)}>添加管理员</button>}
    </header>
    {message && <p className={`auth-message ${message.startsWith("变更成功") || message.startsWith("变更结果曾无法确认") || message.startsWith("变更已确认") ? "is-success" : "is-error"}`} role="status">{message}</p>}
    {pendingAdministrator && <button type="button" disabled={busy || account.hard_stale_read_only} onClick={() => void retryAdministrator()}>使用同一请求重试确认</button>}
    {(confirmedAdministrator || inflightAdministrator) && <button type="button" disabled={busy} onClick={() => void refreshNonReplayAdministrator()}>刷新当前角色</button>}
    {!loaded && !message && <p role="status">正在加载…</p>}
    {!loaded && message && <button type="button" onClick={() => void load()}>重试</button>}
    {adding && canManage && <AdministratorSearch disabled={blocked} excludedIds={users.map(user => user.internal_user_id)}
      onSelect={user => void mutateAdministrator(user, false)} onClose={() => setAdding(false)} />}
    <h2>平台管理员</h2>
    <div className="administrator-list">
      {users.map(user => <article key={user.internal_user_id}>
        <div><strong>{user.display_name}</strong>
          {user.departments.length > 0 && <p>{user.departments.join("、")}</p>}
          {user.status !== "active" && <small className="administrator-warning">账号不可用</small>}
        </div>
        {user.role === "platform_owner" ? <span className="administrator-owner">所有者</span>
          : canManage && <button type="button" className="is-secondary" disabled={blocked} onClick={() => void mutateAdministrator(user, true)}>撤销平台管理员</button>}
      </article>)}
    </div>
    {loaded && !users.length && <p>暂无管理员</p>}
    <details className="identity-other-access"><summary>其他授权</summary>
      <a href={platformPath("/admin/identity/observers")}>观察者权限</a>
      {canManage && <a href={platformPath("/admin/identity/partners")}>合作方权限</a>}
    </details>
  </section>;
}
