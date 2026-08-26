import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";


const USER_WORKROOM_FILES = [
  "src/pages/ConversationPage.tsx",
  "src/components/conversation/PublicProgress.tsx",
  "src/components/conversation/MultiAgentWorkroom.tsx",
  "src/components/conversation/WorkroomTeamView.tsx",
  "src/components/conversation/WorkroomTimeline.tsx",
  "src/components/conversation/WorkroomAgentSession.tsx",
];


describe("live workroom progress", () => {
  it("contains no timed or invented progress machinery", () => {
    const source = USER_WORKROOM_FILES.map((path) => readFileSync(path, "utf8")).join("\n");
    expect(source).not.toMatch(/setTimeout|setInterval/);
    expect(source).not.toContain("深入思考");
    expect(source).not.toContain("正在整理");
    expect(source).not.toContain("诊断详情");
  });
});
