# MetaBot HR Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Claude parallel-tool protocol corruption and allow at most one safe fresh-session replay across every HR turn execution path.

**Architecture:** The Opus compatibility profile forces serial client-tool calls at the loopback Messages adapter. A turn-scoped recovery budget is created by each Feishu/API coordinator and shared with startup, stream-terminal, and exception recovery so independent policies cannot stack retries.

**Tech Stack:** TypeScript, Node.js HTTP, Vitest, Claude Code compatibility gateway, MetaBot MessageBridge.

## Global Constraints

- Preserve Claude Code `2.1.220`, model `claude-opus-5`, and the existing compatibility profile identifier.
- A turn may replay once only when it is not cancelled, has no external side effect, and has no usable terminal answer.
- Fresh and resumed `tool use concurrency issues` failures are eligible; the failed attempt remains immutable evidence.
- Do not retry HTTP POSTs inside the adapter and do not change any Bot other than `metabot-hr` during deployment.
- Release artifacts contain code/build output only; no data, uploads, logs, indexes, `.venv`, `node_modules`, or caches.

---

### Task 1: Enforce serial client-tool calls in the compatibility adapter

**Files:**
- Modify: `src/engines/claude/compatibility/profile.ts`
- Modify: `src/engines/claude/compatibility/runtime.ts`
- Modify: `src/engines/claude/compatibility/adapter.ts`
- Modify: `tests/claude-compatibility-profile.test.ts`
- Modify: `tests/claude-gateway-adapter.test.ts`

**Interfaces:**
- Consumes: `ClaudeCompatibilityProfile` and raw `/v1/messages` JSON bodies.
- Produces: adapter option `disableParallelToolUse?: boolean` and pure transformation `enforceSerialToolUse(body: Buffer)` returning `{ body, enforced }`.

- [ ] **Step 1: Write failing profile/runtime tests**

Add assertions that `OPUS_PROFILE.disableParallelToolUse === true` and that the adapter starter receives it:

```ts
expect(profile).toMatchObject({ disableParallelToolUse: true });
expect(adapterStarter).toHaveBeenCalledWith(expect.objectContaining({
  disableParallelToolUse: true,
}));
```

- [ ] **Step 2: Write failing adapter behavior tests**

Table-test tool requests with absent, `auto`, `any`, `tool`, and `none` choices. Assert absent becomes `{type:'auto', disable_parallel_tool_use:true}`, existing fields survive for `auto|any|tool`, `none` is unchanged, requests without client tools remain byte-equal, and `content-length` matches the transformed bytes. Assert logs contain only `serialToolUseApplied` and counts, never prompt/image markers.

- [ ] **Step 3: Run RED**

Run:

```bash
npx vitest run tests/claude-compatibility-profile.test.ts tests/claude-gateway-adapter.test.ts
```

Expected: failures for the missing profile field, missing adapter option, and unchanged `tool_choice`.

- [ ] **Step 4: Implement the minimal transformation**

Add `disableParallelToolUse: true as const` to the profile and pass it from `runtime.ts`. In `adapter.ts`, parse only when the option is enabled, the route is `/v1/messages`, and `tools` is a non-empty array:

```ts
export function enforceSerialToolUse(body: Buffer): { body: Buffer; enforced: boolean } {
  const value = JSON.parse(body.toString('utf8')) as Record<string, unknown>;
  if (!Array.isArray(value.tools) || value.tools.length === 0) return { body, enforced: false };
  const choice = value.tool_choice;
  if (choice && typeof choice === 'object' && !Array.isArray(choice)
      && (choice as { type?: unknown }).type === 'none') return { body, enforced: false };
  const current = choice && typeof choice === 'object' && !Array.isArray(choice)
    ? choice as Record<string, unknown> : { type: 'auto' };
  value.tool_choice = { ...current, disable_parallel_tool_use: true };
  return { body: Buffer.from(JSON.stringify(value)), enforced: true };
}
```

Run image promotion first, serial enforcement second, and set `content-length` from the final buffer.

- [ ] **Step 5: Run GREEN and commit**

Run the focused command from Step 3 plus `npm run build:bridge`. Commit only the five files:

```bash
git add src/engines/claude/compatibility/profile.ts src/engines/claude/compatibility/runtime.ts src/engines/claude/compatibility/adapter.ts tests/claude-compatibility-profile.test.ts tests/claude-gateway-adapter.test.ts
git commit -m "fix(claude): serialize compatibility tool calls"
```

### Task 2: Add one shared recovery budget

**Files:**
- Create: `src/bridge/turn-replay-budget.ts`
- Create: `tests/turn-replay-budget.test.ts`
- Modify: `src/bridge/message-bridge.ts`
- Modify: `tests/message-bridge-session-corruption.test.ts`
- Modify: `tests/message-bridge.test.ts`

**Interfaces:**
- Consumes: decisions from `decideClaudeTurnRecovery`, `decideProviderTurnRecovery`, and `decideSessionCorruptionRecovery`.
- Produces: `createTurnReplayBudget(maxReplays = 1)` with readonly `replayCount` and atomic `claim(decision): boolean`.

- [ ] **Step 1: Write the failing budget unit test**

```ts
const budget = createTurnReplayBudget();
expect(budget.replayCount).toBe(0);
expect(budget.claim('replay_fresh_once')).toBe(true);
expect(budget.replayCount).toBe(1);
expect(budget.claim('replay_fresh_once')).toBe(false);
expect(budget.claim('stop_without_replay')).toBe(false);
```

- [ ] **Step 2: Run RED**

Run `npx vitest run tests/turn-replay-budget.test.ts`; expect module-not-found.

- [ ] **Step 3: Implement the budget**

```ts
export type ReplayDecision = 'replay_fresh_once' | 'stop_without_replay' | 'recover_completed';
export interface TurnReplayBudget { readonly replayCount: number; claim(decision: ReplayDecision): boolean }
export function createTurnReplayBudget(maxReplays = 1): TurnReplayBudget {
  let count = 0;
  return {
    get replayCount() { return count; },
    claim(decision) {
      if (decision !== 'replay_fresh_once' || count >= maxReplays) return false;
      count += 1;
      return true;
    },
  };
}
```

- [ ] **Step 4: Write failing coordinator regression tests**

Cover both `executeQueryCore` and `executeApiTask`: startup replay followed by provider/stale failure must total two attempts, provider replay followed by stale classification must total two attempts, a fresh concurrency failure replays once, and cancellation/side-effect/usable-output paths do not replay. Assert only one terminal card/callback and distinct attempt IDs in activity evidence.

- [ ] **Step 5: Run RED**

Run:

```bash
npx vitest run tests/turn-replay-budget.test.ts tests/message-bridge-session-corruption.test.ts tests/message-bridge.test.ts
```

Expected: fresh concurrency and stacked-policy cases fail because `retryCount: 0` is reused.

- [ ] **Step 6: Thread one budget through all paths**

Create the budget once near each turn's `attemptId`. Pass it into `runOneTurnWithStartupRecovery`; every policy receives `budget.replayCount`, and every replay starts only after `budget.claim(decision)` succeeds. Context overflow must also claim this budget. Remove all coordinator-level hard-coded `retryCount: 0`/`replayCount: 0` values. Keep the current side-effect and usable-answer inputs unchanged.

- [ ] **Step 7: Run GREEN and commit**

Run the focused command from Step 5, `npm run build:bridge`, `npm run lint -- --quiet`, and `npm run format:check`. Commit:

```bash
git add src/bridge/turn-replay-budget.ts src/bridge/message-bridge.ts tests/turn-replay-budget.test.ts tests/message-bridge-session-corruption.test.ts tests/message-bridge.test.ts
git commit -m "fix(bridge): share one replay budget per turn"
```

### Task 3: Verify and prepare the isolated HR release

**Files:**
- Modify only if required by an existing release test: deployment scripts already used for per-Bot MetaBot releases.

**Interfaces:**
- Consumes: Tasks 1-2 commits.
- Produces: one code/build-only `metabot-hr` release and complete test evidence.

- [ ] **Step 1: Run full verification**

```bash
npm test
npm run build
npm run lint
npm run format:check
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 2: Check release contents and production gate**

Verify release input excludes `data uploads logs index answer_reviews knowledge .venv node_modules` and record `df -B1 / /data` on the production host before changing service state. Abort under the approved 25 GB/20 GB/75% thresholds.

- [ ] **Step 3: Deploy only `metabot-hr`**

Use the existing per-Bot release controller, keep the current release plus two rollbacks, clean only this deployment's exact staging path via trap, and do not move other Bot pointers or restart unrelated PM2 processes.

- [ ] **Step 4: Run the nine-image canary**

Use the archived nine attachments in original archive-ID/SHA-256/order. Assert gateway logs show nine sequential promoted-image requests, no `tool use concurrency issues`, Flywheel has a non-empty assistant answer and new Trace, and Feishu receives exactly one complete reply.
