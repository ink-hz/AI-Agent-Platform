/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { aiEngineeringClient, AiEngineeringApiError } from "./aiEngineeringApi";


afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});


describe("AI engineering API client", () => {
  it("uses the deployment prefix and authenticated same-origin reads", async () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/ai-engineering");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ allowed: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        title: "AI 工程全景", version: "v1", updated_at: "2026-09-20T00:00:00Z",
        diagram: { svg_path: "/api/v1/ai-engineering/assets/panorama.svg", png_path: "/api/v1/ai-engineering/assets/panorama.png", alt: "AI 工程全景图" },
        documents: [{ slug: "overview", title: "全景总览" }],
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ slug: "overview", title: "全景总览", markdown: "# 正文" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(aiEngineeringClient.fetchAccess()).resolves.toEqual({ allowed: true });
    await expect(aiEngineeringClient.fetchIndex()).resolves.toMatchObject({ title: "AI 工程全景" });
    await expect(aiEngineeringClient.fetchDocument("overview")).resolves.toMatchObject({ markdown: "# 正文" });

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/_preview/dingtalk-r1/api/v1/ai-engineering/access", expect.objectContaining({ credentials: "same-origin", cache: "no-store" }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/_preview/dingtalk-r1/api/v1/ai-engineering", expect.objectContaining({ credentials: "same-origin" }));
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/_preview/dingtalk-r1/api/v1/ai-engineering/documents/overview", expect.objectContaining({ credentials: "same-origin" }));
  });

  it("rejects unknown document slugs before issuing a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(aiEngineeringClient.fetchDocument("../private" as never)).rejects.toThrow("unsupported AI engineering document");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("preserves authorization status and rejects malformed contracts", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 403 })));
    await expect(aiEngineeringClient.fetchIndex()).rejects.toEqual(expect.objectContaining<Partial<AiEngineeringApiError>>({ status: 403 }));

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ allowed: "yes" }), { status: 200 })));
    await expect(aiEngineeringClient.fetchAccess()).rejects.toThrow("AI engineering access response invalid");
  });
});
