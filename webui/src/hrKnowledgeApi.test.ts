/** @vitest-environment jsdom */

import { afterEach, expect, it, vi } from "vitest";
import { fetchHrKnowledgeArticle, fetchHrKnowledgeIndex } from "./hrKnowledgeApi";

afterEach(() => { vi.unstubAllGlobals(); });

it("reads the versioned HR knowledge index and article with same-origin credentials", async () => {
  const resource = { id: "structured-interview", title: "结构化面试", revision: 1,
    domains: ["招聘"], knowledge_forms: ["方法"], path: "recruiting/structured-interview.md", sha256: "a".repeat(64) };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ source_commit: "abc123", index: "# 索引", resources: [resource] }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...resource, source_commit: "abc123", markdown: "# 结构化面试" }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  const index = await fetchHrKnowledgeIndex();
  await expect(fetchHrKnowledgeArticle(index.sourceCommit, resource.id)).resolves.toMatchObject({ markdown: "# 结构化面试" });
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/hr/knowledge", "/api/hr/knowledge/abc123/structured-interview",
  ]);
  expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
});

it("rejects absolute server paths and invalid hashes", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    source_commit: "abc123", index: "# 索引", resources: [{ id: "bad", title: "Bad", revision: 1,
      domains: [], knowledge_forms: [], path: "/srv/private.md", sha256: "bad" }],
  }), { status: 200 })));
  await expect(fetchHrKnowledgeIndex()).rejects.toThrow("invalid");
});
