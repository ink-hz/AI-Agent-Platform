import { describe, expect, it } from "vitest";

import { sessionFiltersFromSearch, sessionsPath } from "./sessionNavigation";


describe("Session URL state", () => {
  it("round-trips Agent, source, and Unicode query text", () => {
    const path = sessionsPath({
      agent_id: "marketing-inbound-bot",
      source_kind: "metabot",
      q: "周报 机器人",
    });

    expect(path).toBe("/sessions?agent_id=marketing-inbound-bot&source_kind=metabot&q=%E5%91%A8%E6%8A%A5+%E6%9C%BA%E5%99%A8%E4%BA%BA");
    expect(sessionFiltersFromSearch(path.slice(path.indexOf("?")))).toEqual({
      agent_id: "marketing-inbound-bot",
      source_kind: "metabot",
      q: "周报 机器人",
    });
  });

  it("omits empty values and rejects unsupported sources", () => {
    expect(sessionFiltersFromSearch("?agent_id=test-bot&source_kind=other&q=%20%20")).toEqual({
      agent_id: "test-bot",
      source_kind: "",
      q: "",
    });
    expect(sessionsPath({ agent_id: "", source_kind: "", q: "" })).toBe("/sessions");
  });

  it("rejects malformed Agent IDs without rejecting Unicode search text", () => {
    expect(sessionFiltersFromSearch("?agent_id=bad%20id&q=%E6%9C%BA%E5%99%A8%E4%BA%BA")).toEqual({
      agent_id: "",
      source_kind: "",
      q: "机器人",
    });
  });
});
