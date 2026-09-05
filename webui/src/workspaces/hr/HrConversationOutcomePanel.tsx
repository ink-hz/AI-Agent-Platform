import { useEffect, useMemo, useRef, useState } from "react";

import { createHrApi, type HrApi } from "../../hrApi";
import type { HrConfirmedPositionPackage, HrPositionPackage } from "../../hrTypes";
import { navigate } from "../../router";
import { completeMutationRequest, retainMutationRequest } from "./hrMutationRequest";
import { HrPositionProposalCard } from "./HrPositionProposalCard";


type PositionPackageApi = Pick<HrApi, "positionPackage" | "confirmPositionPackage">;


function errorStatus(error: unknown): number | undefined {
  return typeof error === "object" && error !== null && "status" in error
    && typeof (error as { status?: unknown }).status === "number"
    ? (error as { status: number }).status : undefined;
}


export function HrConversationOutcomePanel({
  api,
  confirmed = false,
  conversationId,
  csrfToken,
  onConfirmed,
  onNavigate = navigate,
  readOnly = false,
  refreshKey = 0,
}: {
  api?: PositionPackageApi;
  confirmed?: boolean;
  conversationId?: string;
  csrfToken: string;
  onConfirmed?: (confirmed: HrConfirmedPositionPackage, positionPackage: HrPositionPackage) => void;
  onNavigate?: (path: string) => void;
  readOnly?: boolean;
  refreshKey?: number;
}) {
  const defaultApi = useMemo(() => createHrApi(csrfToken), [csrfToken]);
  const client = api ?? defaultApi;
  const [positionPackage, setPositionPackage] = useState<HrPositionPackage | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmedLocally, setConfirmedLocally] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const mutation = useRef<AbortController | null>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    mutation.current?.abort();
    setPositionPackage(null); setNotice(null); setConfirmedLocally(false);
    if (!conversationId) { setState("empty"); return; }
    const controller = new AbortController();
    let packagePoll: number | undefined;
    setState("loading");
    void client.positionPackage(conversationId, controller.signal).then((loaded) => {
      if (!controller.signal.aborted) {
        setPositionPackage(loaded); setRefreshRequired(false); setNotice(null); setState("ready");
      }
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      if (errorStatus(error) === 404) {
        setState("empty");
        if (loadAttempt < 12) {
          const delay = Math.min(5_000 * (2 ** Math.min(loadAttempt, 3)), 30_000);
          packagePoll = window.setTimeout(() => setLoadAttempt((value) => value + 1), delay);
        }
      } else {
        setState("error");
      }
    });
    return () => {
      controller.abort();
      if (packagePoll !== undefined) window.clearTimeout(packagePoll);
    };
  }, [client, conversationId, loadAttempt, refreshKey]);

  useEffect(() => () => mutation.current?.abort(), []);

  const confirm = async () => {
    if (confirmed || confirmedLocally || readOnly || refreshRequired || !positionPackage || inFlight.current) return;
    const selected = positionPackage;
    const payload = {
      draftVersionId: selected.draftVersionId,
      rowVersion: selected.rowVersion,
    };
    const operation = retainMutationRequest(`position-package:${selected.draftId}`, payload);
    mutation.current?.abort();
    const controller = new AbortController();
    mutation.current = controller;
    inFlight.current = true;
    setNotice(null);
    try {
      const result = await client.confirmPositionPackage(
        selected.draftId, selected.draftVersionId, selected.rowVersion,
        operation.requestId, controller.signal,
      );
      if (controller.signal.aborted) return;
      completeMutationRequest(operation.key);
      setConfirmedLocally(true);
      onConfirmed?.(result, selected);
      onNavigate(`/hr/positions/${encodeURIComponent(result.positionId)}/conversations/${encodeURIComponent(result.conversationId)}`);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (errorStatus(error) !== 409) {
        setNotice("确认未完成，请重试。");
        return;
      }
      completeMutationRequest(operation.key);
      try {
        const refreshed = await client.positionPackage(selected.conversationId, controller.signal);
        if (!controller.signal.aborted) {
          setPositionPackage(refreshed);
          setRefreshRequired(false);
          setNotice("岗位方案已更新，请核对后重试。");
        }
      } catch {
        if (!controller.signal.aborted) {
          setRefreshRequired(true);
          setNotice("岗位方案已更新，但暂时无法重新读取。请重新读取最新方案后再确认。");
        }
      }
    } finally {
      if (mutation.current === controller) {
        inFlight.current = false;
        mutation.current = null;
      }
    }
  };

  if (state === "loading" || state === "empty") return null;
  if (state === "error") return <section aria-label="岗位方案读取状态" className="conversation-flow-supplement hr-position-proposal-state">
    <p role="alert">岗位方案暂时无法读取。</p>
    <button onClick={() => setLoadAttempt((value) => value + 1)} type="button">重试</button>
  </section>;
  if (!positionPackage) return null;
  return <section className="conversation-flow-supplement">
    <HrPositionProposalCard confirmationDisabled={readOnly || refreshRequired} confirmed={confirmed || confirmedLocally} notice={notice} onConfirm={confirm} positionPackage={positionPackage} />
    {refreshRequired && <button className="hr-position-proposal-refresh" onClick={() => setLoadAttempt((value) => value + 1)} type="button">重新读取最新方案</button>}
  </section>;
}
