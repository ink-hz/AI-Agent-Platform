/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createHrCompanyIntelligenceApi,
  parseCompanyDirectory,
} from "./hrCompanyIntelligenceApi";
import companiesFixture from "../../backend/tests/fixtures/hr_intelligence_company/companies.json";
import detailFixture from "../../backend/tests/fixtures/hr_intelligence_company/company-insta360.json";
import missingMetricsFixture from "../../backend/tests/fixtures/hr_intelligence_company/company-scantech-missing-metrics.json";
import jobsFixture from "../../backend/tests/fixtures/hr_intelligence_company/company-insta360-jobs-page.json";
import {
  parseCompanyDetail,
  parseCompanyJobs,
} from "./hrCompanyIntelligenceApi";

const bundleId = "11111111-1111-4111-8111-111111111111";
const directory = {
  bundle_id: bundleId,
  generated_at: "2026-09-08T08:00:00Z",
  items: [
    {
      company_key: "acme",
      canonical_name: "艾克米",
      aliases: ["ACME"],
      summary: null,
      coverage: {
        state: "partial",
        observed_at: null,
        job_count: null,
        limitations: ["部分来源暂不可用"],
        document_limitations: [],
      },
    },
  ],
  topics: { state: "blocked" },
};

afterEach(() => vi.restoreAllMocks());

describe("HR company intelligence parser", () => {
  it("consumes the real-bundle-derived fixtures without losing independent records", () => {
    const parsedDirectory = parseCompanyDirectory(companiesFixture);
    const parsedDetail = parseCompanyDetail(detailFixture);
    const parsedMissing = parseCompanyDetail(missingMetricsFixture);
    const parsedJobs = parseCompanyJobs(jobsFixture);
    expect(parsedDirectory.items).toHaveLength(companiesFixture.items.length);
    expect(
      parsedDetail.units[0].response.facts.some((fact) =>
        fact.sourceUrl.includes("insta360.com"),
      ),
    ).toBe(true);
    expect(parsedMissing.metrics).toBeNull();
    expect(parsedJobs.items).toHaveLength(jobsFixture.items.length);
    expect(parsedJobs.items[0].requirementExcerpt).toBe(jobsFixture.items[0].requirement_excerpt);
  });
  it("parses the independent company directory without inventing missing counts", () => {
    const parsed = parseCompanyDirectory(directory);
    expect(parsed.items[0].coverage?.jobCount).toBeNull();
    expect(parsed.items[0].aliases).toEqual(["ACME"]);
    expect(parsed.topics.state).toBe("blocked");
  });

  it("rejects unsafe source URLs in facts", () => {
    const detail = {
      bundle_id: bundleId,
      generated_at: directory.generated_at,
      company: directory.items[0],
      metrics: null,
      units: [
        {
          unit_id: "unit-acme",
          kind: "company",
          scope_key: "acme",
          response: {
            summary: "摘要",
            confidence: "medium",
            facts: [
              {
                fact_id: "f1",
                text: "事实",
                evidence_sha256: "a".repeat(64),
                source_url: "javascript:alert(1)",
                observed_at: directory.generated_at,
              },
            ],
            inferences: [],
            recommendations: [],
            alternatives: [],
            unknowns: [],
          },
        },
      ],
    };
    expect(() =>
      createHrCompanyIntelligenceApi("csrf").parseCompanyDetail(detail),
    ).toThrow("invalid company intelligence response");
  });
  it("rejects non-numeric company metric buckets", () => {
    const invalidMetrics = structuredClone(detailFixture) as any;
    invalidMetrics.metrics.directions = { 算法: "很多" };
    expect(() => parseCompanyDetail(invalidMetrics)).toThrow(
      "invalid company intelligence response",
    );
  });
  it("rejects units and jobs belonging to another company", () => {
    const wrongUnit = structuredClone(detailFixture) as any;
    wrongUnit.units[0].scope_key = "another-company";
    expect(() => parseCompanyDetail(wrongUnit)).toThrow(
      "invalid company intelligence response",
    );
    const wrongJob = structuredClone(jobsFixture) as any;
    wrongJob.items[0].company_key = "another-company";
    expect(() => parseCompanyJobs(wrongJob)).toThrow(
      "invalid company intelligence response",
    );
  });
});

describe("HR company intelligence API", () => {
  it("uses the configured platform prefix and bypasses shared caches", async () => {
    history.replaceState({}, "", "/_preview/dingtalk-r1/hr/panorama");
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    await createHrCompanyIntelligenceApi("csrf").companies();
    expect(fetcher).toHaveBeenCalledWith(
      "/_preview/dingtalk-r1/api/hr/panorama/companies",
      expect.objectContaining({ cache: "no-store" }),
    );
    history.replaceState({}, "", "/");
  });
  it("maps an empty publication to null", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    await expect(
      createHrCompanyIntelligenceApi("csrf").companies(),
    ).resolves.toBeNull();
  });

  it("pins detail and paginated jobs to the directory bundle", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
      requests.push(String(request));
      return new Response(
        JSON.stringify(
          requests.length === 1
            ? {
                bundle_id: bundleId,
                generated_at: directory.generated_at,
                company: directory.items[0],
                units: [],
                metrics: null,
              }
            : {
                bundle_id: bundleId,
                company_key: "acme",
                items: [],
                total: 0,
                offset: 25,
                limit: 25,
              },
        ),
        { status: 200 },
      );
    });
    const api = createHrCompanyIntelligenceApi("csrf");
    await api.company("acme", bundleId);
    await api.jobs("acme", bundleId, {
      offset: 25,
      limit: 25,
      location: "上海",
      status: "open",
    });
    expect(requests).toEqual([
      `/api/hr/panorama/companies/acme?bundle_id=${bundleId}`,
      `/api/hr/panorama/companies/acme/jobs?bundle_id=${bundleId}&offset=25&limit=25&location=%E4%B8%8A%E6%B5%B7&status=open`,
    ]);
  });
  it("rejects a response that does not match the requested bundle", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(detailFixture),
    );
    await expect(
      createHrCompanyIntelligenceApi("csrf").company(
        detailFixture.company.company_key,
        "22222222-2222-4222-8222-222222222222",
      ),
    ).rejects.toThrow("invalid company intelligence response");
  });
});
