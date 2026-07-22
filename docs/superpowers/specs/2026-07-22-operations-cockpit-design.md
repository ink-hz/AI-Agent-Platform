# Operations Cockpit Design

**Date:** 2026-07-22  
**Status:** Approved for implementation planning  
**Scope:** Read-only Daily Brief, operational event ledger, Activity History, and Agent activity timeline

## 1. Context

AI Agent Platform currently provides a reliable read-only view of nine Business Agents and two System Agents. It shows current runtime state, durable lifecycle dates, real usage, Agent profiles, Sessions, turns, answers, evidence, Trace data where supported, and synchronized FAE/ADMIN data.

The platform can answer:

- Which Agents exist?
- Are they currently reachable?
- How much have they been used?
- What happened in a specific Session or turn?

It does not yet answer the two questions that matter most during a daily operational review:

1. What needs attention now?
2. What changed during the last 24 hours?

The Operations Cockpit adds those answers without turning the product into a traditional infrastructure monitoring wall or an Agent control plane.

## 2. Product Position

The platform is a read-only Operations Workspace for understanding Agent fleet runtime, adoption, work, and change.

It is not:

- an Agent chat entry point;
- an Agent builder;
- a Prompt or Knowledge editor;
- a deployment or restart console;
- a generic infrastructure metrics dashboard;
- an automated learning or remediation system.

## 3. Product Principles

### 3.1 Evidence before interpretation

Every operational conclusion must be backed by a concrete state transition, timestamp, count, Session, turn, Trace, synchronization run, or deployment value. The first release does not use an LLM to write or classify the Daily Brief.

### 3.2 Quiet by default

`Needs Attention` only contains facts that merit inspection. Low traffic by itself is not an incident. Missing unsupported telemetry is not an incident. Routine successful polls and synchronizations are not events.

### 3.3 Business reporting stays clean

The default Cockpit, Fleet totals, Activity History, Sessions, and Flywheel views cover the nine Business Agents. Test and Feishu Default remain monitored System Agents and remain available through explicit Agent and Session views.

### 3.4 Read-only Agent boundary

The Platform may persist its own derived snapshots, rule state, and events. It must not modify an Agent, Prompt, Knowledge source, Tool configuration, deployment, Session, or source Flywheel record.

### 3.5 Partial failure does not erase known facts

One unavailable source must not clear other Agent data or the last successful Daily Brief. Stale results must be marked with their last evaluation time and must never be described as healthy merely because evaluation failed.

## 4. Information Architecture

### 4.1 Overview

The existing Overview becomes the Operations Cockpit. Its order is:

```text
Fleet Status + Key Facts
        ↓
Needs Attention | Last 24 Hours
        ↓
7-Day Trend | Active Agents
        ↓
Business Agent Cards
```

The first viewport must answer the current-state and recent-change questions without requiring the user to inspect every Agent.

The existing key facts remain compact:

- Business Agents
- Online Business Agents
- Total Conversations
- Conversations in the last seven days

### 4.2 Activity History

A new `/activity` page provides the full event ledger. It is reached through `View all activity` from the Overview and from Agent detail pages. It is not added to the primary navigation in the first release.

Activity History supports:

- Agent filter;
- event type filter;
- severity filter;
- date range filter;
- grouping by Today, Yesterday, and calendar date;
- direct navigation to the related Agent, Session, or Trace.

The default filter contains Business Agent events only. A System Agent can be selected explicitly.

### 4.3 Agent Detail

Agent Detail gains a `Recent Activity` section containing only events for that Agent. It includes runtime transitions, deployments, usage changes, synchronization changes, and supported execution signals.

Existing Sessions, turns, answers, Evidence, Trace, and lifecycle presentation remain in place.

## 5. Daily Brief

### 5.1 Needs Attention

The module shows active, evidence-backed conditions. It does not show a numeric health score and does not support acknowledge, dismiss, or close actions.

| Category | Condition | Severity | Clears when |
|---|---|---|---|
| Runtime | Business Agent is `offline` after two consecutive observations | Critical | Two consecutive healthy observations |
| Runtime | Business Agent is `degraded` after two consecutive observations | Attention | Two consecutive non-degraded observations |
| Data freshness | Latest FAE or ADMIN synchronization failed, or the last successful synchronization is more than 36 hours old | Attention | A genuinely fresh successful synchronization is observed |
| Data access | A required local business-data source cannot be read | Attention | A later evaluation reads it successfully |
| Execution | A supported source records Tool Error, Fallback, Empty Answer, or an explicitly incomplete task | Attention | The occurrence remains historical; the active grouped item clears after its aggregation window ends |

Rules must honor source capability. For example, an Agent without engineering Trace collection cannot produce a missing-Trace incident.

Execution occurrences are grouped by Agent, signal type, and rolling one-hour window so repeated failures do not flood the Brief. A grouped item shows its count and links to the filtered Sessions or the most recent supporting Session.

Synchronization failure and staleness are two states of one active condition per remote source. A failed source may become stale after 36 hours by updating the same event's facts; it must not create a second active Attention item.

Synchronization freshness is measured from the actual last successful
completion exposed by `platform_read.sync_status.last_success_at`; the latest
run's `completed_at` is used only when that latest run itself succeeded. Exactly
36 hours is fresh, and one microsecond beyond the boundary is stale. A stale
latest successful row does not clear Attention. A latest non-successful row
without `last_success_at` is incomplete evidence and fails the synchronization
rule group; it does not start a local replacement freshness clock.

When no active item exists and the engine has evaluated all required sources successfully, the module displays `No critical issues` with the evaluation timestamp. If evaluation is incomplete or stale, it displays the last successful evaluation time and does not make a healthy claim.

### 5.2 Last 24 Hours

This module is a concise change summary, not a raw log. It uses a fixed rolling 24-hour period so its meaning does not depend on login state or local browser history.

The module supports four event families:

| Family | Examples |
|---|---|
| Usage | New Conversations, number of active Agents, largest real usage contributors |
| Lifecycle | First deployment, detected deployment update, version or durable update metadata change |
| Adoption | First production conversation and genuine count milestones |
| Recovery | Runtime recovery, synchronization recovery, restored data access |

Rules:

- A fleet-level summary leads the module, for example `33 new conversations across 6 Business Agents`.
- Repeated activity for one Agent is aggregated rather than emitted per conversation.
- At most five changes appear on Overview.
- The largest two or three contributors may be named when they materially explain the total.
- Milestones use real cumulative Conversations at 100, 250, 500, 1,000, and subsequent 1,000 increments.
- Routine successful health polls and daily synchronization runs do not appear.
- Synchronization success appears only when it resolves a failure or stale condition.
- The complete history remains available in Activity History.

## 6. Operational Event Ledger

### 6.1 Storage boundary

The ledger uses a dedicated local SQLite database:

```text
data/platform-operations.db
```

The path is configurable through `PLATFORM_OPERATIONS_DATABASE_PATH`. The database file is runtime state and is excluded from Git.

SQLite is appropriate for the current single-user, single-Platform-process deployment. It keeps derived operational writes separate from the shared Flywheel PostgreSQL and from the read-only FAE/ADMIN synchronization boundary. The ledger can be rebuilt from source data and migrated to PostgreSQL if the Platform later becomes multi-user or multi-process.

### 6.2 Components

```text
Health snapshots ─────────────┐
Session and turn data ────────┤
Trace capability/signals ─────┤
Remote sync status ───────────┼─→ Operational Rule Engine
Deployment metadata ──────────┘             │
                                             ▼
                                  SQLite Event Ledger
                                             │
                          ┌──────────────────┴─────────────────┐
                          ▼                                    ▼
                   Daily Brief API                     Activity API
                          │                                    │
                          └──────── Overview / Agent / Activity
```

The implementation is divided into independent units:

- `OperationsRepository`: SQLite migrations, transactions, event queries, and rule-state persistence.
- `OperationsRuleEngine`: deterministic evaluation with no HTTP or UI responsibilities.
- `OperationsScheduler`: invokes rule groups at their required intervals and records run health.
- `OperationsService`: assembles the Brief and paginated Activity response.
- `Operations routes`: read-only HTTP endpoints.

### 6.3 Data model

`operational_events` stores:

- `event_id`
- `agent_id`, nullable for fleet-wide events
- `agent_visibility` (`business` or `system`)
- `event_type`
- `event_family`
- `severity` (`info`, `attention`, or `critical`)
- `status` (`active`, `resolved`, or `historical`)
- `title`
- `summary`
- `source_kind`
- `occurred_at`
- `first_observed_at`
- `last_observed_at`
- `resolved_at`, nullable
- `facts_json`
- `target_kind`, nullable
- `target_id`, nullable
- `target_path`, nullable
- `fingerprint`, unique for the active logical condition

`operational_rule_state` stores the last normalized value and cursor for each rule and Agent. It supports transition detection, incremental Session processing, milestone detection, and idempotency.

`operational_usage_occurrences` is the exact usage replay ledger. It stores one row per stable source Turn key with `occurrence_key`, canonical `agent_id`, Asia/Shanghai `bucket_start`, source `occurred_at`, and local `processed_at`. It stores identifiers and timestamps only; it never stores question text, answer text, or other source payloads.

`operational_runs` stores scheduler run name, start and finish time, status, cursor, and sanitized error. It provides the evaluation freshness shown by the UI.

The ledger does not copy question text, answer text, Trace payloads, credentials, or Knowledge content. It stores identifiers, counts, normalized facts, and links back to the canonical data.

### 6.4 State transitions and deduplication

An active-condition fingerprint is derived from rule, Agent, normalized condition, and aggregation window where relevant.

- Re-observing the same condition updates `last_observed_at` and facts instead of inserting another active event.
- Clearing a recoverable runtime, synchronization, or data-access condition marks the active event `resolved` and inserts one Recovery Event.
- An occurrence-based Execution Event becomes `historical` after its aggregation window and does not create a synthetic Recovery Event.
- Reappearing after resolution creates a new event.
- A database transaction updates event and rule state together.
- Local MetaBot usage and execution scan by `created_at`, replaying the hour
  before the persisted local cursor to cover rows committed around the prior
  snapshot. Remote FAE/ADMIN data is scanned by successful synchronization
  generation: each new `completed_at` triggers one read-only full snapshot scan
  for that source, while repeated polls of the same generation do not rescan.
  Failed synchronizations never mark a generation processed. All event
  bucketing uses the true source `created_at`; usage occurrence keys and
  execution turn/signal keys make local overlap and remote snapshot replay
  idempotent.
- Each filtered usage scan returns a typed `UsageBatch`: its exact answered-Turn
  occurrences and the per-Agent cumulative answered-Turn totals for that same
  source. Both queries execute on one PostgreSQL connection in one read-only,
  repeatable-read transaction, so an occurrence cannot be paired with an older
  cumulative total. Occurrences and totals count distinct stable Turn keys, so
  duplicate joined run rows cannot inflate usage. Fleet Overview supplies Agent
  name and visibility metadata only; its cached conversation totals are never
  rule inputs. A missing or failed occurrence/total read fails the usage group
  without advancing either the local cursor or the affected remote generation.

Usage activity is recorded in hourly Asia/Shanghai buckets. Its fingerprint includes Agent, usage event type, and bucket start. Each `UsageObservation` carries the exact typed `UsageOccurrence` rows represented by that Agent/hour aggregation. One SQLite transaction applies the complete evaluation batch: it inserts unseen occurrence keys, ignores replayed keys, recomputes every affected bucket count from the ledger, creates or updates and finalizes hourly Events, persists cumulative, bucket, and milestone state, writes crossed milestone Events, expires closed buckets, and inserts the successful usage `operational_runs` row with the candidate `local_through` and `remote_generations` cursor. Success data and its cursor therefore become visible together; a crash or cursor-write failure rolls back both. The scheduler receives a typed outcome marking that success as already committed and does not write a second successful usage run. If evaluation or the atomic success commit fails, no usage mutation is visible and the scheduler separately attempts to record a failed run with the prior cursor so other groups can continue. Replay buckets retain their historical cumulative state; only the newest source observation carries the aligned final total into milestone evaluation, and a crossed milestone uses that carrier's latest exact occurrence time. Late unseen keys increment only the hour derived from their source occurrence timestamps. The Brief aggregates those buckets over the rolling 24-hour interval; empty buckets are not stored.

## 7. Evaluation Schedule

| Rule group | Schedule | Input |
|---|---|---|
| Runtime transitions | Existing health/cluster polling cycle | Normalized Business and System Agent states |
| Usage and adoption | Every 5 minutes | Local created-time overlap plus new successful remote snapshot generations |
| Execution signals | Every 5 minutes | Local created-time overlap plus new successful remote snapshot generations |
| Remote data freshness | Every minute | Existing sync status and snapshot timestamps |
| Lifecycle changes | Startup and every 10 minutes | Catalog, runtime contract, deployment and update metadata |

Slow or failed evaluation of one rule group must not block another group.
Due groups execute concurrently while sharing one Fleet snapshot and one sync
status snapshot for the scheduler pass. Scheduler passes do not overlap.
The runtime group succeeds only when the runtime source is current, every ID in
the authoritative Agent catalog is present in that Fleet snapshot, and every
returned Agent has usable `active`, `online`, `degraded`, or `offline` evidence.
An empty snapshot or any `checking`/`unknown` Agent makes the group partial
without mutating runtime rule state.

### 7.1 Initial baseline

The first successful engine run seeds rule state before emitting change events. Existing Agents, deployments, counts, and healthy runtime states must not appear as newly discovered changes merely because Operations was enabled. This baseline runs inside the owned Operations background task, so application lifespan and existing APIs become available after lightweight construction and migration; periodic change evaluation begins only after the baseline completes.

During initialization:

- currently active runtime and data-freshness problems may enter Attention after satisfying their normal debounce or threshold;
- real usage during the preceding 24 hours is backfilled into hourly buckets and the exact occurrence ledger;
- successful remote generations are seeded as processed after scanning only
  rows whose true `created_at` is within the preceding 24 hours and atomically
  applying the source-aligned cumulative totals from that scan;
- every Agent represented by a source-aligned cumulative total seeds usage and
  milestone state even when the preceding 24 hours contain no occurrences;
- durable lifecycle dates may be imported as historical events using their actual dates, but dates older than 24 hours never appear in `Last 24 Hours`;
- milestone state advances to the highest milestone already reached without emitting old milestones.

## 8. APIs

### `GET /api/operations/brief`

Returns:

- current evaluation freshness;
- active Business Agent attention items;
- fleet-level 24-hour summary;
- up to five Business Agent change events;
- source coverage and any partial-evaluation state.

### `GET /api/operations/events`

Supports:

- `agent_id`
- `event_type`
- `severity`
- `date_from`
- `date_to`
- pagination

Without `agent_id`, results contain Business Agent events only. An explicit System Agent ID returns that Agent's events.

### `GET /api/agents/{agent_id}`

The existing response gains a bounded `recent_activity` collection, or the page requests the Activity API with the Agent filter. The implementation plan must choose one approach based on existing repository boundaries; it must not duplicate event assembly logic.

## 9. UI Behavior

- Attention and change items show Agent, cause, occurrence time, source, and a concise supporting fact.
- Severity is communicated by label and icon in addition to color.
- Items are links only when a valid target exists.
- Zero-state language is calm and specific.
- Loading or stale Brief data does not replace the existing Overview with a full-page error.
- Mobile layout stacks `Needs Attention` above `Last 24 Hours`.
- The current visual system, typography scale, card weight, and source-language copy rules remain unchanged.

## 10. Failure Handling

- Event Engine failure never blocks Fleet Overview, Agent directory, Sessions, Flywheel, or health polling.
- The last successfully assembled Brief remains readable with its evaluation time.
- A stale or partially evaluated Brief cannot claim that there are no issues.
- A single unavailable source suppresses only rules requiring that source.
- SQLite write failures are logged with sanitized context and do not affect source data.
- Startup migration failure disables Operations features and leaves existing Platform routes available.
- Corrupt or missing SQLite state may be archived and rebuilt without changing source Agent data; destructive recovery is never automatic.

## 11. Testing Strategy

### Rule tests

- every Attention threshold and clear condition;
- two-observation runtime debounce;
- 36-hour synchronization boundary;
- source-capability suppression;
- execution-event aggregation;
- adoption milestone crossing;
- Recovery generation for recoverable runtime and data conditions;
- Business/System visibility behavior.

### Repository tests

- migrations from an empty database;
- transactional event/state writes;
- active fingerprint uniqueness;
- idempotent replay;
- filtering and pagination;
- persisted run freshness.

### Service and API tests

- Brief ordering and five-item limit;
- Business default and explicit System Agent access;
- partial-source responses;
- stale evaluation behavior;
- valid target paths;
- existing Overview remains available when Operations is unavailable.

### Frontend tests

- Attention, healthy, partial, stale, and empty states;
- Last 24 Hours aggregation presentation;
- Activity filters and date groups;
- Agent Recent Activity;
- accessible severity treatment;
- responsive stacking;
- no System Agent leakage into default views.

### Deployment verification

- run all backend tests;
- run all frontend tests and the production build;
- restart only `com.orbbec.ai-agent-platform`;
- verify existing APIs remain compatible;
- verify Brief and Activity against real production data;
- visually inspect Overview, Activity History, and an Agent detail page.

## 12. Non-Goals for This Release

- notifications to DingTalk, email, or other channels;
- acknowledgement, assignment, or incident workflow;
- a universal Agent quality or health score;
- universal Feedback metrics;
- automatic remediation;
- Agent restart, deployment, editing, or publishing;
- LLM-generated operational summaries;
- access control or multi-user preferences;
- Prompt, Tool, or Knowledge editing;
- cost, port, CPU, memory, or latency dashboards.

## 13. Acceptance Criteria

1. Overview shows current active Attention and a factual rolling 24-hour change summary above the existing trend and Agent cards.
2. Every Attention item is backed by a supported source fact and can be traced to the relevant object when a target exists.
3. Runtime flapping and scheduler replay do not create duplicate events.
4. Clearing a recoverable runtime or data condition closes it and creates one historical Recovery Event; occurrence-based Execution Events do not generate Recovery.
5. Default Brief and Activity results contain only the nine Business Agents.
6. Explicit System Agent activity remains available.
7. Unsupported telemetry never creates an incident.
8. Operations failure does not break existing Platform functionality.
9. Stale or partial evaluation never produces a false healthy statement.
10. The feature remains read-only with respect to all Agent systems and source Flywheel data.
