# Iris Codex Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register `Iris Codex` as the tenth Business Agent and make PostgreSQL the sole Platform source for its runtime status, conversations, answers, execution data, tools, and usage.

**Architecture:** Extend the versioned MetaBot runtime and flywheel contracts with `codex-assistant`, prove the generic bridge captures real-shaped Codex events, enable the write-only PostgreSQL flywheel on only the dedicated PM2 instance, and reuse Platform's canonical MetaBot views. SQLite remains private runtime resume state and is never read by Platform; its four legacy messages are handled only by an optional one-shot PostgreSQL migration after live capture passes.

**Tech Stack:** TypeScript 5.9, Node.js 22, Vitest, MetaBot Codex engine, PostgreSQL 16, PL/pgSQL, Python 3.12, FastAPI/Pydantic, React/Vite, PM2 on macOS.

## Global Constraints

- PostgreSQL is the only observability authority and Platform data source.
- Platform must never open, copy, tail, or query the Codex instance SQLite database.
- Runtime ID is `codex-assistant`; product name is `Iris Codex`.
- Business domain is `personal_productivity`; visibility is `business`.
- Show complete questions and answers, but never expose Codex account, auth, token, environment, or provider identifiers.
- Flywheel failure must never alter or delay Iris's Bot response.
- Restart only `metabot-codex-assistant`; preserve all other PM2 processes.
- Preserve unrelated dirty-worktree changes in every repository.

---

### Task 1: Version the Iris Runtime Contract

**Files:**
- Modify: `/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json`
- Modify: `/Users/neo/Developer/work/Orbbec-Agent-Team/scripts/reliability/runtime-contract.mjs`
- Modify: `/Users/neo/Developer/work/Orbbec-Agent-Team/scripts/reliability/tests/runtime-contract.test.mjs`

**Interfaces:**
- Consumes: the existing runtime facts at PM2 `metabot-codex-assistant`, port `9109`, and `/Users/agentops/Developer/work/Codex-Assistant`.
- Produces: one canonical `bots[]` entry with `name=codex-assistant`.

- [ ] **Step 1: Write the failing runtime-contract test**

```js
assert.deepEqual(findBot(contract, 'codex-assistant'), {
  name: 'codex-assistant',
  engine: 'codex',
  backend: 'cli',
  workdir: '/Users/agentops/Developer/work/Codex-Assistant',
  instance: {
    pm2Name: 'metabot-codex-assistant', apiPort: 9109,
    stateDir: '/Users/agentops/AgentRuntime/instances/codex-assistant/state',
    configPath: '/Users/agentops/AgentRuntime/instances/codex-assistant/bots.json',
    logDir: '/Users/agentops/AgentRuntime/instances/codex-assistant/logs',
  },
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `cd /Users/neo/Developer/work/Orbbec-Agent-Team && node --test scripts/reliability/tests/runtime-contract.test.mjs`

Expected: FAIL because `codex-assistant` is absent.

- [ ] **Step 3: Add the exact runtime entry and validation**

Read the non-secret Feishu App ID from the protected runtime config without printing the App Secret. Add the Bot to `bots[]`, require unique port `9109`, require engine `codex`, and require its dedicated workspace and instance paths. Do not add secrets or copy the runtime `bots.json` into Git.

- [ ] **Step 4: Run contract tests and JSON validation**

Run: `cd /Users/neo/Developer/work/Orbbec-Agent-Team && node --test scripts/reliability/tests/runtime-contract.test.mjs && jq empty deploy/metabot.runtime-contract.json`

Expected: PASS and exit 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/neo/Developer/work/Orbbec-Agent-Team
git add deploy/metabot.runtime-contract.json scripts/reliability/runtime-contract.mjs scripts/reliability/tests/runtime-contract.test.mjs
git commit -m "feat: register Iris Codex runtime"
```

---

### Task 2: Register the PostgreSQL Flywheel Contract

**Files:**
- Create: `/Users/neo/Developer/work/Orbbec-Agent-Team/flywheel/migrations/011_iris_codex_bot.sql`
- Modify: `/Users/neo/Developer/work/Orbbec-Agent-Team/scripts/verify_flywheel_bot_coverage.mjs`
- Modify: `/Users/neo/Developer/work/Orbbec-Agent-Team/scripts/reliability/tests/verify-flywheel-bot-coverage.test.mjs`
- Modify: `/Users/neo/Developer/work/metabot-dev/src/flywheel/index.ts`
- Modify: `/Users/neo/Developer/work/metabot-dev/tests/flywheel-bot-contract.test.ts`

**Interfaces:**
- Consumes: `codex-assistant` runtime ID.
- Produces: `businessDomainForBot('codex-assistant') === 'personal_productivity'` and an active PG governance row.

- [ ] **Step 1: Write failing domain and coverage tests**

```ts
expect(businessDomainForBot('codex-assistant')).toBe('personal_productivity');
```

```js
assert.equal(FORMAL_BOT_DOMAINS['codex-assistant'], 'personal_productivity');
```

- [ ] **Step 2: Run both tests and verify RED**

Run: `cd /Users/neo/Developer/work/metabot-dev && npx vitest run tests/flywheel-bot-contract.test.ts`

Run: `cd /Users/neo/Developer/work/Orbbec-Agent-Team && node --test scripts/reliability/tests/verify-flywheel-bot-coverage.test.mjs`

Expected: both fail because the Bot contract is absent.

- [ ] **Step 3: Add the domain and idempotent governance migration**

```sql
insert into flywheel_governance.bot_registry
  (bot_id, display_name, business_domain, is_test, status, effective_at, metadata)
values
  ('codex-assistant', 'Iris Codex', 'personal_productivity', false, 'active',
   '2026-07-21T18:01:18+08:00', '{"engine":"codex","visibility":"business"}'::jsonb)
on conflict (bot_id) do update set
  display_name=excluded.display_name,
  business_domain=excluded.business_domain,
  is_test=false,
  status='active',
  metadata=flywheel_governance.bot_registry.metadata || excluded.metadata,
  updated_at=now();
```

Add the same stable mapping to MetaBot and the coverage verifier.

- [ ] **Step 4: Run tests and migration replay**

Run: `cd /Users/neo/Developer/work/metabot-dev && npx vitest run tests/flywheel-bot-contract.test.ts`

Run: `cd /Users/neo/Developer/work/Orbbec-Agent-Team && node --test scripts/reliability/tests/verify-flywheel-bot-coverage.test.mjs && flywheel/migrations/apply.sh && flywheel/migrations/apply.sh`

Expected: tests PASS and both migration runs exit 0.

- [ ] **Step 5: Commit each repository separately**

```bash
cd /Users/neo/Developer/work/metabot-dev
git add src/flywheel/index.ts tests/flywheel-bot-contract.test.ts
git commit -m "feat: support Iris Codex flywheel events"

cd /Users/neo/Developer/work/Orbbec-Agent-Team
git add flywheel/migrations/011_iris_codex_bot.sql scripts/verify_flywheel_bot_coverage.mjs scripts/reliability/tests/verify-flywheel-bot-coverage.test.mjs
git commit -m "feat: register Iris Codex in flywheel governance"
```

---

### Task 3: Prove Codex Capture Before Production Enablement

**Files:**
- Modify: `/Users/neo/Developer/work/metabot-dev/tests/message-bridge.test.ts`
- Modify if required by the failing test: `/Users/neo/Developer/work/metabot-dev/src/bridge/message-bridge.ts`
- Modify if required by the failing test: `/Users/neo/Developer/work/metabot-dev/src/engines/codex/jsonl-translator.ts`

**Interfaces:**
- Consumes: a real-shaped Codex `thread.started`, item/tool, and `turn.completed` event stream.
- Produces: one question, one answer, one completed run, tool evidence, duration, and token usage through `FlywheelRecorder`.

- [ ] **Step 1: Add a failing Codex-specific flywheel test**

```ts
expect(flywheel.recordMessageReceived).toHaveBeenCalledWith(expect.objectContaining({
  botId: 'codex-assistant', payload: expect.objectContaining({ content: 'Iris question' }),
}));
expect(flywheel.recordRunCompleted).toHaveBeenCalledWith(expect.objectContaining({
  payload: expect.objectContaining({
    content: 'Codex answer', input_tokens: 120, output_tokens: 30,
  }),
}));
expect(flywheel.recordToolCall).toHaveBeenCalledWith(expect.objectContaining({
  payload: expect.objectContaining({ tool_name: expect.any(String) }),
}));
```

- [ ] **Step 2: Run and verify the test fails for a real missing behavior**

Run: `cd /Users/neo/Developer/work/metabot-dev && npx vitest run tests/message-bridge.test.ts tests/codex-jsonl-translator.test.ts`

Expected: FAIL only if a Codex field is not propagated; if it passes immediately, retain the regression test and do not change production code unnecessarily.

- [ ] **Step 3: Implement only missing Codex mappings**

Map reliable Codex usage fields to `input_tokens`, `cache_read_tokens`, and `output_tokens`; preserve the final response text; map tool items through the existing safe tool-call hook. Do not invent cost for subscription usage and do not store reasoning text.

- [ ] **Step 4: Run bridge, translator, and redactor tests**

Run: `cd /Users/neo/Developer/work/metabot-dev && npx vitest run tests/message-bridge.test.ts tests/codex-jsonl-translator.test.ts tests/flywheel-redactor.test.ts tests/flywheel-recorder.test.ts && npm run build:bridge`

Expected: PASS and TypeScript build exits 0.

- [ ] **Step 5: Commit only if tracked content changed**

```bash
cd /Users/neo/Developer/work/metabot-dev
git add tests/message-bridge.test.ts src/bridge/message-bridge.ts src/engines/codex/jsonl-translator.ts
git commit -m "test: verify Codex PostgreSQL capture"
```

---

### Task 4: Add Iris Codex to the Platform Business Fleet

**Files:**
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/backend/app/fleet/catalog.yaml`
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/backend/tests/test_fleet_catalog.py`
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/backend/tests/test_fleet_service.py`

**Interfaces:**
- Consumes: runtime contract entry and canonical MetaBot PG rows.
- Produces: `Iris Codex` with Business visibility and correct lifecycle semantics.

- [ ] **Step 1: Write failing catalog and summary tests**

```py
profile = catalog.profile("codex-assistant", "codex-assistant")
assert (
    profile.id, profile.name, profile.domain, profile.description,
    profile.glyph, profile.accent, profile.visibility,
    profile.live_since, profile.live_since_basis,
) == (
    "codex-assistant", "Iris Codex", "Personal Workspace",
    "Private Codex workspace for Iris's development and operational work.",
    "IC", "intelligence", "business",
    "2026-07-21T18:01:18+08:00", "release_artifact",
)
assert overview.summary.total_agents == 10
```

- [ ] **Step 2: Run and verify RED**

Run: `cd /Users/neo/Developer/work/AI-Agent-Platform/backend && .venv/bin/pytest tests/test_fleet_catalog.py tests/test_fleet_service.py -q`

Expected: FAIL because the profile and tenth Business Agent are absent.

- [ ] **Step 3: Add the English catalog profile and update exact fixtures**

```yaml
codex-assistant:
  name: Iris Codex
  domain: Personal Workspace
  description: Private Codex workspace for Iris's development and operational work.
  glyph: IC
  accent: intelligence
  visibility: business
  live_since: "2026-07-21T18:01:18+08:00"
  live_since_basis: release_artifact
  last_updated_at: "2026-07-21T18:01:18+08:00"
  last_updated_basis: release_artifact
```

Do not add new API or page types: the existing canonical MetaBot views already accept arbitrary registered Bot IDs and the existing Agent/Sessions/Flywheel pages are Agent-driven.

- [ ] **Step 4: Run backend and frontend focused tests**

Run: `cd /Users/neo/Developer/work/AI-Agent-Platform/backend && .venv/bin/pytest tests/test_fleet_catalog.py tests/test_fleet_service.py tests/test_observability_repository.py -q`

Expected: PASS with Business total 10 and unchanged System visibility. No frontend production change is required because every Agent surface is driven by API data and the `intelligence` accent already exists.

- [ ] **Step 5: Commit**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git add backend/app/fleet/catalog.yaml backend/tests/test_fleet_catalog.py backend/tests/test_fleet_service.py
git commit -m "feat: add Iris Codex to the Business fleet"
```

---

### Task 5: Enable PG Capture on Only the Iris Instance

**Files:**
- Modify in protected runtime: `/Users/agentops/AgentRuntime/instances/codex-assistant/ecosystem.config.cjs`
- Preserve: `/Users/agentops/AgentRuntime/instances/codex-assistant/bots.json`

**Interfaces:**
- Consumes: existing write-only `/Users/agentops/.metabot/flywheel.env` and tested MetaBot bridge.
- Produces: live `codex-assistant` records in PostgreSQL.

- [ ] **Step 1: Capture safe pre-deployment evidence and backups**

Record exact PM2 process names/statuses and PG aggregate counts. Back up the Codex ecosystem file and PostgreSQL using existing encrypted procedures. Do not print environment values or credentials.

- [ ] **Step 2: Add only the two flywheel environment variables**

```js
FLYWHEEL_ENABLED: '1',
FLYWHEEL_ENV_FILE: '/Users/agentops/.metabot/flywheel.env',
```

Verify the environment file is owned by `agentops`, mode `0600`, outside every Agent workspace, and contains the write-only ingest URL.

- [ ] **Step 3: Restart only `metabot-codex-assistant`**

Run as `agentops` with the existing sanitized PM2 environment:

```bash
pm2 restart /Users/agentops/AgentRuntime/instances/codex-assistant/ecosystem.config.cjs --only metabot-codex-assistant --update-env
pm2 save
```

Expected: only the Iris PID/uptime changes; every other PM2 process identity remains online and unchanged.

- [ ] **Step 4: Verify live PostgreSQL capture**

Send one real Iris question. Verify the answer reaches Feishu before checking PG. Through the analyst role, assert one non-synthetic `codex-assistant` conversation, user message, assistant message, completed run, and events with `business_domain=personal_productivity`.

Expected: Platform reads the new Session from `platform_read`; no runtime SQLite query is involved.

- [ ] **Step 5: Roll back precisely if verification fails**

Restore only the backed-up Iris ecosystem file and restart only `metabot-codex-assistant`. Do not disable or restart the rest of the fleet.

---

### Task 6: Full Verification, Platform Deployment, and Optional Legacy Migration

**Files:**
- Create only if legacy migration is approved after live PG verification: `/Users/neo/Developer/work/metabot-dev/scripts/migrate-codex-legacy-session.ts`
- Test only if created: `/Users/neo/Developer/work/metabot-dev/tests/migrate-codex-legacy-session.test.ts`

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: deployed tenth Business Agent and optional idempotent PG-only legacy records.

- [ ] **Step 1: Run complete verification suites**

```bash
cd /Users/neo/Developer/work/metabot-dev && npm run test:bridge && npm run build:bridge
cd /Users/neo/Developer/work/Orbbec-Agent-Team && node --test scripts/reliability/tests/*.test.mjs
cd /Users/neo/Developer/work/AI-Agent-Platform/backend && .venv/bin/pytest -q
cd /Users/neo/Developer/work/AI-Agent-Platform/webui && npm test -- --run && npm run build
```

Expected: all tests PASS.

- [ ] **Step 2: Deploy Platform and verify the Fleet**

Restart only `com.orbbec.ai-agent-platform`. Verify `/api/health`, `/api/cluster/status`, `/api/fleet/overview`, `/api/agents`, and `/api/sessions?agent_id=codex-assistant` return 200.

Expected: Business total 10; `Iris Codex` is Active or Online according to recent PG activity; all previous Agent states remain correct.

- [ ] **Step 3: Verify complete content and secret absence**

Open Agent Detail, Sessions, Flywheel, and one Session Detail. Confirm the real question, Codex answer, execution status, tool evidence, duration, and available token fields. Scan API JSON and built HTML for `open_id`, `union_id`, `auth.json`, access tokens, environment values, and SQLite paths.

Expected: content is present; secret/path scan finds nothing.

- [ ] **Step 4: Implement the optional legacy migration only after live PG passes**

If the owner still wants the four legacy messages, write a one-shot importer that reads the protected SQLite file as `agentops`, emits deterministic PostgreSQL events with `source=legacy_sqlite_backfill`, and exits. Its test fixture must produce one Session/two Turns/four messages and identical counts after replay. Platform remains PG-only before, during, and after this command.

- [ ] **Step 5: Push verified commits**

Inspect `git status --short` in all repositories, preserve pre-existing changes, and push only the commits created by this plan after production verification succeeds.
