/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import type { Account } from "../../auth";
import { HrCompanyIntelligenceApiError } from "../../hrCompanyIntelligenceApi";
import type {
  HrTopicIntelligenceApi,
  TopicDetail,
} from "../../hrTopicIntelligenceApi";
import { HrTopicWorkspace } from "./HrTopicWorkspace";
const account = { csrf_token: "csrf", internal_user_id: "one" } as Account;
const detail: TopicDetail = {
  bundleId: "11111111-1111-4111-8111-111111111111",
  generatedAt: "2026-09-08T09:00:00Z",
  topic: {
    topicId: "research",
    title: "人才布局",
    question: "样本说明什么？",
    scope: {
      description: "公开岗位样本",
      companyKeys: ["acme", "sample-only"],
      tracks: ["social"],
    },
    analysisState: "limited",
    unitIds: ["unit-1"],
    discussedCompanies: [
      {
        companyKey: "acme",
        unitId: "unit-1",
        claimIds: ["i1"],
        explanation: "正文讨论其布局",
      },
    ],
    limitations: ["不能推断编制"],
    summary: "样本覆盖有限",
  },
  units: [
    {
      unitId: "unit-1",
      kind: "topic",
      scopeKey: "research",
      response: {
        summary: "公开岗位摘要",
        confidence: "low",
        facts: [
          {
            factId: "f1",
            text: "公开事实",
            evidenceSha256: "a".repeat(64),
            sourceUrl: "https://example.com/source",
            observedAt: "2026-09-08T08:00:00Z",
          },
        ],
        inferences: [
          { inferenceId: "i1", text: "可能集中在研发", basisFactIds: ["f1"] },
        ],
        recommendations: [
          { recommendationId: "r1", text: "继续核查", basisFactIds: ["f1"] },
        ],
        alternatives: [
          {
            alternativeId: "a1",
            text: "可能是补缺",
            challengedInferenceIds: ["i1"],
            basisFactIds: ["f1"],
          },
        ],
        unknowns: ["真实编制未知"],
      },
    },
  ],
  companies: [{ companyKey: "acme", canonicalName: "艾克米" }],
};
const fakeApi = (
  overrides: Partial<HrTopicIntelligenceApi> = {},
): HrTopicIntelligenceApi => ({
  topics: vi
    .fn()
    .mockResolvedValue({
      bundleId: detail.bundleId,
      generatedAt: detail.generatedAt,
      state: "available",
      items: [detail.topic],
    }),
  topic: vi.fn().mockResolvedValue(detail),
  ...overrides,
});
const settle = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};
describe("topic workspace", () => {
  let container: HTMLDivElement, root: ReturnType<typeof createRoot>;
  const click = async (text: string) => {
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((node) => node.textContent === text)!
        .click(),
    );
    await settle();
  };
  beforeEach(() => {
    history.replaceState({}, "", "/hr/panorama?view=topics");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (
      globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });
  it("opens the declared topic, displays limits before claims and distinguishes sample scope from discussion", async () => {
    const api = fakeApi(),
      select = vi.fn(),
      company = vi.fn();
    await act(async () =>
      root.render(
        <HrTopicWorkspace
          account={account}
          api={api}
          onSelectReference={select}
          onOpenCompany={company}
        />,
      ),
    );
    await click("人才布局样本覆盖有限分析有限");
    expect(api.topic).toHaveBeenCalledWith(
      "research",
      detail.bundleId,
      expect.any(AbortSignal),
    );
    expect(container.textContent).toContain("样本说明什么？");
    expect(container.textContent).toContain("不能推断编制");
    expect(container.textContent).toContain("样本范围内公司：2 家");
    expect(container.textContent).toContain("正文讨论的公司");
    expect(container.textContent).toContain("可能是补缺");
    expect(container.querySelectorAll("[data-company-key]")).toHaveLength(1);
    expect(select).not.toHaveBeenCalled();
    await click("带入专题情报");
    expect(select).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "topic",
        topicId: "research",
        analysisState: "limited",
        limitations: ["不能推断编制"],
      }),
    );
    expect(select.mock.calls[0][0]).not.toHaveProperty("companyKey");
    await click("带入判断");
    expect(select).toHaveBeenLastCalledWith(
      expect.objectContaining({
        unitId: "unit-1",
        localId: "i1",
        sourceUrls: ["https://example.com/source"],
      }),
    );
    expect(
      container.querySelector('a[href*="/evidence/"]')?.getAttribute("href"),
    ).toContain(detail.bundleId);
    await click("艾克米");
    expect(company).toHaveBeenCalledWith("acme", detail.bundleId);
    await click("返回专题目录");
    expect(container.querySelector(".hr-topic-detail")).toBeNull();
  });
  it("searches business titles and questions without choosing fixed topic names", async () => {
    await act(async () =>
      root.render(<HrTopicWorkspace account={account} api={fakeApi()} />),
    );
    const input = container.querySelector("input")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.set!.call(input, "不存在");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.textContent).toContain("没有匹配的专题");
  });
  it("keeps missing metadata distinct and does not reconstruct technical placeholder topics", async () => {
    await act(async () =>
      root.render(
        <HrTopicWorkspace
          account={account}
          api={fakeApi({
            topics: vi
              .fn()
              .mockResolvedValue({
                bundleId: detail.bundleId,
                generatedAt: detail.generatedAt,
                state: "metadata_missing",
                items: [],
              }),
          })}
        />,
      ),
    );
    expect(container.textContent).toContain("专题目录尚未就绪");
    expect(container.querySelectorAll("[data-topic-id]")).toHaveLength(0);
  });
  it("ignores a late detail after returning and aborts its request", async () => {
    let resolve!: (detail: TopicDetail) => void;
    const api = fakeApi({
      topic: vi.fn().mockImplementation(
        () =>
          new Promise((r) => {
            resolve = r;
          }),
      ),
    });
    await act(async () =>
      root.render(<HrTopicWorkspace account={account} api={api} />),
    );
    await click("人才布局样本覆盖有限分析有限");
    await click("返回专题目录");
    expect(vi.mocked(api.topic).mock.calls[0][2]?.aborted).toBe(true);
    await act(async () => resolve(detail));
    expect(container.querySelector(".hr-topic-detail")).toBeNull();
  });
  it("opens an explicitly pinned topic even when current catalog is unavailable", async () => {
    history.replaceState(
      {},
      "",
      `/hr/panorama?view=topics&topic=research&bundle_id=${detail.bundleId}`,
    );
    const api = fakeApi({ topics: vi.fn().mockResolvedValue(null) });
    await act(async () =>
      root.render(<HrTopicWorkspace account={account} api={api} />),
    );
    expect(api.topic).toHaveBeenCalledWith(
      "research",
      detail.bundleId,
      expect.any(AbortSignal),
    );
    expect(container.textContent).toContain("可能集中在研发");
  });
  it("keeps an initially unpinned topic immutable when the latest directory changes", async () => {
    history.replaceState({}, "", "/hr/panorama?view=topics&topic=research");
    const nextBundle = "22222222-2222-4222-8222-222222222222";
    const topics = vi
      .fn()
      .mockResolvedValueOnce({
        bundleId: detail.bundleId,
        generatedAt: detail.generatedAt,
        state: "available",
        items: [detail.topic],
      })
      .mockResolvedValue({
        bundleId: nextBundle,
        generatedAt: detail.generatedAt,
        state: "available",
        items: [detail.topic],
      });
    const api = fakeApi({
      topics,
      topic: vi
        .fn()
        .mockImplementation(async (_id: string, pinned: string) => ({
          ...detail,
          bundleId: pinned,
        })),
    });
    await act(async () =>
      root.render(<HrTopicWorkspace account={account} api={api} />),
    );
    await settle();
    await act(async () => window.dispatchEvent(new Event("platform:navigate")));
    await settle();
    expect(
      vi
        .mocked(api.topic)
        .mock.calls.every((call) => call[1] === detail.bundleId),
    ).toBe(true);
    await click("返回专题目录");
    await click("人才布局样本覆盖有限分析有限");
    expect(api.topic).toHaveBeenLastCalledWith(
      "research",
      nextBundle,
      expect.any(AbortSignal),
    );
  });
  it.each([401, 403])(
    "clears previously visible private content after auth failure %s",
    async (status) => {
      const topics = vi
        .fn()
        .mockResolvedValueOnce({
          bundleId: detail.bundleId,
          generatedAt: detail.generatedAt,
          state: "available",
          items: [detail.topic],
        })
        .mockRejectedValue(new HrCompanyIntelligenceApiError(status));
      await act(async () =>
        root.render(
          <HrTopicWorkspace account={account} api={fakeApi({ topics })} />,
        ),
      );
      await click("人才布局样本覆盖有限分析有限");
      await act(async () =>
        window.dispatchEvent(new Event("platform:navigate")),
      );
      expect(container.querySelector(".hr-topic-detail")).toBeNull();
      expect(container.textContent).toContain(
        status === 401 ? "登录状态已失效" : "当前账号无法查看",
      );
      expect(container.textContent).not.toContain("人才布局");
    },
  );
  it("rejects oversized topic selection without truncating its caveats", async () => {
    history.replaceState(
      {},
      "",
      `/hr/panorama?view=topics&topic=research&bundle_id=${detail.bundleId}`,
    );
    const select = vi.fn();
    await act(async () =>
      root.render(
        <HrTopicWorkspace
          account={account}
          api={fakeApi({
            topic: vi
              .fn()
              .mockResolvedValue({
                ...detail,
                topic: { ...detail.topic, summary: "招".repeat(5000) },
              }),
          })}
          onSelectReference={select}
        />,
      ),
    );
    await click("带入专题情报");
    expect(select).not.toHaveBeenCalled();
    expect(container.textContent).toContain("所选材料过长");
  });
});
