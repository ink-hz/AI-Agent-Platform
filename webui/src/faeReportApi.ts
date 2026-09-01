import { platformPath } from "./auth";
import { parseFaeReport, type FaeAnalysisReport } from "./faeReportTypes";


async function get(path: string, signal?: AbortSignal): Promise<FaeAnalysisReport> {
  const response = await fetch(platformPath(path), {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error(`FAE report API ${response.status}`);
  return parseFaeReport(await response.json());
}

export const faeReportApi = {
  latest: (signal?: AbortSignal) => get("/api/admin/fae/reports/latest", signal),
  detail: (reportId: string, signal?: AbortSignal) => get(
    `/api/admin/fae/reports/${encodeURIComponent(reportId)}`,
    signal,
  ),
};
