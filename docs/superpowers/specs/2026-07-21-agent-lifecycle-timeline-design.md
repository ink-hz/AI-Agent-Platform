# Agent Lifecycle Timeline Design

**Date:** 2026-07-21

## Objective

Replace misleading process uptime on the Agent Overview with durable lifecycle dates that describe how long an Agent has existed in production and when its deployed implementation was last updated.

## Product semantics

Each Agent has three separate time concepts:

- `live_since`: the earliest trustworthy evidence that the Agent was available in production. It is durable and must never move forward because of a restart.
- `last_updated_at`: the latest trustworthy production release or deployed code/configuration update. It changes only when an Agent is deployed or materially updated.
- `current_runtime_seconds`: the current process/container/service runtime. It remains diagnostic data and is not shown on Overview cards.

Runtime health remains independent. An Agent can be `Online` with a short current runtime while still showing a much older `Live Since` date.

## Evidence and initialization

Lifecycle dates are stored in the checked-in Agent catalog rather than inferred on every request. This makes the values stable across process restarts and keeps the evidence reviewable.

Evidence priority is:

1. Production release artifacts or deployment records.
2. Repository history for the deployed Agent configuration or implementation.
3. The earliest captured production Session as a conservative lower bound.
4. No date when no trustworthy evidence exists.

The API also carries a basis for each date: `release_artifact`, `repository_history`, `earliest_session`, or `not_recorded`. Overview keeps this provenance visually quiet; Agent Detail explains it.

Initial values use the evidence inspected on 2026-07-21:

- AI FAE: first and latest production release directories on the Aliyun host.
- AI ADMIN: first and latest production release artifacts on the Aliyun host.
- Local MetaBots: earliest captured Session for `live_since` when available; repository history for `last_updated_at`.
- Feishu Default: runtime-contract repository history because no captured Session exists.

## API contract

`GET /api/fleet/overview` exposes these fields on every Agent:

```json
{
  "live_since": "2026-06-17T16:34:33+08:00",
  "live_since_basis": "release_artifact",
  "last_updated_at": "2026-07-21T18:20:27+08:00",
  "last_updated_basis": "release_artifact",
  "current_runtime_seconds": 7200
}
```

Unknown dates use `null` with the `not_recorded` basis. The old Fleet field `uptime_seconds` is replaced by the semantically explicit `current_runtime_seconds`; the lower-level cluster health contract remains unchanged.

## Overview presentation

Each Agent card presents lifecycle age as the primary signal and the underlying dates as supporting evidence:

- `In Production`: elapsed full days since `live_since`, such as `34 days`, with `Since Jun 17, 2026` beneath it.
- `Last Updated`: relative time such as `3 hours ago`, with `Jul 21, 2026` beneath it and the exact timestamp in the element title.

Elapsed age is calculated from the persistent `live_since` timestamp at render time. Durations shorter than 24 hours render as `Today`; one full day renders as `1 day`; later values use plural `days`. The label must not use `Running Days`, `Uptime`, or wording that implies an uninterrupted process lifetime.

Conversation totals, recent activity, and health state remain unchanged. Missing or invalid lifecycle dates render as `Not recorded` without a fabricated supporting date.

## Agent Detail presentation

Agent Detail loads the matching Fleet Agent record alongside its existing profile and Sessions. Its operational metadata shows:

- `Live Since`
- `Last Updated`
- `Current Runtime`

Date provenance appears as concise supporting text. Current runtime is deliberately confined to this diagnostic context.

## Update workflow

Future deployment automation should update only `last_updated_at` and `last_updated_basis`. `live_since` is write-once historical metadata. Until deployment automation owns this field, catalog changes are reviewed and committed with the release.

## Non-goals

- Reconstructing a complete deployment history.
- Treating Session activity as a deployment event.
- Replacing health checks or runtime diagnostics.
- Adding lifecycle metrics, alerts, permissions, or controls.
