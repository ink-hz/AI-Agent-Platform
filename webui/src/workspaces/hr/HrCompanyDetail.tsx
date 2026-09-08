import { useEffect, useMemo, useState } from "react";
import type {
  CompanyDetail,
  CompanyJobsPage,
  CompanyMetrics,
  HrCompanyIntelligenceApi,
} from "../../hrCompanyIntelligenceTypes";
import {
  formatHrIntelligenceReferences,
  type HrIntelligenceReference,
} from "./hrIntelligenceReference";

const metricEntries = (value: unknown): [string, string][] => {
  if (Array.isArray(value))
    return value.flatMap((item) => {
      if (typeof item === "string") return [[item, ""]];
      if (item && typeof item === "object") {
        const record = item as Record<string, unknown>;
        const name = record.name ?? record.key ?? record.label;
        const count = record.count ?? record.value;
        return typeof name === "string"
          ? [[name, typeof count === "number" ? String(count) : ""]]
          : [];
      }
      return [];
    });
  if (value && typeof value === "object")
    return Object.entries(value as Record<string, unknown>)
      .filter(([, count]) => typeof count === "number")
      .map(([name, count]) => [name, String(count)]);
  return [];
};
const LABELS: Record<string, string> = {
  succeeded: "已覆盖",
  partial: "部分覆盖",
  failed: "暂不可用",
  not_observed: "尚未观察",
  empty_confirmed: "已核验，未发现",
  medium: "中",
  high: "高",
  low: "低",
  open: "开放",
  closed: "关闭",
  campus: "校招",
  social: "社招",
  intern: "实习",
  unknown: "未知",
  graduate: "应届",
  junior: "初级",
  mid: "中级",
  senior: "高级",
  unspecified: "未标注",
  research_development: "研发",
  sales_marketing: "销售与市场",
  supply_chain: "供应链",
  manufacturing: "制造",
  operations: "运营",
  product: "产品",
  quality: "质量",
  corporate: "职能",
  other: "其他",
};
const localize = (value: string) => LABELS[value] ?? value;
const localizeConfidence = localize;

export function HrCompanyDetail({
  detail,
  api,
  onSelectReference,
}: {
  detail: CompanyDetail;
  api: HrCompanyIntelligenceApi;
  onSelectReference?: (reference: HrIntelligenceReference) => void;
}) {
  const [jobsOpen, setJobsOpen] = useState(false);
  const [jobs, setJobs] = useState<CompanyJobsPage | null>(null);
  const [jobsError, setJobsError] = useState(false);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  const [location, setLocation] = useState("");
  const [status, setStatus] = useState("");
  const limit = 25;
  const [referenceError, setReferenceError] = useState(false);
  const emitReference = (reference: HrIntelligenceReference) => {
    try {
      formatHrIntelligenceReferences([reference]);
      setReferenceError(false);
      onSelectReference?.(reference);
    } catch {
      setReferenceError(true);
    }
  };
  useEffect(() => {
    setJobsOpen(false);
    setJobs(null);
    setJobsError(false);
    setOffset(0);
    setLocation("");
    setStatus("");
  }, [detail.company.companyKey, detail.bundleId]);
  useEffect(() => {
    if (!jobsOpen) return;
    const controller = new AbortController();
    setJobsLoading(true);
    setJobsError(false);
    api
      .jobs(
        detail.company.companyKey,
        detail.bundleId,
        {
          offset,
          limit,
          ...(location ? { location } : {}),
          ...(status ? { status } : {}),
        },
        controller.signal,
      )
      .then((page) => {
        if (!controller.signal.aborted) setJobs(page);
      })
      .catch(() => {
        if (!controller.signal.aborted) setJobsError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setJobsLoading(false);
      });
    return () => controller.abort();
  }, [
    api,
    detail.bundleId,
    detail.company.companyKey,
    jobsOpen,
    location,
    offset,
    status,
  ]);
  const facts = useMemo(
    () =>
      new Map(
        detail.units.flatMap((unit) =>
          unit.response.facts.map((fact) => [
            `${unit.unitId}:${fact.factId}`,
            fact,
          ]),
        ),
      ),
    [detail.units],
  );
  const select = (
    unitId: string,
    localId: string,
    label: string,
    text: string,
    basis: string[],
    claimType: string,
  ) =>
    emitReference({
      key: `${detail.bundleId}:${unitId}:${localId}`,
      bundleId: detail.bundleId,
      companyKey: detail.company.companyKey,
      companyName: detail.company.canonicalName,
      label,
      generatedAt: detail.generatedAt,
      excerpt: text,
      unitId,
      localId,
      claimType,
      sourceUrls: [
        ...new Set(
          basis
            .map((id) => facts.get(`${unitId}:${id}`)?.sourceUrl)
            .filter((url): url is string => Boolean(url)),
        ),
      ],
    });
  const action = (handler: () => void) =>
    onSelectReference ? (
      <button type="button" className="hr-company-reference" onClick={handler}>
        带入对话
      </button>
    ) : (
      <button
        type="button"
        className="hr-company-reference"
        disabled
        title="当前页面未连接对话选择器"
        aria-describedby="hr-company-reference-unavailable"
      >
        带入对话
      </button>
    );
  return (
    <article className="hr-company-detail">
      <header className="hr-company-detail-header">
        <div>
          <p>公司情报</p>
          <h2>{detail.company.canonicalName}</h2>
          {detail.company.aliases.length > 0 && (
            <span>亦称 {detail.company.aliases.join("、")}</span>
          )}
        </div>
        <div>
          <time dateTime={detail.generatedAt}>
            更新于 {new Date(detail.generatedAt).toLocaleString("zh-CN")}
          </time>
          {onSelectReference ? (
            <button
              type="button"
              className="hr-company-reference is-static"
              onClick={() =>
                emitReference({
                  key: `${detail.bundleId}:company:${detail.company.companyKey}`,
                  bundleId: detail.bundleId,
                  companyKey: detail.company.companyKey,
                  companyName: detail.company.canonicalName,
                  label: "公司情报",
                  generatedAt: detail.generatedAt,
                  excerpt:
                    detail.company.summary ??
                    detail.units
                      .map((unit) => unit.response.summary)
                      .join("；"),
                  sourceUrls: [
                    ...new Set(
                      detail.units.flatMap((unit) =>
                        unit.response.facts.map((fact) => fact.sourceUrl),
                      ),
                    ),
                  ],
                })
              }
            >
              带入公司情报
            </button>
          ) : (
            <button
              type="button"
              className="hr-company-reference is-static"
              disabled
              title="当前页面未连接对话选择器"
              aria-describedby="hr-company-reference-unavailable"
            >
              带入公司情报
            </button>
          )}
        </div>
      </header>
      {detail.company.summary && (
        <p className="hr-company-lead">{detail.company.summary}</p>
      )}
      {detail.company.coverage && (
        <aside className="hr-company-coverage">
          <strong>资料覆盖：{localize(detail.company.coverage.state)}</strong>
          {detail.company.coverage.jobCount !== null && (
            <span>观察到 {detail.company.coverage.jobCount} 个岗位</span>
          )}
          {[
            ...detail.company.coverage.limitations,
            ...detail.company.coverage.documentLimitations,
          ].map((item) => (
            <span key={item}>{item}</span>
          ))}
        </aside>
      )}
      {!detail.company.coverage && (
        <p className="hr-company-missing">资料覆盖情况未提供。</p>
      )}
      {!onSelectReference && (
        <p className="hr-company-missing" id="hr-company-reference-unavailable">
          当前页面未连接对话，暂不能带入材料。
        </p>
      )}
      {referenceError && (
        <p className="hr-company-job-error" role="alert">
          所选材料过长，请选择范围更小的判断或岗位页。
        </p>
      )}
      {detail.units.map((unit) => (
        <section className="hr-company-unit" key={unit.unitId}>
          <header>
            <h3>
              {detail.units.length === 1
                ? "核心研判"
                : unit.response.summary === detail.company.summary
                  ? "核心研判"
                  : unit.response.summary}
            </h3>
            <span>置信度 {localizeConfidence(unit.response.confidence)}</span>
          </header>
          {unit.response.inferences.length > 0 && (
            <section>
              <h4>判断</h4>
              {unit.response.inferences.map((claim) => (
                <article className="hr-company-claim" key={claim.inferenceId}>
                  <p>{claim.text}</p>
                  {action(() =>
                    select(
                      unit.unitId,
                      claim.inferenceId,
                      "公司判断",
                      claim.text,
                      claim.basisFactIds,
                      claim.claimType ?? "inference",
                    ),
                  )}
                  <Evidence
                    unitId={unit.unitId}
                    basis={claim.basisFactIds}
                    facts={facts}
                  />
                </article>
              ))}
            </section>
          )}
          {unit.response.recommendations.length > 0 && (
            <section>
              <h4>建议</h4>
              {unit.response.recommendations.map((claim) => (
                <article
                  className="hr-company-claim"
                  key={claim.recommendationId}
                >
                  <p>{claim.text}</p>
                  {action(() =>
                    select(
                      unit.unitId,
                      claim.recommendationId,
                      "行动建议",
                      claim.text,
                      claim.basisFactIds,
                      "recommendation",
                    ),
                  )}
                  <Evidence
                    unitId={unit.unitId}
                    basis={claim.basisFactIds}
                    facts={facts}
                  />
                </article>
              ))}
            </section>
          )}
          {unit.response.alternatives.length > 0 && (
            <section>
              <h4>替代解释</h4>
              {unit.response.alternatives.map((claim) => (
                <article className="hr-company-claim" key={claim.alternativeId}>
                  <p>{claim.text}</p>
                  {action(() =>
                    select(
                      unit.unitId,
                      claim.alternativeId,
                      "替代解释",
                      claim.text,
                      claim.basisFactIds,
                      "alternative",
                    ),
                  )}
                  <Evidence
                    unitId={unit.unitId}
                    basis={claim.basisFactIds}
                    facts={facts}
                  />
                </article>
              ))}
            </section>
          )}
          {unit.response.unknowns.length > 0 && (
            <section>
              <h4>仍待确认</h4>
              <ul>
                {unit.response.unknowns.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
          {unit.response.facts.filter(
            (fact) =>
              ![
                ...unit.response.inferences,
                ...unit.response.recommendations,
                ...unit.response.alternatives,
              ].some((claim) => claim.basisFactIds.includes(fact.factId)),
          ).length > 0 && (
            <section>
              <h4>其他公开事实</h4>
              {unit.response.facts
                .filter(
                  (fact) =>
                    ![
                      ...unit.response.inferences,
                      ...unit.response.recommendations,
                      ...unit.response.alternatives,
                    ].some((claim) => claim.basisFactIds.includes(fact.factId)),
                )
                .map((fact) => (
                  <p key={fact.factId}>
                    <a href={fact.sourceUrl} target="_blank" rel="noreferrer">
                      {fact.text}
                    </a>
                  </p>
                ))}
            </section>
          )}
        </section>
      ))}
      <Metrics metrics={detail.metrics} />
      <section className="hr-company-jobs">
        <header>
          <div>
            <h3>公开岗位</h3>
            <p>按需读取当前公司岗位，不影响上方情报正文。</p>
          </div>
          <button type="button" onClick={() => setJobsOpen((value) => !value)}>
            {jobsOpen ? "收起岗位" : "查看公开岗位"}
          </button>
        </header>
        {jobsOpen && (
          <>
            <div className="hr-company-job-filters">
              <label>
                地点
                <input
                  value={location}
                  onChange={(event) => {
                    setOffset(0);
                    setLocation(event.target.value);
                  }}
                />
              </label>
              <label>
                状态
                <select
                  value={status}
                  onChange={(event) => {
                    setOffset(0);
                    setStatus(event.target.value);
                  }}
                >
                  <option value="">全部</option>
                  <option value="open">开放</option>
                  <option value="closed">关闭</option>
                  <option value="unknown">未知</option>
                </select>
              </label>
            </div>
            {jobsLoading && <p>正在读取岗位…</p>}
            {jobsError && (
              <p className="hr-company-job-error">
                岗位暂时无法读取。
                <button
                  type="button"
                  onClick={() => {
                    setJobsOpen(false);
                    queueMicrotask(() => setJobsOpen(true));
                  }}
                >
                  重试
                </button>
              </p>
            )}
            {jobs && !jobsError && (
              <>
                <div className="hr-company-job-list">
                  {jobs.items.map((job) => (
                    <article key={job.jobId}>
                      <h4>{job.title}</h4>
                      <p>
                        {[
                          job.location,
                          job.status ? localize(job.status) : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                      <details>
                        <summary>查看岗位内容</summary>
                        {job.dutyExcerpt && (
                          <p>
                            <strong>岗位职责：</strong>
                            {job.dutyExcerpt}
                          </p>
                        )}
                        {job.requirementExcerpt && (
                          <p>
                            <strong>任职要求：</strong>
                            {job.requirementExcerpt}
                          </p>
                        )}
                      </details>
                      <a href={job.sourceUrl} target="_blank" rel="noreferrer">
                        查看来源
                      </a>
                    </article>
                  ))}
                </div>
                <nav className="hr-company-pagination">
                  <button
                    type="button"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - limit))}
                  >
                    上一页
                  </button>
                  <span>
                    {jobs.total === 0
                      ? "暂无岗位"
                      : `${offset + 1}–${Math.min(offset + limit, jobs.total)} / ${jobs.total}`}
                  </span>
                  <button
                    type="button"
                    disabled={offset + limit >= jobs.total}
                    onClick={() => setOffset(offset + limit)}
                  >
                    下一页
                  </button>
                </nav>
                {jobs.items.length > 0 &&
                  (onSelectReference ? (
                    <button
                      type="button"
                      className="hr-company-reference is-static"
                      onClick={() =>
                        emitReference({
                          key: `${detail.bundleId}:jobs:${detail.company.companyKey}:${offset}:${location}:${status}`,
                          bundleId: detail.bundleId,
                          companyKey: detail.company.companyKey,
                          companyName: detail.company.canonicalName,
                          label: "公开岗位范围",
                          generatedAt: detail.generatedAt,
                          excerpt: jobs.items
                            .map(
                              (job) =>
                                `${job.title}${job.location ? `（${job.location}）` : ""}`,
                            )
                            .join("；"),
                          sourceUrls: [
                            ...new Set(jobs.items.map((job) => job.sourceUrl)),
                          ],
                          jobIds: jobs.items.map((job) => job.jobId),
                          filters: {
                            ...(location ? { location } : {}),
                            ...(status ? { status } : {}),
                          },
                        })
                      }
                    >
                      带入本页岗位
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="hr-company-reference is-static"
                      disabled
                      title="当前页面未连接对话选择器"
                      aria-describedby="hr-company-reference-unavailable"
                    >
                      带入本页岗位
                    </button>
                  ))}
              </>
            )}
          </>
        )}
      </section>
    </article>
  );
}

function Metrics({ metrics }: { metrics: CompanyMetrics | null }) {
  if (!metrics)
    return (
      <p className="hr-company-missing">
        本次发布没有可核验的公司统计，不以零值代替。
      </p>
    );
  const groups = [
    ["招聘类型", metrics.tracks],
    ["职级", metrics.seniority],
    ["方向", metrics.directions],
    ["细分方向", metrics.secondaryDirections],
    ["地点", metrics.locations],
    ["岗位族", metrics.jobFamilies],
    ["技能", metrics.skills],
  ] as const;
  return (
    <details className="hr-company-metrics">
      <summary>
        <span>
          <strong>招聘结构</strong>
          <small>{metrics.jobCount} 个岗位 · 展开查看完整统计</small>
        </span>
      </summary>
      <p>各维度可能重叠，不应相加视为总人数。</p>
      <div className="hr-company-metric-groups">
        {groups.map(([label, values]) => (
          <section key={label}>
            <h4>{label}</h4>
            <dl>
              {metricEntries(values).map(([name, count]) => (
                <div key={name}>
                  <dt>{localize(name)}</dt>
                  <dd>{count}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </details>
  );
}

function Evidence({
  unitId,
  basis,
  facts,
}: {
  unitId: string;
  basis: string[];
  facts: Map<
    string,
    { factId: string; text: string; sourceUrl: string; observedAt: string }
  >;
}) {
  const related = basis
    .map((id) => facts.get(`${unitId}:${id}`))
    .filter((fact): fact is NonNullable<typeof fact> => Boolean(fact));
  return related.length ? (
    <details>
      <summary>查看依据（{related.length}）</summary>
      {related.map((fact) => (
        <article className="hr-company-fact" key={fact.factId}>
          <p>{fact.text}</p>
          <footer>
            <a href={fact.sourceUrl} target="_blank" rel="noreferrer">
              打开来源
            </a>
            <time dateTime={fact.observedAt}>
              {new Date(fact.observedAt).toLocaleDateString("zh-CN")}
            </time>
          </footer>
        </article>
      ))}
    </details>
  ) : (
    <p className="hr-company-missing">本条判断没有可展开的公开事实。</p>
  );
}
