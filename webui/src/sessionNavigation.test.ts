import { describe, expect, it } from "vitest";

import { sessionFiltersFromSearch, sessionsPath } from "./sessionNavigation";


describe("Session URL state", () => {
  it("round-trips Agent, source, and Unicode query text", () => {
    const path = sessionsPath({
      agent_id: "marketing-inbound-bot",
      source_kind: "metabot",
      q: "周报 机器人",
      page: 3,
    });

    expect(path).toBe("/sessions?agent_id=marketing-inbound-bot&source_kind=metabot&q=%E5%91%A8%E6%8A%A5+%E6%9C%BA%E5%99%A8%E4%BA%BA&page=3");
    expect(sessionFiltersFromSearch(path.slice(path.indexOf("?")))).toEqual({
      agent_id: "marketing-inbound-bot",
      source_kind: "metabot",
      q: "周报 机器人",
      page: 3,
    });
  });

  it("omits empty values and rejects unsupported sources", () => {
    expect(sessionFiltersFromSearch("?agent_id=test-bot&source_kind=other&q=%20%20")).toEqual({
      agent_id: "test-bot",
      source_kind: "",
      q: "",
      page: 1,
    });
    expect(sessionsPath({ agent_id: "", source_kind: "", q: "", page: 1 })).toBe("/sessions");
  });

  it("rejects malformed Agent IDs without rejecting Unicode search text", () => {
    expect(sessionFiltersFromSearch("?agent_id=bad%20id&q=%E6%9C%BA%E5%99%A8%E4%BA%BA")).toEqual({
      agent_id: "",
      source_kind: "",
      q: "机器人",
      page: 1,
    });
  });

  it.each(["0", "-1", "1.5", "01", "abc", "9007199254740992"])(
    "canonicalizes invalid page %s to the first page",
    (page) => {
      expect(sessionFiltersFromSearch(`?page=${encodeURIComponent(page)}`).page).toBe(1);
    },
  );
});
