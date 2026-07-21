import { describe, expect, it } from "vitest";

import { agentIdentity } from "./identity";


describe("agentIdentity", () => {
  it("gives known runtime bots a friendly operational identity", () => {
    expect(agentIdentity("hr-bot", "hr-bot")).toEqual({
      name: "HR 助手",
      domain: "人力资源",
      description: "支持招聘、人事与员工服务流程。",
      glyph: "HR",
      accent: "people",
    });

    expect(
      agentIdentity("marketing-intelligence-bot", "marketing-intelligence-bot"),
    ).toMatchObject({
      name: "市场情报助手",
      domain: "Marketing · Intelligence",
      glyph: "情",
    });
  });

  it("covers every instance in the current runtime contract", () => {
    const ids = [
      "feishu-default",
      "hr-bot",
      "marketing-prospecting-bot",
      "marketing-inbound-bot",
      "marketing-voice-bot",
      "fae-bot",
      "test-bot",
      "marketing-gtm-bot",
      "marketing-intelligence-bot",
    ];

    for (const id of ids) {
      const identity = agentIdentity(id, id);
      expect(identity.name).not.toBe(id);
      expect(identity.description).not.toBe("");
      expect(identity.glyph).not.toBe("AI");
    }
  });

  it("keeps dynamically discovered bots visible with a generic identity", () => {
    expect(agentIdentity("new-bot", "new-bot")).toEqual({
      name: "new-bot",
      domain: "MetaBot 实例",
      description: "由运行契约动态发现的 Agent Bot 实例。",
      glyph: "AI",
      accent: "default",
    });
  });
});
