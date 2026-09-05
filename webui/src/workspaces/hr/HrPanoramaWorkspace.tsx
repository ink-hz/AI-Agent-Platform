import { useEffect, useMemo, useState } from "react";

import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import { createHrPanoramaApi, HrPanoramaApiError, type HrPanoramaApi } from "../../hrPanoramaApi";
import type { HrPanoramaInsight, HrPanoramaReport } from "../../hrPanoramaTypes";
import { HrPanoramaReport as Report, type HrPanoramaComparison } from "./HrPanoramaReport";

const DATE_TIME = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});

function sameScope(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value) => right.includes(value));
}

function asOf(report: HrPanoramaReport): string {
  const observed = report.snapshots.map((item) => item.observedAt).sort();
  return observed[observed.length - 1] ?? report.insight.createdAt;
}

function readFailure(error: unknown): string {
  if (error instanceof HrPanoramaApiError && error.status === 401) return "登录状态已失效，请重新登录。";
  if (error instanceof HrPanoramaApiError && error.status === 403) return "当前账号无法查看招聘情报。";
  return "招聘情报暂时无法读取，已发布数据不会丢失。";
}

export function HrPanoramaWorkspace({
  account, insightVersionId, api: injectedApi,
}: {
  account: Account;
  insightVersionId?: string;
  executionConversationId?: string;
  api?: HrPanoramaApi;
}) {
  const defaultApi = useMemo(() => createHrPanoramaApi(account.csrf_token), [account.csrf_token]);
  const api = injectedApi ?? defaultApi;
  const [report, setReport] = useState<HrPanoramaReport | null>(null);
  const [reports, setReports] = useState<HrPanoramaInsight[]>([]);
  const [comparison, setComparison] = useState<HrPanoramaComparison>({ state: "loading" });
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      setFailure(null);
      setComparison({ state: "loading" });
      try {
        const historyPromise = api.listReports(controller.signal).catch(() => [] as HrPanoramaInsight[]);
        const selected = insightVersionId
          ? await api.report(insightVersionId, controller.signal)
          : await api.currentReport(controller.signal);
        const history = await historyPromise;
        if (controller.signal.aborted) return;
        setReports(history);
        setReport(selected);
        if (!selected) {
          setComparison({ state: "none", currentSourceFailures: {} });
          return;
        }
        const previous = history
          .filter((item) => item.versionNumber < selected.insight.versionNumber
            && sameScope(item.selectedSourceIds, selected.insight.selectedSourceIds))
          .sort((left, right) => right.versionNumber - left.versionNumber)[0];
        if (!previous) {
          setComparison({ state: "none", currentSourceFailures: {} });
          return;
        }
        try {
          const previousReport = await api.report(previous.insightVersionId, controller.signal);
          if (!controller.signal.aborted) setComparison({
            state: "available", previousReport, currentSourceFailures: {}, previousSourceFailures: {},
          });
        } catch {
          if (!controller.signal.aborted) setComparison({ state: "unavailable" });
        }
      } catch (error) {
        if (!controller.signal.aborted) setFailure(readFailure(error));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => controller.abort();
  }, [api, attempt, insightVersionId]);

  return <main className="hr-panorama-workspace">
    <header className="hr-panorama-header">
      <div>
        <p>RECRUITING INTELLIGENCE</p>
        <h1>招聘全景分析</h1>
        <span>后台持续整理公开招聘原始数据，并用 AI 形成可追溯的分析结果。</span>
      </div>
      {report && <div className="hr-panorama-as-of">
        <span>数据截至</span>
        <strong>{DATE_TIME.format(new Date(asOf(report)))}</strong>
      </div>}
    </header>

    <div className="hr-panorama-layout">
      <aside className="hr-panorama-sidebar">
        <section>
          <header><span>分析历史</span><strong>{reports.length}</strong></header>
          {reports.length ? <nav>{reports.map((item) => <PlatformLink
            aria-current={item.insightVersionId === report?.insight.insightVersionId ? "page" : undefined}
            href={`/hr/panorama/reports/${item.insightVersionId}`}
            key={item.insightVersionId}
          >
            <strong>第 {item.versionNumber} 版</strong>
            <span>{item.summary}</span>
            <time dateTime={item.createdAt}>{DATE_TIME.format(new Date(item.createdAt))}</time>
          </PlatformLink>)}</nav> : <p>首份情报正在后台准备。</p>}
        </section>
      </aside>

      <section className="hr-panorama-content">
        {loading && <div className="hr-panorama-state"><strong>正在读取已发布情报</strong><span>无需操作，完成后会直接展示。</span></div>}
        {!loading && failure && <div className="hr-panorama-state is-error"><strong>{failure}</strong><span>稍后重新进入页面即可，后台采集不会占用你的对话。</span><button onClick={() => setAttempt((value) => value + 1)} type="button">重新读取</button></div>}
        {!loading && !failure && !report && <div className="hr-panorama-state"><strong>首份招聘情报正在后台准备</strong><span>完成质量核验后会自动出现在这里，无需手动发起。</span></div>}
        {!loading && !failure && report && <Report comparison={comparison} report={report} />}
      </section>
    </div>
  </main>;
}
