import { describe, expect, it } from "vitest";
import {
  MARKETING_AGENT_ID_BY_SLUG,
  directConversationPath,
  workspaceForAgent,
  workspaceLaunchPath,
} from "./workspaces";

describe("workspace route registry", () => {
  it.each([
    ["ai-admin-agent", "/office/?view=services"],
    ["ai-fae-agent", "/fae/"],
    ["voc", "/voc/"],
    ["hr-bot", "/hr/"],
    ["marketing-prospecting-bot", "/marketing/prospecting"],
    ["marketing-inbound-bot", "/marketing/inbound"],
    ["marketing-voice-bot", "/marketing/voice"],
    ["marketing-intelligence-bot", "/marketing/intelligence"],
    ["marketing-gtm-bot", "/marketing/gtm"],
  ])("maps %s to %s", (agentId, path) => {
    expect(workspaceLaunchPath(agentId)).toBe(path);
  });

  it("keeps the five marketing slugs stable", () => {
    expect(MARKETING_AGENT_ID_BY_SLUG).toEqual({
      prospecting: "marketing-prospecting-bot",
      inbound: "marketing-inbound-bot",
      voice: "marketing-voice-bot",
      intelligence: "marketing-intelligence-bot",
      gtm: "marketing-gtm-bot",
    });
  });

  it("builds only Platform conversation deep links", () => {
    expect(directConversationPath("hr-bot", "c:1")).toBe("/hr/conversations/c%3A1");
    expect(directConversationPath("marketing-voice-bot", "c:2"))
      .toBe("/marketing/voice/conversations/c%3A2");
    expect(directConversationPath("ai-fae-agent", "c:3"))
      .toBe("/fae/conversations/c%3A3");
    expect(directConversationPath("voc", "c:4")).toBeNull();
  });

  it("rejects unknown agent ids", () => {
    expect(workspaceForAgent("unknown-agent")).toBeNull();
    expect(workspaceLaunchPath("unknown-agent")).toBeNull();
  });
});
