/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createHrTopicIntelligenceApi,
  parseTopicDirectory,
  parseTopicDetail,
} from "./hrTopicIntelligenceApi";
import companyFixture from "../../backend/tests/fixtures/hr_intelligence_company/company-insta360.json";
export const topicWire = {
  topic_id: "study",
  title: "人才布局",
  question: "样本能说明什么？",
  scope: {
    description: "公开岗位",
    company_keys: ["insta360", "sample-only"],
    tracks: ["social"],
  },
  analysis_state: "limited",
  unit_ids: [companyFixture.units[0].unit_id],
  discussed_companies: [
    {
      company_key: "insta360",
      unit_id: companyFixture.units[0].unit_id,
      claim_ids: [companyFixture.units[0].response.inferences[0].inference_id],
      explanation: "正文讨论了该公司的布局。",
    },
  ],
  limitations: ["不能推断真实编制"],
  summary: "公开样本集中",
};
export const topicDetailWire = {
  bundle_id: companyFixture.bundle_id,
  generated_at: companyFixture.generated_at,
  topic: topicWire,
  units: [{ ...companyFixture.units[0], kind: "topic", scope_key: "study" }],
  companies: [{ company_key: "insta360", canonical_name: "影石" }],
};
afterEach(() => vi.restoreAllMocks());
describe("topic intelligence API", () => {
  it("preserves scope, explicit relations and independent analysis fields", () => {
    const directory = parseTopicDirectory({
      bundle_id: topicDetailWire.bundle_id,
      generated_at: topicDetailWire.generated_at,
      state: "available",
      items: [topicWire],
    });
    expect(directory.items[0].scope.companyKeys).toEqual([
      "insta360",
      "sample-only",
    ]);
    const detail = parseTopicDetail(topicDetailWire);
    expect(detail.companies).toEqual([
      { companyKey: "insta360", canonicalName: "影石" },
    ]);
    expect(detail.units[0].response.alternatives).toHaveLength(
      companyFixture.units[0].response.alternatives.length,
    );
  });
  it("keeps missing catalog distinct from a known empty catalog", () => {
    expect(
      parseTopicDirectory({
        bundle_id: topicDetailWire.bundle_id,
        generated_at: topicDetailWire.generated_at,
        state: "metadata_missing",
        items: [],
      }).state,
    ).toBe("metadata_missing");
  });
  it("rejects unsafe evidence URLs and wrong response identity", async () => {
    const unsafe = structuredClone(topicDetailWire);
    unsafe.units[0].response.facts[0].source_url = "javascript:alert(1)";
    expect(() => parseTopicDetail(unsafe)).toThrow();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(topicDetailWire)),
    );
    await expect(
      createHrTopicIntelligenceApi("csrf").topic(
        "different",
        topicDetailWire.bundle_id,
      ),
    ).rejects.toThrow();
  });
  it("sends an authenticated cancellable request pinned to the supplied publication", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(topicDetailWire)));
    const signal = new AbortController().signal;
    await createHrTopicIntelligenceApi("csrf").topic(
      "study",
      topicDetailWire.bundle_id,
      signal,
    );
    expect(fetch).toHaveBeenCalledWith(
      `/api/hr/panorama/topics/study?bundle_id=${topicDetailWire.bundle_id}`,
      expect.objectContaining({
        credentials: "same-origin",
        cache: "no-store",
        signal,
      }),
    );
  });
});
