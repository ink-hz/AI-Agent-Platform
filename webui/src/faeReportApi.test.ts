/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { faeReportApi } from "./faeReportApi";
import { reportFixture } from "./testFixtures/faeReportFixture";


function mockJson(value: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}


afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  history.replaceState({}, "", "/");
});


describe("FAE report API", () => {
  it("parses a lightweight immutable report index", async () => {
    mockJson([{
      report_id: reportFixture.report_id,
      report_version: 2,
      report_type: "topic",
      status: "ready",
      title: reportFixture.title,
      period: reportFixture.period,
      data_cutoff_at: reportFixture.data_cutoff_at,
      generated_at: reportFixture.generated_at,
      analysis_version: reportFixture.analysis_version,
      failure: null,
      publication: reportFixture.publication,
      latest_source_sync_at: reportFixture.latest_source_sync_at,
      currentness: "source_updated",
    }]);

    const [summary] = await faeReportApi.list();

    expect(summary.report_version).toBe(2);
    expect(summary).not.toHaveProperty("metrics");
  });

  it("requests an exact positive report version", async () => {
    const fetchMock = mockJson({ ...reportFixture, report_version: 2 });

    await faeReportApi.detail(reportFixture.report_id, 2);

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `/api/admin/fae/reports/${reportFixture.report_id}?version=2`,
    );
  });

  it("rejects a report response containing raw conversation fields", async () => {
    mockJson({ ...reportFixture, question: "raw private question" });

    await expect(faeReportApi.latest()).rejects.toThrow("FAE report response contract invalid");
  });
});
