# Cloud Fleet Agent Count Design

## Problem

The production Overview reports two Business Agents while the Agent directory reports ten. In cloud-replica mode, `deploy/cloud/metabot.runtime-contract.json` is intentionally empty and runtime pollers are disabled. `FleetReadService` therefore sees only the two placeholder remote statuses for AI FAE and AI ADMIN. It treats that incomplete runtime snapshot as the complete roster, drops usage records for the other catalogued Agents, and derives `total_agents` from the remaining two rows.

## Decision

The Agent Catalog is the authoritative roster for fleet membership and visibility. Runtime snapshots are authoritative only for runtime state.

`FleetReadService` will support an explicit catalog-completion option. Cloud-replica construction enables it; local construction keeps the existing observed-runtime behavior. When enabled, the service will add one synthetic runtime row for each catalog Agent missing from the observed snapshots. Synthetic rows have `unknown` runtime state and no uptime, so missing evidence never appears healthy.

Observed rows continue to win over synthetic rows. Unknown runtime IDs discovered outside the catalog remain available as System Agents. Business totals, usage aggregation, trend filtering, and Overview cards are then calculated from the same completed roster.

## Resulting Behavior

- Production Overview and Agent directory use the same Business Agent roster.
- The production total changes from two to ten with the current catalog.
- MetaBot usage and trends are no longer discarded merely because cloud runtime polling is disabled.
- AI FAE and AI ADMIN retain their observed placeholder/runtime state instead of being duplicated.
- Missing runtime evidence is shown as `unknown`, not `online`, `active`, or `healthy`.
- Local deployments and existing runtime-contract discovery are unchanged.

## Testing

Add a service regression test matching production inputs: an empty local snapshot, two remote Agents, a ten-Agent Business catalog, and usage for a catalog Agent absent from runtime snapshots. Assert that the Overview contains each Agent once, reports ten Business Agents, preserves the MetaBot usage, and marks the missing Agent `unknown`.

Add a construction test proving cloud-replica mode enables catalog completion while the ordinary local service retains observed-only behavior. Run the focused backend tests, the full backend suite, frontend tests, production build, dependency audit, deployment gates, and production acceptance.

## Scope

This change does not start cloud runtime pollers, add cross-server probes, alter authentication, change Agent visibility classifications, or modify FAE/ADMIN services.
