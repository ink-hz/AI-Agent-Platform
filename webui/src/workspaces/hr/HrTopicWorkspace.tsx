import { useEffect, useMemo, useRef, useState } from "react";
import type { Account } from "../../auth";
import { platformPath } from "../../auth";
import { HrCompanyIntelligenceApiError } from "../../hrCompanyIntelligenceApi";
import {
  createHrTopicIntelligenceApi,
  type HrTopicIntelligenceApi,
  type TopicDetail,
  type TopicDirectory,
  type TopicSummary,
} from "../../hrTopicIntelligenceApi";
import {
  formatHrIntelligenceReferences,
  type HrIntelligenceReference,
} from "./hrIntelligenceReference";
import "./hrCompanyIntelligence.css";

type Props = {
  account: Account;
  api?: HrTopicIntelligenceApi;
  onSelectReference?: (reference: HrIntelligenceReference) => void;
  onOpenCompany?: (companyKey: string, bundleId: string) => void;
};
const labels: Record<TopicSummary["analysisState"], string> = {
  available: "已有分析",
  limited: "分析有限",
  insufficient_evidence: "证据不足",
  missing: "尚无分析",
};
function readSelection() {
  const p = new URLSearchParams(location.search);
  return { topicId: p.get("topic"), bundleId: p.get("bundle_id") };
}
const authError = (error: unknown) =>
  error instanceof HrCompanyIntelligenceApiError &&
  [401, 403].includes(error.status);
const failureText = (error: unknown) =>
  error instanceof HrCompanyIntelligenceApiError
    ? ({
        401: "登录状态已失效，请重新登录。",
        403: "当前账号无法查看 HR 情报。",
        404: "这份情报中没有可读取的专题。",
      }[error.status] ?? "专题情报暂时无法读取，请稍后重试。")
    : "专题情报暂时无法读取，请稍后重试。";

export function HrTopicWorkspace(props: Props) {
  return (
    <TopicWorkspaceContent
      key={`${props.account.internal_user_id}:${props.account.csrf_token}`}
      {...props}
    />
  );
}
function TopicWorkspaceContent({
  account,
  api: injectedApi,
  onSelectReference,
  onOpenCompany,
}: Props) {
  const defaultApi = useMemo(
    () => createHrTopicIntelligenceApi(account.csrf_token),
    [account.csrf_token],
  );
  const api = injectedApi ?? defaultApi;
  const [selection, setSelection] = useState(readSelection);
  const [directory, setDirectory] = useState<TopicDirectory | null>(null);
  const [detail, setDetail] = useState<TopicDetail | null>(null);
  const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [detailRetry, setDetailRetry] = useState(0);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const denialLatched = useRef(false);
  const requests = useRef(new Set<AbortController>());
  const deny = (error: unknown) => {
    denialLatched.current = true;
    for (const request of requests.current) request.abort();
    requests.current.clear();
    setDenied(true);
    setDirectory(null);
    setDetail(null);
    setLoading(false);
    setDetailLoading(false);
    setDirectoryError(failureText(error));
  };
  const retry = () => {
    denialLatched.current = false;
    setDenied(false);
    setRefresh((value) => value + 1);
  };
  useEffect(() => {
    const sync = () => {
      if (!/\/hr\/panorama(?:\/|$)/.test(location.pathname)) return;
      setSelection(readSelection());
      setRefresh((v) => v + 1);
    };
    window.addEventListener("platform:navigate", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("platform:navigate", sync);
      window.removeEventListener("popstate", sync);
    };
  }, []);
  useEffect(() => {
    if (denialLatched.current) return;
    const controller = new AbortController();
    requests.current.add(controller);
    setLoading(true);
    setDirectoryError(null);
    api
      .topics(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setDirectory(value);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setDirectory(null);
          setDirectoryError(failureText(error));
          if (authError(error)) deny(error);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      requests.current.delete(controller);
    };
  }, [api, refresh]);
  const bundleId =
    selection.bundleId ??
    (!loading && !directoryError ? directory?.bundleId : null);
  const topicMissing =
    !selection.bundleId &&
    !loading &&
    !!directory &&
    !!selection.topicId &&
    !directory.items.some((item) => item.topicId === selection.topicId);
  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    if (!selection.topicId || !bundleId || denied || topicMissing) {
      setDetailLoading(false);
      return;
    }
    const controller = new AbortController();
    requests.current.add(controller);
    setDetailLoading(true);
    api
      .topic(selection.topicId, bundleId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          const url = new URL(location.href);
          if (
            url.searchParams.get("topic") === value.topic.topicId &&
            !url.searchParams.has("bundle_id")
          ) {
            url.searchParams.set("bundle_id", value.bundleId);
            history.replaceState(
              history.state,
              "",
              `${url.pathname}${url.search}${url.hash}`,
            );
          }
          setDetail(value);
          setSelection((previous) => ({
            ...previous,
            bundleId: value.bundleId,
          }));
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setDetailError(failureText(error));
          if (authError(error)) deny(error);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => {
      controller.abort();
      requests.current.delete(controller);
    };
  }, [api, selection.topicId, bundleId, denied, topicMissing, detailRetry]);
  const navigate = (topicId: string | null) => {
    if (topicId && !directory) return;
    const url = new URL(location.href);
    url.searchParams.set("view", "topics");
    url.searchParams.delete("company");
    if (topicId) {
      url.searchParams.set("topic", topicId);
      url.searchParams.set("bundle_id", directory!.bundleId);
    } else {
      url.searchParams.delete("topic");
      url.searchParams.delete("bundle_id");
    }
    history.pushState({}, "", `${url.pathname}${url.search}`);
    setSelection(readSelection());
    setDetailError(null);
    if (!topicId) setRefresh((v) => v + 1);
    else if (selection.topicId === topicId && !detail)
      setDetailRetry((v) => v + 1);
  };
  const filtered =
    directory?.items.filter((item) =>
      `${item.title} ${item.question}`
        .toLocaleLowerCase()
        .includes(query.trim().toLocaleLowerCase()),
    ) ?? [];
  return (
    <div className="hr-company-layout hr-topic-workspace">
      <aside className="hr-company-index">
        <label>
          <span>搜索专题或研究问题</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入研究问题"
          />
        </label>
        {loading && <p>正在读取专题目录…</p>}
        {directoryError && (
          <p role="alert">
            {directoryError}
            <button type="button" onClick={retry}>
              重试
            </button>
          </p>
        )}
        {!loading && !directoryError && !directory && (
          <p>当前没有已发布情报。</p>
        )}
        {directory?.state === "metadata_missing" && (
          <p>专题目录尚未就绪，当前内容尚未提供核验后的专题范围与公司关系。</p>
        )}
        {directory?.state === "available" && (
          <nav aria-label="专题目录">
            <p className="hr-company-index-time">
              最新内容：
              {new Date(directory.generatedAt).toLocaleString("zh-CN")}
            </p>
            {filtered.map((topic) => (
              <button
                key={topic.topicId}
                data-topic-id={topic.topicId}
                disabled={loading || denied}
                aria-current={
                  selection.topicId === topic.topicId ? "page" : undefined
                }
                onClick={() => navigate(topic.topicId)}
              >
                <strong>{topic.title}</strong>
                {topic.summary && <span>{topic.summary}</span>}
                <small>{labels[topic.analysisState]}</small>
              </button>
            ))}
            {!filtered.length && (
              <p>
                {directory.items.length
                  ? "没有匹配的专题。"
                  : "当前没有已发布专题。"}
              </p>
            )}
          </nav>
        )}
      </aside>
      <section className="hr-company-reading">
        {selection.topicId && (
          <button
            type="button"
            className="hr-company-back"
            onClick={() => navigate(null)}
          >
            返回专题目录
          </button>
        )}
        {detailLoading && !denied && <p>正在读取专题情报…</p>}
        {detailError && !denied && (
          <p role="alert">
            {detailError}
            <button type="button" onClick={() => setDetailRetry((v) => v + 1)}>
              重试专题
            </button>
          </p>
        )}
        {topicMissing && !denied && <p>当前情报不包含这个专题。</p>}
        {!selection.topicId && directory?.state === "available" && (
          <div className="hr-company-state">选择一个专题开始阅读</div>
        )}
        {!denied && detail && (
          <HrTopicDetail
            key={`${detail.bundleId}:${detail.topic.topicId}`}
            detail={detail}
            onSelectReference={onSelectReference}
            onOpenCompany={onOpenCompany}
          />
        )}
      </section>
    </div>
  );
}

function HrTopicDetail({
  detail,
  onSelectReference,
  onOpenCompany,
}: Pick<Props, "onSelectReference" | "onOpenCompany"> & {
  detail: TopicDetail;
}) {
  const { topic } = detail;
  const [referenceError, setReferenceError] = useState(false);
  const select = (
    excerpt: string,
    label: string,
    sourceUrls: string[],
    claim?: { unitId: string; localId: string; claimType: string },
  ) => {
    const reference: HrIntelligenceReference = {
      kind: "topic",
      key: claim
        ? `${detail.bundleId}:topic:${topic.topicId}:${claim.unitId}:${claim.localId}`
        : `${detail.bundleId}:topic:${topic.topicId}`,
      bundleId: detail.bundleId,
      topicId: topic.topicId,
      topicTitle: topic.title,
      question: topic.question,
      scope: topic.scope,
      analysisState: topic.analysisState,
      limitations: topic.limitations,
      generatedAt: detail.generatedAt,
      excerpt,
      label,
      sourceUrls: [...new Set(sourceUrls)],
      ...claim,
    };
    try {
      formatHrIntelligenceReferences([reference]);
      setReferenceError(false);
      onSelectReference?.(reference);
    } catch {
      setReferenceError(true);
    }
  };
  return (
    <article className="hr-company-detail hr-topic-detail">
      <header className="hr-company-detail-header">
        <div>
          <p>专题情报</p>
          <h2>{topic.title}</h2>
          <span>{topic.question}</span>
        </div>
        <div>
          <time dateTime={detail.generatedAt}>
            分析时间 {new Date(detail.generatedAt).toLocaleString("zh-CN")}
          </time>
          <button
            type="button"
            className="hr-company-reference is-static"
            disabled={!onSelectReference || topic.analysisState === "missing"}
            onClick={() =>
              select(
                topic.summary ??
                  detail.units.map((unit) => unit.response.summary).join("；"),
                "专题情报",
                detail.units.flatMap((unit) =>
                  unit.response.facts.map((fact) => fact.sourceUrl),
                ),
              )
            }
          >
            带入专题情报
          </button>
        </div>
      </header>
      <aside className="hr-company-coverage">
        <strong>{labels[topic.analysisState]}</strong>
        {topic.limitations.map((text) => (
          <span key={text}>{text}</span>
        ))}
      </aside>
      {referenceError && (
        <p role="alert" className="hr-company-job-error">
          所选材料过长，请选择范围更小的判断；范围与局限会一并保留。
        </p>
      )}
      {topic.summary && <p className="hr-company-lead">{topic.summary}</p>}
      <section className="hr-topic-scope">
        <h3>研究范围</h3>
        <p>{topic.scope.description}</p>
        <p>
          样本范围内公司：{topic.scope.companyKeys.length}{" "}
          家。这是采集范围，不代表正文逐一讨论了这些公司。
        </p>
        {topic.scope.tracks.length > 0 && (
          <p>
            招聘范围：
            {topic.scope.tracks
              .map(
                (track) =>
                  ({ social: "社招", campus: "校招", intern: "实习" })[track] ??
                  track,
              )
              .join("、")}
          </p>
        )}
      </section>
      {detail.units.map((unit) => {
        const facts = new Map(
          unit.response.facts.map((fact) => [fact.factId, fact]),
        );
        const groups = [
          {
            title: "判断",
            label: "带入判断",
            claims: unit.response.inferences.map((c) => ({
              id: c.inferenceId,
              text: c.text,
              basis: c.basisFactIds,
              kind: c.claimType ?? "inference",
            })),
          },
          {
            title: "建议",
            label: "带入建议",
            claims: unit.response.recommendations.map((c) => ({
              id: c.recommendationId,
              text: c.text,
              basis: c.basisFactIds,
              kind: "recommendation",
            })),
          },
          {
            title: "替代解释",
            label: "带入替代解释",
            claims: unit.response.alternatives.map((c) => ({
              id: c.alternativeId,
              text: c.text,
              basis: c.basisFactIds,
              kind: "alternative",
            })),
          },
        ];
        return (
          <section className="hr-company-unit" key={unit.unitId}>
            <header>
              <h3>核心研判</h3>
              <span>
                置信度{" "}
                {{ low: "低", medium: "中", high: "高" }[
                  unit.response.confidence
                ] ?? unit.response.confidence}
              </span>
            </header>
            <p>{unit.response.summary}</p>
            {groups
              .filter((group) => group.claims.length)
              .map((group) => (
                <section key={group.title}>
                  <h4>{group.title}</h4>
                  {group.claims.map((claim) => (
                    <article
                      className="hr-company-claim"
                      key={claim.id}
                      id={`topic-claim-${unit.unitId}-${claim.id}`}
                    >
                      <p>{claim.text}</p>
                      <button
                        className="hr-company-reference"
                        disabled={!onSelectReference}
                        onClick={() =>
                          select(
                            claim.text,
                            `专题${group.title}`,
                            claim.basis.flatMap((id) =>
                              facts.get(id) ? [facts.get(id)!.sourceUrl] : [],
                            ),
                            {
                              unitId: unit.unitId,
                              localId: claim.id,
                              claimType: claim.kind,
                            },
                          )
                        }
                      >
                        {group.label}
                      </button>
                      <details>
                        <summary>
                          查看依据（
                          {claim.basis.filter((id) => facts.has(id)).length}）
                        </summary>
                        {claim.basis
                          .map((id) => facts.get(id))
                          .filter((fact) => !!fact)
                          .map((fact) => (
                            <p key={fact.factId}>
                              {fact.text}{" "}
                              <a
                                href={fact.sourceUrl}
                                target="_blank"
                                rel="noreferrer"
                              >
                                打开来源
                              </a>{" "}
                              <a
                                href={platformPath(
                                  `/api/hr/panorama/reports/${encodeURIComponent(detail.bundleId)}/evidence/${fact.evidenceSha256}`,
                                )}
                              >
                                查看原始证据
                              </a>
                            </p>
                          ))}
                      </details>
                    </article>
                  ))}
                </section>
              ))}
            {unit.response.unknowns.length > 0 && (
              <section>
                <h4>仍待确认</h4>
                <ul>
                  {unit.response.unknowns.map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              </section>
            )}
            <details>
              <summary>全部公开事实（{unit.response.facts.length}）</summary>
              {unit.response.facts.map((fact) => (
                <article
                  className="hr-company-fact"
                  key={fact.factId}
                  id={`topic-claim-${unit.unitId}-${fact.factId}`}
                >
                  <p>{fact.text}</p>
                  <footer>
                    <a href={fact.sourceUrl} target="_blank" rel="noreferrer">
                      打开来源
                    </a>
                    <a
                      href={platformPath(
                        `/api/hr/panorama/reports/${encodeURIComponent(detail.bundleId)}/evidence/${fact.evidenceSha256}`,
                      )}
                    >
                      查看原始证据
                    </a>
                    <time dateTime={fact.observedAt}>
                      {new Date(fact.observedAt).toLocaleDateString("zh-CN")}
                    </time>
                    <button
                      disabled={!onSelectReference}
                      onClick={() =>
                        select(fact.text, "专题事实", [fact.sourceUrl], {
                          unitId: unit.unitId,
                          localId: fact.factId,
                          claimType: "fact",
                        })
                      }
                    >
                      带入事实
                    </button>
                  </footer>
                </article>
              ))}
            </details>
          </section>
        );
      })}
      <section className="hr-company-unit">
        <h3>正文讨论的公司</h3>
        {!topic.discussedCompanies.length && (
          <p>当前正文未声明可核验的公司关联。</p>
        )}
        {topic.discussedCompanies.map((relation) => (
          <article key={`${relation.companyKey}:${relation.unitId}`}>
            <button
              type="button"
              data-company-key={relation.companyKey}
              disabled={!onOpenCompany}
              onClick={() =>
                onOpenCompany?.(relation.companyKey, detail.bundleId)
              }
            >
              {detail.companies.find(
                (company) => company.companyKey === relation.companyKey,
              )?.canonicalName ?? relation.companyKey}
            </button>
            <p>{relation.explanation}</p>
            {relation.claimIds.map((id) => (
              <a
                className="hr-topic-claim-link"
                key={id}
                href={`#topic-claim-${relation.unitId}-${id}`}
              >
                查看对应结论
              </a>
            ))}
          </article>
        ))}
      </section>
    </article>
  );
}
