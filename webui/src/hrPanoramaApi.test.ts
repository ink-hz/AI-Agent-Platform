import { afterEach, describe, expect, it, vi } from "vitest";

import { createHrPanoramaApi } from "./hrPanoramaApi";

const IDS = {
  source: "11111111-1111-4111-8111-111111111111",
  run: "33333333-3333-4333-8333-333333333333",
  conversation: "44444444-4444-4444-8444-444444444444",
  insight: "55555555-5555-4555-8555-555555555555",
  snapshot: "66666666-6666-4666-8666-666666666666",
  observation: "77777777-7777-4777-8777-777777777777",
  turn: "88888888-8888-4888-8888-888888888888",
};

const source = {
  source_id: IDS.source, source_kind: "company", canonical_name: "联合光电",
  aliases: ["Union Optech"], approved_urls: ["https://example.com/jobs"], active: true,
  created_at: "2026-09-05T08:00:00Z", updated_at: "2026-09-05T08:00:00Z",
};
const insight = {
  insight_version_id: IDS.insight, run_id: IDS.run, version_number: 2,
  selected_source_ids: [IDS.source], snapshot_ids: [IDS.snapshot],
  facts: [{ fact_id: "f1", text: "公开招聘结构工程师", snapshot_id: IDS.snapshot,
    observation_id: IDS.observation, source_url: "https://example.com/jobs/1", observed_at: "2026-09-05T08:00:00Z" }],
  inferences: [{ text: "结构投入增加", basis_fact_ids: ["f1"] }],
  unknowns: [{ text: "实际 HC 未公开" }], direction_clusters: { 结构设计: 4 },
  summary: "结构人才需求上升", source_conversation_id: IDS.conversation,
  source_turn_id: IDS.turn, agent_id: "hr-intelligence-producer", model_version: "configured-model-v1",
  created_at: "2026-09-05T08:02:00Z",
};
const snapshot = {
  snapshot_id: IDS.snapshot, run_id: IDS.run, source_id: IDS.source,
  public_job_key: "job-1", title: "结构工程师", location: "中山",
  duty_excerpt: "负责精密结构设计", requirement_excerpt: "五年以上光学行业经验",
  source_url: "https://example.com/jobs/1", observed_at: "2026-09-05T08:00:00Z",
  content_sha256: "a".repeat(64), status: "open", created_at: "2026-09-05T08:01:00Z",
};
const report = { insight, sources: [source], snapshots: [snapshot] };

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("HR Panorama read-only API", () => {
  it("exposes only published-report reads to the HR workbench", () => {
    const api = createHrPanoramaApi("ignored") as unknown as Record<string, unknown>;

    expect(Object.keys(api).sort()).toEqual(["currentReport", "listReports", "report"]);
    expect(api).not.toHaveProperty("addCompany");
    expect(api).not.toHaveProperty("startRun");
    expect(api).not.toHaveProperty("runStatus");
  });

  it("loads the newest published report without a mutation", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ items: [insight] }))
      .mockResolvedValueOnce(json(report));

    const selected = await createHrPanoramaApi("ignored").currentReport();

    expect(selected?.insight.summary).toBe("结构人才需求上升");
    expect(fetcher.mock.calls.map(([request]) => String(request))).toEqual([
      "/api/hr/panorama/reports?limit=1",
      `/api/hr/panorama/reports/${IDS.insight}`,
    ]);
    for (const [, init] of fetcher.mock.calls) {
      expect(init).toMatchObject({ cache: "no-store", credentials: "same-origin" });
      expect(new Headers(init?.headers).get("Accept")).toBe("application/json");
      expect(init?.method).toBeUndefined();
    }
  });

  it("returns null when no publication exists", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ items: [] }));
    await expect(createHrPanoramaApi("ignored").currentReport()).resolves.toBeNull();
  });

  it("lists history and opens a historical report", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ items: [insight] }))
      .mockResolvedValueOnce(json(report));
    const api = createHrPanoramaApi("ignored");

    expect((await api.listReports())[0].modelVersion).toBe("configured-model-v1");
    expect((await api.report(IDS.insight)).snapshots[0].title).toBe("结构工程师");
  });

  it("rejects an AI inference that does not bind to raw evidence", async () => {
    const invalid = { ...insight, inferences: [{ text: "无依据判断", basis_fact_ids: ["missing"] }] };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ...report, insight: invalid }));

    await expect(createHrPanoramaApi("ignored").report(IDS.insight)).rejects.toThrow("HR Panorama response invalid");
  });

  it("rejects a detail response for another resource", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ...report, insight: { ...insight, insight_version_id: IDS.source } }));
    await expect(createHrPanoramaApi("ignored").report(IDS.insight)).rejects.toThrow("HR Panorama response invalid");
  });
});
