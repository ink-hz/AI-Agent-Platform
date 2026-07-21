# Unified Agent Observability and Session Data Model

- Date: 2026-07-21
- Status: Approved for implementation
- Scope: AI Agent Platform monitoring, Session inspection, Trace inspection, and Data Flywheel read experience
- Supersedes: `docs/2026-06-29-platform-flywheel-review-design.md` for ingestion and storage architecture

## 1. Product intent

AI Agent Platform is the management and observability surface for the Agent fleet. It is not a chat entry point and does not replace the business Agents.

The first product milestone must answer four questions:

1. How many Agents exist and which ones are running?
2. How much are the Agents being used?
3. What happened in a specific Session and Turn?
4. What Feedback, Review, evaluation, or knowledge work came out of that interaction?

The fleet contains eleven Agents:

- Nine local MetaBot Agents.
- AI FAE in Alibaba Cloud.
- AI ADMIN in Alibaba Cloud, used through DingTalk and operated as a backend Agent.

The Platform must present these Agents as one coherent fleet while preserving the source data and source-specific capabilities of each runtime.

## 2. Product principles

### 2.1 One product model, multiple source models

The Platform defines a canonical read model for Agent, Session, Turn, Trace, TraceStep, Evidence, Feedback, Review, and ImprovementItem.

Source databases are not rewritten into one physical source schema. Each source keeps its native data model and a source adapter maps it into the canonical model.

### 2.2 Read and inspect before control

This milestone is read-only from the Platform user experience. It does not edit prompts, knowledge, tools, reviews, or production configuration. It does not publish Agent releases.

### 2.3 Production remains authoritative

Alibaba Cloud production PostgreSQL and production Trace files remain the source of truth for AI FAE and AI ADMIN. Platform synchronization is read-only against production.

### 2.4 Operational freshness and business-data freshness are different

- Health is polled every 60 seconds.
- Session, Turn, Trace, Feedback, Review, and Improvement data are synchronized once per day.

The UI must display the relevant timestamp and must not imply that daily business data is real time.

### 2.5 Preserve original language and semantics

Questions, answers, descriptions, source titles, and Agent-authored content are rendered exactly as stored. English product concepts remain English. Existing Chinese source content is not translated.

## 3. Non-goals

- No unified chat entry point.
- No cross-Agent orchestration.
- No automatic prompt, knowledge, tool, or release modification.
- No new public Session or Review endpoints on the Alibaba Cloud host.
- No direct dependency on Langfuse internal database tables.
- No full RBAC system in this milestone.
- No migration that stops the source Agents from writing their existing databases.
- No attempt to make all source-specific metadata identical.

## 4. Verified source models

### 4.1 Local MetaBot flywheel

MetaBot stores event-oriented observability data in the local `flywheel` PostgreSQL database:

- `flywheel_core.conversations`
- `flywheel_core.messages`
- `flywheel_core.feedback`
- `flywheel_trace.runs`
- `flywheel_trace.events`
- `flywheel_evidence.evidence`
- `flywheel_governance.*`
- `flywheel_analytics.*`

The association chain is:

```text
conversation -> message(turn_id) -> run(turn_id) -> event(run_id) -> evidence(event_id)
```

A MetaBot conversation is the canonical Session. Messages sharing a `turn_id` form the canonical Turn. A run is the canonical Trace and events are TraceSteps.

The existing analyst role intentionally cannot read raw `flywheel_evidence` payloads. The Platform must preserve that boundary.

### 4.2 AI FAE flywheel

AI FAE stores business and review data in production PostgreSQL:

- `chat_sessions`
- `chat_turns`
- `turn_feedback`
- `turn_reviews`
- `eval_candidates`
- `knowledge_improvement_tasks`
- `qa_review_items`

`chat_turns` contains question, answer, sources, public execution stages, completion metadata, capability planning, coverage, fallback, outcome, and duration.

`trace_id` links each Turn to execution tracing. AI FAE writes every sampled Trace to both Langfuse and `traces.jsonl`. The JSONL record contains:

- `trace_id`
- `span_id`
- `parent_span_id`
- `node`
- start and end timestamps
- duration
- redacted input and output summaries
- metadata
- error

The verified production file contains root and child spans for nodes such as `chat_request`, `session_check`, `guardrail_precheck`, `schema_extract`, `llm_call`, `bucket_handle`, and `loop_runtime`.

FAE has two distinct execution representations:

- `stages`: public, user-facing progress emitted during a Turn.
- spans: engineering Trace details written to JSONL and Langfuse.

The Platform must retain that distinction rather than labeling stages as spans.

### 4.3 AI ADMIN flywheel

AI ADMIN stores its business and review data in production PostgreSQL:

- `admin_chat_sessions`
- `admin_chat_turns`
- `admin_turn_feedback`
- `admin_turn_reviews`
- `admin_eval_candidates`
- `admin_knowledge_improvement_tasks`

Its Turn model is structurally close to AI FAE. It adds `source_groups` and uses its own Review classification model. It currently has no FAE-equivalent span recorder, so its canonical Trace is composed from the Turn `trace_id`, duration, outcome, stages, source groups, and completion metadata.

The absence of spans must be represented as `Trace detail unavailable`, not as an error or an empty broken chart.

## 5. Architecture decision

The selected architecture is:

> Native source storage + read-only daily mirror + canonical read model.

```text
Local MetaBot PostgreSQL ------------------------------+
                                                       |
Alibaba Cloud FAE PostgreSQL -- daily SSH sync --> FAE mirror
Alibaba Cloud FAE traces.jsonl -- daily SSH sync --> FAE spans
                                                       |
Alibaba Cloud ADMIN PostgreSQL -- daily SSH sync --> ADMIN mirror
                                                       |
                                                       v
                                             Canonical read model
                                                       |
                                                       v
                                          Platform API and Web UI
```

The canonical layer is the compatibility boundary. UI code and public Platform APIs never branch on native table names.

### 5.1 Rejected alternatives

#### Rewrite every source into the MetaBot tables

Rejected because FAE stages, FAE spans, ADMIN source groups, and the Review models do not have lossless one-to-one equivalents in the MetaBot schema. It would also blur local Evidence access controls.

#### Add source-specific branches throughout the UI

Rejected because every new Agent would multiply API, filtering, pagination, empty-state, and detail-page behavior.

#### Real-time direct ingest from FAE and ADMIN

Rejected for this milestone because the user selected daily synchronization. Direct ingest would require production runtime changes, credentials, retry queues, and replay semantics without improving the current viewing requirement.

#### Read Langfuse internal database tables

Rejected because it couples the Platform to Langfuse storage versions. AI FAE already produces a stable, redacted JSONL Trace contract.

## 6. Local storage layout

All synchronized data is stored in the existing local PostgreSQL instance, using isolated schemas.

### 6.1 Native data

- Existing `flywheel_*` schemas remain the source for the nine MetaBot Agents.
- `platform_source_fae` mirrors FAE business tables and stores imported FAE spans.
- `platform_source_admin` mirrors ADMIN business tables.
- `platform_sync` stores synchronization runs, source counts, validation results, and freshness state.
- `platform_read` contains canonical views exposed to the Platform repository layer.

Source primary keys and timestamps are preserved. Mirror rows include `source_synced_at` and `source_environment = 'production'`.

### 6.2 Synchronization is atomic

Each daily source synchronization follows this sequence:

1. Export through SSH using read-only production queries.
2. Parse and validate the export locally.
3. Load into staging tables.
4. Run linkage and count checks.
5. Merge into mirror tables inside a transaction.
6. Mark the sync successful only after the transaction commits.

If any step fails, the previous successful mirror remains readable. The UI shows `Sync failed` and the timestamp of the last successful sync.

### 6.3 Existing FAE script

`AI-FAE-Agent/deploy/scripts/sync_prod_data_flywheel.sh` is the starting point, but it cannot be used unchanged:

- It assumes a local Docker container named `ai-fae-postgres`; Docker is not installed on the Platform machine.
- It currently replaces production `corrected_answer` with an empty string.
- It does not import FAE Trace spans.
- Its destination tables are not isolated from other sources.

The implementation must parameterize the local PostgreSQL destination, preserve `corrected_answer`, target `platform_source_fae`, and include `traces.jsonl` import.

### 6.4 FAE Trace import

The initial implementation downloads the complete redacted `traces.jsonl` once per day and upserts by `(trace_id, span_id)`. The current data size is small enough that an incremental byte-offset protocol is unnecessary.

The import records malformed-line counts and never replaces previously imported valid spans with malformed data.

### 6.5 ADMIN synchronization

ADMIN receives a parallel read-only synchronization script for its native tables. It does not fabricate spans. If ADMIN adds a span recorder later, a new adapter can populate the same canonical TraceStep model without changing the UI contract.

## 7. Canonical read model

Every canonical record carries:

- `agent_id`
- `source_kind`: `metabot`, `fae`, or `admin`
- `source_environment`
- `native_id`
- a stable Platform key scoped by Agent and source
- source timestamps
- `source_synced_at` for mirrored data

Native IDs are never treated as globally unique.

### 7.1 Agent

Common fields:

- identity, name, description, category, deployment location
- operational status and status reason
- process start time and uptime when available
- last activity time
- Session count
- answered Turn count
- last successful data sync
- data freshness state

`Conversations` in fleet usage metrics means answered Turns, not Sessions. The UI displays Sessions separately so usage is meaningful without inflating or falsifying activity.

### 7.2 Session

A Session is a user-visible interaction thread scoped to one Agent.

Mapping:

- MetaBot: `flywheel_core.conversations`
- FAE: `chat_sessions`
- ADMIN: `admin_chat_sessions`

Common fields:

- `session_key`
- `agent_id`
- channel
- title, falling back to the first user question
- masked user identity where available
- created time and last active time
- Turn count
- feedback and review summary
- latest outcome

### 7.3 Turn

A Turn is one user question, one Agent answer, and the execution that produced it.

Mapping:

- MetaBot: user and assistant messages sharing `turn_id`
- FAE: one `chat_turns` row
- ADMIN: one `admin_chat_turns` row

Common fields:

- `turn_key`
- Session and Agent keys
- turn index
- question and answer
- created time
- outcome and fallback state
- duration when available
- Trace key
- sources and Evidence summary
- Feedback summary
- Review summary
- source-specific `details`

The `details` object preserves non-common fields such as FAE capability coverage and ADMIN source groups.

### 7.4 Trace

A Trace represents one Agent execution for a Turn.

Mapping:

- MetaBot: `flywheel_trace.runs.id`
- FAE: `chat_turns.trace_id`
- ADMIN: `admin_chat_turns.trace_id`

Common fields:

- status
- start and completion timestamps
- duration
- engine, backend, and model when available
- token and cost fields when available
- error category and safe error summary
- Trace detail availability
- ordered TraceSteps

Missing source fields are `null`; they are not converted to zero.

### 7.5 TraceStep

TraceSteps use a common envelope while retaining their native semantics:

- `kind`: `stage`, `span`, `tool_call`, or `event`
- `name`
- `status`
- `parent_step_key`
- sequence or start time
- duration
- safe input/output summary
- safe metadata
- error summary

Mapping:

- MetaBot run events become `event` or `tool_call` steps.
- FAE `chat_turns.stages` become `stage` steps.
- FAE JSONL child spans become `span` steps and retain their parent hierarchy.
- ADMIN stages become `stage` steps.

The Session Detail UI groups public stages and engineering spans separately by default, with a unified chronological view available.

### 7.6 Evidence

Canonical Evidence exposes a safe summary:

- type and source label
- title or reference
- classification
- related Turn, Trace, and TraceStep
- availability and expiry state when known
- sanitized metadata

MetaBot raw Evidence payloads remain behind their existing database privilege boundary. The Platform does not grant itself raw Evidence access in this milestone.

FAE sources, capability coverage, and safe tool-call summaries map into Evidence summaries. ADMIN sources and source groups map into the same shape.

### 7.7 Feedback

Feedback normalizes source-specific values into:

- `sentiment`: `positive`, `negative`, or `other`
- raw kind or rating
- reason code
- comment
- related Session, Turn, and Trace
- timestamp

The original rating and payload remain available in source details.

### 7.8 Review

Review exposes common fields without discarding native workflow semantics:

- status
- severity or priority
- failure layer
- reason or notes
- corrected answer when available
- reviewer
- timestamps
- source-specific decision flags

FAE P0-P3 priority and ADMIN low-to-blocker severity are normalized for cross-Agent filtering, while the original value is retained.

### 7.9 ImprovementItem

Evaluation candidates, knowledge tasks, and QA candidates share a common list envelope:

- `type`: `evaluation`, `knowledge`, or `qa`
- status
- priority when available
- title and summary
- related Agent, Session, Turn, Feedback, and Review
- created and updated timestamps
- native details

## 8. Platform API

The API remains read-only and supports server-side pagination and filtering.

Required resources:

- `GET /api/fleet/overview`
- `GET /api/agents`
- `GET /api/agents/{agent_id}`
- `GET /api/sessions`
- `GET /api/sessions/{session_key}`
- `GET /api/turns/{turn_key}/trace`
- `GET /api/flywheel/overview`
- `GET /api/flywheel/items`
- `GET /api/sync/status`

Session filters:

- Agent
- operational source
- channel
- time range
- Feedback sentiment
- Review status
- outcome or failure
- text search across question and answer

The API returns explicit availability and freshness metadata. An unavailable Trace or stale daily mirror is never represented as an empty successful dataset.

## 9. User experience

### 9.1 Overview

The Overview presents all eleven Agents and answers:

- running, degraded, stopped, or unknown
- uptime
- total answered Turns
- Sessions
- recent usage
- last activity
- daily data freshness

Ports and latency are not primary card content.

### 9.2 Agents

The Agents page provides the complete fleet list. An Agent detail page combines:

- description and deployment context
- current operational state
- usage summary
- recent Sessions
- Feedback and Review summary
- knowledge and Evidence availability
- last synchronization result

AI ADMIN is described as a DingTalk-backed backend Agent, not as a WebUI application.

### 9.3 Sessions

The Sessions page is the main investigation surface. Each row shows:

- Agent
- title or first question
- channel
- Turn count
- last activity
- outcome
- Feedback/Review indicators
- data freshness when mirrored

### 9.4 Session Detail

Session Detail displays the full conversation in chronological order. Each Turn expands into:

- Question
- Answer
- Sources and Evidence summary
- public stages
- engineering Trace timeline when available
- Feedback
- Review
- linked ImprovementItems
- native details

Long Trace details are collapsed initially. Question and answer remain visually dominant.

### 9.5 Flywheel

The Flywheel page is a read-only operational view of:

- Feedback volume and sentiment
- pending Reviews
- evaluation candidates
- knowledge tasks
- QA candidates
- broken data associations
- synchronization freshness

It does not offer automatic remediation or production mutation.

## 10. Health monitoring

Health is independent from daily business-data synchronization.

### 10.1 AI FAE

- Poll public `/health` every 60 seconds.
- Normalize service, model, composition mode, and dependency state.
- Do not use public Review endpoints for Platform data ingestion.

### 10.2 AI ADMIN

- Use one read-only SSH command every 60 seconds.
- Query `127.0.0.1:8011/health` on the host.
- Include `ai-admin-agent`, `ai-admin-job-worker`, and `ai-admin-dingtalk-bot` systemd states.
- Do not expose ADMIN publicly merely to simplify monitoring.

### 10.3 Failure behavior

- A failed health poll marks status `unknown` before it marks the Agent `stopped`.
- The last successful observation and failure reason remain visible.
- Health failure does not erase synchronized Sessions.
- Data synchronization failure does not mark a healthy Agent as stopped.

## 11. Security and privacy

- Production access uses the existing SSH key and read-only queries.
- Production database credentials are sourced remotely and never written to logs or the repository.
- No new public endpoints are created.
- Synchronization output is written only to local PostgreSQL and protected temporary files.
- Temporary exports are removed after a successful import; failed exports are retained only when safe and explicitly needed for diagnosis.
- API responses use sanitized Trace summaries.
- User identifiers are masked in list views.
- Raw MetaBot Evidence remains inaccessible to the Platform analyst role.
- The Platform has a single local user today, but the API retains Agent scoping so later access control does not require a data-model rewrite.

## 12. Integrity and drift checks

Each synchronization records:

- source row counts by table
- inserted and updated rows
- malformed Trace lines
- duplicate native IDs
- Session-less Turns
- Turns without answers
- Turns without Trace IDs
- orphan Feedback and Reviews
- Trace IDs without a matching Turn
- Turns with a Trace ID but no imported root span
- synchronization duration and error summary

Schema drift fails the affected source synchronization without damaging the previous mirror.

The FAE corrected-answer field receives a dedicated regression check so daily synchronization cannot silently blank reviewed answers.

## 13. Empty, unavailable, and stale states

The UI distinguishes:

- `No data`: the source is healthy and the query returned zero records.
- `Unavailable`: the source does not provide this capability, such as ADMIN spans.
- `Stale`: the latest daily synchronization did not complete within the expected window.
- `Failed`: a query or synchronization failed.
- `Restricted`: data exists but the Platform role cannot read it, such as raw MetaBot Evidence.

Charts never render a zero-height or visually empty plot when numeric values exist.

## 14. Testing strategy

### 14.1 Adapter contract tests

The same canonical test suite runs against MetaBot, FAE, and ADMIN fixtures. It verifies Session, Turn, Trace, TraceStep, Evidence, Feedback, Review, and ImprovementItem output.

### 14.2 Synchronization tests

- repeat imports are idempotent
- partial exports do not replace the last successful mirror
- malformed Trace lines are counted and skipped
- corrected answers are preserved
- source deletions follow an explicit policy and are not inferred from a partial export
- production access performs no writes

### 14.3 API tests

- cross-Agent pagination and filtering
- stable canonical keys
- Agent-scoped detail lookup
- freshness and availability metadata
- original question and answer text round-trips without translation or truncation

### 14.4 UI tests

- all eleven Agents appear
- Session list and detail states
- FAE hierarchical spans
- MetaBot event timeline
- ADMIN unavailable-span state
- stale and failed synchronization notices
- long questions, answers, and source titles
- desktop and narrow-screen card hierarchy

### 14.5 Security tests

- no secrets in logs or API responses
- no direct public ADMIN endpoint
- Platform role cannot read raw MetaBot Evidence
- source keys cannot retrieve another Agent's records

## 15. Acceptance criteria

The milestone is complete when:

1. Overview reports eleven Agents with independent health and data-freshness states.
2. AI FAE and AI ADMIN health update without exposing new public management endpoints.
3. FAE and ADMIN business data synchronize once daily and preserve the previous successful snapshot on failure.
4. A single Sessions page searches and filters MetaBot, FAE, and ADMIN data.
5. Session Detail displays complete questions and answers for all sources.
6. FAE Trace spans, stages, Feedback, Reviews, and improvement links are navigable from the related Turn.
7. MetaBot Runs and Events render through the same Trace API and UI model.
8. ADMIN stages render without pretending that span data exists.
9. FAE corrected answers survive synchronization.
10. No production data is mutated and no sensitive management endpoint is newly exposed.
11. The existing nine MetaBot processes remain running throughout Platform deployment.
12. Automated backend and frontend tests pass, followed by a live smoke test against the Platform service.

## 16. Delivery sequence

1. Fix the existing Usage Trend bar-height defect.
2. Add local mirror and synchronization-state schemas.
3. Adapt and test FAE daily synchronization, including Trace spans.
4. Add and test ADMIN daily synchronization.
5. Implement the canonical read repositories and APIs.
6. Extend fleet monitoring to eleven Agents.
7. Build Agents, Sessions, Session Detail, and Flywheel pages.
8. Install the daily synchronization schedule.
9. Run integrity, security, responsive UI, and live-service verification.

