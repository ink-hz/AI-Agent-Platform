# DingTalk Identity and Unified Internal Agent Entry Design

**Status:** Approved design baseline

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
- one platform owner and ordinary members;
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
- multiple administrator levels;
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
- sanitized FAE management data remains available to `platform_owner`;
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

## 5. Data domains

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

### 5.3 Identity namespaces

Platform distinguishes:

```text
internal_user    Orbbec employee bound to DingTalk
external_subject source-scoped pseudonymous user from an external product
legacy_unknown   historical actor without a trustworthy stable mapping
```

Historical Sessions are not assigned to employees by name, mobile number, or
other guesswork. A legacy or external Session without a verified internal owner
is visible only to `platform_owner`.

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
- keyed HMAC lookup values for exact matching and uniqueness.

Names are display data only. Phone number, email, title, avatar, job number,
and unrelated profile fields are not synchronized.

### 7.2 Roles

The only roles are:

- `platform_owner`; and
- `member`.

A database constraint permits at most one owner. The first person to log in is
not automatically promoted. An offline, audited operator command binds the
owner role to a previously verified stable DingTalk identity. Frontend role,
name, mobile-number, and department values are ignored.

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
Department events trigger a targeted branch refresh and authorization
recalculation.

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
events maintain freshness between runs. If the last successful full
reconciliation is older than six hours:

- new login is refused;
- Agent grant changes are refused;
- current Sessions continue only for the last confirmed active users; and
- the owner sees a persistent degraded-state warning.

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

## 11. Permission matrix

| Operation | `member` | `platform_owner` |
|---|---|---|
| View internal Agent directory | Granted Agents only | All Agents |
| Use an internal Agent | Granted Agents only | All internal Agents |
| Create an internal Session | Self and granted Agent | Allowed |
| Read or continue a Session | Own Session only | All Sessions |
| Read attachments | Own Session only | All, audited when cross-user |
| Submit and view Feedback | Own only | All |
| Trace and Evidence | Own allowed presentation only | All |
| Review and repair closure | No | Yes |
| FAE external-product management data | No | Yes |
| Agent grant management | No | Yes |
| Organization and audit administration | No | Yes |
| Prompt, model, tool, or Agent release editing | No | Not provided in phase one |

Opening another user's Session, attachment, Feedback, Trace, Evidence, or
export always records a management-view audit event.

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

The native uniqueness boundary is `(agent_id, external_session_id)`. Browser
random IDs are external identifiers, never access credentials. Query,
continuation, Feedback, attachment, archive, and export all recheck the stored
owner, Agent, and current Agent grant. Revoking a member's Agent access also
hides that Agent's prior internal Sessions from the member; the data remains
available to the owner and the retention process.

Ordinary users cannot hard-delete Sessions. They may archive their own Session.
Messages, Feedback, and attachments are retained for one year and removed by a
central retention job. The flywheel may retain a necessary, non-content,
sanitized audit outcome after source content expires.

The first release supports single-Session HTML and JSON export:

- a member may export an owned Session;
- the owner may export any one Session after supplying an internal purpose;
- bulk export is not provided;
- owner export of another user's data is audited; and
- attachment links in exports are short-lived rather than public permanent
  URLs.

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
POST /api/v1/agents/{agent_id}/sessions
GET  /api/v1/sessions
GET  /api/v1/sessions/{session_id}
POST /api/v1/sessions/{session_id}/messages
POST /api/v1/runs/{run_id}/cancel
POST /api/v1/messages/{message_id}/feedback
POST /api/v1/sessions/{session_id}/attachments
GET  /api/v1/attachments/{attachment_id}/download
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
run.interrupted
run.failed
run.completed
heartbeat
```

Events include request ID, run ID, monotonic sequence, and timestamp.
Reconnect resumes after the last accepted sequence and does not create a new
run. Raw chain-of-thought is never transmitted. Progress events contain only
safe user-facing stage descriptions.

The initial presentation types are user text, Assistant Markdown, progress,
Evidence, simple tables, images, user attachments, generated attachments,
explicit errors, and Feedback.

### 14.4 Adapter contract and reliability

An Adapter declares:

```text
streaming
attachments_in
attachments_out
evidence
tables
cancellation
max_duration
max_attachment_size
```

Registry configuration, not browser input, selects the upstream URL and
Adapter. Calls use an idempotency key. A connection failure may be retried once
only before the Agent has started output. Once output begins, an interrupted
run is not silently replayed. SSE emits a heartbeat every 15 seconds. Default
maximum duration is 300 seconds; longer work later uses an asynchronous job
protocol. The gateway never switches to another Agent or model as a fallback.

## 15. Attachments

Platform durably stores both user uploads and Agent-generated files. An Agent
local path is never treated as a durable download result.

Private MinIO is used with:

- no public S3 or management listener;
- random object keys;
- application-level envelope encryption;
- sanitized display filenames;
- SHA-256, MIME, size, source, Session, and owner metadata;
- a 50 MB per-file limit;
- executable-file rejection; and
- quarantine and malware scanning before use or download.

Every upload, preview, and download rechecks Session ownership. Downloads use a
single-use ticket valid for no more than 60 seconds. MinIO addresses and
permanent presigned URLs are never returned to the browser. Owner download of
another user's attachment is audited.

Objects and sensitive metadata expire after one year. Deletion failures retry
and surface as operations alerts. Backup and restore verify object/database
referential consistency.

## 16. Audit model

Audit covers:

- login success, failure, and logout;
- user disablement and Session revocation;
- Agent grant creation and revocation;
- owner access to another user's or an external subject's content;
- cross-user attachment download and Session export;
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
https://agent.orbbec.com.cn/_preview/dingtalk-r1/api/auth/dingtalk/callback
```

Nginx keeps `/` on the current Basic-Auth-protected production release and
routes only the exact `/_preview/` namespace to an isolated candidate
listener. Preview uses a separate database, MinIO bucket, Cookie name, signing
key, OAuth state namespace, and test-member scope. It does not read production
control data.

Because preview and production share an origin:

- authentication is never stored in localStorage;
- preview and production use distinct Host-only Cookie names and signing keys;
- no Service Worker is registered;
- preview OAuth state is environment-bound;
- CSP and exact proxy routing prevent fallback into another backend; and
- preview is removed and its Sessions revoked immediately after cutover.

The DingTalk application homepage temporarily points to preview during real
acceptance. The formal homepage and callback are restored before production
publication. Public and internal DNS and company proxy configuration are
required only for `agent.orbbec.com.cn`, not for every Agent.

## 19. Incremental releases

### Release 1: identity security foundation

- `platform_control` migrations and database roles;
- DingTalk QR and in-client login;
- internal identity mapping;
- unique owner binding;
- Web Session, CSRF, and whole-site backend authentication;
- Stream and six-hour organization synchronization;
- audit logging; and
- owner-only access to existing FAE and other management data.

Unified chat is not enabled in this release.

### Release 2: Agent management and grants

- Registry access modes;
- user, recursive department, and all-member grants;
- authenticated Agent directory;
- Session, Feedback, attachment, and management row authorization;
- owner-only legacy and external Sessions; and
- preservation of current Review, Trace, Evidence, and flywheel behavior.

### Release 3: unified internal use

- standard chat and SSE protocol;
- one selected internal MetaBot Adapter;
- Platform-owned online message history;
- private MinIO uploads and generated attachments;
- identity-bound Feedback;
- archive and single-Session export.

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
| Directory API unavailable | Last complete generation remains; partial data is discarded |
| Stream disconnected | Automatic reconnect and owner alert; reconciliation continues |
| Control database unavailable | `503`; no authorization bypass |
| Authorization data invalid | Default deny |
| Internal Agent unavailable | Explicit failure; no Agent/model fallback |
| Signing key unavailable | Downstream call denied |
| Required audit write unavailable | Sensitive action fails |
| Attachment scan unavailable | File remains quarantined |

## 22. Backup and recovery

`platform_control` receives:

- daily encrypted base backup;
- continuous encrypted WAL archive with target RPO no greater than 15 minutes;
- keys stored separately from backups; and
- a quarterly restore drill.

A restore verifies users, identities, grants, ownership, audit, and MinIO
referential integrity. All restored Web Sessions are revoked and users log in
again. `platform_replica` remains separately rebuildable from approved source
data.

## 23. Security and backend test requirements

Automated tests cover:

- forged, replayed, expired, and cross-environment OAuth state;
- non-Orbbec, inactive, departed, and reactivated users;
- Session fixation, CSRF, logout, and revoked Cookie reuse;
- ignored frontend user, role, department, Agent, and upstream claims;
- direct management URL and API access by a member;
- unauthorized Agent use;
- cross-user and cross-Agent Session access;
- cross-user attachment, Feedback, Review, and export access;
- Stream duplicate, out-of-order, disconnect, and reconnect behavior;
- full-sync partial failure and atomic-generation preservation;
- downstream token issuer, audience, Agent, expiry, and signature validation;
- required audit failure semantics; and
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
8. six-hour reconciliation and stale-directory behavior are demonstrated;
9. owner access to a FAE Session creates an audit record; and
10. a member cannot read FAE management data.

## 25. Production cutover and rollback

Before cutover:

- back up PostgreSQL, Nginx, current image, and the Basic Auth credential file;
- run migrations and the candidate on an isolated loopback listener;
- complete automated and real DingTalk acceptance through `/_preview/`;
- verify 8000, 8080, MinIO, and PostgreSQL remain closed to the public;
- switch the DingTalk homepage and callback to the formal root; and
- prepare exact Nginx and application rollback commands.

Cutover atomically changes the root upstream and replaces Nginx Basic Auth with
application-level DingTalk authentication only after the candidate is proven.
Preview routes are then disabled.

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
