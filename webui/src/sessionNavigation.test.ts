import { describe, expect, it } from "vitest";

import { EMPTY_SESSION_FILTERS, sessionFiltersFromSearch, sessionsPath } from "./sessionNavigation";


describe("Session URL state", () => {
  it("round-trips Agent, source, and Unicode query text", () => {
    const path = sessionsPath({
      ...EMPTY_SESSION_FILTERS,
      agent_id: "marketing-inbound-bot",
      source_kind: "metabot",
      q: "周报 机器人",
      page: 3,
    });

    expect(path).toBe("/admin/sessions?agent_id=marketing-inbound-bot&source_kind=metabot&q=%E5%91%A8%E6%8A%A5+%E6%9C%BA%E5%99%A8%E4%BA%BA&page=3");
    expect(sessionFiltersFromSearch(path.slice(path.indexOf("?")))).toEqual({
      ...EMPTY_SESSION_FILTERS,
      agent_id: "marketing-inbound-bot",
      source_kind: "metabot",
      q: "周报 机器人",
      page: 3,
    });
  });

  it("omits empty values and rejects unsupported sources", () => {
    expect(sessionFiltersFromSearch("?agent_id=test-bot&source_kind=other&q=%20%20")).toEqual({
      ...EMPTY_SESSION_FILTERS,
      agent_id: "test-bot",
      source_kind: "",
      q: "",
      page: 1,
    });
    expect(sessionsPath({ ...EMPTY_SESSION_FILTERS })).toBe("/admin/sessions");
  });

  it("rejects malformed Agent IDs without rejecting Unicode search text", () => {
    expect(sessionFiltersFromSearch("?agent_id=bad%20id&q=%E6%9C%BA%E5%99%A8%E4%BA%BA")).toEqual({
      ...EMPTY_SESSION_FILTERS,
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

  it("uses the supplied listing base path without changing generic defaults", () => {
    expect(sessionsPath(EMPTY_SESSION_FILTERS)).toBe("/admin/sessions");
    expect(sessionsPath(EMPTY_SESSION_FILTERS, "/admin/fae/sessions")).toBe("/admin/fae/sessions");
  });

  it("keeps FAE-only filters out of generic Session URLs", () => {
    const filters = {
      ...EMPTY_SESSION_FILTERS,
      channel: "fae",
      sentiment: "negative" as const,
      date_before: "2026-08-31T00:00:00+08:00",
    };

    expect(sessionsPath(filters)).toBe("/admin/sessions");
    expect(sessionsPath(filters, "/admin/fae/sessions")).toBe("/admin/fae/sessions?channel=fae&sentiment=negative&date_before=2026-08-31T00%3A00%3A00%2B08%3A00");
    expect(sessionFiltersFromSearch("?date_before=2026-08-31T00%3A00%3A00%2B08%3A00").date_before).toBe("2026-08-31T00:00:00+08:00");
  });
});
