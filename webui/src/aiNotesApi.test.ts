import { afterEach, describe, expect, it, vi } from "vitest";

import { aiNotesClient } from "./aiNotesApi";
import { AiNotesApiError, AiNotesContractError, parseAiNotesIndex } from "./aiNotesTypes";


const summary = {
  slug: "handbook",
  title: "Agent 系统手册",
  filename: "handbook.md",
  description: "从原理到实践。",
  published_at: "2026-08-27",
  updated_at: null,
  tags: ["Agent"],
  reading_minutes: 8,
};


describe("AI notes API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("fetches and validates the published index", async () => {
    const controller = new AbortController();
    const payload = {
      categories: [{ slug: "foundations", title: "基础与原理", articles: [summary] }],
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(payload),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(aiNotesClient.fetchIndex(controller.signal)).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/ai-notes", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
  });

  it("fetches an encoded article path", async () => {
    const article = {
      ...summary,
      category_slug: "foundations",
      category_title: "基础与原理",
      markdown: "# 正文",
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(article),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(aiNotesClient.fetchArticle("foundations", "handbook")).resolves.toEqual(article);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/ai-notes/foundations/handbook");
  });

  it("rejects unknown fields and malformed dates", async () => {
    const invalid = {
      categories: [{
        slug: "foundations",
        title: "基础与原理",
        articles: [{ ...summary, published_at: "27-08-2026", leaked: true }],
      }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(invalid),
    }));

    await expect(aiNotesClient.fetchIndex()).rejects.toBeInstanceOf(AiNotesContractError);
  });

  it.each(["2026-99-99", "2026-02-29", "2026-04-31", "0000-01-01"])(
    "rejects the invalid calendar date %s",
    (publishedAt) => {
      expect(() => parseAiNotesIndex({
        categories: [{
          slug: "foundations",
          title: "基础与原理",
          articles: [{ ...summary, published_at: publishedAt }],
        }],
      })).toThrow(AiNotesContractError);
    },
  );

  it("accepts a valid leap day", () => {
    expect(parseAiNotesIndex({
      categories: [{
        slug: "foundations",
        title: "基础与原理",
        articles: [{ ...summary, published_at: "2028-02-29" }],
      }],
    }).categories[0].articles[0].published_at).toBe("2028-02-29");
  });

  it.each([401, 404, 503])("preserves HTTP status %s", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status }));

    const error = await aiNotesClient.fetchArticle("foundations", "missing")
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(AiNotesApiError);
    expect((error as AiNotesApiError).status).toBe(status);
  });
});
