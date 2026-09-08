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
type IntakeHashRules = {
  includedTopLevel: string[];
  identityProjection: string[];
  senderProjection: string[];
  attachmentProjection: string[];
  excludedWireFields: string[];
  excludedTransportFields: string[];
};
type IntakeHashVector = {
  channelBridgeValidRequestIndex: number;
  contentHash: string;
};
type Cases = {
  requestIdVectors: RequestIdVector[];
  intakeReplayCases: ReplayCase[];
  intakeHashRules: IntakeHashRules;
  intakeHashVectors: IntakeHashVector[];
  commandHashRules: HashRules;
  command: { valid: Record<string, unknown> };
  channelBridge: { validRequests: Record<string, unknown>[] };
};

const UUID_NAMESPACE_URL = "6ba7b811-9dad-11d1-80b4-00c04fd430c8";

function check(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function checkCanonicalUuid(value: unknown, field: string): asserts value is string {
  check(
    typeof value === "string"
      && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value),
    `${field} UUID is not canonical lowercase wire form`,
  );
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
  for (const key of [
    "runId", "commandId", "attemptId", "turnId", "conversationId", "triggerMessageId",
  ]) {
    checkCanonicalUuid(command[key], key);
  }
  if (command.retryOf !== null) checkCanonicalUuid(command.retryOf, "retryOf");
  const permissionScope = command.permissionScope as Record<string, unknown>;
  checkCanonicalUuid(permissionScope.conversationId, "permissionScope.conversationId");
  for (const grant of command.inputAttachmentGrants as Record<string, unknown>[]) {
    checkCanonicalUuid(grant.attachmentId, "inputAttachmentGrants.attachmentId");
  }
  const writeGrant = command.outputWriteGrant as Record<string, unknown> | null;
  if (writeGrant !== null) checkCanonicalUuid(writeGrant.taskId, "outputWriteGrant.taskId");
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

function intakeHash(envelope: Record<string, unknown>, rules: IntakeHashRules): string {
  checkCanonicalUuid(envelope.requestId, "requestId");
  checkCanonicalUuid(envelope.conversationId, "conversationId");
  for (const attachment of envelope.attachments as Record<string, unknown>[]) {
    checkCanonicalUuid(attachment.attachmentId, "attachments.attachmentId");
  }
  const document = project(envelope, rules.includedTopLevel);
  document.identity = project(
    envelope.identity as Record<string, unknown>,
    rules.identityProjection,
  );
  document.senderIdentity = project(
    envelope.senderIdentity as Record<string, unknown>,
    rules.senderProjection,
  );
  document.attachments = (envelope.attachments as Record<string, unknown>[]).map(
    (attachment) => project(attachment, rules.attachmentProjection),
  );
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

check(cases.intakeHashVectors.length === 1, "intake hash vectors incomplete");
const intakeVector = cases.intakeHashVectors[0];
const intake = cases.channelBridge.validRequests[
  intakeVector.channelBridgeValidRequestIndex
];
const expectedIntakeKeys = [
  "attachments", "chatId", "contentHash", "conversationId", "identity",
  "operation", "principalRef", "requestId", "senderIdentity", "text", "threadKey",
].sort();
check(
  JSON.stringify(Object.keys(intake).sort()) === JSON.stringify(expectedIntakeKeys),
  "turn-intake fixture is not a strict wire body",
);
check(
  intakeHash(intake, cases.intakeHashRules) === intakeVector.contentHash,
  "turn-intake golden hash mismatch",
);
check(intake.contentHash === intakeVector.contentHash, "turn-intake contentHash drifted");
const intakeMutations: Record<string, unknown>[] = [];
for (const mutate of [
  (value: Record<string, unknown>) => { value.text = `${String(value.text)}changed`; },
  (value: Record<string, unknown>) => {
    (value.senderIdentity as Record<string, unknown>).openId = "ou_other_user";
  },
  (value: Record<string, unknown>) => { value.principalRef = "principal:hr-user-002"; },
  (value: Record<string, unknown>) => {
    value.conversationId = "00000000-0000-4000-8000-000000000599";
  },
  (value: Record<string, unknown>) => {
    ((value.attachments as Record<string, unknown>[])[0]).sha256 = "f".repeat(64);
  },
  (value: Record<string, unknown>) => { (value.attachments as unknown[]).reverse(); },
]) {
  const value = structuredClone(intake);
  mutate(value);
  intakeMutations.push(value);
}
check(
  intakeMutations.every(
    (value) => intakeHash(value, cases.intakeHashRules) !== intakeVector.contentHash,
  ),
  "turn-intake business mutation did not change hash",
);
const rotatedRequest = structuredClone(intake);
rotatedRequest.requestId = "00000000-0000-4000-8000-000000000699";
check(
  intakeHash(rotatedRequest, cases.intakeHashRules) === intakeVector.contentHash,
  "excluded request UUID changed turn-intake hash",
);
const firstTransport = {
  authorization: "Bearer FIRST-ROTATING-SECRET",
  signature: "FIRST-SIGNATURE",
  retryAt: "2026-09-07T08:00:00Z",
  body: intake,
};
const retriedTransport = {
  authorization: "Bearer SECOND-ROTATING-SECRET",
  signature: "SECOND-SIGNATURE",
  retryAt: "2026-09-07T08:00:05Z",
  body: structuredClone(intake),
};
check(
  intakeHash(firstTransport.body, cases.intakeHashRules)
    === intakeHash(retriedTransport.body, cases.intakeHashRules),
  "transport rotation changed turn-intake hash",
);
const uppercaseIntake = structuredClone(intake);
uppercaseIntake.conversationId = "ABCDEFAB-0000-4000-8000-000000000599";
let uppercaseIntakeRejected = false;
try {
  intakeHash(uppercaseIntake, cases.intakeHashRules);
} catch {
  uppercaseIntakeRejected = true;
}
check(uppercaseIntakeRejected, "uppercase intake UUID was not rejected");

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
const uppercaseCommand = structuredClone(command);
uppercaseCommand.commandId = "ABCDEFAB-0000-4000-8000-000000000599";
let uppercaseCommandRejected = false;
try {
  commandHash(uppercaseCommand, cases.commandHashRules);
} catch {
  uppercaseCommandRejected = true;
}
check(uppercaseCommandRejected, "uppercase command UUID was not rejected");

process.stdout.write("v5 cross-language fixtures: ok\n");
