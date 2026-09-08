export type CompanyCoverage = {
  state: string;
  observedAt: string | null;
  jobCount: number | null;
  limitations: string[];
  documentLimitations: string[];
};

export type CompanySummary = {
  companyKey: string;
  canonicalName: string;
  aliases: string[];
  summary: string | null;
  coverage: CompanyCoverage | null;
};

export type CompanyFact = {
  factId: string;
  text: string;
  evidenceSha256: string;
  sourceUrl: string;
  observedAt: string;
};
export type CompanyInference = {
  inferenceId: string;
  text: string;
  claimType?: string;
  basisFactIds: string[];
};
export type CompanyRecommendation = {
  recommendationId: string;
  text: string;
  targetTasks?: string[];
  basisFactIds: string[];
};
export type CompanyAlternative = {
  alternativeId: string;
  text: string;
  challengedInferenceIds: string[];
  basisFactIds: string[];
};
export type CompanyUnit = {
  unitId: string;
  kind: "company";
  scopeKey: string;
  response: {
    summary: string;
    confidence: string;
    facts: CompanyFact[];
    inferences: CompanyInference[];
    recommendations: CompanyRecommendation[];
    alternatives: CompanyAlternative[];
    unknowns: string[];
  };
};

export type MetricCount = { key: string; count: number };
export type CompanyMetrics = {
  jobCount: number;
  directions: Record<string, number>;
  secondaryDirections: Record<string, number>;
  locations: Record<string, number>;
  tracks: Record<string, number>;
  seniority: Record<string, number>;
  jobFamilies: Record<string, number>;
  skills: Record<string, number>;
  sampleSnapshotIds: string[];
};

export type CompanyDirectory = {
  bundleId: string;
  generatedAt: string;
  items: CompanySummary[];
  topics: { state: "blocked" };
};
export type CompanyDetail = {
  bundleId: string;
  generatedAt: string;
  company: CompanySummary;
  units: CompanyUnit[];
  metrics: CompanyMetrics | null;
};

export type CompanyJob = {
  jobId: string;
  companyKey: string;
  title: string;
  location: string | null;
  status: string | null;
  dutyExcerpt: string | null;
  requirementExcerpt: string | null;
  sourceUrl: string;
  observedAt: string;
  [key: string]: unknown;
};
export type CompanyJobsPage = {
  bundleId: string;
  companyKey: string;
  items: CompanyJob[];
  total: number;
  offset: number;
  limit: number;
};

export type CompanyJobsFilters = {
  offset?: number;
  limit?: number;
  location?: string;
  status?: string;
};

export type HrCompanyIntelligenceApi = {
  companies(signal?: AbortSignal): Promise<CompanyDirectory | null>;
  company(
    companyKey: string,
    bundleId?: string,
    signal?: AbortSignal,
  ): Promise<CompanyDetail>;
  jobs(
    companyKey: string,
    bundleId: string,
    filters?: CompanyJobsFilters,
    signal?: AbortSignal,
  ): Promise<CompanyJobsPage>;
  parseCompanyDetail(value: unknown): CompanyDetail;
};
