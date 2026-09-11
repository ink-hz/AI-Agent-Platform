import { afterEach, expect, it, vi } from "vitest";
import { createHrLoopCandidatesApi } from "./hrLoopCandidatesApi";

afterEach(() => vi.unstubAllGlobals());
it("keeps exact confirmation content, caller identity and CSRF", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(
      async () =>
        new Response(JSON.stringify({ state: "confirmed" }), { status: 200 }),
    );
  vi.stubGlobal("fetch", fetcher);
  const api = createHrLoopCandidatesApi("csrf");
  const input = {
    expected_row_version: 3,
    result_ref: {
      kind: "result",
      id: "r",
      revision: "original",
      sha256: "a".repeat(64),
    },
    display_name: "合成姓名",
    summary: "人工核对摘要",
    decision: { kind: "link_existing" as const, candidate_id: "explicit" },
    reviewed_limitations: true,
  };
  await api.confirm("item", input, "same-intent");
  const [path, init] = fetcher.mock.calls[0];
  expect(path).toContain("/candidate-items/item/confirm");
  expect(init).toMatchObject({
    credentials: "include",
    cache: "no-store",
    method: "POST",
  });
  expect(init.headers["X-CSRF-Token"]).toBe("csrf");
  expect(init.headers["Idempotency-Key"]).toBe("same-intent");
  expect(JSON.parse(init.body)).toEqual(input);
});
it("exposes the server conflict without changing the review identity", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ code: "revision_conflict" }), {
          status: 409,
        }),
      ),
  );
  await expect(
    createHrLoopCandidatesApi("csrf").retry(
      "item",
      { expected_row_version: 2, stage: "parse" },
      "key",
    ),
  ).rejects.toMatchObject({ status: 409, code: "revision_conflict" });
});
it("uses exact candidate interview record endpoints and preserves registration identity", async () => {
  const fetcher = vi.fn().mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetcher);
  const api = createHrLoopCandidatesApi("csrf");
  await api.interviewRecords("candidate/one");
  await api.interviewRecord("candidate/one", "record/two");
  const body = {
    material_ref: { kind: "material", id: "a:text", revision: "v1", sha256: "a".repeat(64) },
    title: "一面原文",
    occurred_at: null,
    position_id: null,
    interview_plan_ref: null,
  };
  await api.registerInterviewRecord("candidate/one", body, "record-key");
  expect(fetcher.mock.calls[0][0]).toContain("/candidates/candidate%2Fone/interview-records");
  expect(fetcher.mock.calls[1][0]).toContain("/interview-records/record%2Ftwo");
  expect(fetcher.mock.calls[2][1]).toMatchObject({ method: "POST" });
  expect(fetcher.mock.calls[2][1].headers["Idempotency-Key"]).toBe("record-key");
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual(body);
});
