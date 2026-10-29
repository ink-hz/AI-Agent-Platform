import { useEffect, useState } from "react";

import { startVisibleRefresh } from "./visibleRefresh";

import { fetchReviewTurnSummaries } from "../../api";
import type { SessionDetail, TurnClosureSummary } from "../../types";


export interface SessionDetailState {
  session: SessionDetail | null;
  closureSummaries: Record<string, TurnClosureSummary>;
  error: boolean;
}


type SessionDetailLoader = (key: string, signal: AbortSignal) => Promise<SessionDetail>;

interface RequestScopedSessionDetailState extends SessionDetailState {
  loader: SessionDetailLoader;
  sessionKey: string;
  closureMode: "negative-only" | "all-turns";
}


const SUMMARY_BATCH_SIZE = 200;
const EMPTY_CLOSURE_SUMMARIES: Record<string, TurnClosureSummary> = {};


export function useSessionDetail(
  loader: SessionDetailLoader,
  sessionKey: string,
  closureMode: "negative-only" | "all-turns",
): SessionDetailState {
  const [state, setState] = useState<RequestScopedSessionDetailState>(() => ({
    loader,
    sessionKey,
    closureMode,
    session: null,
    closureSummaries: EMPTY_CLOSURE_SUMMARIES,
    error: false,
  }));

  useEffect(() => {
    setState({
      loader,
      sessionKey,
      closureMode,
      session: null,
      closureSummaries: EMPTY_CLOSURE_SUMMARIES,
      error: false,
    });
    return startVisibleRefresh(async (signal) => {
      try {
        const value = await loader(sessionKey, signal);
        if (signal.aborted) return;
        const turnKeys = value.turns
          .filter((turn) => closureMode === "all-turns"
            || turn.feedback.some((item) => item.sentiment === "negative"))
          .map((turn) => turn.turn_key);
        const batches: string[][] = [];
        for (let index = 0; index < turnKeys.length; index += SUMMARY_BATCH_SIZE) {
          batches.push(turnKeys.slice(index, index + SUMMARY_BATCH_SIZE));
        }
        const summaries = (await Promise.all(
          batches.map((turnKeyBatch) => fetchReviewTurnSummaries(turnKeyBatch, signal)),
        )).flat();
        if (signal.aborted) return;
        setState({
          loader,
          sessionKey,
          closureMode,
          session: value,
          closureSummaries: Object.fromEntries(summaries.map((item) => [item.turn_key, item])),
          error: false,
        });
      } catch {
        if (!signal.aborted) setState((previous) => ({ ...previous, error: true }));
      }
    });
  }, [closureMode, loader, sessionKey]);

  if (state.loader !== loader || state.sessionKey !== sessionKey || state.closureMode !== closureMode) {
    return { session: null, closureSummaries: EMPTY_CLOSURE_SUMMARIES, error: false };
  }
  return { ...state, session: state.session && state.error ? { ...state.session, freshness: "stale" } : state.session };
}
