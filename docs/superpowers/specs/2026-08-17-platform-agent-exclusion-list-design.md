# Platform Agent Exclusion List Design

## Goal

Provide one Platform-level exclusion list for Agents that must not appear in the management product while preserving their source and replica data. The initial exclusions are:

- `fae-bot` (FAE / Felix MetaBot)
- `codex-assistant` (Iris Codex)

`ai-fae-agent` is a different Agent and remains included.

## Decision

`backend/app/fleet/catalog.yaml` will contain an `excluded_ids` list. `AgentCatalog` is the single policy owner and exposes exclusion-aware roster, visibility, canonical-ID, and membership operations.

Exclusion is stronger than `visibility: system`:

- Business and System rosters omit excluded IDs.
- Runtime discovery cannot reintroduce an excluded ID.
- Usage, trend, Operations, and summary aggregation ignore excluded IDs.
- Agent directory and Agent detail/runtime endpoints return no excluded Agent.
- Default and explicitly filtered Session queries return no excluded Agent data.
- Direct Session and Trace URLs return not found when their owning Agent is excluded.

The source records remain stored and synchronized. No database row, attachment, feedback, trace, or source payload is deleted or rewritten.

## Catalog Contract

`AgentCatalog.default()` reads:

```yaml
excluded_ids:
  - fae-bot
  - codex-assistant
```

The catalog validates that every excluded ID names a declared profile and that aliases cannot bypass exclusion. Its behavior is:

- `is_excluded(agent_id)` resolves aliases and returns the policy decision.
- `canonical_id(agent_id)` returns `None` for excluded or unresolved IDs.
- `all_profiles()` and `ids_for_visibility()` return only included profiles.
- `profile()` retains metadata lookup for internal parsing, but consumers must use membership methods before exposing records.

This preserves historical identity metadata without making excluded Agents Platform members.

## Enforcement Boundaries

### Fleet and runtime

Observed local or remote instances are filtered through `is_excluded()` before Fleet cards, totals, usage, trends, expected IDs, or runtime views are built. Catalog completion only adds included profiles. With the current catalog, the Business Agent total becomes eight.

### Agent directory

Local and cloud-replica Agent directory repositories omit excluded source rows and catalog profiles. Explicit Agent detail and runtime requests return 404.

### Sessions and traces

Both PostgreSQL and cloud-replica repositories reject an explicitly excluded `agent_id`. Direct Session and Trace resolution checks the resolved record's `agent_id` and returns no result for excluded owners. This prevents a saved URL from bypassing list filtering.

### Operations and management projections

Operations source mapping relies on exclusion-aware canonical IDs and catalog profiles, so excluded usage and execution observations are discarded before aggregation. Review and Flywheel projections that accept or return an Agent ID apply the same policy before exposing records. Existing stored Operations or Review data is not deleted; read paths suppress it.

## API Behavior

- Collection endpoints silently omit excluded records.
- Explicit excluded Agent filters return an empty collection, not data.
- Direct excluded Agent, Session, Turn, Trace, runtime, Review, or Flywheel resources return 404 where the endpoint already has not-found semantics.
- Authentication and authorization behavior is unchanged.

## Testing

Tests will establish the policy before implementation and cover:

1. Catalog validation and alias-safe exclusion.
2. Fleet production shape: 8 Business Agents, 10 unique included Agents, no excluded cards or usage.
3. Local and cloud Agent directory omission.
4. Default, explicit-filter, and direct Session/Trace denial.
5. Runtime detail denial for excluded IDs while `ai-fae-agent` remains available.
6. Operations, Review, and Flywheel projections omit excluded records.
7. Existing non-excluded Agent behavior remains unchanged.

Production acceptance compares Overview and Agent directory Business rosters, asserts both excluded IDs are absent, asserts `ai-fae-agent` remains present, and confirms FAE service identity and uptime are unchanged.

## Non-goals

- Deleting or rewriting source, replica, Session, attachment, Review, or Operations data.
- Changing the MetaBot runtime contract.
- Renaming `ai-fae-agent` or merging it with `fae-bot`.
- Adding an owner-facing exclusion editor in this release.
- Changing Agent authorization or DingTalk identity behavior.
