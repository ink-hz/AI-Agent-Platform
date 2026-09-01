import { useEffect, useState } from "react";

import { LoadingState } from "../components/DataState";
import { AnswerEffectivenessChapter } from "../components/fae-reports/AnswerEffectivenessChapter";
import { BusinessValueChapter } from "../components/fae-reports/BusinessValueChapter";
import { ExecutiveOutcomeCover } from "../components/fae-reports/ExecutiveOutcomeCover";
import { InsightAndImprovementChapter } from "../components/fae-reports/InsightAndImprovementChapter";
import { ReportChapterNav } from "../components/fae-reports/ReportChapterNav";
import { UsageChapter } from "../components/fae-reports/UsageChapter";
import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";
import { FaeReportApiError, faeReportApi } from "../faeReportApi";
import type { FaeAnalysisReport, FaeReportSummary } from "../faeReportTypes";

function Report({ report, summaries }: { report: FaeAnalysisReport; summaries: FaeReportSummary[] }) {
  if (report.status === "failed") return <section className="fae-workbench__empty" role="alert"><h2>报告发布失败</h2><p>{report.failure?.message ?? "本次分析未通过发布门禁。"}</p></section>;
  return <article className="fae-report" data-report-id={report.report_id}>
    <ExecutiveOutcomeCover report={report} summaries={summaries} />
    <ReportChapterNav />
    <UsageChapter report={report} />
    <BusinessValueChapter report={report} />
    <AnswerEffectivenessChapter report={report} />
    <InsightAndImprovementChapter report={report} />
  </article>;
}

function selectedReportVersion(search: string): number | undefined | null {
  const values = new URLSearchParams(search).getAll("version");
  if (values.length === 0) return undefined;
  if (values.length !== 1 || !/^[1-9]\d*$/.test(values[0])) return null;
  const version = Number(values[0]);
  return Number.isSafeInteger(version) ? version : null;
}

export function FaeReportsPage({ reportId }: { reportId?: string }) {
  const [report, setReport] = useState<FaeAnalysisReport | null>(null);
  const [summaries, setSummaries] = useState<FaeReportSummary[]>([]);
  const [failureStatus, setFailureStatus] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);
  const version = reportId ? selectedReportVersion(window.location.search) : undefined;
  useEffect(() => {
    const controller = new AbortController(); setReport(null); setSummaries([]); setFailureStatus(null);
    if (version === null) return () => controller.abort();
    const request = reportId ? faeReportApi.detail(reportId, version, controller.signal) : faeReportApi.latest(controller.signal);
    void Promise.all([faeReportApi.list(controller.signal), request]).then(([index, value]) => {
      if (!controller.signal.aborted) { setSummaries(index); setReport(value); }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setFailureStatus(error instanceof FaeReportApiError ? error.status : -1);
    });
    return () => controller.abort();
  }, [reportId, version, attempt]);
  return <FaeWorkbenchShell currentSection="reports">{version === null
    ? <section className="fae-workbench__empty" role="alert"><h2>报告版本无效</h2><p>版本必须是一个正整数，请返回分析报告重新选择。</p></section>
    : failureStatus === 404
    ? <section className="fae-workbench__empty" role="status"><h2>{reportId ? "找不到该分析报告" : "尚无已发布的分析报告"}</h2><p>{reportId ? "该报告不存在、已撤回或当前账号无权读取。" : "完成真实数据分析、复审和发布后，成果会显示在这里。"}</p></section>
    : failureStatus === 401
      ? <section className="fae-workbench__empty" role="alert"><h2>需要登录后查看分析报告</h2><p>请重新登录企业账号后再访问已发布的 FAE 成果。</p></section>
    : failureStatus === 403
      ? <section className="fae-workbench__empty" role="alert"><h2>当前账号无权查看分析报告</h2><p>分析报告仅向已授权的管理层、FAE 团队和平台所有者开放。</p></section>
    : failureStatus === -1
      ? <section className="fae-workbench__empty" role="alert"><h2>报告内容未通过读取校验</h2><p>该版本不符合已发布报告契约，Platform 已停止展示，未使用不完整数据代替。</p></section>
    : failureStatus !== null
      ? <section className="data-state data-error fae-report-error" role="alert"><strong>分析报告读取失败</strong><p>报告数据暂时无法读取，FAE Agent 服务不受影响。</p><button type="button" onClick={() => setAttempt((value) => value + 1)}>重新尝试</button></section>
    : report ? <Report report={report} summaries={summaries} /> : <LoadingState label="正在读取已发布的 FAE 成果报告" />}</FaeWorkbenchShell>;
}
