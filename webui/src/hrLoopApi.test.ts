import { afterEach, describe, expect, it, vi } from "vitest";
import { createHrLoopApi, HrLoopError } from "./hrLoopApi";

afterEach(() => vi.unstubAllGlobals());
describe("HR loop API", () => {
  it("submits explicit references and retains caller mutation identity", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ work_id: "work" }), { status: 201 }),
      );
    vi.stubGlobal("fetch", fetcher);
    const api = createHrLoopApi("csrf");
    const input = {
      thread_id: null,
      text: "公开岗位",
      objects: [],
      references: [],
      budget_profile: "approved",
    };
    await api.submit(input, "intent-1");
    const [, init] = fetcher.mock.calls[0];
    expect(init.credentials).toBe("include");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf");
    expect(init.headers["Idempotency-Key"]).toBe("intent-1");
    expect(JSON.parse(init.body)).toEqual(input);
  });
  it("exposes conflict without replacing expected revision", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({
              code: "revision_conflict",
              details: { current_revision: "new" },
            }),
            { status: 409 },
          ),
        ),
    );
    const api = createHrLoopApi("csrf");
    await expect(
      api.confirm(
        "p",
        {
          proposal_ref: {
            kind: "result",
            id: "r",
            revision: "old",
            sha256: "a",
          },
          selected_change_ids: ["one"],
          expected_standard_revision: "old",
        },
        "intent",
      ),
    ).rejects.toMatchObject({ status: 409, code: "revision_conflict" });
  });
  it("downloads an exact revision through current authenticated API", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response("body", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const blob = await createHrLoopApi("csrf").download({
      kind: "result",
      id: "r",
      revision: "v1",
      sha256: "a",
    });
    expect(await blob.text()).toBe("body");
    expect(fetcher.mock.calls[0][0]).toContain("/results/r/revisions/v1/file");
  });
  it("does not show raw provider errors as user messages", () => {
    expect(
      new HrLoopError(503, "configuration_unavailable", {}).message,
    ).not.toContain("configuration_unavailable");
  });
});

it("paginates streams to the end without losing entries", async () => {
  const { readStream } = await import("./hrLoopApi");
  const fetchPage = vi
    .fn()
    .mockResolvedValueOnce({
      items: Array.from({ length: 200 }, (_, i) => ({ seq: i + 1 })),
      next_after: 200,
    })
    .mockResolvedValueOnce({ items: [{ seq: 201 }], next_after: 201 });
  const result = await readStream(fetchPage, 0, () => true);
  expect(result.items).toHaveLength(201);
  expect(result.next_after).toBe(201);
  expect(fetchPage).toHaveBeenNthCalledWith(2, 200);
});
