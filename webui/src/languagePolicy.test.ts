import { readFileSync, readdirSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";


const sourceRoot = fileURLToPath(new URL(".", import.meta.url));
const bannedSystemCopy = [
  "Read-only",
  "FLEET DIRECTORY",
  "AGENT OPERATIONS",
  "Fleet Snapshot",
  "Runtime Detail",
  "Activity History",
  "Session Replay",
  "Data unavailable",
];

function productionSources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return productionSources(path);
    if (![".ts", ".tsx"].includes(extname(entry.name)) || entry.name.includes(".test.")) return [];
    return [path];
  });
}


describe("Chinese system language policy", () => {
  it("does not reintroduce template-era English system copy", () => {
    const source = productionSources(sourceRoot)
      .map((path) => readFileSync(path, "utf8"))
      .join("\n");

    for (const phrase of bannedSystemCopy) expect(source).not.toContain(phrase);
  });
});
