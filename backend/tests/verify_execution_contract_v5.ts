import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

type Identity = {
  tenantId: string;
  appId: string;
  botId: string;
  messageId: string;
};

type RequestIdVector = { identity: Identity; requestId: string };
type ReplayCase = {
  existingRequestId: string;
  existingContentHash: string;
  incomingRequestId: string;
  incomingContentHash: string;
  expectedStatus: "accepted" | "duplicate" | "conflict";
};
type HashRules = {
  includedTopLevel: string[];
  attachmentProjection: string[];
  outputProjection: string[];
};
type Cases = {
  requestIdVectors: RequestIdVector[];
  intakeReplayCases: ReplayCase[];
  commandHashRules: HashRules;
  command: { valid: Record<string, unknown> };
};

const UUID_NAMESPACE_URL = "6ba7b811-9dad-11d1-80b4-00c04fd430c8";

function check(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function uuidBytes(value: string): Buffer {
  check(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value), "uuid fixture invalid");
  return Buffer.from(value.replaceAll("-", ""), "hex");
}

function formatUuid(bytes: Buffer): string {
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

function feishuRequestId(identity: Identity): string {
  const name = JSON.stringify([
    "hr-feishu-intake-v1",
    identity.tenantId,
    identity.appId,
    identity.botId,
    identity.messageId,
  ]);
  const digest = createHash("sha1")
    .update(uuidBytes(UUID_NAMESPACE_URL))
    .update(Buffer.from(name, "utf8"))
    .digest()
    .subarray(0, 16);
  digest[6] = (digest[6] & 0x0f) | 0x50;
  digest[8] = (digest[8] & 0x3f) | 0x80;
  return formatUuid(digest);
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right, "en"))
        .map(([key, member]) => [key, sortJson(member)]),
    );
  }
  return value;
}

function project(source: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  return Object.fromEntries(keys.map((key) => [key, source[key]]));
}

function commandHash(command: Record<string, unknown>, rules: HashRules): string {
  const document = project(command, rules.includedTopLevel);
  const grants = command.inputAttachmentGrants as Record<string, unknown>[];
  document.inputAttachments = grants.map((grant, index) => ({
    index,
    ...project(grant, rules.attachmentProjection.filter((key) => key !== "index")),
  }));
  const output = command.outputWriteGrant as Record<string, unknown> | null;
  document.outputScope = output === null ? null : project(output, rules.outputProjection);
  const encoded = JSON.stringify(sortJson(document));
  return createHash("sha256").update(Buffer.from(encoded, "utf8")).digest("hex");
}

function replayStatus(value: ReplayCase): "accepted" | "duplicate" | "conflict" {
  if (value.existingRequestId !== value.incomingRequestId) return "accepted";
  return value.existingContentHash === value.incomingContentHash
    ? "duplicate"
    : "conflict";
}

const fixturePath = process.argv[2];
check(typeof fixturePath === "string" && fixturePath.length > 0, "cases path required");
const cases = JSON.parse(readFileSync(fixturePath, "utf8")) as Cases;
check(cases.requestIdVectors.length === 4, "request-id vectors incomplete");
for (const vector of cases.requestIdVectors) {
  check(feishuRequestId(vector.identity) === vector.requestId, "request-id parity mismatch");
}
check(cases.intakeReplayCases.length === 3, "replay cases incomplete");
for (const replay of cases.intakeReplayCases) {
  check(replayStatus(replay) === replay.expectedStatus, "replay disposition mismatch");
}

const command = cases.command.valid;
const expectedCommandKeys = [
  "attemptId", "attemptNo", "commandHash", "commandId", "commandSeq",
  "contextHash", "contextMode", "contractVersion", "conversationId",
  "eventCallbackUrl", "inputAttachmentGrants", "leaseEpoch", "outputWriteGrant",
  "permissionScope", "principalRef", "prompt", "resultMode", "retryOf", "runId",
  "targetBot", "taskSessionId", "triggerMessageId", "turnId", "turnSeq",
].sort();
check(
  JSON.stringify(Object.keys(command).sort()) === JSON.stringify(expectedCommandKeys),
  "command fixture keys drifted",
);
check(
  commandHash(command, cases.commandHashRules) === command.commandHash,
  "command hash parity mismatch",
);

const rotated = structuredClone(command);
rotated.leaseEpoch = 2;
rotated.eventCallbackUrl = String(rotated.eventCallbackUrl).replace(/A{43}$/, "Z".repeat(43));
(rotated.inputAttachmentGrants as Record<string, unknown>[])[0].bearerToken = "Y".repeat(43);
(rotated.outputWriteGrant as Record<string, unknown>).bearerToken = "X".repeat(43);
check(
  commandHash(rotated, cases.commandHashRules) === command.commandHash,
  "transport rotation changed business hash",
);
const reordered = structuredClone(command);
(reordered.inputAttachmentGrants as unknown[]).reverse();
check(
  commandHash(reordered, cases.commandHashRules) !== command.commandHash,
  "attachment order missing from business hash",
);

process.stdout.write("v5 cross-language fixtures: ok\n");
