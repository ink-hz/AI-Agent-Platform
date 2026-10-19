import { expect, it } from "vitest";

import { RequestIdentity } from "./requestIdentity";

it("reuses a request ID for a retry and changes it when content changes", () => {
  let next = 0;
  const identity = new RequestIdentity(() => `request-${++next}`);
  expect(identity.forPayload({ title: "USB" })).toBe("request-1");
  expect(identity.forPayload({ title: "USB" })).toBe("request-1");
  expect(identity.forPayload({ title: "Linux" })).toBe("request-2");
  identity.clear();
  expect(identity.forPayload({ title: "USB" })).toBe("request-3");
});
