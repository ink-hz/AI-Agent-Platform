# Agent Data Browser Design

## Goal

Replace the generic Flywheel metrics dashboard with a read-only Agent data browser. The operator selects one Agent and sees only the data that the Platform actually has for that Agent.

## Product boundary

- This is a data viewer, not a universal Flywheel operating model.
- Do not expose global KPI cards.
- Do not generalize FAE-specific Feedback, Review, Eval, Knowledge Task, or QA concepts to every Agent.
- Do not display zero values for unsupported capabilities.
- Do not add write actions, permissions, prompt changes, knowledge changes, or release controls.
- Preserve source questions, answers, titles, descriptions, and other content without translation.

## Page composition

The existing `/flywheel` route remains in place.

1. `Agent selector`
   - Lists all 11 catalog Agents.
   - Uses the Agent name, glyph, domain, and source.
   - Defaults to the first Agent returned by the canonical Agent API.
   - Selecting another Agent immediately reloads that Agent's data.

2. `Selected Agent context`
   - Shows Agent name, description, deployment, source, and data freshness.
   - Explains that the page contains captured data rather than performance scoring.

3. `Captured Sessions`
   - Lists the selected Agent's real Sessions, newest first.
   - Each row shows the original title, source/channel, Turn count, and last activity.
   - Opening a row uses the existing Session Detail page, where Questions, Answers, Evidence, and Trace are already available.
   - An Agent with no Sessions gets an explicit no-data state.

4. `Data availability`
   - Sessions and Conversations are available through the canonical store.
   - Answers and Trace are inspected inside Session Detail rather than duplicated on the browser page.
   - Source-specific Feedback/Review/Flywheel modules are intentionally absent from this common page.

## Data flow

The page calls `GET /api/agents`, chooses an Agent, then calls `GET /api/sessions?agent_id=<id>&limit=50`. It reuses the existing `AgentSummary`, `Page<SessionSummary>`, `SessionListItem`, loading, empty, and error components. No backend schema or endpoint changes are required.

Stale remote data remains visible with its existing freshness label. Switching Agents cancels the previous request so an older response cannot replace the newly selected Agent.

## Visual direction

Use a strong horizontal Agent switcher rather than a generic dropdown. The selected Agent has a visible accent and substantial context card. Session rows retain the established high-contrast card treatment and responsive layout. The page contains no metric-card grid and no Feedback/Review KPI language.

## Verification

- Component tests prove all Agents are selectable and selection state is visible.
- Source contract tests reject generic Feedback, Review, KPI, and remote-sync sections on this page.
- Existing Session list tests continue to preserve source language and encoded navigation.
- Full Vitest and production build must pass before deployment.
