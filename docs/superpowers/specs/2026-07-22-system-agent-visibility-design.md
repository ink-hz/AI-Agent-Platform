# System Agent Visibility Design

**Date:** 2026-07-22

## Objective

Keep the default product experience focused on Agents that represent real business capabilities while preserving operational access to infrastructure and test identities.

`test-bot` and `feishu-default` are monitored runtime identities, but they are not part of the business Agent fleet presented on Overview. Their synthetic or infrastructure activity must not inflate business Agent counts, usage totals, trends, rankings, or default conversation browsing.

## Classification

The Agent catalog gains a required presentation classification:

```yaml
visibility: business | system
```

Initial classification:

- `test-bot`: `system`
- `feishu-default`: `system`
- All nine remaining cataloged Agents: `business`

Unknown runtime identities default to `system` until intentionally cataloged. This prevents newly discovered test or infrastructure processes from silently entering business reporting.

The classification is catalog metadata, not a frontend ID blacklist. It is projected into both `FleetAgent` and `AgentSummary` API models so every view uses the same rule.

## Overview scope

Overview is a Business Agent view.

The following include only `visibility: business` Agents:

- `Agents` and `Online` summary counts
- active, degraded, offline, and checking counts
- hero running/attention message
- total and seven-day Conversations
- comparison percentage
- seven-day usage trend
- Active Agents ranking
- Agent card grid and its displayed count

The Fleet API continues returning both business and system Agent records in `agents` for diagnostic consumers, but its `summary` and `trend` are explicitly business-scoped. Overview filters `agents` before rendering cards and rankings so its list and summary remain consistent at nine Agents.

A System Agent failure does not change the Overview hero or business incident count.

## Agents directory

The Agents page remains the complete inventory:

- Business Agents appear first in the existing primary grid.
- System Agents appear afterward under a visually quieter `System Agents` heading.
- The page headline count reports Business Agents, not the combined inventory.
- Each System Agent remains linkable to its Agent Detail page.

Agent Detail continues loading the all-Agent Fleet payload, so lifecycle and `Current Runtime` remain available for `test-bot` and `feishu-default`.

## Sessions and Agent Data

Default business browsing excludes System Agents:

- The Sessions Agent selector contains only Business Agents.
- An unfiltered Sessions request excludes Sessions belonging to System Agents, keeping totals and pagination honest.
- Agent Data / Flywheel selection contains only Business Agents.

Explicit diagnostic access remains available:

- A request with an explicit System `agent_id` returns that Agent's Sessions.
- Opening a System Agent Detail page can therefore still show its recorded conversation history.

This rule hides `TEST_BOT_FEISHU_OK` and similar synthetic traffic from default product surfaces without deleting the underlying records.

## Data flow

1. `catalog.yaml` defines `visibility` for each Agent.
2. `AgentCatalog` defaults unknown runtime identities to `system`.
3. Fleet aggregation builds runtime records for all Agents but computes summary, trend, and usage totals from the Business Agent ID set.
4. Observability Agent summaries expose the same classification.
5. Default Session queries exclude System Agent IDs unless `agent_id` is explicitly provided.
6. Frontend pages group or filter records according to `visibility`.

## Failure behavior

- Missing classification is treated as `system`, never as business.
- If catalog lookup fails for a Session's `bot_id`, the Session is excluded from the default business list; an explicit `agent_id` query still matches that raw ID.
- If only System Agents are available, Overview renders a zero-Agent business fleet rather than promoting System identities.
- System Agent runtime health remains collected by the existing cluster monitor.

## Testing

Backend tests must verify:

- Fleet summaries and trends exclude `test-bot` and `feishu-default` while the diagnostic Agent payload retains them.
- Unknown runtime identities default to `system` and do not enter business summaries.
- Default Session listing excludes System Sessions.
- Explicit `agent_id=test-bot` still returns Test Sessions.
- Agent summary APIs expose `visibility`.

Frontend tests must verify:

- Overview renders nine Business Agent cards and excludes System identities from ranking and counts.
- Agents groups the two System Agents separately and reports nine Business Agents.
- Sessions and Agent Data selectors omit System Agents.
- Direct System Agent detail remains renderable.

## Non-goals

- Removing System Agents from runtime discovery or health polling.
- Deleting Test or Feishu Sessions.
- Adding permissions, authentication, or user-configurable visibility controls.
- Building a separate infrastructure monitoring product.
