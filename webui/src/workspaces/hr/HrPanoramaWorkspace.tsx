import { useEffect, useMemo, useRef, useState } from "react";
import type { Account } from "../../auth";
import {
  createHrCompanyIntelligenceApi,
  HrCompanyIntelligenceApiError,
} from "../../hrCompanyIntelligenceApi";
import type {
  CompanyDetail,
  CompanyDirectory,
  HrCompanyIntelligenceApi,
} from "../../hrCompanyIntelligenceTypes";
import type { HrIntelligenceReference } from "./hrIntelligenceReference";
import { HrTopicWorkspace } from "./HrTopicWorkspace";
import type { HrTopicIntelligenceApi } from "../../hrTopicIntelligenceApi";
import { HrCompanyDetail } from "./HrCompanyDetail";
import "./hrCompanyIntelligence.css";

function failureText(error: unknown) {
  if (error instanceof HrCompanyIntelligenceApiError && error.status === 404)
    return "当前情报不包含这家公司。";
  if (error instanceof HrCompanyIntelligenceApiError && error.status === 401)
    return "登录状态已失效，请重新登录。";
  if (error instanceof HrCompanyIntelligenceApiError && error.status === 403)
    return "当前账号无法查看 HR 情报。";
  return "HR 情报暂时无法读取，请稍后重试。";
}
const coverageLabel = (state: string) =>
  ({
    succeeded: "已覆盖",
    partial: "部分覆盖",
    failed: "暂不可用",
    not_observed: "尚未观察",
    empty_confirmed: "已核验，未发现",
  })[state] ?? state;
export function HrPanoramaWorkspace({
  account,
  api: injectedApi,
  topicApi,
  onSelectReference,
}: {
  account: Account;
  insightVersionId?: string;
  executionConversationId?: string;
  api?: HrCompanyIntelligenceApi;
  topicApi?: HrTopicIntelligenceApi;
  onSelectReference?: (reference: HrIntelligenceReference) => void;
}) {
  const defaultApi = useMemo(
    () => createHrCompanyIntelligenceApi(account.csrf_token),
    [account.csrf_token],
  );
  const api = injectedApi ?? defaultApi;
  const [root, setRoot] = useState<"companies" | "topics">(() =>
    new URLSearchParams(location.search).has("topic") ||
    new URLSearchParams(location.search).get("view") === "topics"
      ? "topics"
      : "companies",
  );
  const [directory, setDirectory] = useState<CompanyDirectory | null>(null);
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const detailCache = useRef(new Map<string, CompanyDetail>());
  const [detailBundleId, setDetailBundleId] = useState<string | null>(() =>
    new URLSearchParams(location.search).get("bundle_id"),
  );
  const [selectedCompanyKey, setSelectedCompanyKey] = useState(() =>
    new URLSearchParams(location.search).get("company"),
  );
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRetry, setDetailRetry] = useState(0);
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    let controller: AbortController | null = null;
    const refresh = () => {
      if (!/\/hr\/panorama(?:\/|$)/.test(location.pathname)) return;
      const params = new URLSearchParams(location.search);
      if (params.has("topic") || params.get("view") === "topics") {
        controller?.abort();
        return;
      }
      controller?.abort();
      const request = new AbortController();
      controller = request;
      setLoading(true);
      setFailure(null);
      api
        .companies(request.signal)
        .then((value) => {
          if (!request.signal.aborted) setDirectory(value);
        })
        .catch((error) => {
          if (!request.signal.aborted) {
            setDirectory(null);
            setFailure(failureText(error));
          }
        })
        .finally(() => {
          if (!request.signal.aborted) setLoading(false);
        });
    };
    refresh();
    window.addEventListener("platform:navigate", refresh);
    window.addEventListener("popstate", refresh);
    return () => {
      controller?.abort();
      window.removeEventListener("platform:navigate", refresh);
      window.removeEventListener("popstate", refresh);
    };
  }, [api]);
  const navigateRoot = (
    view: "companies" | "topics",
    objectId?: string,
    bundleId?: string,
  ) => {
    const url = new URL(location.href);
    for (const key of ["company", "topic", "view", "bundle_id"])
      url.searchParams.delete(key);
    if (view === "topics") url.searchParams.set("view", "topics");
    if (objectId)
      url.searchParams.set(view === "topics" ? "topic" : "company", objectId);
    if (bundleId) url.searchParams.set("bundle_id", bundleId);
    history.pushState({}, "", `${url.pathname}${url.search}`);
    setRoot(view);
    window.dispatchEvent(new Event("platform:navigate"));
  };
  const openCompany = (companyKey: string) => {
    if (!directory || loading) return;
    if (
      selectedCompanyKey === companyKey &&
      (detailBundleId ?? directory.bundleId) === directory.bundleId
    ) {
      if (!detail && !detailLoading) setDetailRetry((attempt) => attempt + 1);
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("company", companyKey);
    url.searchParams.delete("topic");
    url.searchParams.delete("view");
    url.searchParams.delete("bundle_id");
    history.pushState({}, "", `${url.pathname}${url.search}`);
    setDetail(null);
    setDetailBundleId(directory.bundleId);
    setSelectedCompanyKey(companyKey);
  };
  const closeCompany = () => {
    const url = new URL(window.location.href);
    url.searchParams.delete("company");
    url.searchParams.delete("bundle_id");
    history.pushState({}, "", `${url.pathname}${url.search}`);
    setSelectedCompanyKey(null);
    setDetail(null);
    setFailure(null);
    window.dispatchEvent(new Event("platform:navigate"));
  };
  useEffect(() => {
    const syncLocation = () => {
      if (!/\/hr\/panorama(?:\/|$)/.test(location.pathname)) return;
      const params = new URLSearchParams(location.search);
      const isTopics = params.has("topic") || params.get("view") === "topics";
      setRoot(isTopics ? "topics" : "companies");
      const companyKey = isTopics ? null : params.get("company");
      const pinned = params.get("bundle_id");
      if (
        companyKey === selectedCompanyKey &&
        (!pinned || pinned === detailBundleId)
      )
        return;
      setSelectedCompanyKey(companyKey);
      setFailure(null);
      if (!companyKey) {
        setDetail(null);
        setDetailBundleId(null);
      } else {
        const cached = detailCache.current.get(companyKey);
        if (cached && (!pinned || cached.bundleId === pinned)) {
          setDetail(cached);
          setDetailBundleId(cached.bundleId);
        } else {
          setDetail(null);
          setDetailBundleId(pinned);
        }
      }
    };
    window.addEventListener("popstate", syncLocation);
    window.addEventListener("platform:navigate", syncLocation);
    return () => {
      window.removeEventListener("popstate", syncLocation);
      window.removeEventListener("platform:navigate", syncLocation);
    };
  }, [selectedCompanyKey, detailBundleId]);
  // Existing reading stays pinned; a new deep link waits for the current directory.
  const requestedBundleId =
    detailBundleId ?? (loading ? null : directory?.bundleId) ?? null;
  const companyMissing = Boolean(
    !detailBundleId &&
      !loading &&
      directory &&
      selectedCompanyKey &&
      !directory.items.some(
        (company) => company.companyKey === selectedCompanyKey,
      ),
  );
  useEffect(() => {
    if (!selectedCompanyKey || !requestedBundleId || companyMissing) {
      setDetailLoading(false);
      if (companyMissing) setFailure("当前情报不包含这家公司。");
      return;
    }
    const pinnedBundleId = requestedBundleId;
    const cached = detailCache.current.get(selectedCompanyKey);
    if (cached?.bundleId === pinnedBundleId) {
      setDetail(cached);
      setDetailLoading(false);
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    setFailure(null);
    api
      .company(selectedCompanyKey, pinnedBundleId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          detailCache.current.set(selectedCompanyKey, value);
          setDetail(value);
          setDetailBundleId(value.bundleId);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setDetail(null);
          setFailure(failureText(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [api, requestedBundleId, selectedCompanyKey, companyMissing, detailRetry]);
  const filtered =
    directory?.items.filter((company) =>
      `${company.canonicalName} ${company.aliases.join(" ")}`
        .toLocaleLowerCase()
        .includes(query.trim().toLocaleLowerCase()),
    ) ?? [];
  return (
    <main className="hr-company-workspace">
      <header className="hr-company-hero">
        <div>
          <p>HR INTELLIGENCE</p>
          <h1>HR 情报</h1>
          <span>按公司或专题阅读最新情报与可核验依据。</span>
        </div>
      </header>
      <nav className="hr-company-roots" aria-label="情报范围">
        <button
          aria-current={root === "companies" ? "page" : undefined}
          onClick={() => navigateRoot("companies")}
        >
          公司
        </button>
        <button
          aria-current={root === "topics" ? "page" : undefined}
          onClick={() => navigateRoot("topics")}
        >
          专题
        </button>
      </nav>
      {root === "topics" ? (
        <HrTopicWorkspace
          account={account}
          api={topicApi}
          onSelectReference={onSelectReference}
          onOpenCompany={(companyKey, bundleId) =>
            navigateRoot("companies", companyKey, bundleId)
          }
        />
      ) : (
        <div className="hr-company-layout">
          <aside className="hr-company-index">
            <label>
              <span>搜索公司或别名</span>
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="输入公司名"
              />
            </label>
            {loading && <p>正在读取公司目录…</p>}
            {!loading && !failure && !directory && <p>当前没有已发布情报。</p>}
            {directory && (
              <nav>
                <p className="hr-company-index-time">
                  最新内容：
                  {new Date(directory.generatedAt).toLocaleString("zh-CN")}
                </p>
                {filtered.map((company) => (
                  <button
                    type="button"
                    data-company-key={company.companyKey}
                    aria-current={
                      detail?.company.companyKey === company.companyKey
                        ? "page"
                        : undefined
                    }
                    onClick={() => openCompany(company.companyKey)}
                    disabled={loading}
                    key={company.companyKey}
                  >
                    <strong>{company.canonicalName}</strong>
                    {company.summary && <span>{company.summary}</span>}
                    {company.coverage && (
                      <small>
                        {coverageLabel(company.coverage.state)}
                        {company.coverage.observedAt
                          ? ` · ${new Date(company.coverage.observedAt).toLocaleDateString("zh-CN")}`
                          : ""}
                      </small>
                    )}
                  </button>
                ))}
                {filtered.length === 0 && <p>没有匹配的公司。</p>}
              </nav>
            )}
          </aside>
          <section className="hr-company-reading">
            {failure && (
              <div className="hr-company-state is-error">{failure}</div>
            )}
            {detailLoading && (
              <div className="hr-company-state">正在读取公司情报…</div>
            )}
            {!detailLoading && detail && (
              <>
                <button
                  type="button"
                  className="hr-company-back"
                  onClick={closeCompany}
                >
                  返回公司目录
                </button>
                <HrCompanyDetail
                  key={`${detail.bundleId}:${detail.company.companyKey}`}
                  detail={detail}
                  api={api}
                  onSelectReference={onSelectReference}
                  onOpenTopic={(topicId, bundleId) =>
                    navigateRoot("topics", topicId, bundleId)
                  }
                />
              </>
            )}
            {!detailLoading && !detail && !failure && directory && (
              <div className="hr-company-state">
                <strong>选择一家公司开始阅读</strong>
                <span>目录只显示最新发布的独立公司摘要。</span>
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
