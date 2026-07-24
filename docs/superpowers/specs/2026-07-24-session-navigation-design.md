# Session Navigation and Product Naming Design

**Date:** 2026-07-24<br>
**Status:** Approved direction, pending written-spec review

## Purpose

Remove the current overlap between Sessions and Flywheel, make Session inspection a non-disruptive drill-down, and replace the remaining MetaBot-era browser title with Orbbec Agent Platform naming.

The result should give the operator one obvious place to inspect captured conversations while preserving the PostgreSQL data flywheel as the underlying data system.

## Product Decisions

1. Sessions is the only top-level conversation-data entry point.
2. Flywheel remains a backend capability, not a duplicate navigation destination.
3. Returning from Session Replay restores the actual source page, its filters, and its scroll position.
4. Browser-tab titles use Orbbec Agent Platform branding and reflect the current page.

## Navigation

The primary navigation becomes:

> Overview · Agents · Sessions

`Flywheel` is removed from the visible navigation. This change does not disable PostgreSQL ingestion, remote synchronization, retention, backups, Trace capture, or any existing read model.

The legacy `/flywheel` route remains as a compatibility redirect. It uses history replacement rather than adding another browser-history entry and resolves to `/sessions`. This prevents old bookmarks from failing and prevents the Back button from bouncing through an obsolete page.

No current Flywheel page content is moved because its Agent selector and Session list duplicate capabilities already available in Sessions.

## Sessions as the Canonical Data Browser

Sessions continues to provide:

- question-and-answer search;
- Agent filtering;
- source filtering;
- captured Session totals and rows;
- links to Session Replay for Questions, Answers, Evidence, Feedback, Review, and Trace when supported by the source.

Filter state is canonicalized in the URL with these query parameters:

- `agent_id`;
- `source_kind`;
- `q`.

Empty values are omitted. Malformed values and unsupported `source_kind` values are discarded safely. A syntactically valid `agent_id` remains in the URL even when the current catalog does not expose that Agent, allowing explicit System Agent and historical links to remain addressable. The visible controls and the applied request must always be derived from the canonical URL when the page is opened or restored through browser history.

Filtering replaces the current Sessions history entry without a full page reload, so browser Back leaves the filtered view rather than stepping through every filter change. Agent and source selections apply immediately; the text query applies when Search is submitted. A copied URL opens the same filtered result set in another tab. Opening Sessions from the primary navigation uses the unfiltered `/sessions` route.

## Session Drill-Down and Return Behavior

Before a Session row opens, the Platform records the current source location and window scroll position in the current browser-history entry. The Session Detail entry records a safe internal return target.

Session Detail uses these rules:

- when opened from Sessions, `Back` returns to the same filtered URL and restores its scroll position;
- when opened from Agent Detail, `Back` returns to that Agent Detail page;
- when opened from another supported internal Platform surface, `Back` returns to that surface;
- when opened directly or without a valid internal origin, the fallback is `All Sessions` linking to `/sessions`.

Return targets must be same-origin Platform paths. External URLs, protocol-relative URLs, malformed paths, and unsupported routes are rejected and use the fallback.

Scroll restoration occurs only after the restored source page has rendered enough content to support the saved position. On Sessions, that means waiting for the restored result list; on Agent Detail, it means waiting for Agent content and Recent Sessions. Restoration is scoped to the specific browser-history entry so two differently filtered Sessions views do not overwrite each other. A normal navigation to a new page starts at the top.

The Platform does not persist Session filters or scroll position as a cross-device preference. Browser-history state is sufficient for this single-user, per-tab navigation behavior.

## Browser-Tab Titles

The static HTML fallback title becomes:

> Orbbec Agent Platform

The running application sets a contextual title for each route:

- Overview: `Orbbec Agent Platform`;
- Agents: `Agents · Orbbec Agent Platform`;
- Agent Detail: `<Agent name> · Orbbec Agent Platform`;
- Sessions: `Sessions · Orbbec Agent Platform`;
- Session Detail: `Session Replay · Orbbec Agent Platform`;
- Activity History: `Activity History · Orbbec Agent Platform`;
- unknown or failed routes: `Orbbec Agent Platform`.

When an Agent name is loaded asynchronously, the page initially uses `Agent · Orbbec Agent Platform` and updates once the canonical display name is available. Source-language Agent names are preserved.

## Architecture

The frontend owns this change. Existing Session and flywheel backend endpoints and database schemas remain unchanged.

The implementation introduces three focused concerns:

1. A canonical Sessions query parser and serializer shared by initial render, filter application, and `popstate` restoration.
2. A navigation-state helper that validates internal return targets and stores or restores per-history-entry scroll state.
3. A route-aware document-title resolver with asynchronous Agent-name support.

The router continues to own Platform navigation. It must treat pathname plus search parameters as the current location rather than comparing only pathname values.

## Failure and Edge Cases

- If a filtered Sessions request fails, the URL and filters remain intact so Retry repeats the same request.
- If a saved scroll position exceeds the restored document height, the browser restores to the maximum valid position without repeated jumping.
- If Session Detail is refreshed, its validated fallback return target remains usable when stored in the current history entry; otherwise it falls back to `/sessions`.
- If an Agent referenced by `agent_id` is no longer in the visible selector, the URL remains intact and the existing selected-placeholder behavior is preserved where applicable.
- Redirecting `/flywheel` never creates a redirect loop.
- Title resolution failure always falls back to `Orbbec Agent Platform`; it never displays `MetaBot Cluster Monitor`.

## Testing

Router and state tests verify:

- `/flywheel` resolves to a history-replacing `/sessions` redirect;
- Sessions query parameters round-trip without losing Unicode search text;
- empty, malformed, and unsupported source parameters are normalized safely while valid explicit Agent IDs are retained;
- navigation recognizes changes in pathname plus search;
- only validated same-origin Platform paths can become return targets.

Sessions tests verify:

- filters initialize from the URL;
- Agent, source, and submitted search changes update the canonical URL and request;
- Back from Session Replay restores the same filters;
- the saved scroll position is restored after results render;
- two browser-history entries retain independent positions;
- Retry preserves the current filters.

Session Detail tests verify:

- internal-origin navigation renders `Back` and returns to the true source;
- returning to Agent Detail restores the originating Recent Sessions position after Agent content renders;
- direct entry renders `All Sessions`;
- unsafe or unsupported return targets fall back to `/sessions`.

Title tests verify every supported route, the asynchronous Agent-name update, the static HTML fallback, and the absence of the old MetaBot title.

The full frontend test suite and production build must pass before deployment. Production verification covers filtered Session drill-down and return, an Agent Detail drill-down, a direct Session URL, the legacy Flywheel redirect, and browser-tab titles.

## Scope Boundaries

This release does not:

- delete or rename PostgreSQL flywheel schemas;
- stop local or remote flywheel ingestion and synchronization;
- introduce generic Feedback, Review, evaluation, or improvement KPIs;
- add Flywheel write actions;
- preserve navigation state across devices or browser sessions;
- redesign Session rows or Session Replay content;
- change the Agent Control Room specification.

## Success Criteria

The operator sees only one top-level conversation-data destination, can inspect a Session and return without losing context, and sees Orbbec Agent Platform naming in every browser tab. Existing captured data and synchronization continue unchanged.
