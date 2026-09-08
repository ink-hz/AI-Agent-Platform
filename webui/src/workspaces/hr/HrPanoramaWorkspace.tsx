import { useEffect, useMemo, useState } from "react";
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
export function HrPanoramaWorkspace({
  account,
  api: injectedApi,
  onSelectReference,
}: {
  account: Account;
  insightVersionId?: string;
  executionConversationId?: string;
  api?: HrCompanyIntelligenceApi;
  onSelectReference?: (reference: HrIntelligenceReference) => void;
}) {
  const defaultApi = useMemo(
    () => createHrCompanyIntelligenceApi(account.csrf_token),
    [account.csrf_token],
  );
  const api = injectedApi ?? defaultApi;
  const [root, setRoot] = useState<"companies" | "topics">("companies");
  const [directory, setDirectory] = useState<CompanyDirectory | null>(null);
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [selectedCompanyKey, setSelectedCompanyKey] = useState(() =>
    new URLSearchParams(location.search).get("company"),
  );
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailure(null);
    api
      .companies(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDirectory(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFailure(failureText(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api]);
  const openCompany = (companyKey: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("company", companyKey);
    history.pushState({}, "", `${url.pathname}${url.search}`);
    setSelectedCompanyKey(companyKey);
  };
  const closeCompany = () => {
    const url = new URL(window.location.href);
    url.searchParams.delete("company");
    history.pushState({}, "", `${url.pathname}${url.search}`);
    setSelectedCompanyKey(null);
    setDetail(null);
    setFailure(null);
  };
  useEffect(() => {
    const syncLocation = () => {
      const companyKey = new URLSearchParams(location.search).get("company");
      setSelectedCompanyKey(companyKey);
      if (!companyKey) setDetail(null);
    };
    window.addEventListener("popstate", syncLocation);
    return () => window.removeEventListener("popstate", syncLocation);
  }, []);
  useEffect(() => {
    if (!directory || !selectedCompanyKey) return;
    const controller = new AbortController();
    setDetailLoading(true);
    setFailure(null);
    api
      .company(selectedCompanyKey, directory.bundleId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDetail(value);
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
  }, [api, directory, selectedCompanyKey]);
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
          <span>阅读最新发布的公司公开情报与可核验依据。</span>
        </div>
      </header>
      <nav className="hr-company-roots" aria-label="情报范围">
        <button
          aria-current={root === "companies" ? "page" : undefined}
          onClick={() => setRoot("companies")}
        >
          公司
        </button>
        <button
          aria-current={root === "topics" ? "page" : undefined}
          onClick={() => setRoot("topics")}
        >
          专题
        </button>
      </nav>
      {root === "topics" ? (
        <section className="hr-company-topic-state">
          <h2>专题情报暂不可用</h2>
          <p>
            当前专题范围及公司关联尚未完成核验，因此暂不提供跨公司专题比较。
          </p>
        </section>
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
                    key={company.companyKey}
                  >
                    <strong>{company.canonicalName}</strong>
                    {company.summary && <span>{company.summary}</span>}
                    {company.coverage && (
                      <small>
                        {company.coverage.state}
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
                  detail={detail}
                  api={api}
                  onSelectReference={onSelectReference}
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
