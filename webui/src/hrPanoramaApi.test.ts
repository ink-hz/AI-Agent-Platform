import { afterEach, describe, expect, it, vi } from "vitest";

import { createHrPanoramaApi } from "./hrPanoramaApi";

const IDS = {
  source: "11111111-1111-4111-8111-111111111111",
  source2: "22222222-2222-4222-8222-222222222222",
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
  source_turn_id: IDS.turn, agent_id: "hr-bot", model_version: "gpt-5",
  created_at: "2026-09-05T08:02:00Z",
};
const snapshot = {
  snapshot_id: IDS.snapshot, run_id: IDS.run, source_id: IDS.source,
  public_job_key: "job-1", title: "结构工程师", location: "中山",
  duty_excerpt: "负责精密结构设计", requirement_excerpt: "五年以上光学行业经验",
  source_url: "https://example.com/jobs/1", observed_at: "2026-09-05T08:00:00Z",
  content_sha256: "a".repeat(64), status: "open", created_at: "2026-09-05T08:01:00Z",
};
const run = {
  run_id: IDS.run, selected_source_ids: [IDS.source], conversation_id: IDS.conversation,
  state: "running", error_code: null, source_failures: {}, row_version: 2,
  started_at: "2026-09-05T08:00:30Z", finished_at: null,
  created_at: "2026-09-05T08:00:00Z", updated_at: "2026-09-05T08:00:30Z",
};

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("HR Panorama API", () => {
  it("uses authenticated no-store requests and strictly parses every public response", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ items: [source] }))
      .mockResolvedValueOnce(json({ items: [insight] }))
      .mockResolvedValueOnce(json({ insight, sources: [source], snapshots: [snapshot] }))
      .mockResolvedValueOnce(json(run));
    const api = createHrPanoramaApi("csrf-token");

    expect((await api.listCompanies())[0].canonicalName).toBe("联合光电");
    expect((await api.listReports())[0].facts[0].sourceUrl).toBe("https://example.com/jobs/1");
    expect((await api.report(IDS.insight)).snapshots[0].requirementExcerpt).toContain("光学");
    expect((await api.runStatus(IDS.run)).state).toBe("running");

    for (const [, init] of fetcher.mock.calls) {
      expect(init).toMatchObject({ cache: "no-store", credentials: "same-origin" });
      expect(new Headers(init?.headers).get("Accept")).toBe("application/json");
    }
  });

  it("sends bounded mutations with CSRF and idempotency while preserving the exact retry scope", async () => {
    const requestId = "99999999-9999-4999-8999-999999999999";
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(source))
      .mockResolvedValueOnce(json({ ...run, selected_source_ids: [IDS.source2] }, 202));
    const api = createHrPanoramaApi("csrf-token");

    await api.addCompany({ canonicalName: "联合光电", aliases: [], approvedUrls: ["https://example.com/jobs"] }, requestId);
    await api.startRun({ sourceIds: [IDS.source2], conversationId: IDS.conversation }, requestId);

    const [, addInit] = fetcher.mock.calls[0];
    expect(new Headers(addInit?.headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(new Headers(addInit?.headers).get("Idempotency-Key")).toBe(requestId);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
      source_ids: [IDS.source2], conversation_id: IDS.conversation,
    });
  });

  it("lets the server create the dedicated execution conversation when one is not supplied", async () => {
    const requestId = "99999999-9999-4999-8999-999999999999";
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(run, 202));

    await createHrPanoramaApi("csrf").startRun({ sourceIds: [IDS.source] }, requestId);

    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ source_ids: [IDS.source] });
  });

  it.each([
    [{ ...source, credential: "secret" }],
    [{ ...source, approved_urls: ["http://example.com/jobs"] }],
  ])("rejects an invalid source contract", async (invalid) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ items: [invalid] }));
    await expect(createHrPanoramaApi("csrf").listCompanies()).rejects.toThrow("HR Panorama response invalid");
  });

  it("rejects reports whose evidence references are inconsistent", async () => {
    const invalid = { ...insight, inferences: [{ text: "无依据判断", basis_fact_ids: ["missing"] }] };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ insight: invalid, sources: [source], snapshots: [snapshot] }));
    await expect(createHrPanoramaApi("csrf").report(IDS.insight)).rejects.toThrow("HR Panorama response invalid");
  });

  it("rejects impossible run lifecycle combinations", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ...run, state: "completed", finished_at: null }));
    await expect(createHrPanoramaApi("csrf").runStatus(IDS.run)).rejects.toThrow("HR Panorama response invalid");
  });

  it("parses a real lowercase partial-failure response without exposing the code to presentation", async () => {
    const partial = { ...run, selected_source_ids: [IDS.source, IDS.source2], state: "partially_completed", finished_at: "2026-09-05T08:02:00Z", source_failures: { [IDS.source2]: "search_unavailable" } };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(partial));
    const parsed = await createHrPanoramaApi("csrf").runStatus(IDS.run);
    expect(parsed.state).toBe("partially_completed");
    expect(parsed.sourceFailures).toEqual({ [IDS.source2]: "search_unavailable" });
  });

  it("rejects a detail response that does not belong to the requested resource", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ ...run, run_id: IDS.source2 }))
      .mockResolvedValueOnce(json({ insight: { ...insight, insight_version_id: IDS.source2 }, sources: [source], snapshots: [snapshot] }));
    const api = createHrPanoramaApi("csrf");
    await expect(api.runStatus(IDS.run)).rejects.toThrow("HR Panorama response invalid");
    await expect(api.report(IDS.insight)).rejects.toThrow("HR Panorama response invalid");
  });

  it("rejects a start response whose execution scope differs from the request", async () => {
    const requestId = "99999999-9999-4999-8999-999999999999";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ...run, selected_source_ids: [IDS.source2] }, 202));
    await expect(createHrPanoramaApi("csrf").startRun({ sourceIds: [IDS.source] }, requestId)).rejects.toThrow("HR Panorama response invalid");
  });
});
