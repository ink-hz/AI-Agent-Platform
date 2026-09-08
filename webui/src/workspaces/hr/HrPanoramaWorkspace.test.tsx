/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Account } from "../../auth";
import type { HrCompanyIntelligenceApi } from "../../hrCompanyIntelligenceTypes";
import { HrPanoramaWorkspace } from "./HrPanoramaWorkspace";

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
    expect(container.textContent).toContain("研发投入保持活跃");
    expect(container.textContent).toContain("岗位暂时无法读取");
    expect(container.textContent).toContain("重试");
  });
  it("explains blocked topics honestly", async () => {
    await act(async () =>
      root.render(<HrPanoramaWorkspace account={account} api={fakeApi()} />),
    );
    await settle();
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((node) => node.textContent === "专题")!
        .click(),
    );
    expect(container.textContent).toContain("专题情报暂不可用");
    expect(container.textContent).not.toContain("公司排名");
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
    const companies = vi
      .fn()
      .mockResolvedValue({
        bundleId,
        generatedAt: detail.generatedAt,
        items: [summary, other],
        topics: { state: "blocked" },
      });
    const company = vi
      .fn()
      .mockImplementation(async (key: string) => ({
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
    history.replaceState({}, "", "/hr/panorama?company=acme");
    await act(async () => window.dispatchEvent(new PopStateEvent("popstate")));
    await settle();
    expect(container.querySelector(".hr-company-detail h2")?.textContent).toBe(
      "艾克米",
    );
  });
});
