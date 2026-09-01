import { platformPath } from "./auth";
import {
  parseFaeReport,
  parseFaeReportSummaryList,
  type FaeAnalysisReport,
  type FaeReportSummary,
} from "./faeReportTypes";


export class FaeReportApiError extends Error {
  constructor(public readonly status: number) {
    super(`FAE report API ${status}`);
    this.name = "FaeReportApiError";
  }
}


async function response(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(platformPath(path), {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new FaeReportApiError(response.status);
  return response.json();
}

async function get(path: string, signal?: AbortSignal): Promise<FaeAnalysisReport> {
  return parseFaeReport(await response(path, signal));
}

async function list(path: string, signal?: AbortSignal): Promise<FaeReportSummary[]> {
  return parseFaeReportSummaryList(await response(path, signal));
}

export const faeReportApi = {
  list: (signal?: AbortSignal) => list("/api/admin/fae/reports", signal),
  latest: (signal?: AbortSignal) => get("/api/admin/fae/reports/latest", signal),
  detail: (reportId: string, version?: number, signal?: AbortSignal) => get(
    `/api/admin/fae/reports/${encodeURIComponent(reportId)}${version === undefined ? "" : `?version=${encodeURIComponent(String(version))}`}`,
    signal,
  ),
};
