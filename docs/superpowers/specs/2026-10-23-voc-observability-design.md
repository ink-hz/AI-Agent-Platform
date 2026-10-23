# VOC unified observability

The production VOC application owns its inbound messages, interaction jobs,
outbox deliveries and formal feedback records. Platform's admin overview, agent
directory and session replay all read the normalized observability model, which
currently supports MetaBot, FAE and Admin but has no VOC producer. Registering a
VOC application entry does not populate that model. This missing producer is the
common cause across the three admin pages, rather than three UI defects.

Integrate VOC through the existing producer mirror -> normalized read views ->
sanitized, signed replica export -> encrypted cloud read repository. Do not add
per-page live fallbacks or write directly to replica tables. The existing trusted
SSH source transport executes a fixed read-only PostgreSQL snapshot of exactly
VOC inbound messages, interaction job states and outbox deliveries. No raw staff
ID, webhook, download code, token, DSN or attachment content leaves that source.

Use source_kind and agent_id `voc`, a separate platform_source_voc schema, stable
source session/turn UUIDs, and `voc:` namespaced keys. A session groups the same
canonical internal user and DingTalk conversation; unresolved identities remain
separate and do not acquire a fabricated owner. Canonical owners come only from
the persisted VOC internal UUID, never a display-name match. The source snapshot
must identify VOC and prove both tables complete before any import occurs.

Each real inbound message is one turn. A delivered answer exists only when its
outbox row is sent; failed/pending processing remains visible with no invented
answer. Source timestamps and available delivery duration are preserved. Model,
token, cost, feedback, review and trace facts that were not collected remain
unavailable. Formal VOC count is a business-record count and is not substituted
for session count. Browser drafts do not contain full conversation history and
must not be fabricated into a transcript.

Add VOC to source allowlists, UI source filtering and source labels. Existing
roles and admin access boundaries remain in force. Existing business submission,
confirmation, bot delivery and unrelated data are unchanged. Repeated source
imports and replica exports must remain idempotent. Source failures retain the
last snapshot and expose failed/stale status instead of resetting counts.

Verify producer fixture -> isolated PostgreSQL mirror -> normalized repository
-> sanitizer/signed export -> encrypted replica -> admin HTTP responses, plus
the three routes' shared source totals. Use only disposable data for engineering
tests. Production verification reads real counts and status, not business text.
The user performs browser acceptance under the repository's established rule.
