/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Account } from "../../auth";
import { HrPanoramaWorkspace } from "./HrPanoramaWorkspace";

const account: Account = {
  internal_user_id: "member", display_name: "HR", role: "member", departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};
const sourceId = "11111111-1111-4111-8111-111111111111";
const batchId = "22222222-2222-4222-8222-222222222222";
const publicationId = "33333333-3333-4333-8333-333333333333";
const insightId = "44444444-4444-4444-8444-444444444444";
const snapshotId = "55555555-5555-4555-8555-555555555555";
const observationId = "66666666-6666-4666-8666-666666666666";
const publication = {
  publication_id: publicationId, batch_id: batchId, insight_version_id: insightId,
  bundle_id: publicationId, manifest_sha256: "b".repeat(64), generated_at: "2026-09-06T08:20:00Z",
  coverage_state: "partial", published_at: "2026-09-06T08:30:00Z",
  source_coverage: [{ source_id: sourceId, state: "succeeded", observed_at: "2026-09-06T08:00:00Z",
    source_urls: ["https://example.com/jobs", "https://example.com/campus"], job_count: 1,
    channel_failures: { "https://example.com/campus": "source_timeout" } }],
};
const insight = {
  insight_version_id: insightId, run_id: null, production_batch_id: batchId, version_number: 1,
  selected_source_ids: [sourceId], snapshot_ids: [snapshotId],
  facts: [{ fact_id: "f1", text: "联合光电公开招聘高级结构工程师", snapshot_id: snapshotId,
    observation_id: observationId, source_url: "https://example.com/jobs/1", observed_at: "2026-09-06T08:00:00Z" }],
  inferences: [{ text: "结构研发投入明确", basis_fact_ids: ["f1"] }],
  unknowns: [{ text: "实际 HC 未公开" }], direction_clusters: { 结构: 1 },
  summary: "重点企业继续投入结构研发人才", source_conversation_id: null, source_turn_id: null,
  agent_id: "hr-intelligence-producer", model_version: "configured:gpt:2026-09", created_at: "2026-09-06T08:20:00Z",
};
const report = {
  publication, insight,
  sources: [{ source_id: sourceId, source_kind: "company", canonical_name: "联合光电", aliases: [],
    approved_urls: ["https://example.com/jobs", "https://example.com/campus"], active: true,
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-06T08:00:00Z" }],
  snapshots: [{ snapshot_id: snapshotId, run_id: null, production_batch_id: batchId,
    observation_id: observationId, source_id: sourceId, public_job_key: "job-1", title: "高级结构工程师",
    location: "中山", duty_excerpt: "负责精密结构研发", requirement_excerpt: "五年以上量产经验",
    source_url: "https://example.com/jobs/1", observed_at: "2026-09-06T08:00:00Z",
    content_sha256: "a".repeat(64), status: "open", created_at: "2026-09-06T08:01:00Z" }],
  evidence: [{ source_id: sourceId, source_url: "https://example.com/jobs", attempt_number: 1,
    state: "succeeded", error_code: null, sha256: "a".repeat(64), mime: "text/html", size_bytes: 2048,
    normalized_job_count: 1, observed_at: "2026-09-06T08:00:00Z" }],
  analysis_usage: [{ provider: "openai", model: "gpt-5.6" }],
};

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

it("uses a published report without starting collection and exposes raw evidence downloads", async () => {
  const requests: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request, init) => {
    const path = String(request); requests.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/hr/panorama/current") return json(report);
    if (path === "/api/hr/panorama/reports?limit=100") return json({ items: [{ publication, insight }] });
    throw new Error(`unexpected request ${path}`);
  });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  try {
    await act(async () => { root.render(<HrPanoramaWorkspace account={account} />); await Promise.resolve(); await Promise.resolve(); });
    expect(container.textContent).toContain("重点企业继续投入结构研发人才");
    expect(container.textContent).toContain("AI 分析");
    expect(container.textContent).toContain("原始岗位数据");
    const evidenceTab = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent === "来源证据");
    await act(async () => evidenceTab?.click());
    expect(container.textContent).toContain("原始来源响应");
    expect(container.querySelector<HTMLAnchorElement>(`a[href="/api/hr/panorama/reports/${publicationId}/evidence/${"a".repeat(64)}"]`)?.textContent).toBe("下载原始响应");
    expect(requests).toEqual(["GET /api/hr/panorama/reports?limit=100", "GET /api/hr/panorama/current"]);
    expect(requests.every((item) => item.startsWith("GET "))).toBe(true);
  } finally {
    await act(async () => root.unmount()); container.remove();
  }
});

afterEach(() => vi.restoreAllMocks());
beforeEach(() => vi.restoreAllMocks());
