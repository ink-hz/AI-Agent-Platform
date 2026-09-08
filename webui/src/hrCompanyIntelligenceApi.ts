import type {
  CompanyCoverage,
  CompanyDetail,
  CompanyDirectory,
  CompanyJob,
  CompanyJobsFilters,
  CompanyJobsPage,
  CompanyMetrics,
  CompanySummary,
  CompanyUnit,
  HrCompanyIntelligenceApi,
} from "./hrCompanyIntelligenceTypes";
import { platformPath } from "./auth";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/i;
const invalid = (): never => {
  throw new Error("invalid company intelligence response");
};
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : invalid();
const string = (value: unknown): string =>
  typeof value === "string" ? value : invalid();
const nullableString = (value: unknown): string | null =>
  value === null ? null : string(value);
const number = (value: unknown): number =>
  typeof value === "number" && Number.isFinite(value) ? value : invalid();
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.map(string) : invalid();
const array = <T>(value: unknown, parser: (item: unknown) => T): T[] =>
  Array.isArray(value) ? value.map(parser) : invalid();
const uuid = (value: unknown): string => {
  const parsed = string(value);
  return UUID.test(parsed) ? parsed : invalid();
};
const date = (value: unknown): string => {
  const parsed = string(value);
  return Number.isNaN(Date.parse(parsed)) ? invalid() : parsed;
};
const url = (value: unknown): string => {
  const parsed = string(value);
  try {
    const protocol = new URL(parsed).protocol;
    return protocol === "http:" || protocol === "https:" ? parsed : invalid();
  } catch {
    return invalid();
  }
};
const optionalStrings = (value: unknown): string[] | undefined =>
  value === undefined ? undefined : strings(value);
const counts = (value: unknown): Record<string, number> =>
  Object.fromEntries(
    Object.entries(record(value)).map(([key, count]) => [key, number(count)]),
  );

function parseCoverage(value: unknown): CompanyCoverage | null {
  if (value === null) return null;
  const item = record(value);
  return {
    state: string(item.state),
    observedAt: item.observed_at === null ? null : date(item.observed_at),
    jobCount: item.job_count === null ? null : number(item.job_count),
    limitations: strings(item.limitations),
    documentLimitations: strings(item.document_limitations),
  };
}

function parseSummary(value: unknown): CompanySummary {
  const item = record(value);
  return {
    companyKey: string(item.company_key),
    canonicalName: string(item.canonical_name),
    aliases: strings(item.aliases),
    summary: nullableString(item.summary),
    coverage: parseCoverage(item.coverage),
  };
}

export function parseCompanyDirectory(value: unknown): CompanyDirectory {
  const item = record(value);
  const topics = record(item.topics);
  if (topics.state !== "blocked") invalid();
  return {
    bundleId: uuid(item.bundle_id),
    generatedAt: date(item.generated_at),
    items: array(item.items, parseSummary),
    topics: { state: "blocked" },
  };
}

function parseUnit(value: unknown): CompanyUnit {
  const item = record(value);
  const response = record(item.response);
  if (item.kind !== "company") invalid();
  return {
    unitId: string(item.unit_id),
    kind: "company",
    scopeKey: string(item.scope_key),
    response: {
      summary: string(response.summary),
      confidence: string(response.confidence),
      facts: array(response.facts, (raw) => {
        const fact = record(raw);
        const sha = string(fact.evidence_sha256);
        if (!SHA256.test(sha)) invalid();
        return {
          factId: string(fact.fact_id),
          text: string(fact.text),
          evidenceSha256: sha,
          sourceUrl: url(fact.source_url),
          observedAt: date(fact.observed_at),
        };
      }),
      inferences: array(response.inferences, (raw) => {
        const claim = record(raw);
        return {
          inferenceId: string(claim.inference_id),
          text: string(claim.text),
          ...(claim.claim_type === undefined
            ? {}
            : { claimType: string(claim.claim_type) }),
          basisFactIds: strings(claim.basis_fact_ids),
        };
      }),
      recommendations: array(response.recommendations, (raw) => {
        const claim = record(raw);
        return {
          recommendationId: string(claim.recommendation_id),
          text: string(claim.text),
          ...(claim.target_tasks === undefined
            ? {}
            : { targetTasks: optionalStrings(claim.target_tasks) }),
          basisFactIds: strings(claim.basis_fact_ids),
        };
      }),
      alternatives: array(response.alternatives, (raw) => {
        const claim = record(raw);
        return {
          alternativeId: string(claim.alternative_id),
          text: string(claim.text),
          challengedInferenceIds: strings(claim.challenged_inference_ids),
          basisFactIds: strings(claim.basis_fact_ids),
        };
      }),
      unknowns: strings(response.unknowns),
    },
  };
}

function parseMetrics(value: unknown): CompanyMetrics | null {
  if (value === null) return null;
  const item = record(value);
  return {
    jobCount: number(item.job_count),
    directions: counts(item.directions),
    secondaryDirections: counts(item.secondary_directions),
    locations: counts(item.locations),
    tracks: counts(item.tracks),
    seniority: counts(item.seniority),
    jobFamilies: counts(item.job_families),
    skills: counts(item.skills),
    sampleSnapshotIds: strings(item.sample_snapshot_ids),
  };
}

export function parseCompanyDetail(value: unknown): CompanyDetail {
  const item = record(value);
  const parsed = {
    bundleId: uuid(item.bundle_id),
    generatedAt: date(item.generated_at),
    company: parseSummary(item.company),
    units: array(item.units, parseUnit),
    metrics: parseMetrics(item.metrics),
  };
  if (parsed.units.some((unit) => unit.scopeKey !== parsed.company.companyKey))
    invalid();
  return parsed;
}

function parseJob(value: unknown): CompanyJob {
  const item = record(value);
  return {
    ...item,
    jobId: string(item.job_id),
    companyKey: string(item.company_key),
    title: string(item.title),
    location: nullableString(item.location),
    status: nullableString(item.status),
    dutyExcerpt: nullableString(item.duty_excerpt),
    requirementExcerpt: nullableString(item.requirement_excerpt),
    sourceUrl: url(item.source_url),
    observedAt: date(item.observed_at),
  };
}

export function parseCompanyJobs(value: unknown): CompanyJobsPage {
  const item = record(value);
  const parsed = {
    bundleId: uuid(item.bundle_id),
    companyKey: string(item.company_key),
    items: array(item.items, parseJob),
    total: number(item.total),
    offset: number(item.offset),
    limit: number(item.limit),
  };
  if (parsed.items.some((job) => job.companyKey !== parsed.companyKey))
    invalid();
  return parsed;
}

export class HrCompanyIntelligenceApiError extends Error {
  constructor(readonly status: number) {
    super(`company intelligence request failed (${status})`);
  }
}

async function get(
  path: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(platformPath(path), {
    headers: { "X-CSRF-Token": csrfToken },
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
}

export function createHrCompanyIntelligenceApi(
  csrfToken: string,
): HrCompanyIntelligenceApi {
  return {
    async companies(signal) {
      const response = await get(
        "/api/hr/panorama/companies",
        csrfToken,
        signal,
      );
      if (response.status === 204) return null;
      if (!response.ok)
        throw new HrCompanyIntelligenceApiError(response.status);
      return parseCompanyDirectory(await response.json());
    },
    async company(companyKey, bundleId, signal) {
      const query = bundleId
        ? `?bundle_id=${encodeURIComponent(bundleId)}`
        : "";
      const response = await get(
        `/api/hr/panorama/companies/${encodeURIComponent(companyKey)}${query}`,
        csrfToken,
        signal,
      );
      if (!response.ok)
        throw new HrCompanyIntelligenceApiError(response.status);
      const parsed = parseCompanyDetail(await response.json());
      if (
        parsed.company.companyKey !== companyKey ||
        (bundleId && parsed.bundleId !== bundleId)
      )
        invalid();
      return parsed;
    },
    async jobs(companyKey, bundleId, filters = {}, signal) {
      const requestedOffset = filters.offset ?? 0;
      const requestedLimit = filters.limit ?? 25;
      const params = new URLSearchParams({
        bundle_id: bundleId,
        offset: String(requestedOffset),
        limit: String(requestedLimit),
      });
      if (filters.location) params.set("location", filters.location);
      if (filters.status) params.set("status", filters.status);
      const response = await get(
        `/api/hr/panorama/companies/${encodeURIComponent(companyKey)}/jobs?${params}`,
        csrfToken,
        signal,
      );
      if (!response.ok)
        throw new HrCompanyIntelligenceApiError(response.status);
      const parsed = parseCompanyJobs(await response.json());
      if (
        parsed.companyKey !== companyKey ||
        parsed.bundleId !== bundleId ||
        parsed.offset !== requestedOffset ||
        parsed.limit !== requestedLimit
      )
        invalid();
      return parsed;
    },
    parseCompanyDetail,
  };
}
