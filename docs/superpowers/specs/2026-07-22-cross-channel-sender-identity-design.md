# Cross-channel Sender Identity Design

**Date:** 2026-07-22

## Purpose

AI Agent Platform must show who sent each Feishu message as a human-readable employee identity. The target presentation is `Name · Department`, without exposing Feishu `open_id`, Feishu `union_id`, DingTalk staff IDs, or other provider identifiers.

The company directory of record is DingTalk, not Feishu. Feishu supplies the sender and conversation context; DingTalk supplies the authoritative employee name and department.

## Current State

The local MetaBot flywheel already has the identity foundation:

- all 131 current user messages have a `sender_user_id`;
- six canonical users are represented in `flywheel_identity.users`;
- Feishu application-scoped `open_id` values are linked to cross-application `union_id` identities;
- `flywheel_identity.external_identities` already has `display_name`, `department`, and `attributes` columns.

However, all 26 current external identity rows have empty `display_name` and `department` values. The MetaBot Feishu adapter currently writes only `union_id` and `open_id`. Platform's canonical MetaBot Session view also projects `user_identity` as `null`, and the API models do not expose sender identity.

AI ADMIN currently receives DingTalk Stream messages but does not read or synchronize the DingTalk corporate directory.

## Product Behavior

### Session surfaces

Sender identity appears anywhere a Session row is reused:

- Sessions;
- Agent Detail recent Sessions;
- Flywheel Agent Sessions.

For a direct conversation, the row shows `Name · Department`. For a group conversation, it shows the most recent participant and a participant count, for example `Lina · Marketing + 2 people`.

Session Detail shows `Name · Department` beside every user question. This is the authoritative answer to who sent a specific message in a multi-user group.

Overview Agent cards do not show employee identity. That surface remains an aggregate fleet view.

### Missing identity states

The UI never displays raw provider IDs.

- Feishu name known, DingTalk match unavailable: `Feishu name · Department unavailable`.
- Feishu name unavailable: `Feishu User`.
- Duplicate DingTalk names: preserve the Feishu name and show `Department unavailable`; never select a department by guess.
- Former or disabled DingTalk employee: retain the last known display value with an `Inactive` marker in identity metadata, but do not add an alarming badge to historical Session rows.

## Architecture

The implementation has three isolated stages.

### 1. Feishu name observation in MetaBot

MetaBot resolves the sender's Feishu display name through the Feishu IM chat-members API, using the Bot application that received the message. This API is preferred over the Feishu corporate contact API because these users are not managed in a company Feishu directory and may be external to the Bot application's tenant.

The resolver matches the incoming `open_id` against the current chat's members and records only the returned display name. Results are cached by `(bot_id, open_id)` for 24 hours.

Identity enrichment is non-blocking. Message normalization and Agent execution proceed immediately. A failed, forbidden, rate-limited, or timed-out profile lookup cannot delay, reject, or alter the Bot response.

MetaBot adds an `identity_observed` flywheel envelope. Its payload contains no message body and carries:

- existing sender identifiers in the protected envelope;
- the Feishu display name when found;
- lookup source and observation time in safe attributes.

The flywheel ingest function upserts the display name on both the Bot-scoped `open_id` identity and the canonical `union_id` identity. The event is idempotent.

### 2. DingTalk directory snapshot and matching

AI ADMIN gains a directory-only synchronization job that uses its existing application credential boundary. The required DingTalk directory read permission must be enabled before the job is activated. The job runs once per day and stores only fields needed by Platform:

- stable DingTalk staff ID;
- employee display name;
- department name or names;
- active status;
- source update and synchronization times.

The existing daily ADMIN-to-Platform synchronization bundle transports this directory snapshot. Credentials never leave the production AI ADMIN environment.

Platform performs identity matching after import:

1. normalize surrounding whitespace and Unicode presentation without transliterating or translating names;
2. require an exact name match;
3. accept the match only when exactly one active DingTalk employee has that name;
4. persist `match_method=exact_unique_name`, the matched staff identity, and synchronization provenance in protected identity attributes;
5. leave zero-match and multi-match cases unresolved.

This release does not add fuzzy name matching. It would create false identity associations that are worse than showing an unavailable department.

### 3. Canonical Platform read model

The PostgreSQL canonical views join each MetaBot user message's `sender_user_id` to the best available identity record.

`platform_read.turns` adds:

- `sender_name`;
- `sender_department`;
- `sender_identity_status` (`resolved`, `name_only`, or `unavailable`).

`platform_read.sessions` adds:

- `participant_count`;
- `primary_sender_name`;
- `primary_sender_department`;
- `sender_identity_status`.

The primary sender is the sender of the most recent user message, selected deterministically by message time and message ID. Participant count is the number of distinct non-null `sender_user_id` values in the Session.

Backend Pydantic models and frontend TypeScript types expose these presentation fields. They never expose identity-provider identifiers.

FAE and ADMIN Sessions remain compatible. Their existing masked `user_identity` behavior is not reinterpreted as an employee name, and this feature does not attempt to assign DingTalk departments to non-MetaBot Sessions.

## Historical Backfill

A one-shot MetaBot backfill command processes the six current canonical users and their recent Feishu chat contexts. It uses the same resolver, cache, envelope, and database ingest path as live enrichment; it does not update flywheel tables directly.

After the first DingTalk directory snapshot reaches Platform, the matching job enriches historical Sessions because identity is joined at read time. The existing 131 messages do not need rewriting.

Backfill is successful when every resolvable current Feishu user has a name, every unique DingTalk match has a department, and unresolved or ambiguous users remain explicitly unassigned.

## Security and Privacy

- Platform is currently single-user and does not add access control in this change.
- Raw Feishu and DingTalk identifiers remain in backend identity tables only.
- API responses contain presentation fields and resolution status only.
- Logs contain Bot ID, lookup result class, and timing, but not names, departments, message bodies, or provider IDs.
- Directory synchronization excludes phone numbers, email addresses, titles, avatars, and unrelated profile fields.
- Removing or anonymizing a canonical flywheel user removes the corresponding name and department from current reads.

## Failure Handling

- Feishu lookup failure: record a sanitized metric, keep processing the message, and retry on later messages or backfill.
- DingTalk permission missing: directory sync reports `restricted`; existing Sessions continue to show Feishu names.
- Daily sync stale: retain the last known department and expose source freshness to diagnostics, not as noise on each Session row.
- Ambiguous name: do not match automatically.
- Database enrichment failure: retry through the bounded flywheel queue; never affect message delivery.
- Partial rollout: new API fields are nullable so backend, migration, and frontend can deploy without an all-at-once outage.

## Observability

Operations diagnostics record aggregate counts only:

- Feishu identities with names;
- identities matched to active DingTalk employees;
- name-only identities;
- ambiguous identities;
- directory snapshot freshness;
- lookup success, restricted, failure, and latency counts.

No new fleet KPI or feedback metric is added to Overview.

## Test Strategy

### MetaBot

- normalize and record a Feishu display name from a matching chat member;
- do not attach the wrong member in group conversations;
- verify cache behavior and expiry;
- verify lookup timeout, permission failure, and rate limit do not delay or prevent `onMessage`;
- verify `identity_observed` events are idempotent and contain no message content;
- backfill uses the same enrichment path as live traffic.

### AI ADMIN synchronization

- map only required DingTalk directory fields;
- exclude phone, email, avatar, and unrelated profile data;
- represent permission denial as `restricted`;
- produce an idempotent daily snapshot and preserve active status.

### Platform backend

- exact unique name produces `resolved` with a department;
- zero and duplicate matches remain `name_only`;
- direct Session selects its sender;
- group Session participant count and latest sender are deterministic;
- Turn responses contain presentation identity but no raw provider identifiers;
- FAE and ADMIN compatibility remains intact;
- migration upgrades the live schema and canonical views safely.

### Platform frontend

- direct and group Session rows render their respective identity formats;
- each question renders its own sender on Session Detail;
- missing and ambiguous states are readable without raw IDs;
- narrow layouts do not reduce the question or answer content width excessively.

### Production verification

- send one real message to a Feishu Business Agent and confirm Agent delivery is unaffected;
- confirm the sender name appears after enrichment;
- run one directory synchronization and confirm a unique employee receives the correct department;
- confirm the API and browser never reveal `open_id`, `union_id`, or DingTalk staff ID;
- confirm current historical Sessions are enriched through read-time joins.

## Scope Boundaries

This release does not add Platform login, role-based access, a general employee directory browser, fuzzy identity matching, automated resolution of duplicate names, employee analytics, or identity-based usage rankings.

