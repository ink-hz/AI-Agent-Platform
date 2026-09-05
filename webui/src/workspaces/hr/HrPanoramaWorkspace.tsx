import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import { createHrPanoramaApi, HrPanoramaApiError, type HrPanoramaApi } from "../../hrPanoramaApi";
import type { HrPanoramaInsight, HrPanoramaReport, HrPanoramaRun, HrPanoramaSource, StartHrPanoramaRunInput } from "../../hrPanoramaTypes";
import { HrPanoramaReport as Report, type HrPanoramaComparison } from "./HrPanoramaReport";
import { HR_PANORAMA_COMPANY_CATALOG, HR_PANORAMA_SESSION_LEADS, catalogSource } from "./hrPanoramaCatalog";

const DATE_TIME = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
const TERMINAL = new Set<HrPanoramaRun["state"]>(["completed", "partially_completed", "failed"]);
const REPORT_HISTORY_LIMIT = 100;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Notice = "partial" | "failed" | "refresh-failed" | null;

function failureMessage(error: unknown, noun: string): string {
  if (error instanceof HrPanoramaApiError && error.status === 401) return `请重新登录后再${noun}。`;
  if (error instanceof HrPanoramaApiError && error.status === 403) return `当前账号无权${noun}。`;
  if (error instanceof HrPanoramaApiError && error.status === 503) return `服务暂时无法${noun}，请稍后重试。`;
  return `${noun}失败，请检查公开地址或稍后重试。`;
}

type RetainedExecution = { kind: "starting"; requestId: string; input: StartHrPanoramaRunInput } | { kind: "active"; runId: string };

function activeRunKey(ownerId: string): string { return `platform.hr.panorama.active-run.${ownerId}`; }
function retainedExecution(ownerId: string): RetainedExecution | null {
  try {
    const value = JSON.parse(window.localStorage?.getItem(activeRunKey(ownerId)) ?? "null");
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (value.kind === "active" && Object.keys(value).length === 2 && typeof value.runId === "string" && UUID.test(value.runId)) return value;
    const input = value.input;
    if (value.kind !== "starting" || Object.keys(value).length !== 3 || typeof value.requestId !== "string" || !UUID.test(value.requestId)
      || !input || typeof input !== "object" || Array.isArray(input) || !Array.isArray(input.sourceIds) || input.sourceIds.length < 1 || input.sourceIds.length > 100
      || input.sourceIds.some((id: unknown) => typeof id !== "string" || !UUID.test(id)) || new Set(input.sourceIds).size !== input.sourceIds.length
      || (input.conversationId !== undefined && (typeof input.conversationId !== "string" || !UUID.test(input.conversationId)))) return null;
    return { kind: "starting", requestId: value.requestId, input: { sourceIds: [...input.sourceIds], ...(input.conversationId ? { conversationId: input.conversationId } : {}) } };
  } catch { return null; }
}
function persistExecution(ownerId: string, execution: RetainedExecution | null): void {
  try {
    if (execution) window.localStorage?.setItem(activeRunKey(ownerId), JSON.stringify(execution));
    else window.localStorage?.removeItem(activeRunKey(ownerId));
  } catch { /* optional continuity cache */ }
}
function permanentProgressFailure(error: unknown): boolean {
  return error instanceof HrPanoramaApiError && [401, 403, 404].includes(error.status);
}
function permanentStartFailure(error: unknown): boolean {
  return error instanceof HrPanoramaApiError && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status);
}
function sameScope(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value) => right.includes(value));
}

export function HrPanoramaWorkspace({
  account, insightVersionId, executionConversationId, api: injectedApi,
}: {
  account: Account;
  insightVersionId?: string;
  executionConversationId?: string;
  api?: HrPanoramaApi;
}) {
  const defaultApi = useMemo(() => createHrPanoramaApi(account.csrf_token), [account.csrf_token]);
  const api = injectedApi ?? defaultApi;
  const [sources, setSources] = useState<HrPanoramaSource[]>([]);
  const [reports, setReports] = useState<HrPanoramaInsight[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [report, setReport] = useState<HrPanoramaReport | null>(null);
  const [comparison, setComparison] = useState<HrPanoramaComparison>({ state: "loading" });
  const [loading, setLoading] = useState(true);
  const [loadFailure, setLoadFailure] = useState<string | null>(null);
  const [sourcesFailure, setSourcesFailure] = useState(false);
  const [reportsFailure, setReportsFailure] = useState(false);
  const [run, setRun] = useState<HrPanoramaRun | null>(null);
  const retainedAtMount = useRef<RetainedExecution | null>(retainedExecution(account.internal_user_id));
  const [trackingRunId, setTrackingRunId] = useState<string | null>(() => retainedAtMount.current?.kind === "active" ? retainedAtMount.current.runId : null);
  const [pendingStart, setPendingStart] = useState<RetainedExecution & { kind: "starting" } | null>(() => retainedAtMount.current?.kind === "starting" ? retainedAtMount.current : null);
  const [starting, setStarting] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [operationFailure, setOperationFailure] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  const [companyName, setCompanyName] = useState("");
  const [companyUrl, setCompanyUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [catalogAdding, setCatalogAdding] = useState(false);
  const [catalogNotice, setCatalogNotice] = useState<string | null>(null);
  const operationController = useRef<AbortController | null>(null);
  const startingRef = useRef(false);
  const addingRef = useRef(false);
  const catalogAddingRef = useRef(false);
  const trackingRunRef = useRef<string | null>(trackingRunId);
  const pendingStartRef = useRef(pendingStart);
  const pollTimer = useRef<number | null>(null);

  const loadReport = useCallback(async (selectedInsightVersionId: string, signal?: AbortSignal) => {
    const detail = await api.report(selectedInsightVersionId, signal);
    if (!signal?.aborted) setReport(detail);
    return detail;
  }, [api]);

  const loadComparison = useCallback(async (detail: HrPanoramaReport, items: HrPanoramaInsight[], signal: AbortSignal): Promise<HrPanoramaComparison> => {
    try {
      const currentRun = await api.runStatus(detail.insight.runId, signal);
      const coherent = (runValue: HrPanoramaRun, reportValue: HrPanoramaReport) => runValue.runId === reportValue.insight.runId
        && (runValue.state === "completed" || runValue.state === "partially_completed")
        && sameScope(runValue.selectedSourceIds, reportValue.insight.selectedSourceIds);
      if (!coherent(currentRun, detail)) return { state: "unavailable" };
      const previous = items.filter((item) => item.versionNumber < detail.insight.versionNumber
        && sameScope(item.selectedSourceIds, detail.insight.selectedSourceIds))
        .sort((left, right) => right.versionNumber - left.versionNumber)[0];
      if (!previous) return items.length >= REPORT_HISTORY_LIMIT
        ? { state: "unavailable" }
        : { state: "none", currentSourceFailures: currentRun.sourceFailures };
      const previousReport = await api.report(previous.insightVersionId, signal);
      const previousRun = await api.runStatus(previousReport.insight.runId, signal);
      if (!coherent(previousRun, previousReport)) return { state: "unavailable" };
      return { state: "available", previousReport, currentSourceFailures: currentRun.sourceFailures, previousSourceFailures: previousRun.sourceFailures };
    } catch { return { state: "unavailable" }; }
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadFailure(null); setSourcesFailure(false); setReportsFailure(false); setComparison({ state: "loading" });
    void api.listCompanies(controller.signal).then((companyItems) => {
      if (controller.signal.aborted) return;
      setSources(companyItems);
      setSelectedSourceIds((current) => current.length ? current.filter((id) => companyItems.some((source) => source.sourceId === id && source.active)) : companyItems.filter((source) => source.active).map((source) => source.sourceId));
    }).catch(() => { if (!controller.signal.aborted) setSourcesFailure(true); });
    const reportItems = api.listReports(controller.signal).then((items) => {
      if (!controller.signal.aborted) setReports(items);
      return items;
    }).catch(() => {
      if (!controller.signal.aborted) setReportsFailure(true);
      return null;
    });
    const loadSelected = async () => {
      const items = insightVersionId ? undefined : await reportItems;
      if (controller.signal.aborted) return;
      const selectedInsightVersionId = insightVersionId ?? items?.[0]?.insightVersionId;
      if (!selectedInsightVersionId) {
        if (!insightVersionId && items === null) setLoadFailure("读取最近分析报告失败，请稍后重试。");
        else setReport(null);
        setLoading(false);
        return;
      }
      try {
        const detail = await loadReport(selectedInsightVersionId, controller.signal);
        if (!controller.signal.aborted) setLoading(false);
        const availableItems = items ?? await reportItems;
        const nextComparison = availableItems ? await loadComparison(detail, availableItems, controller.signal) : { state: "unavailable" as const };
        if (!controller.signal.aborted) setComparison(nextComparison);
      } catch (error) {
        if (!controller.signal.aborted) { setLoadFailure(failureMessage(error, "读取分析报告")); setLoading(false); }
      }
    };
    void loadSelected();
    return () => controller.abort();
  }, [api, attempt, insightVersionId, loadComparison, loadReport]);

  useEffect(() => () => {
    startingRef.current = false;
    addingRef.current = false;
    operationController.current?.abort();
    if (pollTimer.current !== null) window.clearTimeout(pollTimer.current);
  }, []);

  const refreshAfterRun = useCallback(async (completedRun: HrPanoramaRun, signal: AbortSignal) => {
    if (completedRun.state === "failed") { setNotice("failed"); return; }
    if (completedRun.state === "partially_completed") setNotice("partial");
    try {
      const items = await api.listReports(signal);
      if (signal.aborted) return;
      setReports(items);
      const generated = items.find((item) => item.runId === completedRun.runId);
      if (generated) {
        const detail = await loadReport(generated.insightVersionId, signal);
        setComparison({ state: "loading" });
        setComparison(await loadComparison(detail, items, signal));
      }
      else if (completedRun.state === "completed") setNotice("refresh-failed");
    } catch {
      if (!signal.aborted) setNotice(completedRun.state === "partially_completed" ? "partial" : "refresh-failed");
    }
  }, [api, loadComparison, loadReport]);

  const trackRun = useCallback((runId: string, controller: AbortController, initial?: HrPanoramaRun) => {
    const schedule = (selectedRunId: string, delay: number, failures: number) => {
      if (pollTimer.current !== null) window.clearTimeout(pollTimer.current);
      pollTimer.current = window.setTimeout(() => {
        void api.runStatus(selectedRunId, controller.signal).then((next) => {
          if (!controller.signal.aborted) accept(next);
        }).catch((error: unknown) => {
          if (controller.signal.aborted) return;
          if (permanentProgressFailure(error)) {
            trackingRunRef.current = null; setTrackingRunId(null); setRun(null); persistExecution(account.internal_user_id, null);
            setOperationFailure(error instanceof HrPanoramaApiError && error.status === 401
              ? "登录状态已失效，请重新登录后再更新。" : "上次更新记录已结束，你可以重新发起更新。");
            return;
          }
          setOperationFailure("更新仍在后台进行，正在重新确认进度。");
          schedule(selectedRunId, Math.min(12_000, 1_500 * (2 ** (failures + 1))), failures + 1);
        });
      }, delay);
    };
    const accept = (activeRun: HrPanoramaRun) => {
      setRun(activeRun); setOperationFailure(null);
      pendingStartRef.current = null; setPendingStart(null); startingRef.current = false; setStarting(false);
      if (TERMINAL.has(activeRun.state)) {
        trackingRunRef.current = null; setTrackingRunId(null); persistExecution(account.internal_user_id, null);
        void refreshAfterRun(activeRun, controller.signal);
        return;
      }
      trackingRunRef.current = activeRun.runId; setTrackingRunId(activeRun.runId); persistExecution(account.internal_user_id, { kind: "active", runId: activeRun.runId });
      schedule(activeRun.runId, 1_500, 0);
    };
    if (initial) accept(initial);
    else schedule(runId, 0, 0);
  }, [account.internal_user_id, api, refreshAfterRun]);

  const submitStart = useCallback((pending: RetainedExecution & { kind: "starting" }, controller: AbortController) => {
    const attemptStart = (failures: number) => {
      void api.startRun(pending.input, pending.requestId, controller.signal).then((next) => {
        if (!controller.signal.aborted) trackRun(next.runId, controller, next);
      }).catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (permanentStartFailure(error)) {
          pendingStartRef.current = null; setPendingStart(null); startingRef.current = false; setStarting(false); persistExecution(account.internal_user_id, null);
          setOperationFailure(failureMessage(error, "开始更新"));
          return;
        }
        if (error instanceof HrPanoramaApiError && error.status === 503) {
          startingRef.current = false; setStarting(false);
          setOperationFailure("更新请求已安全保留，恢复写入权限后将继续确认。");
          return;
        }
        setOperationFailure("更新请求仍在确认，正在安全重试。");
        if (pollTimer.current !== null) window.clearTimeout(pollTimer.current);
        pollTimer.current = window.setTimeout(
          () => attemptStart(failures + 1),
          Math.min(12_000, 1_500 * (2 ** (failures + 1))),
        );
      });
    };
    attemptStart(0);
  }, [account.internal_user_id, api, trackRun]);

  useEffect(() => {
    const retained = retainedExecution(account.internal_user_id);
    if (!retained) return;
    const controller = new AbortController(); operationController.current = controller;
    if (retained.kind === "active") {
      trackingRunRef.current = retained.runId; setTrackingRunId(retained.runId);
      trackRun(retained.runId, controller);
    } else {
      pendingStartRef.current = retained; setPendingStart(retained); startingRef.current = true; setStarting(true);
      if (account.hard_stale_read_only) {
        startingRef.current = false; setStarting(false);
        setOperationFailure("更新请求已安全保留，恢复写入权限后将继续确认。");
      } else submitStart(retained, controller);
    }
    return () => controller.abort();
  }, [account.hard_stale_read_only, account.internal_user_id, submitStart, trackRun]);

  const start = useCallback((sourceIds: string[]) => {
    if (account.hard_stale_read_only || startingRef.current || pendingStartRef.current || trackingRunRef.current) return;
    const pending: RetainedExecution & { kind: "starting" } = {
      kind: "starting", requestId: crypto.randomUUID(),
      input: { sourceIds, ...(executionConversationId ? { conversationId: executionConversationId } : {}) },
    };
    pendingStartRef.current = pending; setPendingStart(pending); startingRef.current = true; setStarting(true);
    persistExecution(account.internal_user_id, pending);
    operationController.current?.abort();
    if (pollTimer.current !== null) window.clearTimeout(pollTimer.current);
    const controller = new AbortController(); operationController.current = controller;
    setOperationFailure(null); setNotice(null);
    submitStart(pending, controller);
  }, [account.hard_stale_read_only, account.internal_user_id, executionConversationId, submitStart]);

  const addCompany = async () => {
    const canonicalName = companyName.trim(); const approvedUrl = companyUrl.trim();
    if (!canonicalName || !approvedUrl || addingRef.current || account.hard_stale_read_only) return;
    addingRef.current = true; setAdding(true); setOperationFailure(null);
    try {
      const created = await api.addCompany({ canonicalName, aliases: [], approvedUrls: [approvedUrl] }, crypto.randomUUID());
      setSources((current) => [...current.filter((item) => item.sourceId !== created.sourceId), created]);
      setSelectedSourceIds((current) => current.includes(created.sourceId) ? current : [...current, created.sourceId]);
      setCompanyName(""); setCompanyUrl(""); setAddOpen(false);
    } catch (error) { setOperationFailure(failureMessage(error, "添加关注公司")); }
    finally { addingRef.current = false; setAdding(false); }
  };

  const updateInProgress = starting || Boolean(pendingStart) || Boolean(trackingRunId);
  const missingCatalog = HR_PANORAMA_COMPANY_CATALOG.filter((company) => !catalogSource(company, sources));
  const catalogSources = HR_PANORAMA_COMPANY_CATALOG.map((company) => ({ company, source: catalogSource(company, sources) }));
  const extraSources = sources.filter((source) => !HR_PANORAMA_COMPANY_CATALOG.some((company) => catalogSource(company, [source])));
  const addCatalog = async () => {
    if (account.hard_stale_read_only || catalogAddingRef.current || missingCatalog.length === 0) return;
    catalogAddingRef.current = true; setCatalogAdding(true); setCatalogNotice(null); setOperationFailure(null);
    const results = await Promise.allSettled(missingCatalog.map((company) => api.addCompany({
      canonicalName: company.canonicalName,
      aliases: company.aliases,
      approvedUrls: company.approvedUrls,
    }, crypto.randomUUID())));
    const created = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
    setSources((current) => [...current, ...created.filter((item) => !current.some((existing) => existing.sourceId === item.sourceId))]);
    setSelectedSourceIds((current) => [...new Set([...current, ...created.filter((item) => item.active).map((item) => item.sourceId)])]);
    const failed = results.length - created.length;
    setCatalogNotice(failed === 0 ? "10 家重点公司已加入" : `已加入 ${created.length} 家，${failed} 家暂时失败，可再次补齐。`);
    catalogAddingRef.current = false; setCatalogAdding(false);
  };
  const startPaused = Boolean(pendingStart) && !starting;
  const failedSources = run?.state === "partially_completed"
    ? Object.keys(run.sourceFailures).map((id) => sources.find((source) => source.sourceId === id)).filter((source): source is HrPanoramaSource => Boolean(source)) : [];

  return <section aria-labelledby="hr-panorama-title" className="hr-panorama-workspace" data-panorama-workspace>
    <header className="hr-panorama-toolbar">
      <div><span>RECRUITING INTELLIGENCE</span><h1 id="hr-panorama-title">全景分析</h1><p>基于你明确关注公司的公开招聘信息，区分事实、推断与未知项。</p></div>
      <div><span className="hr-panorama-scope-count">分析范围：{selectedSourceIds.length} 家</span><button className="is-secondary" disabled={account.hard_stale_read_only} onClick={() => setAddOpen((value) => !value)} type="button">添加关注公司</button><button disabled={account.hard_stale_read_only || selectedSourceIds.length === 0 || updateInProgress} onClick={() => void start(selectedSourceIds)} type="button">立即更新</button></div>
    </header>
    {addOpen && <section className="hr-panorama-add-company" aria-label="添加关注公司">
      <label>公司名称<input value={companyName} onChange={(event) => setCompanyName(event.target.value)} /></label>
      <label>公开招聘页（HTTPS）<input inputMode="url" placeholder="https://example.com/jobs" value={companyUrl} onChange={(event) => setCompanyUrl(event.target.value)} /></label>
      <button disabled={account.hard_stale_read_only || adding || !companyName.trim() || !companyUrl.trim()} onClick={() => void addCompany()} type="button">{adding ? "正在添加…" : "确认关注"}</button>
    </section>}
    {updateInProgress && <aside className="hr-panorama-run-status" role="status"><span className="hr-panorama-run-pulse" aria-hidden="true" /><div><strong>{startPaused ? "更新请求已安全保留" : "正在收集公开招聘岗位"}</strong><p>{startPaused ? "恢复写入权限后会使用同一请求继续确认，不会重复发起更新。" : "完成后会生成新的分析版本；当前报告会继续保留。"}</p></div></aside>}
    {notice === "partial" && <aside className="hr-panorama-partial" role="status"><div><strong>部分公开来源暂时未能更新</strong><p>已完成的公司仍会形成分析；在新结果可用前，继续显示最近一次有效报告。</p></div>{failedSources.map((source) => <button disabled={account.hard_stale_read_only || updateInProgress} key={source.sourceId} onClick={() => void start([source.sourceId])} type="button">重试 {source.canonicalName}</button>)}</aside>}
    {notice === "failed" && <aside className="hr-panorama-partial" role="status"><div><strong>本次公开信息更新未完成</strong><p>继续显示最近一次有效报告，你可以稍后再次更新。</p></div></aside>}
    {notice === "refresh-failed" && <aside className="hr-panorama-partial" role="status"><div><strong>新分析暂时无法读取</strong><p>最近一次有效报告仍在下方保留。</p></div></aside>}
    {operationFailure && <aside className="hr-panorama-operation-error" role="alert"><span>{operationFailure}</span><button onClick={() => setOperationFailure(null)} type="button">知道了</button></aside>}

    <div className="hr-panorama-layout">
      <aside className="hr-panorama-master">
        <section><header><h2>重点公司</h2><span>{catalogSources.filter((item) => item.source?.active).length} / 10</span></header>
          {sourcesFailure && <p role="alert">关注公司暂时无法读取，重点公司目录仍可查看。</p>}
          <ul>{catalogSources.map(({ company, source }) => <li key={company.canonicalName}><label><input checked={Boolean(source && selectedSourceIds.includes(source.sourceId))} disabled={!source?.active || updateInProgress || catalogAdding} onChange={(event) => source && setSelectedSourceIds((current) => event.target.checked ? [...current, source.sourceId] : current.filter((id) => id !== source.sourceId))} type="checkbox" /><span><strong>{company.canonicalName}</strong><small>{source?.active ? "已关注 · 社招/校招" : "待加入 · 社招/校招入口已配置"}</small><small>{company.sourceNote}</small></span></label></li>)}</ul>
          {missingCatalog.length > 0 && <button className="hr-panorama-fill-catalog" disabled={account.hard_stale_read_only || catalogAdding} onClick={() => void addCatalog()} type="button">{catalogAdding ? "正在补齐…" : `补齐 ${missingCatalog.length} 家重点公司`}</button>}
          {catalogNotice && <p role="status">{catalogNotice}</p>}
          {extraSources.length > 0 && <details className="hr-panorama-extra-sources"><summary>其他已关注公司（{extraSources.length}）</summary><ul>{extraSources.map((source) => <li key={source.sourceId}><label><input checked={selectedSourceIds.includes(source.sourceId)} disabled={!source.active || updateInProgress} onChange={(event) => setSelectedSourceIds((current) => event.target.checked ? [...current, source.sourceId] : current.filter((id) => id !== source.sourceId))} type="checkbox" /><span><strong>{source.canonicalName}</strong><small>{source.active ? "已关注" : "已停止关注"}</small></span></label></li>)}</ul></details>}
        </section>
        <section className="hr-panorama-session-leads"><header><h2>历史会话线索</h2><span>{HR_PANORAMA_SESSION_LEADS.length}</span></header><p>确认后再加入，不自动采集。</p><div>{HR_PANORAMA_SESSION_LEADS.map((name) => <span key={name}>{name}</span>)}</div>
        </section>
        <section><header><h2>分析历史</h2><span>{reports.length}</span></header>
          {reportsFailure ? <p role="alert">分析历史暂时无法读取，当前报告仍可继续查看。</p> : reports.length ? <nav aria-label="分析历史">{reports.map((item) => <PlatformLink aria-current={report?.insight.insightVersionId === item.insightVersionId ? "page" : undefined} href={`/hr/panorama/reports/${encodeURIComponent(item.insightVersionId)}`} key={item.insightVersionId}><strong>第 {item.versionNumber} 版</strong><span>{item.summary}</span><time dateTime={item.createdAt}>{DATE_TIME.format(new Date(item.createdAt))}</time></PlatformLink>)}</nav> : <p>完成第一次更新后，分析版本会保留在这里。</p>}
        </section>
      </aside>
      <section className="hr-panorama-detail">
        {loading ? <div className="hr-panorama-state" role="status"><strong>正在读取全景分析</strong><p>正在加载关注公司和历史报告。</p></div>
          : loadFailure ? <div className="hr-panorama-state is-error" role="alert"><strong>全景分析暂时无法读取</strong><p>{loadFailure}</p><button onClick={() => setAttempt((value) => value + 1)} type="button">重新尝试</button></div>
            : report ? <Report comparison={comparison} report={report} />
              : <div className="hr-panorama-state"><strong>从公开招聘信息开始</strong><p>选择关注公司并点击“立即更新”，首份报告完成后会显示在这里。</p></div>}
      </section>
    </div>
  </section>;
}
