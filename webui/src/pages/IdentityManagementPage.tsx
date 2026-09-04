import { useEffect, useState } from "react";

import {
  DirectoryUnavailable,
  ManagementMutationIndeterminate,
  PermissionDenied,
  PlatformApiError,
  changeAdministrator,
  changeObservationScope,
  changeViewer,
  createAdministratorMutation,
  listManagedUsers,
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
import { FaeAccessPanel } from "../components/FaeAccessPanel";
import { VocAccessPanel } from "../components/VocAccessPanel";
import { PartnerAccessPanel } from "./PartnerAccessPanel";


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
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [reason, setReason] = useState("");
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
  const [scopeDrafts, setScopeDrafts] = useState<Record<string, string>>({});
  const refreshUsers = async () => {
    const refreshed = await listManagedUsers();
    setUsers(refreshed);
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
  if (account.role !== "platform_owner" && account.role !== "platform_admin") {
    return <section className="permission-state" role="alert"><h1>无权访问</h1><p>只有平台管理账号可以修改角色和观察范围。</p></section>;
  }
  const mutate = async (user: ManagedUser) => {
    if (!reason.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      await changeViewer(account, user, reason.trim());
      setMessage("变更成功，服务端已记录审计事件。");
      setReason("");
      await load();
    } catch (error) {
      setMessage(failureMessage(error));
    } finally { setBusy(false); }
  };
  const expectedAdministratorRole = (operation: AdministratorMutation) => (
    operation.revoke ? "member" : "platform_admin"
  );
  const matchesAdministratorOutcome = (
    refreshed: ManagedUser[], operation: AdministratorMutation,
  ) => refreshed.some((user) => (
    user.internal_user_id === operation.targetInternalUserId
    && user.role === expectedAdministratorRole(operation)
  ));
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
  const mutateScope = async (user: ManagedUser, agentId: string, revoke = false) => {
    if (!reason.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      await changeObservationScope(account, user, agentId, reason.trim(), revoke);
      setMessage("变更成功，服务端已记录审计事件。");
      setScopeDrafts((current) => ({ ...current, [user.internal_user_id]: "" }));
      setReason("");
      await load();
    } catch (error) {
      setMessage(failureMessage(error));
    } finally { setBusy(false); }
  };
  return (<>
    <section className="identity-page">
      <header><p>PLATFORM GOVERNANCE</p><h1>身份与观察范围</h1><span>角色和 Agent 范围均由后端执行；页面隐藏不是权限控制。</span></header>
      <label className="identity-reason">变更原因
        <input aria-label="变更原因" value={reason} onInput={(event) => setReason(event.currentTarget.value)} placeholder="填写审批或业务原因" />
      </label>
      {message && <p className={`auth-message ${message.startsWith("变更成功") || message.startsWith("变更结果曾无法确认") || message.startsWith("变更已确认") ? "is-success" : "is-error"}`} role="status">{message}</p>}
      {pendingAdministrator && <button type="button" disabled={busy} onClick={() => void retryAdministrator()}>
        使用同一请求重试确认
      </button>}
      {(confirmedAdministrator || inflightAdministrator) && <button type="button" disabled={busy} onClick={() => void refreshNonReplayAdministrator()}>
        刷新当前角色
      </button>}
      <div className="identity-users">
        {users.map((user) => <article key={user.internal_user_id}>
          <div><strong>{user.display_name}</strong><span>{user.status === "active" ? "在职" : "不可用"}</span></div>
          <p>{user.role === "management_viewer" ? "只读观察者" : user.role === "platform_admin" ? "平台管理员" : user.role === "platform_owner" ? "平台所有者" : "企业成员"}</p>
          <small>{user.scopes.length ? `范围：${user.scopes.join("、")}` : "未授予 Agent 观察范围"}</small>
          {account.role === "platform_owner" && (user.role === "platform_admin" || (user.role === "member" && user.status === "active")) && <button type="button" disabled={busy || administratorMutationBlocked} onClick={() => void mutateAdministrator(user, user.role === "platform_admin")}>
            {user.role === "platform_admin" ? "撤销平台管理员" : "设为平台管理员"}
          </button>}
          {(user.role === "member" || user.role === "management_viewer") && <button type="button" disabled={busy || !reason.trim()} onClick={() => void mutate(user)}>
            {user.role === "management_viewer" ? "撤销只读观察者" : "设为只读观察者"}
          </button>}
          {user.role === "management_viewer" && <div className="scope-controls">
            <label>新增 Agent 范围<input
              aria-label={`${user.display_name}的新 Agent 范围`}
              value={scopeDrafts[user.internal_user_id] || ""}
              onInput={(event) => setScopeDrafts((current) => ({ ...current, [user.internal_user_id]: event.currentTarget.value }))}
              placeholder="精确 Agent ID"
            /></label>
            <button type="button" disabled={busy || !reason.trim() || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(scopeDrafts[user.internal_user_id] || "")} onClick={() => void mutateScope(user, scopeDrafts[user.internal_user_id] || "")}>授予范围</button>
            {user.scopes.map((scope) => <button className="scope-revoke" type="button" key={scope} disabled={busy || !reason.trim()} onClick={() => void mutateScope(user, scope, true)}>撤销 {scope}</button>)}
          </div>}
        </article>)}
      </div>
    </section>
    <FaeAccessPanel account={account} />
    <VocAccessPanel account={account} />
    {account.role === "platform_owner" && <PartnerAccessPanel account={account} />}
  </>);
}
