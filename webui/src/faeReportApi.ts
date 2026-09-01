import { platformPath } from "./auth";
import { parseFaeReport, type FaeAnalysisReport } from "./faeReportTypes";


export class FaeReportApiError extends Error {
  constructor(public readonly status: number) {
    super(`FAE report API ${status}`);
    this.name = "FaeReportApiError";
  }
}


async function get(path: string, signal?: AbortSignal): Promise<FaeAnalysisReport> {
  const response = await fetch(platformPath(path), {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new FaeReportApiError(response.status);
  return parseFaeReport(await response.json());
}

export const faeReportApi = {
  latest: (signal?: AbortSignal) => get("/api/admin/fae/reports/latest", signal),
  detail: (reportId: string, signal?: AbortSignal) => get(
    `/api/admin/fae/reports/${encodeURIComponent(reportId)}`,
    signal,
  ),
};
