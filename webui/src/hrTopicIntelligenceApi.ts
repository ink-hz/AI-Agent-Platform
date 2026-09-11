import { platformPath } from "./auth";
import {
  HrCompanyIntelligenceApiError,
  parseAnalysisUnit,
} from "./hrCompanyIntelligenceApi";
import type { CompanyUnit } from "./hrCompanyIntelligenceTypes";

export type TopicSummary = {
  topicId: string;
  title: string;
  question: string;
  scope: { description: string; companyKeys: string[]; tracks: string[] };
  analysisState: "available" | "limited" | "insufficient_evidence" | "missing";
  unitIds: string[];
  discussedCompanies: {
    companyKey: string;
    unitId: string;
    claimIds: string[];
    explanation: string;
  }[];
  limitations: string[];
  summary: string | null;
};
export type TopicDirectory = {
  bundleId: string;
  generatedAt: string;
  state: "available" | "metadata_missing";
  items: TopicSummary[];
};
export type TopicDetail = {
  bundleId: string;
  generatedAt: string;
  topic: TopicSummary;
  units: (Omit<CompanyUnit, "kind"> & { kind: "topic" | "track" })[];
  companies: { companyKey: string; canonicalName: string }[];
};
export type HrTopicIntelligenceApi = {
  topics(signal?: AbortSignal): Promise<TopicDirectory | null>;
  topic(
    topicId: string,
    bundleId?: string,
    signal?: AbortSignal,
  ): Promise<TopicDetail>;
};
const invalid = (): never => {
  throw new Error("invalid topic intelligence response");
};
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : invalid();
const string = (value: unknown): string =>
  typeof value === "string" ? value : invalid();
const array = <T>(value: unknown, parser: (value: unknown) => T): T[] =>
  Array.isArray(value) ? value.map(parser) : invalid();
const strings = (value: unknown) => array(value, string);
const identity = (value: unknown) => {
  const v = record(value);
  const bundleId = string(v.bundle_id),
    generatedAt = string(v.generated_at);
  if (
    !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(bundleId) ||
    Number.isNaN(Date.parse(generatedAt))
  )
    invalid();
  return { bundleId, generatedAt };
};
function parseTopic(value: unknown): TopicSummary {
  const v = record(value),
    scope = record(v.scope);
  if (
    !["available", "limited", "insufficient_evidence", "missing"].includes(
      string(v.analysis_state),
    )
  )
    invalid();
  const parsed = {
    topicId: string(v.topic_id),
    title: string(v.title),
    question: string(v.question),
    scope: {
      description: string(scope.description),
      companyKeys: strings(scope.company_keys),
      tracks: strings(scope.tracks),
    },
    analysisState: v.analysis_state as TopicSummary["analysisState"],
    unitIds: strings(v.unit_ids),
    discussedCompanies: array(v.discussed_companies, (raw) => {
      const c = record(raw);
      return {
        companyKey: string(c.company_key),
        unitId: string(c.unit_id),
        claimIds: strings(c.claim_ids),
        explanation: string(c.explanation),
      };
    }),
    limitations: strings(v.limitations),
    summary: v.summary === null ? null : string(v.summary),
  };
  if (
    parsed.analysisState === "missing" &&
    (parsed.unitIds.length || parsed.discussedCompanies.length)
  )
    invalid();
  return parsed;
}
export function parseTopicDirectory(value: unknown): TopicDirectory {
  const v = record(value);
  if (v.state !== "available" && v.state !== "metadata_missing") invalid();
  const items = array(v.items, parseTopic);
  if (
    new Set(items.map((item) => item.topicId)).size !== items.length ||
    (v.state === "metadata_missing" && items.length)
  )
    invalid();
  return { ...identity(v), state: v.state as TopicDirectory["state"], items };
}
export function parseTopicDetail(value: unknown): TopicDetail {
  const v = record(value),
    topic = parseTopic(v.topic);
  const units = array(v.units, (raw) => {
    const unit = parseAnalysisUnit(raw);
    if (unit.kind === "company" || !topic.unitIds.includes(unit.unitId))
      invalid();
    return { ...unit, kind: unit.kind as "topic" | "track" };
  });
  const companies = array(v.companies, (raw) => {
    const c = record(raw);
    return {
      companyKey: string(c.company_key),
      canonicalName: string(c.canonical_name),
    };
  });
  if (
    units.length !== topic.unitIds.length ||
    new Set(units.map((unit) => unit.unitId)).size !== units.length
  )
    invalid();
  if (
    companies.some(
      (c) =>
        !topic.discussedCompanies.some((r) => r.companyKey === c.companyKey),
    )
  )
    invalid();
  return { ...identity(v), topic, units, companies };
}
export function createHrTopicIntelligenceApi(
  csrfToken: string,
): HrTopicIntelligenceApi {
  const get = (path: string, signal?: AbortSignal) =>
    fetch(platformPath(path), {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "X-CSRF-Token": csrfToken },
      signal,
    });
  return {
    async topics(signal) {
      const response = await get("/api/hr/panorama/topics", signal);
      if (response.status === 204) return null;
      if (!response.ok)
        throw new HrCompanyIntelligenceApiError(response.status);
      return parseTopicDirectory(await response.json());
    },
    async topic(topicId, bundleId, signal) {
      const query = bundleId
        ? `?bundle_id=${encodeURIComponent(bundleId)}`
        : "";
      const response = await get(
        `/api/hr/panorama/topics/${encodeURIComponent(topicId)}${query}`,
        signal,
      );
      if (!response.ok)
        throw new HrCompanyIntelligenceApiError(response.status);
      const parsed = parseTopicDetail(await response.json());
      if (
        parsed.topic.topicId !== topicId ||
        (bundleId && parsed.bundleId !== bundleId)
      )
        invalid();
      return parsed;
    },
  };
}
