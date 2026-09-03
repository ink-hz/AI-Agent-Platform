# Web Research Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `web-search` independent of the mutable global Codex installation and allow one deadline-bounded transient retry with a real single-probe half-open circuit.

**Architecture:** A service-owned Codex `0.153.0` toolchain lives outside immutable source releases and is addressed by absolute path. Existing request controls retain the single 180-second deadline while the circuit state machine admits exactly one half-open provider probe.

**Tech Stack:** Node.js ESM, node:test, Bash, launchd, Codex CLI.

## Global Constraints

- Codex version is exactly `0.153.0`; never fall back to `/opt/homebrew/bin/codex`.
- Keep one 180-second total request deadline and at most one same-provider retry.
- Authentication, invalid request, invalid output, and policy failures do not retry.
- Toolchains are outside source releases and retain current plus two rollback versions.
- Do not modify Bot prompts, Provider choice, MetaBot processes, Platform, Nginx, or other applications.

---

### Task 1: Pin the adapter to a service-owned executable

**Files:**
- Modify: `services/marketing-web-research/codex-adapter.mjs`
- Modify: `services/marketing-web-research/tests/codex-adapter.test.mjs`
- Modify: `services/marketing-web-research/tests/deployment-contract.test.mjs`

**Interfaces:**
- Produces: `CODEX_VERSION = '0.153.0'` and `CODEX_EXECUTABLE = '/Users/agentops/AgentRuntime/web-research/toolchains/codex/0.153.0/bin/codex'`.

- [ ] **Step 1: Write failing executable/version tests**

Assert the constants equal the exact private path/version, every adapter invocation passes that executable, and no runtime/deployment file contains `/opt/homebrew/bin/codex` as an execution fallback.

- [ ] **Step 2: Run RED**

```bash
node --test services/marketing-web-research/tests/codex-adapter.test.mjs services/marketing-web-research/tests/deployment-contract.test.mjs
```

Expected: `0.146.0` and global executable assertions fail.

- [ ] **Step 3: Update adapter constants and health expectations**

```js
export const CODEX_VERSION = '0.153.0';
export const CODEX_EXECUTABLE = '/Users/agentops/AgentRuntime/web-research/toolchains/codex/0.153.0/bin/codex';
```

Keep strict version/auth checks and safe error envelopes.

- [ ] **Step 4: Run GREEN and commit**

Run Step 2 and commit the three files with `fix(web-research): pin private Codex runtime`.

### Task 2: Install and retain the private toolchain safely

**Files:**
- Modify: `services/marketing-web-research/scripts/bootstrap-launchd.sh`
- Modify: `scripts/deploy_marketing_web_research.sh`
- Modify: `services/marketing-web-research/tests/deployment-contract.test.mjs`

**Interfaces:**
- Produces: deployment action `toolchain`, exact directory `$ROOT/toolchains/codex/0.153.0`, executable validation, and current-plus-two retention.

- [ ] **Step 1: Write failing deployment contract tests**

Assert: private executable path is used by bootstrap/preflight; installation uses an exact version; source bundles exclude toolchains and `node_modules`; staging is below `$ROOT/staging/<deployment_id>` and an EXIT trap removes only that exact path; retention enumerates validated semantic-version directories and keeps three without globs or `rm -rf`.

- [ ] **Step 2: Run RED**

Run `node --test services/marketing-web-research/tests/deployment-contract.test.mjs`; expect missing toolchain/staging/retention markers.

- [ ] **Step 3: Implement installation and exact cleanup**

The `toolchain` action creates a unique `$ROOT/staging/web-research/<deployment_id>`, runs npm as `agentops` with an exact package version into that path, verifies `bin/codex --version` and `login status`, atomically moves it to `$ROOT/toolchains/codex/0.153.0`, and traps cleanup of the exact validated staging directory. Enumerate direct children, reject symlinks/non-semver names, protect active plus previous two, and remove only individually resolved older toolchain directories.

- [ ] **Step 4: Make bootstrap and probes use the private binary**

Set:

```bash
CODEX="$ROOT/toolchains/codex/0.153.0/bin/codex"
CODEX_VERSION='codex-cli 0.153.0'
```

Change `run_agentops` working directory from `/tmp` to `$ROOT/codex-workdir`. Update all health/probe envelopes to `0.153.0`.

- [ ] **Step 5: Run GREEN and commit**

Run deployment contracts plus adapter/process/sidecar tests. Commit with `fix(deploy): isolate web research Codex toolchain`.

### Task 3: Implement a single-probe half-open circuit

**Files:**
- Modify: `services/marketing-web-research/controls.mjs`
- Modify: `services/marketing-web-research/sidecar.mjs`
- Modify: `services/marketing-web-research/tests/controls-ledger-audit.test.mjs`
- Modify: `services/marketing-web-research/tests/sidecar-cli.test.mjs`

**Interfaces:**
- Produces: circuit states `closed|open|half_open`, one leased half-open probe, and outcome release through the existing request lifecycle.

- [ ] **Step 1: Write failing state-machine tests**

Open the circuit after five provider failures; advance beyond cooldown; assert the first admission is allowed as half-open and a concurrent second admission returns `SEARCH_UNAVAILABLE`; a success closes the circuit; a provider failure reopens it for another full cooldown. Assert non-provider failures neither open nor close it.

- [ ] **Step 2: Run RED**

```bash
node --test services/marketing-web-research/tests/controls-ledger-audit.test.mjs services/marketing-web-research/tests/sidecar-cli.test.mjs
```

Expected: concurrent calls are both admitted after cooldown and `half_open` is absent.

- [ ] **Step 3: Implement the minimal state machine**

Track `halfOpenProbeInFlight`. When `openUntil` expires, only the first provider admission sets it. Include `{ halfOpenProbe: true }` in the release lease or provide `recordRequestOutcome` enough state to close/reopen and clear the flag. `circuitState()` returns `half_open` during the probe. Queue draining rejects additional work until the probe succeeds.

- [ ] **Step 4: Verify retry classification**

Add assertions that `temporary`, `spawn`, rate-limit and process timeout remain retryable only when the delay fits the original deadline, while auth/version/invalid-output/policy errors make one process call. Keep jitter within the existing 250–350 ms default.

- [ ] **Step 5: Run GREEN and commit**

Run all web-research tests and commit with `fix(web-research): harden retry circuit recovery`.

### Task 4: Deploy and validate live search

- [ ] **Step 1: Run full test suite and shell syntax checks**

```bash
node --test services/marketing-web-research/tests/*.test.mjs
bash -n scripts/deploy_marketing_web_research.sh services/marketing-web-research/scripts/bootstrap-launchd.sh
git diff --check
```

- [ ] **Step 2: Record disk and release inputs**

Record before `df -B1 / /data` where `/data` exists, source bundle size, private toolchain size, current release, and two rollbacks. Abort at the approved disk gates.

- [ ] **Step 3: Install toolchain, preflight, activate, and verify**

Run toolchain installation first, then bundle/stage/preflight/activate. Activation may restart only `com.orbbec.web-research`; MetaBot identities and restart counters must remain unchanged.

- [ ] **Step 4: Run HR canaries**

Health must report `0.153.0`; an `hr-bot` recruitment search must return public URLs; one returned URL must pass Fetch. Confirm audit records bounded retries without query-body or credential leakage.
