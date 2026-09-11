import { readFileSync, writeFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  parseTopicDirectory,
  parseTopicDetail,
} from "./hrTopicIntelligenceApi";
import { formatHrIntelligenceReferences } from "./workspaces/hr/hrIntelligenceReference";
const evidencePath = process.env.HR_TOPIC_HTTP_RESPONSES;
describe.skipIf(!evidencePath)(
  "real authenticated topic HTTP responses",
  () => {
    it("preserves produced identities, scope and seven analysis fields", () => {
      const evidence = JSON.parse(readFileSync(evidencePath!, "utf8"));
      const directory = parseTopicDirectory(evidence.topicDirectory);
      expect(directory.state).toBe("available");
      expect(directory.items.length).toBeGreaterThan(0);
      const details = Array.isArray(evidence.topicDetails)
        ? evidence.topicDetails
        : Object.values(evidence.topicDetails);
      for (const raw of details) {
        const detail = parseTopicDetail(raw);
        expect(
          directory.items.some((item) => item.topicId === detail.topic.topicId),
        ).toBe(true);
        expect(detail.units.length).toBe(detail.topic.unitIds.length);
        const selected = formatHrIntelligenceReferences([
          {
            kind: "topic",
            key: `${detail.bundleId}:topic:${detail.topic.topicId}`,
            bundleId: detail.bundleId,
            topicId: detail.topic.topicId,
            topicTitle: detail.topic.title,
            question: detail.topic.question,
            scope: detail.topic.scope,
            analysisState: detail.topic.analysisState,
            limitations: detail.topic.limitations,
            generatedAt: detail.generatedAt,
            excerpt: detail.topic.summary ?? "",
            label: "专题情报",
            sourceUrls: [
              ...new Set(
                detail.units.flatMap((unit) =>
                  unit.response.facts.map((fact) => fact.sourceUrl),
                ),
              ),
            ],
          },
        ]);
        expect(selected).toContain(
          `topic_id: ${JSON.stringify(detail.topic.topicId)}`,
        );
        expect(selected).toContain(
          `scope: ${JSON.stringify(detail.topic.scope)}`,
        );
        if (process.env.HR_TOPIC_SELECTED_REFERENCE_OUT && raw === details[0])
          writeFileSync(process.env.HR_TOPIC_SELECTED_REFERENCE_OUT, selected);
      }
    });
  },
);
