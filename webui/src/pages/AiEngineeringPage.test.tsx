/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AiEngineeringClient, AiEngineeringIndex } from "../aiEngineeringApi";
import { AiEngineeringApiError } from "../aiEngineeringApi";
import type { Account } from "../auth";
import { AiEngineeringLanding } from "./AiEngineeringPage";


const owner: Account = {
  internal_user_id: "owner", display_name: "Owner", role: "platform_owner", departments: ["管理层"], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf",
};
const index: AiEngineeringIndex = {
  title: "AI 工程全景", version: "v1", updated_at: "2026-09-20T00:00:00Z",
  diagram: {
    svg_path: "/api/v1/ai-engineering/assets/panorama.svg",
    png_path: "/api/v1/ai-engineering/assets/panorama.png",
    alt: "AI 工程全景图",
  },
  documents: [
    { slug: "overview", title: "全景总览" },
    { slug: "reading", title: "阅读指南" },
  ],
};

function clientWith(allowed = true): AiEngineeringClient & Record<"fetchAccess" | "fetchIndex" | "fetchDocument", ReturnType<typeof vi.fn>> {
  return {
    fetchAccess: vi.fn().mockResolvedValue({ allowed }),
    fetchIndex: vi.fn().mockResolvedValue(index),
    fetchDocument: vi.fn().mockResolvedValue({
      slug: "overview", title: "全景总览",
      markdown: "[![AI 工程全景图](assets/2026-09-20-panorama-v1.png)](assets/2026-09-20-panorama-v1.svg)\n\n[阅读](2026-09-20-panorama-reading-guide.md) [事实来源](https://example.com/fact) [本地文件](../private.md)\n\n| 域 | 状态 |\n|---|---|\n| 平台 | 已覆盖 |\n\n<script>alert(1)</script>",
    }),
  };
}


describe("AiEngineeringLanding", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  it("keeps the old homepage for a disallowed owner and never loads protected content", async () => {
    const client = clientWith(false);
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} selectedDocument="overview" fallback={<div>旧 AI 助手</div>} />,
    ));

    expect(container.textContent).toContain("旧 AI 助手");
    expect(client.fetchIndex).not.toHaveBeenCalled();
    expect(client.fetchDocument).not.toHaveBeenCalled();
  });

  it("denies direct access even when the disallowed account is a platform owner", async () => {
    const client = clientWith(false);
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} direct selectedDocument="overview" fallback={null} />,
    ));

    expect(container.textContent).toContain("无权限");
    expect(client.fetchIndex).not.toHaveBeenCalled();
  });

  it("falls back to the old homepage when the access check is unavailable", async () => {
    const client = clientWith();
    client.fetchAccess.mockRejectedValue(new TypeError("offline"));
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} selectedDocument="overview" fallback={<div>旧 AI 助手</div>} />,
    ));

    expect(container.textContent).toContain("旧 AI 助手");
    expect(container.textContent).not.toContain("AI 工程全景");
  });

  it("bounds a stalled access probe and restores the ordinary homepage", async () => {
    vi.useFakeTimers();
    const client = clientWith();
    client.fetchAccess.mockImplementation(() => new Promise(() => undefined));
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} selectedDocument="overview" fallback={<div>旧 AI 助手</div>} />,
    ));
    const signal = client.fetchAccess.mock.calls[0][0] as AbortSignal;
    expect(container.textContent).toContain("正在确认访问权限");

    await act(async () => vi.advanceTimersByTimeAsync(5_000));

    expect(signal.aborted).toBe(true);
    expect(container.textContent).toContain("旧 AI 助手");
    expect(client.fetchIndex).not.toHaveBeenCalled();
  });

  it("loads protected content only after server authorization and maps Markdown URLs safely", async () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/");
    const client = clientWith();
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} selectedDocument="overview" fallback={<div>旧 AI 助手</div>} />,
    ));

    expect(client.fetchAccess.mock.invocationCallOrder[0]).toBeLessThan(client.fetchIndex.mock.invocationCallOrder[0]);
    expect(client.fetchDocument).toHaveBeenCalledWith("overview", expect.any(AbortSignal));
    expect(container.querySelector<HTMLImageElement>("img.ai-engineering-diagram")?.getAttribute("src"))
      .toBe("/_preview/dingtalk-r1/api/v1/ai-engineering/assets/panorama.png");
    expect(container.querySelector<HTMLAnchorElement>('a[href="/_preview/dingtalk-r1/api/v1/ai-engineering/assets/panorama.svg"]')).not.toBeNull();
    expect(container.querySelector<HTMLAnchorElement>('a[href="/_preview/dingtalk-r1/ai-engineering?document=reading"]')).not.toBeNull();
    expect(container.querySelector<HTMLAnchorElement>('a[href="https://example.com/fact"]')?.getAttribute("target")).toBe("_blank");
    expect([...container.querySelectorAll("span.ai-engineering-unsafe-link")].some((node) => node.textContent === "本地文件")).toBe(true);
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).not.toContain("alert(1)");
  });

  it("clears protected content on a document 403", async () => {
    const client = clientWith();
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} direct selectedDocument="overview" fallback={<div>旧 AI 助手</div>} />,
    ));
    expect(container.textContent).toContain("全景总览");

    client.fetchDocument.mockRejectedValueOnce(new AiEngineeringApiError(403));
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} direct selectedDocument="reading" fallback={<div>旧 AI 助手</div>} />,
    ));

    expect(container.textContent).toContain("无权限");
    expect(container.querySelector(".ai-engineering-markdown")).toBeNull();
  });

  it("clears the protected article if its authenticated image cannot be read", async () => {
    const client = clientWith();
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} direct selectedDocument="overview" fallback={null} />,
    ));
    const image = container.querySelector<HTMLImageElement>("img.ai-engineering-diagram")!;

    await act(async () => image.dispatchEvent(new Event("error", { bubbles: true })));

    expect(container.textContent).toContain("AI 工程全景暂时不可用");
    expect(container.querySelector(".ai-engineering-markdown")).toBeNull();
    expect(container.textContent).not.toContain("事实来源");
  });

  it("aborts requests and ignores stale completion when the account changes", async () => {
    let resolveFirst!: (value: { allowed: boolean }) => void;
    const firstAccess = new Promise<{ allowed: boolean }>((resolve) => { resolveFirst = resolve; });
    const client = clientWith(false);
    client.fetchAccess.mockReturnValueOnce(firstAccess).mockResolvedValueOnce({ allowed: false });

    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} selectedDocument="overview" fallback={<div>旧 AI 助手</div>} />,
    ));
    const firstSignal = client.fetchAccess.mock.calls[0][0] as AbortSignal;
    await act(async () => root.render(
      <AiEngineeringLanding account={{ ...owner, internal_user_id: "other" }} client={client} selectedDocument="overview" fallback={<div>另一账号的大脑</div>} />,
    ));
    expect(firstSignal.aborted).toBe(true);

    await act(async () => resolveFirst({ allowed: true }));
    expect(container.textContent).toContain("另一账号的大脑");
    expect(client.fetchIndex).not.toHaveBeenCalled();
  });

  it("removes an already displayed protected snapshot as soon as the account changes", async () => {
    let resolveSecond!: (value: { allowed: boolean }) => void;
    const secondAccess = new Promise<{ allowed: boolean }>((resolve) => { resolveSecond = resolve; });
    const client = clientWith();
    client.fetchAccess.mockResolvedValueOnce({ allowed: true }).mockReturnValueOnce(secondAccess);
    await act(async () => root.render(
      <AiEngineeringLanding account={owner} client={client} selectedDocument="overview" fallback={<div>旧 AI 助手</div>} />,
    ));
    expect(container.querySelector(".ai-engineering-markdown")).not.toBeNull();

    await act(async () => root.render(
      <AiEngineeringLanding account={{ ...owner, internal_user_id: "other" }} client={client} selectedDocument="overview" fallback={<div>另一账号的大脑</div>} />,
    ));
    expect(container.querySelector(".ai-engineering-markdown")).toBeNull();
    expect(container.textContent).not.toContain("事实来源");

    await act(async () => resolveSecond({ allowed: false }));
    expect(container.textContent).toContain("另一账号的大脑");
  });
});
