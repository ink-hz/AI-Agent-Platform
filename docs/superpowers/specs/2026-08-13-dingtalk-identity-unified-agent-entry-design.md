# DingTalk Identity and Unified Internal Agent Entry Design

**Status:** Approved design baseline, amended after two implementation reviews

**Date:** 2026-08-13

**Repository:** `AI-Agent-Platform`

## 1. Purpose

Upgrade AI Agent Platform from a protected, read-only observability product into
the internal Orbbec identity, authorization, management, and usage entry for
employee-facing Agents.

The Platform remains an engineering-managed product. It does not become a
low-code Agent builder, prompt editor, model picker, tool marketplace, or
business-user publishing system.

This design adds:

- DingTalk enterprise identity;
- an internal user and organization model;
- one platform owner, scoped management viewers, and ordinary members;
- default-deny Agent authorization;
- authenticated Session, attachment, Feedback, Review, Trace, and Evidence
  access;
- a versioned chat gateway for internal Agents;
- durable attachment storage;
- management auditing; and
- a staged replacement for the temporary production Basic Auth gate.

## 2. Confirmed product boundaries

### 2.1 Platform responsibilities

Agent Platform owns:

- employee login and organization identity;
- the Agent Registry and Agent access modes;
- Agent grants;
- the internal Agent directory;
- the common web shell and navigation;
- internal user Sessions and history;
- the unified chat protocol and gateway;
- attachments and Feedback created through the unified entry;
- Review, Trace, Evidence, flywheel, and operations views;
- platform-owner actions and their audit trail; and
- Agent version, environment, health, and integration metadata.

Each Agent continues to own its inference, knowledge, tools, professional
workflows, and engineering release process.

### 2.2 Explicit non-goals

The first releases do not add:

- drag-and-drop Agent creation;
- online Prompt or tool editing;
- user-selectable models;
- business-user Agent publishing;
- multiple administrator levels (`management_viewer` is a non-administrative,
  scoped read-only role);
- approval workflows;
- external customer accounts;
- commercial multi-tenancy or billing; or
- an Agent plugin marketplace.

### 2.3 FAE is an independent external product

AI FAE is customer-facing and remains independent at `fae.orbbec.com.cn` and
its existing IP entry. It does not adopt Orbbec employee DingTalk login through
this project.

FAE is already a validation source for Platform management, Sessions, Review,
Trace, Evidence, and flywheel behavior. It is represented as an
`external_product`, not as an employee chat Agent:

- FAE public routing, accounts, containers, model configuration, and APIs are
  not modified by this project;
- FAE does not appear in an ordinary employee's internal chat directory;
- sanitized FAE management data remains available to `platform_owner` and to
  a `management_viewer` with an explicit FAE observation scope;
- an FAE customer is represented by a source-scoped, pseudonymous external
  subject and is never merged automatically with a DingTalk employee; and
- a future FAE customer account system is a separate product design.

## 3. Agent access modes

Every Registry entry declares one access mode:

| Access mode | Meaning |
|---|---|
| `platform_chat` | Internal Agent using the standard Platform chat page |
| `platform_extension` | Internal Agent using Platform identity with a professional page |
| `external_product` | Independent product; Platform supplies management and governance views only |
| `observability_only` | Runtime and data observability only |

Agent grants apply only to `platform_chat` and `platform_extension`. External
product access is not advertised as being controlled by the internal grant
system.

`platform_extension` is not an arbitrary embedded application. It must be
first-party code reviewed and released from the Platform repository, use only
Platform backend APIs, inherit the Platform CSP, register no Service Worker,
load no remote or third-party JavaScript, and embed no remote iframe. It cannot
read the HttpOnly authentication Cookie. A professional surface that requires
independently deployed code or a separate trust boundary is classified as an
`external_product` and uses a separate origin.

## 4. Architecture and trust boundaries

```text
DingTalk identity and organization
              |
              v
Agent Platform
  +-- Authentication / Web Sessions
  +-- Users / Departments / Membership
  +-- Agent Registry / Authorization
  +-- Unified Chat Gateway
  +-- Session / Attachment / Feedback ownership
  +-- Review / Trace / Evidence / Flywheel
  +-- Audit Log / Operations
  +-- Agent extension surfaces
              |
              +-- internal Agent Adapter --> MetaBot / AI ADMIN / future Agents
              |
              +-- sanitized management data <-- FAE and other external products
```

The browser talks only to Platform for internal Agent use. It never supplies a
trusted user ID, department, role, upstream URL, or Agent authorization scope.

For internal Agent calls, Platform signs a short-lived internal identity token.
An Agent must reject a missing, invalid, expired, or incorrectly addressed
token. Agent APIs used by Platform must not be publicly usable as an alternate
path around Platform authorization.

## 5. Data domains and database isolation

The existing sanitized replica and the new control plane have different trust
and recovery properties and must remain separate.

### 5.1 `platform_control`

This is the writable source of truth for:

- internal users and protected DingTalk mappings;
- departments and current membership;
- DingTalk synchronization runs and event inbox;
- login attempts and Web Sessions;
- Agent grants;
- Platform-owned Agent Sessions and messages;
- attachment metadata;
- Feedback ownership;
- audit records; and
- control-plane schema versions.

### 5.2 `platform_replica`

This remains the current sanitized, read-only management replica for:

- historical Agent and Session data;
- FAE, ADMIN, and MetaBot observability;
- Trace, Evidence, Review, and flywheel projections; and
- operations and usage snapshots.

It is rebuildable from approved source synchronization and is never used as an
authentication or online authorization database.

### 5.3 Physical and logical database boundary

The first release uses the existing PostgreSQL 17 cluster but separates the
domains into two databases, not two schemas in one database:

```text
agent_platform          existing database; platform_replica remains here
agent_platform_control  new database; platform_control is the application schema
```

Preview uses a third database, `agent_platform_control_preview`, and cannot
connect to production control data. The existing `platform_identity` and
`flywheel_identity` schemas in source systems remain replica-enrichment inputs;
they do not become the new control plane.

Database roles are separated:

- `platform_control_migrator`: deployment-only DDL in the control database;
- `platform_control_app`: normal control-plane reads and writes, without DDL;
- `platform_control_sync`: DingTalk inbox and organization-generation writes;
- `platform_control_audit_append`: execute-only access to an append audit
  function, without audit update or delete;
- `platform_replica_reader`: existing replica read-only role; and
- `platform_replica_importer`: existing replica import role.

`PUBLIC` receives no control-database connect or schema privileges. FDW,
`dblink`, cross-database grants, and runtime use of the cluster owner are
forbidden. Platform joins control and replica results in the authenticated
service layer after authorization; SQL cannot join the two databases.

Runtime connection strings come from separate root-owned mode-0600 files:

```text
/etc/orbbec-agent-platform/control-database-url
/etc/orbbec-agent-platform/replica-database-url
```

The migration DSN is mounted only into the migration job and is not available
to the running API. Because both databases share one PostgreSQL cluster,
physical base backup and WAL retention apply to the entire cluster. The
control database has the stated recovery objective; the replica remains
rebuildable even though it is included in the same physical recovery stream.

### 5.4 Identity namespaces

Platform distinguishes:

```text
internal_user    Orbbec employee bound to DingTalk
external_subject source-scoped pseudonymous user from an external product
legacy_unknown   historical actor without a trustworthy stable mapping
```

Historical Sessions are not assigned to employees by name, mobile number, or
other guesswork. A `legacy_unknown` Session is visible only to
`platform_owner`. An `external_subject` Session may also be read by a
`management_viewer` with an explicit observation scope for its source Agent;
that observation never converts it into an employee-owned Session.

## 6. DingTalk integration facts

The selected application is an Orbbec-owned enterprise internal web
application, not an ISV third-party enterprise application.

The real development application has been verified with:

- desktop and mobile web capability;
- production homepage and callback domain configuration;
- an outbound API IP allowlist containing only the Platform cloud host;
- minimal contact read permissions;
- full-employee contact data scope;
- DingTalk access-token exchange;
- department and member API reads; and
- a successful Stream connection and event-subscription channel validation.

The subscribed organization events are:

- user added;
- user modified;
- user left the organization;
- user activated after joining;
- department created;
- department modified; and
- department removed.

No message, robot, approval, attendance, mobile-number, email, external-contact,
or address-book write permission is required.

The verified development secret is stored outside the repository in a
mode-0600 file. Production credentials will be stored under a root-only
`/etc/orbbec-agent-platform/` secret boundary and mounted only into the
authentication, identity synchronization, and Stream components. Secrets never
enter Git, images, database rows, frontend bundles, URLs, or logs.

## 7. Internal identity model

### 7.1 Stable mapping

Platform generates a random `internal_user_id`. The primary provider mapping is
the stable DingTalk `(corp_id, userid)` tuple. `unionid` is retained as a
secondary stable mapping so in-client login and browser QR login converge on
the same internal user.

Provider identifiers are stored as:

- application-encrypted values for required server-side recovery; and
- keyed HMAC lookup values for exact matching and uniqueness; and
- an explicit `key_version` beside every encrypted or HMAC-derived value.

Names are display data only. Phone number, email, title, avatar, job number,
and unrelated profile fields are not synchronized.

### 7.2 Roles

The roles are:

- `platform_owner`;
- `management_viewer`; and
- `member`.

A database constraint permits at most one owner. The first person to log in is
not automatically promoted. An offline, audited operator command binds the
owner role to a previously verified stable DingTalk identity. Frontend role,
name, mobile-number, and department values are ignored.

`management_viewer` is not an administrator. The owner assigns or revokes the
role and one or more Agent observation scopes. A viewer can read management
data only for scoped Agents and can review immutable audit metadata, including
owner actions. A viewer cannot change grants or roles, mutate Review state,
continue another user's conversation, export another user's content, or invoke
an Agent using observation scope.

### 7.3 Owner break-glass replacement

Departure or disablement of the owner immediately revokes the owner's Web
Sessions like any other user. Platform management then fails closed until an
offline operator rebinds the role.

The offline owner command is the break-glass path. It requires:

- OS root access on the Platform host;
- the deployment-only control migration credential;
- an exact stable DingTalk identity, never a name or mobile number;
- a target that is active in the latest complete organization generation;
- an explicit incident reason and a dry-run followed by a separate confirmation;
- one transaction that demotes the old owner, promotes the new owner, preserves
  the one-owner constraint, and revokes both users' existing Web Sessions; and
- append-only audit output containing the OS operator identity, target internal
  ID, reason, result, and time, but no raw DingTalk identifier.

With a fresh directory, the command requires the target to be active in the
latest complete generation. During a hard-stale directory incident, the
operator may instead supply an explicit
`--accept-stale-generation={generation_id}` flag. Stale replacement requires a
target that was active in that exact last complete generation and already has a
verified stable DingTalk mapping, with no later locally processed departure or
disablement event; it never creates an identity from a name.
The replacement owner remains restricted to hard-stale read-only management
until a fresh generation confirms the target active.

The command refuses a target absent from the selected generation or without a
previously verified stable mapping. Recovery from an unreadable control
database or a target absent from all complete generations is a separate
database incident runbook requiring two-person approval, not an application
identity fallback.

## 8. Authentication flows

### 8.1 DingTalk in-client login

1. Platform creates a short-lived, one-time login attempt.
2. DingTalk supplies an authorization code to the web application.
3. The code is submitted only to Platform backend.
4. Backend exchanges the code, verifies the expected Orbbec organization, and
   resolves a current active member.
5. Backend maps or creates the internal user.
6. Backend rotates the login attempt into a new Web Session.

### 8.2 Browser QR login

1. Backend creates a one-time OAuth state bound to the intended environment and
   safe return path.
2. The browser is redirected to DingTalk QR authorization.
3. DingTalk returns only to an exact configured callback.
4. Backend validates state, expiry, single use, authorization code,
   organization, and active membership.
5. `unionid` and the enterprise member mapping converge on the same
   `internal_user_id` used by in-client login.

No failed flow creates a partially trusted account. There is no anonymous or
username/password fallback.

The active-membership steps above describe normal and degraded-freshness
login. The only hard-stale exception is the previously bound owner/viewer
read-only management reauthentication in section 9.2; it cannot create an
identity, grant member access, or start an Agent Session.

### 8.3 Web Session security

Web Sessions are opaque, server-side Sessions:

- only a random token is placed in a Host-only Cookie;
- the database stores a token hash, not the raw Cookie;
- Cookies are `HttpOnly`, `Secure`, `SameSite=Lax`, and `Path=/`;
- successful login rotates the Session ID;
- default idle lifetime is eight hours and absolute lifetime is 24 hours;
- logout, departure, disablement, or owner revocation invalidates the server
  Session;
- every request rechecks current local user status and Agent authorization; and
- state-changing requests also require an Origin check and CSRF token.

Unauthenticated requests return `401`. Authenticated but unauthorized requests
return `403`; they never degrade silently.

### 8.4 Exact unauthenticated route allowlist

Only the following routes are reachable without an authenticated Web Session:

```text
GET  /                         302 to /login
GET  /login
GET  /assets/{build-hashed-static-file}
GET  /favicon.ico
GET  /api/health
POST /api/v1/auth/dingtalk/start
GET  /api/v1/auth/dingtalk/callback
POST /api/v1/auth/dingtalk/in-client/exchange
GET  /.well-known/acme-challenge/{token}   Nginx only
```

The health response is minimal and contains no build secrets, dependency
addresses, organization state, Agent details, or user counts. Login static
assets are immutable build-hashed files; arbitrary paths under `/assets/` are
not proxied to application handlers. The root redirect contains no user data
and preserves no arbitrary return URL. All routes outside this exact allowlist
require a valid backend Session. Authentication routes also enforce the rate
and concurrency controls in section 14.5.

## 9. DingTalk organization synchronization

### 9.1 Reliable Stream ingestion

```text
DingTalk Stream --> durable event inbox --> idempotent worker --> control tables
```

An event is acknowledged only after durable inbox insertion. Events are
deduplicated using the provider event identity or a stable digest. Workers are
idempotent and retry with bounded exponential backoff.

Departure immediately marks the user inactive and revokes every Web Session.
Add, modify, and activation events trigger a targeted member refresh.
Department events trigger a targeted branch refresh and construction of a new
department-closure generation. No incomplete generation becomes active.

Encrypted raw event payloads are retained for no more than seven days. Durable
audit records retain only sanitized processing facts. Stream reconnects
automatically and reports connection state through Platform health and
operations.

### 9.2 Full reconciliation

The existing sequential directory prototype exceeded a 35-second bounded
probe, so it is not reused as a login-time check. Full synchronization uses:

- one department-tree walk;
- member pagination with pages of at most 100;
- initial bounded concurrency of four;
- member deduplication by stable provider identity;
- rate-limit-aware retry;
- a staging generation; and
- one atomic switch after completeness validation.

A failed or partial run never replaces the last complete generation and never
marks the entire organization inactive.

Full reconciliation runs at Platform startup and every six hours. Stream
events maintain freshness between runs. Directory freshness has three distinct
thresholds:

| Age of last complete generation | Behavior |
|---|---|
| Less than 8 hours | Normal |
| 8 to less than 24 hours | Degraded warning; last confirmed active users may log in and use already granted access; role and grant changes are refused |
| 24 hours or more | Hard stale; member login, member data, Agent use, identity creation, and role or grant changes are refused with `503`; a previously bound owner or viewer may authenticate or continue a Session only for read-only management access, with a persistent critical warning |

The six-hour schedule is not itself a failure threshold. A single missed run
therefore raises an operations warning before it can block the organization.
At hard stale, Platform never creates a new internal identity or resolves a
person through stale display data. Privileged reauthentication is allowed only
when DingTalk OAuth itself succeeds, the stable provider mapping already
exists, the local role is still active, and the person was active in the last
complete generation. It grants a read-only management Session, not member or
Agent-use access. If OAuth is unavailable there is no password fallback;
existing unexpired privileged Sessions and the offline owner replacement
command are the remaining paths.

Read-only continuity includes existing FAE observability, scoped management
projections, Review state, and operations dashboards. All management
mutations, Agent calls, exports, erasure, and grant or role changes remain
blocked until a fresh complete generation. Stream departure or disablement
events still revoke a user immediately, including a privileged user, even
while reconciliation is stale. Every hard-stale privileged login and read is
audited and visibly marked.

The offline break-glass owner replacement in section 7.3 is the sole
hard-stale role-mutation exception. It is not exposed through the Web API and
the replacement owner remains read-only until directory freshness recovers.

Stream online state does not substitute for full-reconciliation freshness.

## 10. Agent authorization

Access defaults to deny. A member may use an internal Agent only when all of
the following hold:

```text
DingTalk member is active
AND Platform user is active
AND Agent is enabled
AND (user grant OR department grant OR all-members grant)
```

Department grants always include all descendant departments in the first
release. There is no negative ACL or exception list. Selecting a top-level
department or all employees requires a confirmation showing current department
and member coverage.

Descendant membership is served from an indexed generation table:

```text
department_closure(
  generation_id,
  ancestor_department_id,
  descendant_department_id,
  depth
)
```

The closure is built and validated with the organization staging generation
and activated in the same atomic switch. Request-time authorization uses
indexed joins against the active generation; it never runs a recursive CTE on
the chat path.

Final grants are computed from indexed control-plane rows for every request;
the first release does not cache a final authorization decision.

Each grant records:

```text
agent_id
target_type = user | department | all_members
target_id
include_descendants = true for department grants
created_by
created_at
revoked_at
```

Only `platform_owner` can add or revoke grants.

Agent use grants and management observation grants are separate tables and
capabilities. An `agent_observation_grant` binds a `management_viewer` to one
Agent and permits only the read operations in section 11. It never satisfies
the Agent-use expression above, and an Agent-use grant never grants management
observation. Only `platform_owner` can assign the viewer role or add and revoke
observation scopes.

Each observation grant records:

```text
agent_id
viewer_internal_user_id
created_by
created_at
revoked_at
```

## 11. Permission matrix

| Operation | `member` | `management_viewer` | `platform_owner` |
|---|---|---|---|
| View internal Agent directory | Granted Agents only | Only Agents covered by a separate use grant | All Agents |
| Use an internal Agent | Granted Agents only | Only with a separate Agent use grant | All internal Agents |
| Create an internal Session | Self and granted Agent | No by observation scope | Allowed |
| Read or continue a Session | Own Session only | Read scoped Sessions; cannot continue another user's Session | All Sessions |
| Read attachments | Own Session only | Read scoped attachments; cross-user audited | All, audited when cross-user |
| Submit and view Feedback | Own only | Read scoped Feedback; no mutation | All |
| Trace and Evidence | Own allowed presentation only | Read for scoped Agents | All |
| Review and repair closure | No | Read scoped Review state only; no mutation | Yes |
| FAE external-product management data | No | Read only with FAE observation scope | Yes |
| Agent grant and role management | No | No | Yes |
| Organization administration | No | No | Yes |
| Read immutable audit metadata | No | Yes, including owner actions | Yes |
| Prompt, model, tool, or Agent release editing | No | No | Not provided in phase one |

Opening another user's Session, attachment, Feedback, Trace, or Evidence always
records a management-view audit event. A viewer cannot export another user's
content. Observation scopes constrain every underlying API row, not only the
navigation.

Release 1 applies a stricter gate before complete row authorization exists:

- `member` can use only login, logout, and account/status routes; every
  management, replica, Session, Feedback, attachment, Review, Trace, Evidence,
  audit, and Agent-use route returns `403`;
- `management_viewer` receives `GET` access only through the exact R1
  read allowlist below; all other reads and every mutation return `403`; and
- `platform_owner` can read existing management projections and perform only
  the management actions explicitly enabled for Release 1.

The R1 viewer allowlist is:

```text
GET /api/agents/{agent_id}/runtime
GET /api/review/overview?agent_id={agent_id}
GET /api/review/inbox?agent_id={agent_id}
GET /api/review/issues?agent_id={agent_id}
GET /api/operations/events?agent_id={agent_id}
GET /api/v1/manage/audit/governance
```

For the first five routes, Platform requires exactly one `agent_id`, obtains
the viewer's observation scopes from the control database, and rejects a
missing or unscoped Agent before calling the existing service. A viewer with
multiple scopes makes one request per Agent; R1 provides no combined result.
The governance-audit route contains no Session, message, filename, Evidence,
or external-subject content.

All aggregate or indirect-detail endpoints remain owner-only in R1, including
`/api/fleet/overview`, `/api/operations/brief`,
`/api/review/turn-summaries`, and `/api/review/issues/{issue_id}`. The same rule
applies to any endpoint not named in the allowlist even if it happens to accept
an optional `agent_id`. This limited, endpoint-level scope check is an explicit
R1 deliverable; it is not interpreted as permission to expose the remaining
management API.

Release 2 refines the R1 gate into the row-level matrix above. It does not
remove authentication or create a temporary all-employee view of existing
data.

## 12. Session ownership and retention

Every new internal Session contains:

```text
session_id
agent_id
internal_user_id
external_session_id
created_at
last_active_at
status
```

The native uniqueness boundary is `(agent_id, external_session_id)`. Platform
generates `external_session_id` on the server using at least 128 bits of
cryptographic entropy; a browser-supplied value is ignored. An internal
collision is regenerated and never returned as a distinguishable conflict that
could reveal another Session. External IDs are linkage identifiers, never
access credentials. Query,
continuation, Feedback, attachment, archive, and export all recheck the stored
owner, Agent, and current Agent grant. Revoking a member's Agent access also
hides that Agent's prior internal Sessions from the member; the data remains
available to the owner and the retention process.

Ordinary users cannot hard-delete Sessions. They may archive their own Session.
Messages, Feedback, and attachments are retained for one year and removed by a
central retention job. The flywheel may retain a necessary, non-content,
sanitized audit outcome after source content expires.

The first unified-chat release supports single-Session HTML and JSON export:

- a member may export an owned Session;
- the owner may export any one Session after supplying an internal purpose;
- bulk export is not provided;
- owner export of another user's data is audited; and
- attachment links in exports are short-lived rather than public permanent
  URLs.

An export is either streamed without server persistence or registered as a
short-lived derived artifact with explicit links to every source Session,
message, and attachment. Materialized export artifacts expire within 24 hours;
unregistered temporary export files are forbidden.

For a file or message uploaded in error, the owner has an emergency single-item
erasure operation. It requires recent reauthentication and an explicit reason,
is fully audited, and cannot be used for bulk deletion. The durable erasure job
has `pending`, `succeeded`, `partial`, and `failed` states and covers every
Platform-owned representation:

- the canonical message or attachment content and display filename, replaced
  by a non-sensitive tombstone while identifiers and referential links remain;
- active runs, which are cancelled and prevented from writing another
  checkpoint containing the erased content;
- unexpired `run_events` payloads, which are cleared or replaced by tombstone
  events while sequence continuity remains;
- MinIO objects and all thumbnail, preview, OCR, text-extraction, search-index,
  cache, and quarantine derivatives;
- pending export jobs and every materialized export artifact linked to the
  erased source; and
- outstanding preview or download tickets, which are revoked immediately.

The job then requests downstream deletion only when the Adapter declares
deletion support. Platform reports `succeeded` only after every online
Platform-controlled copy is confirmed erased and every known downstream copy
is confirmed deleted. An unsupported, unreachable, or failed downstream
deletion produces `partial`, with a sanitized residual-system list and retry
state; it is never recorded as success. A Platform-owned online copy that
cannot be deleted produces `failed` or remains `pending` according to whether
retry is possible.

Encrypted database/WAL and backup remnants are not online copies and age out
under the backup-retention policy in section 22. Any restore must reapply known
erasure records before content is exposed. A file already downloaded or an
export already saved on an authorized user's device cannot be recalled;
Platform reports that boundary explicitly and never describes it as remotely
deleted.

The audit record contains identifiers, reason, actor, covered copy classes,
residual-system status, and outcome but never the deleted content or original
filename.

## 13. Internal Agent request identity

Platform signs an Ed25519 token with a lifetime no greater than 60 seconds. It
contains:

```text
issuer
audience = agent_id
subject = internal_user_id
agent_id
session_id
session_owner
authorized_scope
request_id
issued_at
expires_at
jti
```

It contains no DingTalk identifier, employee name, or department details.
Agents validate signature, issuer, audience, expiry, and Agent identity. Public
keys are distributed separately from the private signing key. Agent code never
trusts a browser-provided identity claim.

Validators allow at most 10 seconds of clock leeway. Every Platform and Agent
host must use NTP; operations alert when offset exceeds two seconds, and token
signing or readiness fails closed when offset exceeds five seconds.

### 13.1 Key versioning and rotation

Key versioning exists before the first protected identity row is written:

- provider encryption and lookup-HMAC rows carry `key_version`;
- encrypted event, message, run-event, audit-detail, and attachment metadata
  carries the envelope-encryption key version;
- object encryption records the wrapped data-key version;
- internal Ed25519 tokens carry `kid`; and
- verifiers accept the active and immediately previous signing public key only
  during a bounded dual-acceptance window.

HMAC rotation decrypts provider identifiers in a controlled offline job,
derives the new lookup value, checks uniqueness, writes both versions, switches
the active version, then removes the old lookup after the rollback window.
Envelope-encryption rotation rewraps data keys rather than rewriting object
content. Rotation progress is resumable and audited; missing or unknown key
versions fail closed.

## 14. Unified chat gateway

### 14.1 Online fact ownership

For Sessions created through Platform, Platform is the user, authorization,
message, Feedback, and attachment source of truth. An Agent may keep its native
inference Session, linked by `external_session_id`, but that native ID does not
control Platform access.

The request lifecycle is:

1. authenticate and authorize user, Agent, and Session;
2. persist the user message;
3. create an idempotent Agent run;
4. sign the short-lived downstream identity;
5. call the configured Adapter;
6. stream safe output while periodically checkpointing;
7. persist final response, Evidence, and generated attachments; and
8. retain partial output as `interrupted` if the run terminates early.

### 14.2 Versioned endpoints

```text
GET  /api/v1/agents
POST /api/v1/agents/{agent_id}/sessions
GET  /api/v1/sessions
GET  /api/v1/sessions/{session_id}
POST /api/v1/sessions/{session_id}/messages
GET  /api/v1/runs/{run_id}/events?after={last_sequence}
POST /api/v1/runs/{run_id}/cancel
POST /api/v1/messages/{message_id}/feedback
POST /api/v1/sessions/{session_id}/attachments
GET  /api/v1/attachments/{attachment_id}/download
POST /api/v1/sessions/{session_id}/exports
POST /api/v1/manage/messages/{message_id}/erase
POST /api/v1/manage/attachments/{attachment_id}/erase
```

### 14.3 SSE events

The initial stream protocol supports:

```text
session.created
message.accepted
run.started
progress.updated
response.delta
response.completed
evidence.added
attachment.created
content.erased
run.interrupted
run.failed
run.completed
heartbeat
```

Events include request ID, run ID, monotonic sequence, and timestamp.
Reconnect resumes after the last accepted sequence and does not create a new
run. Raw chain-of-thought is never transmitted. Progress events contain only
safe user-facing stage descriptions.

Replay is backed by an encrypted `run_events` table keyed by `(run_id, seq)`
and carrying its encryption `key_version`. The gateway coalesces delta events
and writes a checkpoint at least every two seconds or 32 KiB of new safe
output, whichever comes first. Each run is limited to 5,000 persisted events
and 10 MiB of replay payload; exceeding the limit produces an explicit
interrupted result rather than unbounded storage growth. Terminal-run replay is
retained for 30 minutes. Non-terminal and interrupted replay is retained for
24 hours so a client can recover from a longer disconnect. After the replay
window expires, the events endpoint returns `410 Gone` and the client reloads
the canonical persisted Session and message state.

An erasure transaction marks its source first so the checkpoint writer rejects
future matching payloads. It then tombstones existing replay rows and emits a
safe `content.erased` event. Reconnect can preserve monotonic sequence without
replaying the erased content.

The initial presentation types are user text, Assistant Markdown, progress,
Evidence, simple tables, images, user attachments, generated attachments,
explicit errors, and Feedback.

### 14.4 Adapter contract and reliability

An Adapter declares:

```text
streaming
idempotency
attachments_in
attachments_out
evidence
tables
cancellation
content_deletion
max_duration
max_attachment_size
```

Registry configuration, not browser input, selects the upstream URL and
Adapter. Calls use an idempotency key. A connection failure may be retried once
before output only when the Adapter declares and implements idempotency. If it
does not, the gateway reports an explicit interrupted run because a lost
response does not prove the upstream request was unprocessed. Once output
begins, an interrupted run is never silently replayed. SSE emits a heartbeat
every 15 seconds. Default backend maximum duration is 300 seconds; the Nginx
read and send timeout is 360 seconds to leave termination and persistence
margin. Longer work later uses an asynchronous job protocol. The gateway never
switches to another Agent or model as a fallback.

### 14.5 Rate, upload, and connection limits

Initial limits are configuration, not client claims:

| Surface | Initial limit |
|---|---|
| Pre-auth browser challenge | 5 login starts per challenge in 10 minutes, with exponential backoff and at most 3 active attempts |
| OAuth state | One callback attempt, atomic single use, 5-minute expiry, and exact environment binding |
| Coarse login edge-IP ceiling | 600 starts per minute with burst 1,200; never the primary per-person key |
| Coarse callback edge-IP ceiling | 1,200 callbacks per minute; unknown or consumed state is rejected before provider exchange |
| Global OAuth exchange breaker | 100 concurrent exchanges and 3,000 exchanges per minute across all edges |
| Authenticated reads | 300 requests per minute per user |
| Authenticated mutations | 60 requests per minute per user |
| SSE | 3 concurrent connections per user, 2 per run, 200 total |
| Upload | 2 concurrent uploads per user |

The login attempt and OAuth state records are the primary abuse and retry
keys. Edge IP is deliberately only a coarse emergency ceiling because many
employees may share a corporate proxy or NAT address. Before publication, the
coarse ceiling must exceed twice the measured or forecast one-minute company
login burst; otherwise publication fails acceptance. A global circuit breaker
protects the provider exchange separately from any one corporate edge.

Limits are enforced in the application, with coarse Nginx protection for
unauthenticated routes. Exceeding a limit returns an explicit `429` and retry
guidance; it never switches identity, Agent, model, or storage behavior.

### 14.6 Trusted proxy and client-address handling

The Platform application listener remains loopback-only. It accepts forwarded
client-address and scheme headers only when the immediate TCP peer is the
configured local Nginx address. For the current single-proxy topology, Nginx
overwrites rather than appends client-supplied forwarding headers:

```nginx
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Forwarded "";
```

The candidate and formal production configuration replace the current
`$proxy_add_x_forwarded_for` behavior. If the application is reached from any
untrusted peer, it discards `Forwarded`, `X-Forwarded-For`, `X-Real-IP`, and
`X-Forwarded-Proto` and uses the peer address and connection scheme. A future
load balancer requires an explicit CIDR allowlist in both Nginx real-IP
configuration and application configuration; no wildcard trusted proxy is
allowed.

The resulting address is an `edge_source_ip`, not an employee identity. It may
drive coarse limits and sanitized security telemetry but never authentication,
organization membership, authorization, or Session ownership.

## 15. Attachments

Platform durably stores both user uploads and Agent-generated files. An Agent
local path is never treated as a durable download result.

Private MinIO is used with:

- no public S3 or management listener;
- random object keys;
- application-level envelope encryption with recorded key version;
- sanitized display filenames;
- SHA-256, MIME, size, source, Session, and owner metadata;
- a 50 MB per-file limit;
- executable-file rejection; and
- quarantine and malware scanning before use or download.

Every upload, preview, and download rechecks Session ownership. Downloads use a
single-use ticket valid for no more than 60 seconds. MinIO addresses and
permanent presigned URLs are never returned to the browser. Owner download of
another user's attachment is audited.

If MinIO is unavailable, new upload initialization and generated-file
finalization return an explicit `503`; metadata is not committed as a usable
attachment. Existing chat without attachments may continue. Quarantine, scan,
download, and emergency erasure never fall back to a local filesystem or a
public object URL.

Emergency single-attachment erasure follows section 12. The object, quarantine
copy, previews, extracts, index entries, export derivatives, replay rows, and
tombstone are coordinated through the durable deletion job. Until Platform
copies are confirmed removed, the attachment remains inaccessible and visibly
marked as deletion pending. A downstream residual is shown as partial rather
than silently treated as success.

Objects and sensitive metadata expire after one year. Deletion failures retry
and surface as operations alerts. Backup and restore verify object/database
referential consistency.

## 16. Audit model

Audit covers:

- login success, failure, and logout;
- user disablement and Session revocation;
- Agent grant creation and revocation;
- viewer role and observation-scope creation and revocation;
- owner or viewer access to another user's or an external subject's content;
- cross-user attachment download and Session export;
- emergency message or attachment erasure;
- Feedback Review and repair actions;
- owner binding or replacement; and
- authorization denial and suspected access attempts.

Records contain only:

```text
actor_internal_user_id
action
target_type
target_internal_id
request_id
result
reason_code
sanitized_before_after
occurred_at
```

They do not contain raw provider identifiers, Cookies, tokens, message bodies,
or file contents. Audit is append-only to the application. If a required audit
write fails, the sensitive management operation fails. Audit retention is one
year.

`management_viewer` may read immutable governance audit metadata, including
owner binding, role, grant, synchronization, and owner management actions.
Content-access audit details remain constrained to the viewer's Agent
observation scopes. A viewer cannot suppress, annotate, export, update, or
delete audit. Audit reads are themselves audited. The owner cannot mutate audit
records or disable audit for an operation.

Phase one does not claim organizationally independent audit oversight. The
owner may revoke a viewer role; the revocation is append-only and remains
available to the owner and offline database operators, but the former viewer
immediately loses audit access, including access to that revocation record.
This is an explicit governance trade-off of the single-owner model. Phase-one
audit guarantees application immutability, required-write failure semantics,
and post-incident forensic evidence, not an independently controlled
supervision chain. An external append-only audit sink or separately governed
reader is deferred until management requires independent oversight; it is not
silently represented as already present.

## 17. UI information architecture

Members see:

```text
My Agents
My Sessions
My Attachments
My Feedback
Account / Logout
```

The owner also sees:

```text
All Agents
All Sessions
External-product management data
Review / Trace / Evidence
Agent Grants
Users / Organization Sync
Audit Log
System Operations
```

A `management_viewer` sees only the read-only management navigation supported
by current observation scopes:

```text
Scoped Agent Sessions / Feedback
Scoped Review / Trace / Evidence
Immutable Audit Metadata
Read-only System Status
```

Mutation controls are absent and the backend independently returns `403` if a
viewer calls a write route.

In R1 this navigation is limited to per-Agent runtime, per-Agent Review lists,
per-Agent operations events, and sanitized governance audit. Scoped Sessions,
Feedback, Trace, Evidence, detail views, and multi-Agent aggregation appear
only after R2 row authorization is complete.

Owner access to another person or an external subject is visibly marked as a
management view. FAE is labelled as an independent external product rather
than an employee chat entry.

## 18. Single-domain preview and routing

The project does not create a new DNS name for every environment or Agent.
Internal Agents are routed by path under the one formal domain:

```text
https://agent.orbbec.com.cn/
https://agent.orbbec.com.cn/agents/{agent_id}
https://agent.orbbec.com.cn/sessions/{session_id}
https://agent.orbbec.com.cn/manage/...
https://agent.orbbec.com.cn/api/v1/...
```

Development acceptance uses a temporary suffix path on the same host:

```text
https://agent.orbbec.com.cn/_preview/dingtalk-r1/
https://agent.orbbec.com.cn/_preview/dingtalk-r1/api/v1/auth/dingtalk/callback
```

The formal `GET /` redirects to `/login`. The preview-prefix root redirects to
`/_preview/dingtalk-r1/login`, never to the formal login path; all generated
preview links and callbacks preserve the fixed environment prefix.

Nginx keeps `/` on the current Basic-Auth-protected production release and
routes only the exact `/_preview/` namespace to an isolated candidate
listener. Preview uses a separate database, MinIO bucket, Cookie name, signing
key, OAuth state namespace, and test-member scope. It does not read production
control data.

The deployed Nginx baseline currently has server-level Basic Auth, a 1 MB body
limit, a root `limit_except` that permits only `GET`, `HEAD`, and `OPTIONS`, and
300-second proxy timeouts. Preview therefore requires an explicit dedicated
`location ^~ /_preview/dingtalk-r1/` with these properties:

- `auth_basic off`, because DingTalk callbacks and login initiation must reach
  the application without the shared password;
- no inherited or repeated root `limit_except`, so authenticated `POST` routes
  can function;
- application authentication and exact public-route allowlisting from section
  8.4 after stripping only the fixed preview prefix;
- the forwarding-header overwrite and trusted-peer rules in section 14.6;
- removal of any inbound `Authorization` header before proxying to the
  candidate;
- `client_max_body_size 1m` by default and a 50 MB override only on the exact
  attachment-upload route;
- proxy read and send timeouts of 360 seconds; and
- the unauthenticated rate limits and total connection bounds in section 14.5.

Preview is network-reachable on the public host at its login and callback
routes. Its protection is the application login protocol, one-time OAuth
state, Orbbec enterprise membership, explicit test-member scope, CSRF defense,
and rate limiting. It does not rely on a mobile-client source-IP allowlist.
All non-allowlisted preview routes require an authenticated candidate Session.

Because preview and production share an origin:

- authentication is never stored in localStorage;
- preview and production use distinct Host-only Cookie names and signing keys,
  and the preview Cookie is limited to `Path=/_preview/dingtalk-r1/`;
- no Service Worker is registered;
- preview OAuth state is environment-bound;
- CSP and exact proxy routing prevent fallback into another backend; and
- preview is removed and its Sessions revoked immediately after cutover.

The shared origin has one unavoidable preview-specific risk: JavaScript
executing in preview would otherwise be same-origin with production and could
attempt `fetch('/api/...')`; a browser might automatically attach cached Basic
Auth credentials or a production Cookie. Separate Cookie names do not prevent
that read. Preview therefore also requires:

- a path-restricted CSP whose `script-src`, `connect-src`, image, and style
  sources name only the fixed preview asset and API prefixes, without a broad
  same-origin `connect-src 'self'`;
- nonce- or hash-authorized first-party scripts, no remote scripts, no inline
  event handlers, and `object-src 'none'` and `base-uri 'none'`;
- Markdown and Evidence rendering that rejects raw HTML, event attributes,
  executable URL schemes, and unsanitized SVG;
- an automated browser test proving preview JavaScript cannot fetch a
  production management endpoint;
- acceptance in a clean browser profile that has neither the production Basic
  Auth credential cache nor a production Platform Cookie; and
- immediate preview removal after the acceptance window.

Path-restricted CSP and sanitization materially reduce the risk but do not make
two path namespaces equivalent to two origins. Release 1 explicitly accepts
this residual risk only for a short-lived, test-member preview. A permanent or
broad preview must use a separate origin and requires a new DNS and security
decision.

The DingTalk developer UI has been verified to accept multiple comma-separated
redirect URLs, so the preview callback is added alongside the formal callback
without removing it. The application homepage is a single URL. The current
development application is unpublished, so initial in-client acceptance may
use its preview homepage without disrupting existing users. After formal
publication, later preview testers open the preview URL directly; the
production homepage is not switched for routine acceptance. Public and
internal DNS and company proxy configuration are required only for
`agent.orbbec.com.cn`, not for every Agent.

## 19. Incremental releases

### Release 1: identity security foundation

- `platform_control` migrations and database roles;
- DingTalk QR and in-client login;
- internal identity mapping;
- unique owner binding;
- scoped, non-administrative `management_viewer` binding;
- Web Session, CSRF, and whole-site backend authentication;
- state-first login throttling and trusted local-proxy address handling;
- Stream and six-hour organization synchronization;
- audit logging;
- control-only PITR rehearsal and WAL-pressure protection; and
- the section 11 R1 gate: members receive `403` outside authentication and
  account/status, the owner can read existing management data, and a viewer can
  read only the exact per-Agent and governance endpoints listed in section 11.

No existing Session, Feedback, attachment, Review, Trace, Evidence, replica,
or management route becomes generally visible to authenticated employees.
Release 1 viewer routes are `GET` only; existing cloud Review mutation
remains disabled. R1 implements mandatory single-Agent scope enforcement for
its five Agent routes; it does not implement scoped aggregation or indirect
detail lookup. Root-to-login redirect, preview CSP isolation, and the explicit
same-origin residual-risk acceptance are also R1 gates. Unified chat is not
enabled in this release.

### Release 2: Agent management and grants

- Registry access modes;
- user, recursive department, and all-member grants;
- authenticated Agent directory;
- Session, Feedback, attachment, management detail, scoped aggregation, and
  observation-scope row authorization beyond the R1 allowlist;
- owner-only legacy Sessions and observation-scoped external Sessions; and
- preservation of current Review, Trace, Evidence, and flywheel behavior.

### Release 3: unified internal use

- standard chat and SSE protocol;
- one selected internal MetaBot Adapter;
- Platform-owned online message history;
- private MinIO uploads and generated attachments;
- identity-bound Feedback;
- archive and single-Session export; and
- complete emergency-erasure coverage and partial-result reporting.

### Release 4: second Agent shape

- AI ADMIN or another professional internal Agent;
- coexistence with its existing channel entry;
- cross-channel data normalization; and
- professional components only where the standard protocol is insufficient.

FAE remains an external-product management regression target throughout. It is
not moved into Release 3 or Release 4 internal usage.

The exact first MetaBot and the Release 4 Agent are selected before their
implementation plans; they do not block Release 1.

## 20. Compatibility and migration

All migrations are additive. `platform_replica` and current synchronized
source data are retained. Existing read APIs first gain authentication, then
actor-aware row filtering. Historical rows are not rewritten based on names.

The existing `platform_identity.refresh_dingtalk_matches()`
`exact_unique_name` result is retained only as a sanitized replica-side display
annotation for investigation. It never creates or selects an
`internal_user_id`, never owns a Session, and never participates in login,
authorization, grants, export, or audit actor resolution. Control-plane tests
must prove that a unique same-name annotation cannot affect any protected
decision.

The caller-supplied `X-Review-Actor` mechanism is removed from trusted use.
Review actors come only from the authenticated server Session. Compatibility
routes may remain temporarily, but no unauthenticated route can reach them.

Feature flags independently gate DingTalk auth, organization sync, grants,
chat, attachments, and each Adapter. No Release requires restarting or
changing FAE, ADMIN, or MetaBot until that Agent enters an explicit integration
batch.

## 21. Failure behavior

| Failure | Required behavior |
|---|---|
| DingTalk OAuth unavailable | Login fails; no password or anonymous fallback |
| Directory API unavailable | Last complete generation remains; partial data is discarded; freshness thresholds apply, including hard-stale privileged read-only continuity |
| Stream disconnected | Automatic reconnect and owner alert; reconciliation continues |
| Stream credential invalid or revoked | Connection reports hard failure; alert owner; reconciliation continues; no synthetic events |
| Control database unavailable | `503`; no authorization bypass |
| Authorization data invalid | Default deny |
| Internal Agent unavailable | Explicit failure; no Agent/model fallback |
| Signing key unavailable | Downstream call denied |
| Clock offset above limit | Alert above two seconds; readiness and signing fail closed above five seconds |
| Required audit write unavailable | Sensitive action fails |
| MinIO unavailable | Attachment operations return `503`; chat without attachments may continue |
| Attachment scan unavailable | File remains quarantined |
| WAL archive lag, failure, or low free space | Pause replica import first; alert and expose RPO status; shed nonessential high-write work before control integrity is threatened; never report the 15-minute RPO as healthy while breached |

## 22. Backup and recovery

The shared PostgreSQL cluster containing `agent_platform_control` and the
rebuildable `agent_platform` replica receives:

- daily encrypted base backup with a 35-day rolling retention;
- continuous encrypted WAL archive covering every retained base backup, with
  target RPO no greater than 15 minutes;
- keys stored separately from backups; and
- a quarterly restore drill.

The 15-minute RPO is a control-plane requirement even though physical WAL
covers the entire cluster. A direct in-place cluster PITR is not used for a
control-only incident because it would also roll back the current replica.

### 22.1 Control-only point-in-time recovery

The tested control-only recovery sequence is:

1. enter maintenance mode, stop control mutations, pause replica imports, and
   preserve the current production cluster and MinIO state;
2. restore the physical base backup and WAL to the selected timestamp on an
   isolated recovery cluster;
3. validate control schema versions, identities, roles, grants, ownership,
   audit continuity, and row counts on that isolated cluster;
4. logically dump only `agent_platform_control` in custom format without
   cluster-owner credentials and restore it into a new temporary control
   database on production;
5. validate the temporary database, stop application control connections,
   quarantine the previous control database, and rename the validated database
   to the canonical control name; the current replica database is not replaced;
6. reconcile MinIO objects and derived artifacts against restored metadata,
   keeping any unverifiable attachment quarantined;
7. reapply post-target Session revocations and emergency-erasure records from
   the preserved current control database when readable; if their application
   cannot be proven, affected restored content remains quarantined for owner
   review; and
8. revoke every restored Web Session, run authorization and referential
   integrity checks, then leave maintenance mode.

A whole-cluster disaster instead restores both databases physically and then
rebuilds the replica from approved sources as needed. The quarterly restore
drill must exercise the isolated-cluster and logical control-database sequence,
not only prove that PostgreSQL can start from a base backup.

### 22.2 WAL protection from replica imports

Replica rebuild and `sync_remote` import share the control cluster's WAL path,
so replica work is always lower priority than control durability. Imports use
bounded batches, a configured WAL budget, and continuous checks of archive
failure count, oldest unarchived WAL age, archive throughput, `pg_wal` usage,
and filesystem free space.

Initial operational thresholds are:

| Condition | Required action |
|---|---|
| Archive age at least 5 minutes or free space below 25% | Warning; reduce replica-import concurrency |
| Archive age at least 10 minutes or free space below 20% | Pause replica imports and other rebuild jobs; page the owner |
| Archive age above 15 minutes | Declare the control RPO breached until archive catches up; keep replica imports stopped |
| Free space below 10% | Reject new Agent runs, uploads, and exports; preserve authentication, audit, revocation, and recovery writes |
| Free space below 5% | Control API enters `503` protective mode except minimal health and offline recovery |

Thresholds are configuration but cannot be loosened past the documented RPO
without a new reviewed operational decision. A replica import never deletes
WAL, disables archiving, or increases retention pressure silently.

## 23. Security and backend test requirements

Automated tests cover:

- forged, replayed, expired, and cross-environment OAuth state;
- root `302` login redirect without an open return URL;
- non-Orbbec, inactive, departed, and reactivated users;
- exact public-route allowlisting and authentication on every other route;
- Session fixation, CSRF, logout, and revoked Cookie reuse;
- ignored frontend user, role, department, Agent, and upstream claims;
- direct management URL and API access by a member;
- Release 1 gate behavior for member, viewer, and owner;
- exact R1 viewer endpoint allowlisting, mandatory single-Agent parameter,
  observation-scope isolation, omitted/unscoped parameter denial, aggregate
  endpoint denial, detail endpoint denial, and write denial;
- unauthorized Agent use;
- cross-user and cross-Agent Session access;
- cross-user attachment, Feedback, Review, and export access;
- preview inability to read production control data;
- owner departure, Session revocation, and break-glass replacement refusal and
  success paths;
- Stream duplicate, out-of-order, disconnect, reconnect, and invalid-credential
  behavior;
- full-sync partial failure, atomic-generation preservation, and exact 8-hour
  warning and 24-hour hard-stale boundary behavior;
- hard-stale member denial, previously bound owner/viewer reauthentication,
  privileged read-only management continuity, mutation denial, and stale-
  generation owner replacement followed by restricted access;
- closure-table generation activation and absence of request-time recursion;
- proof that replica same-name annotations cannot influence control identity or
  authorization;
- server-generated external Session IDs and non-distinguishable collision
  handling;
- SSE duplicate and out-of-order sequence handling, resume, storage bounds, and
  expired-replay `410` behavior;
- login, mutation, upload, and SSE rate and concurrency limits;
- corporate-NAT login bursts, login-attempt and OAuth-state backoff, coarse
  edge-IP ceilings, and global provider circuit breaking;
- forged forwarding headers, direct untrusted peers, loopback Nginx header
  overwrite, and explicitly configured future proxy CIDRs;
- HMAC, envelope-encryption, and Ed25519 rotation with old/new acceptance
  windows and unknown-version denial;
- downstream token issuer, audience, Agent, expiry, and signature validation;
- downstream token clock-leeway and fail-closed host-clock behavior;
- Adapter retry denial when idempotency is not declared;
- MinIO outage and quarantine behavior;
- emergency erasure of canonical content, active checkpoint writers,
  `run_events`, objects, previews, OCR/extracts, indexes, download tickets, and
  materialized exports, including downstream unsupported and retryable partial
  outcomes;
- required audit failure semantics;
- viewer revocation, persistence of its governance audit record, immediate
  former-viewer access denial, and the documented absence of independent
  phase-one oversight;
- control-only PITR through an isolated cluster and logical database restore;
- replica-import WAL warning, pause, RPO-breach, and low-space protective
  thresholds;
- Nginx preview behavior: Basic Auth disabled only under the exact prefix,
  authenticated POST works, non-upload bodies remain 1 MB, upload is 50 MB,
  a 300-second backend run is not cut off by the proxy, client forwarding
  headers are overwritten, and inbound Authorization is not proxied;
- preview CSP blocks a production API fetch from preview execution context,
  while sanitized Markdown cannot introduce raw HTML or executable URLs; and
- absence of provider IDs, AppSecret, tokens, and Cookies from API and logs.

## 24. Real DingTalk acceptance

Using a real test-member scope before broad publication:

1. in-client login succeeds;
2. browser QR login succeeds;
3. both map to one `internal_user_id`;
4. a person outside application visibility cannot enter;
5. department movement changes Agent access;
6. disabling a test member invalidates existing Web Sessions;
7. Stream reconnects after a controlled disconnect;
8. current organization full reconciliation completes within ten minutes under
   normal conditions and never exceeds a 15-minute hard acceptance deadline;
9. a targeted organization event is reflected within 60 seconds at p95, and a
   departure revokes Sessions within 30 seconds at p95 after Platform receives
   the event;
10. six-hour scheduling, the 8-hour warning threshold, and the 24-hour
    hard-stale threshold are demonstrated without promoting partial data;
    member access is denied while a previously bound owner and viewer retain
    only the documented read-only management access;
11. owner access to a FAE Session creates an audit record;
12. a scoped viewer can use only the exact R1 allowlist for one authorized
    Agent at a time, cannot access fleet, brief, detail, or combined endpoints,
    cannot mutate, and can inspect only sanitized governance audit metadata;
13. a member cannot read FAE or any other management data in Release 1; and
14. a shared corporate-NAT load test stays below the coarse edge ceiling while
    per-attempt abuse is throttled, and upload and SSE limits return the
    documented explicit failures under controlled load;
15. spoofed forwarding headers do not change the trusted edge address;
16. unauthenticated `GET /` returns the documented login redirect; and
17. preview CSP blocks a scripted production management fetch in a clean
    acceptance browser profile.

## 25. Production cutover and rollback

Before cutover:

- back up PostgreSQL, Nginx, current image, and the Basic Auth credential file;
- inventory current shared-Basic-Auth dashboard users, bind approved people as
  owner or scoped viewer, and explicitly accept that anyone not bound loses
  dashboard access at cutover;
- run migrations and the candidate on an isolated loopback listener;
- complete automated and real DingTalk acceptance through `/_preview/`;
- add the preview redirect URL alongside the formal DingTalk callback; do not
  replace the formal callback;
- verify 8000, 8080, MinIO, and PostgreSQL remain closed to the public;
- verify the exact preview Nginx location, 1 MB default body limit, 50 MB upload
  override, 360-second proxy timeouts, public-route allowlist, trusted-header
  overwrite, stripped candidate Authorization, and rate limits;
- verify path-restricted preview CSP and run acceptance from a clean browser
  profile without cached production Basic Auth or production Cookies;
- complete the initial isolated-cluster control-only PITR rehearsal and verify
  replica-import WAL pause thresholds before removing Basic Auth;
- set the DingTalk homepage to the formal root for publication; and
- prepare exact Nginx and application rollback commands.

Cutover atomically changes the root upstream and replaces Nginx Basic Auth with
application-level DingTalk authentication only after the candidate is proven.
Preview routes are then disabled.
The preview callback is removed only after candidate Sessions are revoked and
the formal callback has passed login acceptance.

Rollback restores the old image, old upstream, and Basic Auth. It does not drop
new control data. All candidate Web Sessions are revoked. Audit and failure
evidence are retained. FAE, ADMIN, and MetaBot services are not restarted as
part of Platform rollback.

## 26. Documentation references

- DingTalk developer tutorial index: <https://open.dingtalk.com/tutorial/>
- DingTalk Stream overview:
  <https://opensource.dingtalk.com/developerpedia/docs/learn/stream/overview/>
- DingTalk Stream event subscription workflow:
  <https://opensource.dingtalk.com/developerpedia/docs/explore/tutorials/stream/event/go/subscribe-topic/>

## 27. Next step

After this design is reviewed, create a separate TDD implementation plan for
Release 1 only. Do not combine all four releases into one deployment or expose
the current loopback Platform before DingTalk authentication and backend
authorization pass acceptance.
