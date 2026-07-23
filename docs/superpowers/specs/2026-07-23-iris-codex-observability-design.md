# Iris Codex Observability Design

**Date:** 2026-07-23

## Purpose

Register the existing dedicated `codex-assistant` MetaBot instance as the tenth Business Agent in AI Agent Platform. The Platform must monitor runtime health and usage and must allow the owner to inspect Iris's complete Session questions, Codex answers, execution details, and tool activity.

The product name is `Iris Codex`. The runtime and data identity remains `codex-assistant` so monitoring, flywheel, and Session records use one stable ID.

## Verified Current State

- PM2 process: `metabot-codex-assistant`.
- Runtime user: `agentops`.
- API port: `9109`.
- Health endpoint: `GET http://127.0.0.1:9109/api/health`, currently HTTP 200.
- Engine: MetaBot Codex.
- Workspace: `/Users/agentops/Developer/work/Codex-Assistant`.
- Current legacy runtime history: one Session and four messages in the instance `sessions.db`; this database is not an observability source.
- Flywheel is disabled for this instance.
- The current Fleet runtime contract and Platform catalog do not contain this Agent.
- The original standalone deployment deliberately excluded this instance from the Fleet registry and flywheel. This specification supersedes those two exclusions; its workspace and credential isolation requirements remain in force.

## Product Behavior

### Fleet and Overview

`Iris Codex` is a Business Agent and contributes to Business Agent totals and usage from the moment its real data is available. It appears with:

- runtime state (`Active`, `Online`, `Degraded`, or `Offline`);
- live-since date and running days;
- last updated date;
- total answered conversations;
- recent activity;
- current runtime evidence on Agent Detail.

The profile copy is English:

- Name: `Iris Codex`
- Domain: `Personal Workspace`
- Description: `Private Codex workspace for Iris's development and operational work.`
- Glyph: `IC`

The UI does not state which OpenAI account funds or authenticates the runtime.

### Sessions and Flywheel

The Agent participates in all existing data browsing surfaces:

- Agent Detail recent Sessions;
- Sessions with an explicit Agent filter;
- Flywheel Agent switcher;
- Session Detail question and answer replay;
- Turn execution and tool evidence already supported by the MetaBot flywheel.

The owner explicitly permits Platform to display Iris's complete questions and Codex answers. The Platform still does not expose Codex authentication files, tokens, account identifiers, environment secrets, or raw provider credentials.

## Architecture

### PostgreSQL is the sole observability authority

The production data path is:

`Feishu → MetaBot/Codex → PostgreSQL flywheel → platform_read views → AI Agent Platform`

PostgreSQL is the only durable source for conversation counts, Session content, Turns, execution status, Trace, tool calls, evidence, token usage, retention, and backups. Platform never opens, copies, tails, or queries the Codex instance SQLite database.

MetaBot may continue using its private SQLite files only as runtime implementation state for Session resume and command behavior. Those files do not feed Fleet totals, Session APIs, or the flywheel UI. A SQLite deletion, rotation, or schema change must not invalidate data already committed to PostgreSQL.

The PostgreSQL boundary uses the existing schemas:

- `flywheel_governance` for the registered Bot/domain contract;
- `flywheel_identity` for protected sender identity;
- `flywheel_core` for conversations, messages, and feedback;
- `flywheel_trace` for runs and execution events;
- `flywheel_evidence` for governed evidence payloads;
- `platform_read` for the canonical read model consumed by Platform.

### Runtime contract

Add `codex-assistant` to `deploy/metabot.runtime-contract.json` with its existing PM2 name, port, engine, model metadata, workspace, state directory, configuration path, and log directory. The cluster monitor then probes the existing unauthenticated `/api/health` endpoint exactly as it probes the other MetaBot instances.

The change must not restart or modify any existing Business or System Agent identity.

### Platform catalog

Add `codex-assistant` to the Fleet catalog as `visibility: business` and to the Platform registry as an active Agent. Lifecycle dates use recorded deployment evidence rather than process uptime. A PM2 restart therefore does not reset the displayed running days.

### Live flywheel capture

Enable the existing MetaBot flywheel for only the Codex instance by setting:

- `FLYWHEEL_ENABLED=1`;
- `FLYWHEEL_ENV_FILE=/Users/agentops/.metabot/flywheel.env`.

Add the stable flywheel contract:

- Bot ID: `codex-assistant`;
- business domain: `personal_productivity`.

Register the same pair in `flywheel_governance.bot_registry`. The existing write-only `flywheel_ingest` credential remains the only database credential visible to `agentops`.

MetaBot's generic bridge already records inbound messages, assistant answers, run status, tool calls, evidence, duration, and token usage for the Codex engine. The implementation must prove this with a Codex-specific integration test before production enablement; it must not assume Claude-only payload fields.

### Optional legacy history migration

After live PostgreSQL capture has passed production verification, the current one-Session/four-message SQLite history may be migrated through a one-shot, idempotent command. SQLite is only an input to this controlled migration and is never a continuing or fallback data source. The command emits canonical flywheel events through the same PostgreSQL ingest API used by live capture.

Mapping:

- SQLite Session becomes one flywheel conversation.
- User and assistant message pairs become ordered Turns.
- Original message text and timestamps are preserved.
- Existing Codex Session ID is retained as execution metadata when present.
- Existing duration and cost fields are retained only when recorded; missing values remain null.
- Historical messages have no stored Feishu sender identifier and therefore use `sender_identity_status=unavailable` rather than guessing Iris's provider ID.

Idempotency uses a deterministic UUID derived from `codex-assistant`, SQLite Session ID, and message ID. Re-running the backfill creates no duplicate conversation, message, Turn, or event.

Migration provenance is stored in PostgreSQL as `source=legacy_sqlite_backfill`. After a successful migration, Platform reads the imported records only from PostgreSQL. The legacy source remains outside the monitoring path and may be retained or removed according to the MetaBot runtime recovery policy.

The PG launch acceptance target does not depend on legacy migration. The migration-specific acceptance target is exactly one imported Session and four imported messages with no duplicate on replay.

## State and Usage Semantics

- `Active`: runtime healthy and an answered conversation occurred in the existing active window.
- `Online`: runtime healthy without a recent answered conversation.
- Running days: based on the initial verified deployment date, not PM2 uptime.
- Total conversations: answered assistant Turns from the canonical flywheel view.
- Last updated: repository or runtime release evidence, not conversation activity.
- Token usage: stored per run when Codex emits it, but not added as a new Overview KPI.
- Dollar cost: shown only when the runtime reports a real value; subscription usage is never converted into an invented cost.

## Security and Privacy

- The dedicated workspace restriction remains unchanged.
- Platform is currently a local, single-user application; this change does not add a new authentication system.
- The Codex credential directory, device login, tokens, environment, and account metadata are never read by Platform or the backfill.
- Logs contain runtime ID, event type, aggregate counts, and sanitized failures only.
- Session content is stored under the existing flywheel retention and backup policy.
- Raw Feishu and provider identifiers remain outside API responses.
- The Agent is not labelled `System`, `Test`, or synthetic.

## Failure Handling

- Runtime contract invalid: keep the previous cluster snapshot and report a safe contract error.
- Port unavailable: show `Offline`; retain Sessions and usage.
- Flywheel unavailable: do not affect Iris's Bot response; record a sanitized data gap and retry within the existing bounded queue.
- Legacy migration malformed row: stop the PostgreSQL transaction, report aggregate row position and failure class, and write no partial import. Live PostgreSQL capture remains active.
- Missing historical sender: preserve content with unavailable identity.
- Codex run without token data: preserve the answered Turn with null usage.
- Deployment failure: restore only the Codex instance ecosystem/configuration and leave all other PM2 processes untouched.

## Test Strategy

### Runtime and catalog

- contract loads ten Business/System MetaBot targets plus the existing test target as configured;
- `codex-assistant` maps to PM2 `metabot-codex-assistant`, port `9109`, and the dedicated workspace;
- duplicate Bot IDs and ports remain rejected;
- Fleet summary counts `Iris Codex` as Business;
- lifecycle running days do not reset when uptime changes.

### Flywheel

- `businessDomainForBot("codex-assistant")` returns `personal_productivity`;
- governance accepts this Bot/domain pair;
- one real-shaped Codex result records question, answer, run completion, tool call, duration, and available token fields;
- a database write failure does not alter message delivery;
- System and synthetic filtering behavior remains unchanged.

### Optional legacy history migration

- one Session/four-message fixture produces one Session and two ordered Turns;
- exact text and timestamps survive import;
- missing sender identity remains unavailable;
- two runs produce the same counts and IDs;
- a malformed row rolls back the complete import.

### Platform API and UI

- Fleet exposes `Iris Codex` with Business visibility;
- Agent-filtered Sessions return the imported and new live data;
- Session Detail returns full questions and answers;
- no response contains Codex account or credential metadata;
- Agent Detail, Sessions, Flywheel, and Session Detail render correctly at desktop and narrow widths.

## Production Verification

1. Back up the Codex instance configuration and PostgreSQL flywheel database. Back up SQLite separately only for the optional legacy migration.
2. Apply the idempotent flywheel registration migration.
3. Perform a write-only PostgreSQL ingest preflight for the `codex-assistant` contract and verify it through the analyst read model.
4. Deploy the runtime contract and enable flywheel only for `metabot-codex-assistant`.
5. Verify `GET /api/health` on port `9109` remains HTTP 200.
6. Send one real Iris message and verify the answer is delivered normally.
7. Verify the new Session, Turn, answer, execution data, tool events, and available token fields exist in PostgreSQL and appear in Platform.
8. Optionally run the legacy migration and verify exactly one historical Session/four messages in PostgreSQL, with an idempotent replay.
9. Verify all pre-existing MetaBot instances remain healthy and their totals are unchanged except for normal live traffic.
10. Verify Platform API and HTML contain no Codex credentials, account identifiers, Feishu provider IDs, environment data, or SQLite paths.

## Scope Boundaries

This release does not make SQLite a Platform source, maintain dual-write observability stores, share the Codex workspace with other Agents, change Codex authentication, add per-user Platform permissions, add billing estimates, expose raw Codex transcripts outside PostgreSQL flywheel Sessions, or reinterpret other Agents' historical data.
