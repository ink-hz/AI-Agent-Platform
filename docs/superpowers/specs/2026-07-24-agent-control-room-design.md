# Agent Control Room Design

**Date:** 2026-07-24<br>
**Status:** Approved direction, pending written-spec review

## Purpose

Upgrade Agent Detail from a profile and conversation page into a compact, read-only control room. The first screen must answer four questions without becoming an operations dashboard:

1. What is this Agent for?
2. What can it do?
3. Can it work right now, and which model is it actually using?
4. What is it doing or scheduled to do next?

Fleet Overview remains the place for cross-Agent status. Session Detail remains the place for question, answer, evidence, and Trace inspection.

## Product Principles

- Keep the first screen compact; details remain one click away.
- Show named capabilities instead of counts alone.
- Report the model actually used by the running Agent, not an intended default.
- Distinguish an online process from an Agent that is ready to complete work.
- Show operational facts only when supported by a source.
- Keep the release read-only. It does not edit configuration, restart Agents, or change schedules.
- Preserve the original language of Agent descriptions, Skill names, task names, and generated content.

## Selected Layout: Control Room

The selected direction is the balanced, single-screen Control Room layout. It has five sections in this order.

### 1. About

Show:

- Agent name and glyph;
- business domain;
- one concise description;
- source and deployment context only when useful.

Do not place lifecycle metrics or source implementation details in this section.

### 2. Runtime

Show only:

- readiness status;
- current model and backend;
- primary channel connection;
- production runtime since the Agent was first deployed online.

Example:

> Ready<br>
> Claude Opus 4.8 · PTY<br>
> Feishu Connected · Running for 6 days

The first release supports these readiness states:

- `Ready`: the process and required primary channel are healthy, and no confirmed core dependency failure exists;
- `Busy`: Ready, with an active task;
- `Limited`: the process is online, but a confirmed required capability or dependency is unavailable;
- `Offline`: the runtime process is not available;
- `Unknown`: evidence is incomplete and readiness cannot be established.

`Online` alone must not be translated into `Ready`.

The current model is resolved from runtime evidence. Registry defaults may be used only as a clearly identified fallback and may not be presented as the active model.

Detailed dependency information such as PostgreSQL, Web Search, Scheduler, Gmail, or knowledge loading belongs in Runtime Detail, not on the first screen.

`Running for N days` is a lifecycle value, not process uptime. It is anchored to the first verified production deployment and does not reset after a service restart or routine update. Process start time and uptime may appear in Runtime Detail when available. A later deployment is shown separately as `Last updated`; it does not replace the original production start date.

### 3. Capabilities

Show the most important named capabilities rather than a generic count:

- up to three primary Skills or workflows;
- up to three relevant Tools;
- a short Knowledge summary when a knowledge source exists;
- `View all N` when additional capabilities exist.

Example:

> Weekly Reports · Content Review · Event Evaluation<br>
> Web Search · Gmail · File Output<br>
> View all 5 →

Capability records include:

- stable capability ID;
- display name in its original language;
- kind: `skill`, `tool`, `knowledge`, or `workflow`;
- availability: `available`, `limited`, `unavailable`, or `unknown`;
- optional version or last-updated time;
- evidence source.

The first screen does not show descriptions for every Skill, trigger phrases, filesystem paths, or raw configuration. Those belong in Capability Detail.

### 4. Activity

Show:

- one active task, when present;
- otherwise, the next scheduled task;
- the three most recent completed work items;
- a failed work item only when a real failure record exists.

Each item contains only a title, state, relevant time, and optional output summary such as `3 files`.

Activity does not show queue length, success rate, token totals, or generic performance KPIs on the first screen.

Scheduled work must come from the Agent scheduler. A schedule described only in conversation text is not considered active.

### 5. Recent Sessions

Keep the existing recent Session list below the Control Room. It remains a historical record and does not compete visually with current Activity.

Session rows continue to link to Session Replay. Sender identity is shown only when available through the existing privacy-safe presentation fields.

## Detail Surfaces

The first release adds two drill-down surfaces without adding edit controls.

### Capability Detail

Shows the complete Skill, Tool, Knowledge, and Workflow inventory for one Agent, including availability, evidence source, version, and last update when known.

### Runtime Detail

Shows component-level evidence for process, model, channel, scheduler, data flywheel, and Agent-specific required dependencies. It also shows process uptime separately from production runtime and explains why the aggregate readiness state is `Limited`, `Offline`, or `Unknown`.

## Data Architecture

The backend produces one canonical `AgentControlRoom` read model composed from existing sources:

- Agent registry: identity, domain, description, deployment;
- runtime contract and health probes: process, channel, and process uptime;
- deployment metadata: first verified production deployment and last update;
- runtime observations and Trace: active model and backend;
- Bot configuration and Skill manifests: capabilities;
- scheduler state and operational events: active, scheduled, completed, and failed work;
- PostgreSQL flywheel: recent Sessions.

Each field that affects readiness or availability carries source evidence and an observation time internally. The public API returns presentation-safe values and freshness, never credentials, provider IDs, secret paths, or raw environment variables.

The UI consumes the canonical read model. It does not independently infer readiness from unrelated API fields.

## Source Precedence

- Active model: current runtime observation, then latest completed run; registry value is a labeled fallback only.
- Runtime status: live process health, then runtime-contract state; stale observations become `Unknown` rather than `Ready`.
- Capabilities: explicit Skill/config inventory first; observed use may confirm availability but cannot invent a declared capability.
- Activity: active execution state, scheduler records, operational completion/failure events, in that order.

## Failure and Missing-Data Behavior

- Missing model evidence displays `Model not observed`; it does not guess from a description.
- A missing optional capability source does not make the entire Agent Limited.
- A confirmed failure of an Agent-required tool may produce `Limited`.
- A failed source request does not remove the last known record immediately; stale data is labeled in detail and cannot establish `Ready`.
- First-screen error copy remains minimal. Detailed reason and source freshness live in Runtime Detail.
- One unavailable section must not prevent About, Sessions, or other available sections from rendering.

## Scope Boundaries

This release does not add:

- restart, pause, deploy, schedule-edit, or configuration-edit actions;
- permissions or multi-user access control;
- generic feedback, evaluation, cost, or token KPIs;
- automated capability installation;
- inferred capabilities derived only from conversation text;
- changes to Fleet Overview information density;
- changes to Session Replay content hierarchy.

## API Shape

The canonical response contains:

- `agent`: existing identity and description fields;
- `readiness`: status, reason summary, observed time;
- `runtime`: model, backend, channel, process duration;
- `lifecycle`: first production deployment, production runtime, and last update;
- `capabilities`: featured items, total count, source freshness;
- `activity`: active item, next scheduled item, recent completed/failed items;
- `recent_sessions`: existing Session summaries or a link/count when loaded separately.

All collections are bounded on the first screen. Detail endpoints provide complete inventories.

## Testing

Backend tests verify:

- process Online does not automatically mean Ready;
- current runtime model wins over registry defaults;
- stale or absent runtime evidence becomes Unknown;
- process restarts do not reset production runtime;
- a confirmed required dependency failure becomes Limited;
- capability lists are bounded and preserve original language;
- schedule records, not conversation claims, determine next work;
- partial source failure still returns available sections;
- public responses contain no secrets, provider IDs, or raw filesystem configuration.

Frontend tests verify:

- the five Control Room sections appear in the approved order;
- model and readiness are prominent without adding a separate model panel;
- at most three primary capabilities and three Tools appear before `View all`;
- Activity prefers an active task, otherwise the next schedule;
- only three recent work items render on the first screen;
- missing evidence uses compact fallback copy;
- narrow layouts preserve readable cards without shrinking below the platform typography minimum.

Production verification confirms one MetaBot, AI FAE, AI ADMIN, and Iris Codex Agent against their different source combinations before fleet-wide rollout.

## Success Criteria

For any Agent, the first screen allows the user to identify its purpose, important capabilities, readiness, active model, channel, runtime duration, current or next work, and recent Sessions without opening more than one detail surface and without reading a wall of metrics.
