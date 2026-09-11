import { readFileSync, writeFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { parseCompanyDetail, parseCompanyDirectory, parseCompanyJobs } from "./hrCompanyIntelligenceApi";
import { serializeHrConversationText } from "./workspaces/hr/hrIntelligenceReference";

const artifact = process.env.HR_COMPANY_HTTP_RESPONSES;

function snakeKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(snakeKeys);
  if (value && typeof value === "object") return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [key.replace(/[A-Z]/g, letter => `_${letter.toLowerCase()}`), snakeKeys(child)]),
  );
  return value;
}

describe.skipIf(!artifact)("actual authenticated company HTTP responses", () => {
  it("parses the actual API directory, every company detail and a job page", () => {
    const responses = JSON.parse(readFileSync(artifact!, "utf8"));
    const directory = parseCompanyDirectory(responses.directory);
    const details = responses.details.map((value: unknown) => parseCompanyDetail(value));
    const jobs = parseCompanyJobs(responses.jobs);
    expect(details).toHaveLength(directory.items.length);
    expect(details.every((detail: ReturnType<typeof parseCompanyDetail>) =>
      detail.bundleId === directory.bundleId)).toBe(true);
    expect(jobs.bundleId).toBe(directory.bundleId);
    expect(jobs.items.length).toBeLessThanOrEqual(jobs.limit);
    for (const detail of details) {
      const source = responses.details.find((value: { company: { company_key: string } }) =>
        value.company.company_key === detail.company.companyKey);
      for (const unit of detail.units) {
        const original = source.units.find((value: { unit_id: string }) => value.unit_id === unit.unitId);
        expect(original).toBeDefined();
        expect(snakeKeys(unit.response)).toEqual(expect.objectContaining({
          summary: original.response.summary, facts: original.response.facts,
          inferences: original.response.inferences, recommendations: original.response.recommendations,
          alternatives: original.response.alternatives, unknowns: original.response.unknowns,
          confidence: original.response.confidence,
        }));
      }
      expect(detail.metrics === null ? null : Object.fromEntries(
        Object.entries(detail.metrics).map(([key, value]) => [key.replace(/[A-Z]/g, letter => `_${letter.toLowerCase()}`), value]),
      )).toEqual(source.metrics);
    }
    const chosen = details[0];
    const unit = chosen.units[0];
    const fact = unit.response.facts[0];
    const selectedText = serializeHrConversationText("请结合这份公司材料讨论岗位要求。", [{
      key: `${chosen.bundleId}:${unit.unitId}:fact:${fact.factId}`,
      bundleId: chosen.bundleId, companyKey: chosen.company.companyKey,
      companyName: chosen.company.canonicalName, label: "公司事实依据",
      generatedAt: chosen.generatedAt, excerpt: fact.text, sourceUrls: [fact.sourceUrl],
      unitId: unit.unitId, claimType: "fact", localId: fact.factId,
    }]);
    expect(selectedText).toContain(chosen.bundleId);
    expect(selectedText).toContain(JSON.stringify(fact.text));
    if (process.env.HR_COMPANY_REFERENCE_OUTPUT) writeFileSync(process.env.HR_COMPANY_REFERENCE_OUTPUT, selectedText);
  });
});
