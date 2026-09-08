/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Account } from "../../auth";
import type {
  CompanyDetail,
  CompanyDirectory,
  HrCompanyIntelligenceApi,
} from "../../hrCompanyIntelligenceTypes";
import { HrPanoramaWorkspace } from "./HrPanoramaWorkspace";
import { HrCompanyIntelligenceApiError } from "../../hrCompanyIntelligenceApi";

const account: Account = {
  internal_user_id: "member",
  display_name: "HR",
  role: "member",
  departments: [],
  gender: null,
  observation_agent_ids: [],
  workspace_scopes: [],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf",
};
const bundleId = "11111111-1111-4111-8111-111111111111";
const summary = {
  companyKey: "acme",
  canonicalName: "艾克米",
  aliases: ["ACME"],
  summary: "持续公开招聘研发岗位",
  coverage: {
    state: "partial",
    observedAt: "2026-09-08T08:00:00Z",
    jobCount: 2,
    limitations: ["一个来源暂不可用"],
    documentLimitations: ["官网说明覆盖有限"],
  },
};
const detail = {
  bundleId,
  generatedAt: "2026-09-08T09:00:00Z",
  company: summary,
  metrics: null,
  units: [
    {
      unitId: "unit-1",
      kind: "company" as const,
      scopeKey: "acme",
      response: {
        summary: "研发投入保持活跃",
        confidence: "medium",
        facts: [
          {
            factId: "fact-1",
            text: "官网介绍了新的研发中心",
            evidenceSha256: "a".repeat(64),
            sourceUrl: "https://example.com/research",
            observedAt: "2026-09-08T08:00:00Z",
          },
        ],
        inferences: [
          {
            inferenceId: "inf-1",
            text: "研发能力可能继续扩张",
            claimType: "inference",
            basisFactIds: ["fact-1"],
          },
        ],
        recommendations: [
          {
            recommendationId: "rec-1",
            text: "关注光学人才",
            targetTasks: ["sourcing"],
            basisFactIds: ["fact-1"],
          },
        ],
        alternatives: [
          {
            alternativeId: "alt-1",
            text: "也可能只是团队搬迁",
            challengedInferenceIds: ["inf-1"],
            basisFactIds: ["fact-1"],
          },
        ],
        unknowns: ["实际人数未公开"],
      },
    },
  ],
};
function fakeApi(
  overrides: Partial<HrCompanyIntelligenceApi> = {},
): HrCompanyIntelligenceApi {
  return {
    companies: vi.fn().mockResolvedValue({
      bundleId,
      generatedAt: detail.generatedAt,
      items: [summary],
      topics: { state: "blocked" },
    }),
    company: vi.fn().mockResolvedValue(detail),
    jobs: vi.fn().mockResolvedValue({
      bundleId,
      companyKey: "acme",
      items: [],
      total: 0,
      offset: 0,
      limit: 25,
    }),
    parseCompanyDetail: vi.fn(),
    ...overrides,
  };
}
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("HrPanoramaWorkspace", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    history.replaceState({}, "", "/hr/panorama");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });
  it("shows company and topic roots without report history", async () => {
    await act(async () =>
      root.render(<HrPanoramaWorkspace account={account} api={fakeApi()} />),
    );
    await settle();
    expect(container.textContent).toContain("公司");
    expect(container.textContent).toContain("专题");
    expect(container.textContent).toContain("艾克米");
    expect(container.textContent).not.toContain("分析历史");
    expect(container.textContent).not.toContain("第 1 版");
  });
  it("searches canonical names and aliases neutrally", async () => {
    await act(async () =>
      root.render(<HrPanoramaWorkspace account={account} api={fakeApi()} />),
    );
    await settle();
    const input = container.querySelector<HTMLInputElement>(
      'input[type="search"]',
    )!;
    const setValue = (value: string) =>
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.set!.call(input, value);
    await act(async () => {
      setValue("ACME");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.textContent).toContain("艾克米");
    await act(async () => {
      setValue("不存在");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.textContent).toContain("没有匹配的公司");
  });
  it("pins detail, exposes related evidence, and selects only on explicit action", async () => {
    const api = fakeApi();
    const onSelectReference = vi.fn();
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={api}
          onSelectReference={onSelectReference}
        />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    expect(api.company).toHaveBeenCalledWith(
      "acme",
      bundleId,
      expect.any(AbortSignal),
    );
    expect(location.search).toBe("?company=acme");
    expect(container.textContent).toContain("研发能力可能继续扩张");
    expect(onSelectReference).not.toHaveBeenCalled();
    expect(container.textContent).toContain("官网介绍了新的研发中心");
    expect(
      container.querySelector<HTMLAnchorElement>(
        `a[href="/api/hr/panorama/reports/${bundleId}/evidence/${"a".repeat(64)}"]`,
      )?.textContent,
    ).toBe("查看原始证据");
    expect(onSelectReference).not.toHaveBeenCalled();
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((node) => node.textContent === "带入对话")!
        .click(),
    );
    expect(onSelectReference).toHaveBeenCalledWith(
      expect.objectContaining({
        bundleId,
        companyKey: "acme",
        unitId: "unit-1",
        localId: "inf-1",
      }),
    );
  });
  it("loads jobs only when expanded and keeps content after an error", async () => {
    const jobs = vi.fn().mockRejectedValue(new Error("offline"));
    const api = fakeApi({ jobs });
    await act(async () =>
      root.render(<HrPanoramaWorkspace account={account} api={api} />),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    expect(jobs).not.toHaveBeenCalled();
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((node) => node.textContent === "查看公开岗位")!
        .click(),
    );
    await settle();
    expect(jobs).toHaveBeenCalledWith(
      "acme",
      bundleId,
      expect.objectContaining({ offset: 0, limit: 25 }),
      expect.any(AbortSignal),
    );
    expect(container.textContent).toContain("研发能力可能继续扩张");
    expect(container.textContent).toContain("岗位暂时无法读取");
    expect(container.textContent).toContain("重试");
  });
  it("explains missing topic metadata using the dedicated topic API", async () => {
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi()}
          topicApi={{
            topics: vi.fn().mockResolvedValue({
              bundleId,
              generatedAt: detail.generatedAt,
              state: "metadata_missing",
              items: [],
            }),
            topic: vi.fn(),
          }}
        />,
      ),
    );
    await settle();
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((node) => node.textContent === "专题")!
        .click(),
    );
    expect(container.textContent).toContain("专题目录尚未就绪");
    expect(container.textContent).not.toContain("公司排名");
  });
  it("navigates company to related topic and back with the same publication", async () => {
    const topic = {
      topicId: "study",
      title: "人才布局",
      question: "关注什么？",
      scope: { description: "公开岗位", companyKeys: ["acme"], tracks: [] },
      analysisState: "limited" as const,
      unitIds: ["unit-1"],
      discussedCompanies: [
        {
          companyKey: "acme",
          unitId: "unit-1",
          claimIds: ["inf-1"],
          explanation: "正文讨论公司",
        },
      ],
      limitations: ["样本有限"],
      summary: "专题正文摘要",
    };
    const company = vi.fn().mockResolvedValue({
      ...detail,
      relatedTopics: [
        { topicId: "study", title: "人才布局", summary: "专题正文摘要" },
      ],
    });
    const topicApi = {
      topics: vi.fn().mockResolvedValue({
        bundleId,
        generatedAt: detail.generatedAt,
        state: "available" as const,
        items: [topic],
      }),
      topic: vi.fn().mockResolvedValue({
        bundleId,
        generatedAt: detail.generatedAt,
        topic,
        units: detail.units.map((unit) => ({
          ...unit,
          kind: "topic",
          scopeKey: "study",
        })),
        companies: [{ companyKey: "acme", canonicalName: "艾克米" }],
      }),
    };
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ company })}
          topicApi={topicApi}
        />,
      ),
    );
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-related-topic="study"]')!
        .click(),
    );
    await settle();
    expect(topicApi.topic).toHaveBeenCalledWith(
      "study",
      bundleId,
      expect.any(AbortSignal),
    );
    expect(container.querySelector(".hr-topic-detail h2")?.textContent).toBe(
      "人才布局",
    );
    expect(container.querySelector(".hr-topic-detail")?.textContent).toContain(
      "正文讨论公司",
    );
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>(
          '.hr-topic-detail [data-company-key="acme"]',
        )!
        .click(),
    );
    await settle();
    expect(container.querySelector(".hr-company-detail h2")?.textContent).toBe(
      "艾克米",
    );
    expect(new URLSearchParams(location.search).get("bundle_id")).toBe(
      bundleId,
    );
  });
  it("keeps duplicate local fact IDs scoped to their units", async () => {
    const duplicate = {
      ...detail,
      units: ["第一条依据", "第二条依据"].map((text, index) => ({
        ...detail.units[0],
        unitId: `unit-${index + 1}`,
        response: {
          ...detail.units[0].response,
          facts: [{ ...detail.units[0].response.facts[0], text }],
          inferences: [
            {
              ...detail.units[0].response.inferences[0],
              inferenceId: `inf-${index + 1}`,
            },
          ],
        },
      })),
    };
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ company: vi.fn().mockResolvedValue(duplicate) })}
        />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    const units = [...container.querySelectorAll(".hr-company-unit")];
    expect(units[0].querySelector(".hr-company-fact")?.textContent).toContain(
      "第一条依据",
    );
    expect(units[1].querySelector(".hr-company-fact")?.textContent).toContain(
      "第二条依据",
    );
  });
  it("syncs the shown company when browser history changes", async () => {
    const other = {
      ...summary,
      companyKey: "other",
      canonicalName: "另一家公司",
    };
    const companies = vi.fn().mockResolvedValue({
      bundleId,
      generatedAt: detail.generatedAt,
      items: [summary, other],
      topics: { state: "blocked" },
    });
    const company = vi.fn().mockImplementation(async (key: string) => ({
      ...detail,
      company: key === "other" ? other : summary,
    }));
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ companies, company })}
        />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="other"]')!
        .click(),
    );
    await settle();
    expect(container.querySelector(".hr-company-detail h2")?.textContent).toBe(
      "另一家公司",
    );
    history.replaceState({}, "", "/hr/conversations/chat-1");
    await act(async () => window.dispatchEvent(new Event("platform:navigate")));
    expect(container.querySelector(".hr-company-detail h2")?.textContent).toBe(
      "另一家公司",
    );
    history.replaceState({}, "", "/hr/panorama?company=acme");
    await act(async () => window.dispatchEvent(new Event("platform:navigate")));
    await settle();
    expect(container.querySelector(".hr-company-detail h2")?.textContent).toBe(
      "艾克米",
    );
  });
  it("places analysis before collapsed localized statistics", async () => {
    const withMetrics = {
      ...detail,
      metrics: {
        jobCount: 2,
        directions: { 算法: 2 },
        secondaryDirections: { "算法/SLAM": 1 },
        locations: { 上海: 2 },
        tracks: { campus: 1, social: 1 },
        seniority: { graduate: 1, senior: 1 },
        jobFamilies: { research_development: 2 },
        skills: { Python: 2 },
        sampleSnapshotIds: [],
      },
    };
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ company: vi.fn().mockResolvedValue(withMetrics) })}
        />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    const text = container.textContent ?? "";
    expect(text.indexOf("核心研判")).toBeLessThan(text.indexOf("招聘结构"));
    const metrics = container.querySelector<HTMLDetailsElement>(
      ".hr-company-metrics",
    )!;
    expect(metrics.open).toBe(false);
    expect(metrics.textContent).toContain("校招");
    expect(metrics.textContent).toContain("研发");
    expect(metrics.textContent).not.toContain("research_development");
    expect(container.querySelector(".hr-company-unit h3")?.textContent).toBe(
      "核心研判",
    );
  });
  it("shows missing coverage and rejects an oversized reference before callback", async () => {
    const onSelectReference = vi.fn();
    const oversized = {
      ...detail,
      company: { ...summary, coverage: null, summary: "长".repeat(13000) },
    };
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ company: vi.fn().mockResolvedValue(oversized) })}
          onSelectReference={onSelectReference}
        />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    expect(container.textContent).toContain("资料覆盖情况未提供");
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "带入公司情报")!
        .click(),
    );
    expect(onSelectReference).not.toHaveBeenCalled();
    expect(container.textContent).toContain("所选材料过长");
  });
  it("keeps job duties and requirements reachable without flooding the reading flow", async () => {
    const jobs = vi.fn().mockResolvedValue({
      bundleId,
      companyKey: "acme",
      items: [
        {
          jobId: "job-1",
          companyKey: "acme",
          title: "算法工程师",
          location: "上海",
          status: "open",
          dutyExcerpt: "负责算法开发",
          requirementExcerpt: "熟悉 Python",
          sourceUrl: "https://example.com/job",
          observedAt: detail.generatedAt,
        },
      ],
      total: 1,
      offset: 0,
      limit: 25,
    });
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace account={account} api={fakeApi({ jobs })} />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "查看公开岗位")!
        .click(),
    );
    await settle();
    const content = [
      ...container.querySelectorAll<HTMLDetailsElement>("details"),
    ].find((item) => item.textContent?.includes("任职要求"))!;
    expect(content.open).toBe(false);
    expect(content.textContent).toContain("岗位职责：负责算法开发");
    expect(content.textContent).toContain("任职要求：熟悉 Python");
    expect(container.textContent).toContain("开放");
    expect(container.textContent).not.toContain(" · open");
  });
  it("hides a completed jobs page while a changed filter is pending", async () => {
    let resolveNext!: (value: any) => void;
    const jobs = vi
      .fn()
      .mockResolvedValueOnce({
        bundleId,
        companyKey: "acme",
        items: [
          {
            jobId: "old",
            companyKey: "acme",
            title: "旧范围岗位",
            location: "上海",
            status: "open",
            dutyExcerpt: null,
            requirementExcerpt: null,
            sourceUrl: "https://example.com/old",
            observedAt: detail.generatedAt,
          },
        ],
        total: 1,
        offset: 0,
        limit: 25,
      })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveNext = resolve;
          }),
      );
    const onSelectReference = vi.fn();
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ jobs })}
          onSelectReference={onSelectReference}
        />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "查看公开岗位")!
        .click(),
    );
    await settle();
    expect(container.textContent).toContain("旧范围岗位");
    const status = container.querySelector<HTMLSelectElement>(
      ".hr-company-job-filters select",
    )!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        HTMLSelectElement.prototype,
        "value",
      )!.set!.call(status, "open");
      status.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.textContent).not.toContain("旧范围岗位");
    expect(
      [...container.querySelectorAll("button")].some(
        (button) => button.textContent === "带入本页岗位",
      ),
    ).toBe(false);
    await act(async () =>
      resolveNext({
        bundleId,
        companyKey: "acme",
        items: [],
        total: 0,
        offset: 0,
        limit: 25,
      }),
    );
  });
  it("keeps pinned reading across publication refresh and uses latest bundle for a new company", async () => {
    const nextBundle = "22222222-2222-4222-8222-222222222222";
    const other = {
      ...summary,
      companyKey: "other",
      canonicalName: "另一家公司",
    };
    const companies = vi
      .fn()
      .mockResolvedValueOnce({
        bundleId,
        generatedAt: detail.generatedAt,
        items: [summary],
        topics: { state: "blocked" },
      })
      .mockResolvedValue({
        bundleId: nextBundle,
        generatedAt: "2026-09-09T09:00:00Z",
        items: [other],
        topics: { state: "blocked" },
      });
    const company = vi
      .fn()
      .mockImplementation(async (key: string, pinned: string) => ({
        ...detail,
        bundleId: pinned,
        company: key === "other" ? other : summary,
      }));
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ companies, company })}
        />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    await act(async () => window.dispatchEvent(new Event("platform:navigate")));
    await settle();
    expect(container.querySelector(".hr-company-detail h2")?.textContent).toBe(
      "艾克米",
    );
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="other"]')!
        .click(),
    );
    await settle();
    expect(company).toHaveBeenLastCalledWith(
      "other",
      nextBundle,
      expect.any(AbortSignal),
    );
  });
  it("keeps pinned reading when the refreshed current publication is empty", async () => {
    const companies = vi
      .fn()
      .mockResolvedValueOnce({
        bundleId,
        generatedAt: detail.generatedAt,
        items: [summary],
        topics: { state: "blocked" },
      })
      .mockResolvedValue(null);
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace account={account} api={fakeApi({ companies })} />,
      ),
    );
    await settle();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    await settle();
    await act(async () => window.dispatchEvent(new Event("platform:navigate")));
    await settle();
    expect(container.querySelector(".hr-company-detail h2")?.textContent).toBe(
      "艾克米",
    );
    expect(container.textContent).toContain("研发能力可能继续扩张");
  });
  it("preserves the reading body and expanded jobs on a repeated company click", async () => {
    const api = fakeApi();
    await act(async () =>
      root.render(<HrPanoramaWorkspace account={account} api={api} />),
    );
    await settle();
    const clickCompany = () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click();
    await act(async () => clickCompany());
    await settle();
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((node) => node.textContent === "查看公开岗位")!
        .click(),
    );
    await settle();
    const body = container.querySelector(".hr-company-detail");
    const jobsText = container.querySelector(".hr-company-jobs")?.textContent;
    const push = vi.spyOn(history, "pushState");
    await act(async () => clickCompany());
    await settle();
    expect(container.querySelector(".hr-company-detail")).toBe(body);
    expect(container.textContent).toContain("研发能力可能继续扩张");
    expect(container.querySelector(".hr-company-jobs")?.textContent).toBe(
      jobsText,
    );
    expect(api.jobs).toHaveBeenCalledTimes(1);
    expect(api.company).toHaveBeenCalledTimes(1);
    expect(push).not.toHaveBeenCalled();
  });
  it.each(["latest", "removed", "empty", "error"] as const)(
    "waits for the current directory before an uncached deep link: %s",
    async (result) => {
      const nextBundle = "22222222-2222-4222-8222-222222222222";
      const other = {
        ...summary,
        companyKey: "other",
        canonicalName: "另一家公司",
      };
      let resolveDirectory!: (value: CompanyDirectory | null) => void;
      let rejectDirectory!: (reason: Error) => void;
      const companies = vi
        .fn()
        .mockResolvedValueOnce({
          bundleId,
          generatedAt: detail.generatedAt,
          items: [summary, other],
          topics: { state: "blocked" },
        })
        .mockImplementationOnce(
          () =>
            new Promise((resolve, reject) => {
              resolveDirectory = resolve;
              rejectDirectory = reject;
            }),
        );
      const company = vi
        .fn()
        .mockImplementation(async (key: string, pinned: string) => ({
          ...detail,
          bundleId: pinned,
          company: other,
        }));
      await act(async () =>
        root.render(
          <HrPanoramaWorkspace
            account={account}
            api={fakeApi({ companies, company })}
          />,
        ),
      );
      await settle();
      await act(async () => {
        history.pushState({}, "", "/hr/panorama?company=other");
        window.dispatchEvent(new Event("platform:navigate"));
      });
      await settle();
      expect(company).not.toHaveBeenCalled();
      await act(async () =>
        result === "error"
          ? rejectDirectory(new Error("offline"))
          : resolveDirectory(
              result === "empty"
                ? null
                : {
                    bundleId: nextBundle,
                    generatedAt: detail.generatedAt,
                    items: result === "latest" ? [other] : [summary],
                    topics: { state: "blocked" },
                  },
            ),
      );
      await settle();
      if (result === "latest") {
        expect(company).toHaveBeenCalledExactlyOnceWith(
          "other",
          nextBundle,
          expect.any(AbortSignal),
        );
        expect(
          container.querySelector(".hr-company-detail h2")?.textContent,
        ).toBe("另一家公司");
      } else {
        expect(company).not.toHaveBeenCalled();
        expect(container.textContent).toContain(
          result === "empty"
            ? "当前没有已发布情报"
            : result === "error"
              ? "HR 情报暂时无法读取"
              : "当前情报不包含这家公司",
        );
      }
      expect(container.textContent).not.toContain("正在读取公司情报");
    },
  );
  it.each(["popstate", "platform:navigate"])(
    "stops pending detail on returning to the root via %s and ignores a late response",
    async (event) => {
      let resolveDetail!: (value: CompanyDetail) => void;
      const company = vi.fn().mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveDetail = resolve;
          }),
      );
      await act(async () =>
        root.render(
          <HrPanoramaWorkspace account={account} api={fakeApi({ company })} />,
        ),
      );
      await settle();
      await act(async () =>
        container
          .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
          .click(),
      );
      expect(container.textContent).toContain("正在读取公司情报");
      await act(async () => {
        history.pushState({}, "", "/hr/panorama");
        window.dispatchEvent(new Event(event));
      });
      await settle();
      expect(container.textContent).not.toContain("正在读取公司情报");
      expect(container.textContent).toContain("选择一家公司开始阅读");
      expect(company.mock.calls[0][2].aborted).toBe(true);
      await act(async () => resolveDetail(detail));
      await settle();
      expect(container.querySelector(".hr-company-detail")).toBeNull();
      expect(container.textContent).toContain("选择一家公司开始阅读");
    },
  );
  it("clears company reading and cached content after access is revoked", async () => {
    const companies = vi
      .fn()
      .mockResolvedValueOnce({
        bundleId,
        generatedAt: detail.generatedAt,
        items: [summary],
        topics: { state: "available" },
      })
      .mockRejectedValue(new HrCompanyIntelligenceApiError(403));
    const company = vi.fn().mockResolvedValue(detail);
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ companies, company })}
        />,
      ),
    );
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click(),
    );
    expect(container.textContent).toContain("研发能力可能继续扩张");
    await act(async () => window.dispatchEvent(new Event("platform:navigate")));
    expect(container.querySelector(".hr-company-detail")).toBeNull();
    expect(container.textContent).toContain("当前账号无法查看");
    await act(async () => {
      history.replaceState({}, "", "/hr/panorama");
      window.dispatchEvent(new Event("popstate"));
    });
    await act(async () => {
      history.replaceState(
        {},
        "",
        `/hr/panorama?company=acme&bundle_id=${bundleId}`,
      );
      window.dispatchEvent(new Event("popstate"));
    });
    expect(container.querySelector(".hr-company-detail")).toBeNull();
    expect(company).toHaveBeenCalledTimes(1);
  });
  it("invalidates a pending company directory when pinned detail denies access", async () => {
    history.replaceState(
      {},
      "",
      `/hr/panorama?company=acme&bundle_id=${bundleId}`,
    );
    const directory = {
      bundleId,
      generatedAt: detail.generatedAt,
      items: [summary],
      topics: { state: "available" },
    };
    let resolveDirectory!: (value: typeof directory) => void;
    const companies = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveDirectory = resolve;
        }),
    );
    const company = vi
      .fn()
      .mockRejectedValue(new HrCompanyIntelligenceApiError(403));
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace
          account={account}
          api={fakeApi({ companies, company })}
        />,
      ),
    );
    expect(companies.mock.calls[0][0].aborted).toBe(true);
    await act(async () => resolveDirectory(directory));
    expect(container.querySelector(".hr-company-detail")).toBeNull();
    expect(container.querySelector('[data-company-key="acme"]')).toBeNull();
    expect(container.textContent).toContain("当前账号无法查看");
    expect(company).toHaveBeenCalledTimes(1);
  });
  it("retries a failed company detail when its directory entry is clicked again", async () => {
    const company = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(detail);
    await act(async () =>
      root.render(
        <HrPanoramaWorkspace account={account} api={fakeApi({ company })} />,
      ),
    );
    await settle();
    const clickCompany = () =>
      container
        .querySelector<HTMLButtonElement>('[data-company-key="acme"]')!
        .click();
    await act(async () => clickCompany());
    await settle();
    expect(container.textContent).toContain("HR 情报暂时无法读取");
    await act(async () => clickCompany());
    await settle();
    expect(company).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("研发能力可能继续扩张");
    expect(container.textContent).not.toContain("HR 情报暂时无法读取");
  });
});
