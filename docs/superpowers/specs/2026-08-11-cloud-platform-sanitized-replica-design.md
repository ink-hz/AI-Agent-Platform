# Cloud Platform Sanitized Replica Design

**Date:** 2026-08-11  
**Status:** Approved for implementation  
**Owners:** Orbbec AI Agent Platform / MetaBot operations

## 1. Goal

Deploy the complete read-only AI Agent Platform on the existing Alibaba Cloud
host while keeping the local Mac and Flywheel database as the only source of
truth. The cloud Platform must display the current Agent catalog, internal user
names, Sessions, Turns, final questions and answers, attachment metadata,
usage, execution status, and aggregate operations state from an irreversible
sanitized replica.

The first release is reachable only through an SSH tunnel. It does not expose
a public Platform route and does not depend on Feishu or DingTalk. A later
release may add `agent.orbbec.com.cn`, `fae.orbbec.com.cn`, HTTPS, and a
pluggable enterprise identity provider without changing the replica design.

## 2. Explicit non-goals

- Do not move the source-of-truth Flywheel database to the cloud.
- Do not give the cloud host a route or credential back to the local Mac,
  Flywheel, MetaBot, MinIO, SeaweedFS, or attachment directories.
- Do not upload original attachment bytes.
- Do not upload raw provider IDs, object keys, file paths, system prompts,
  internal tool payloads, full traces, or exception stacks.
- Do not provide Agent restart, release, replay, feedback mutation, Review
  mutation, attachment download, or other control-plane actions.
- Do not modify, rebuild, restart, or migrate the existing FAE service.
- Do not change the existing public Nginx route in the first release.
- Do not add public ports in the first release.
- Do not implement Feishu or DingTalk login in the first release.

## 3. Confirmed production context

The target host is Ubuntu 24.04 with Docker 29, Docker Compose 2.40, active
Nginx and approximately 43 GiB free disk. The existing FAE container is
`ai-fae-backend`, is healthy, and binds only `127.0.0.1:8000`. Nginx currently
listens on public port 80 and proxies to that FAE listener.

The cloud Platform uses a distinct loopback listener:

```text
127.0.0.1:8080  AI Agent Platform
127.0.0.1:8000  existing FAE, unchanged
```

The local Platform remains available during rollout. The cloud deployment is a
read-only replica and can be rebuilt from the local source after a total loss.

## 4. Architecture

```text
Local Mac / Flywheel (source of truth)
  |
  | restricted read-only views
  v
Cloud replica exporter
  |
  | deterministic sanitization + sensitive dictionary + fail-closed gate
  | stable HMAC identities + canonical batch + SHA-256 + Ed25519 signature
  v
restricted SSH forced command (local initiates every connection)
  |
  v
Cloud importer
  |
  | signature / sequence / freshness / schema / size validation
  | one PostgreSQL transaction, idempotent event keys
  v
Dedicated cloud PostgreSQL
  |
  +--> Platform API and built Web UI on 127.0.0.1:8080
  |
  +--> encrypted daily backup and restore drill

Administrator browser
  |
  +--> SSH tunnel --> 127.0.0.1:8080
```

The normal synchronization interval is five minutes. A failed push remains in
a private local queue and retries with bounded exponential backoff. The cloud
keeps the last committed snapshot and displays its exact last-success time and
freshness state.

## 5. Trust boundaries

### 5.1 Local boundary

Only the local exporter can read source views and raw display content. It runs
as `neo`, loads secrets only from mode-0600 regular files below a mode-0700
private directory, and writes its queue below a mode-0700 local state root.

The following keys are separate:

- HMAC key for stable cloud-safe identity IDs;
- Ed25519 private key for batch signatures;
- restricted SSH private key for transport.

None is reused as a MetaBot, database, attachment, Feishu, DingTalk, or cloud
administrator credential.

### 5.2 Transport boundary

The exporter connects to a dedicated `platform-sync` Unix account using a
restricted SSH key. Its `authorized_keys` entry has a forced importer command,
disables PTY, agent forwarding, port forwarding, X11 forwarding, and user
shell commands. The importer accepts a bounded batch on stdin. No secret or
payload is passed in argv.

The cloud never initiates a network connection to the local Mac. The existing
administrator SSH key remains separate and is used only for deployment and the
temporary browser tunnel.

### 5.3 Cloud boundary

Platform and PostgreSQL use a dedicated Compose project and directories. The
database is visible only on the private Compose network. The Platform container
runs as a non-root user with a read-only root filesystem, dropped Linux
capabilities, no Docker socket, and only explicitly mounted state paths.

## 6. Cloud replica data contract

### 6.1 Exported fields

The replica may contain:

- stable cloud-safe Agent ID, display name, department and current category;
- stable cloud-safe user ID and approved manual employee display name;
- Session and Turn stable IDs, timestamps, order, status and source category;
- sanitized user-visible question and final Agent answer;
- attachment category, media type, byte-size bucket and archive/delivery state;
- tool category names without arguments, outputs, source coordinates or URLs;
- token counts, duration, model family, completion status and stable error class;
- aggregate fleet, usage, freshness and lifecycle metrics;
- cloud import sequence, source watermark and sanitizer policy version.

The employee display name is a necessary internal reporting field. Raw Feishu
or DingTalk identifiers never leave the local host. The stable cloud user ID is
`HMAC-SHA-256(local_provider_scope || raw_id)` with a local-only key.

### 6.2 Permanently excluded fields

The exporter never emits:

- raw provider, chat, message, file or user IDs;
- phone numbers, email addresses, identity numbers or detailed addresses;
- customer names, candidate names, project codes or unreleased product codes;
- customer design detail or source document excerpts classified as restricted;
- attachment bytes, original filenames, object keys, local paths or tickets;
- credentials, cookies, authorization headers, signed URL components or query
  parameters;
- system/developer prompts, knowledge file paths, raw tool input/output, full
  engineering traces or exception stacks;
- source database DSNs, topology details or internal listener coordinates.

### 6.3 Stable placeholders

Sensitive entities use stable placeholders within one Session, such as
`[客户1]`, `[候选人1]`, `[项目1]`, `[地址1]`, `[链接1]` and `[附件1]`. The
mapping exists only in memory while sanitizing one Session and is never stored
or exported. Placeholders preserve conversational relationships without
allowing cloud-side recovery.

### 6.4 Attachment representation

No attachment bytes are transferred. A cloud attachment row includes only a
safe category, media type, coarse byte-size bucket, source/generated category,
archive state and delivery state. Its display label is `附件 1`, `附件 2`, and
so on within the Turn. Download and preview endpoints are disabled in cloud
mode and return a stable forbidden response.

## 7. Sanitization pipeline

Sanitization happens before canonical serialization or queue persistence:

1. Normalize Unicode, control characters, whitespace and Markdown links.
2. Remove secrets, credentials, local paths, provider coordinates, object keys,
   signed URLs and query strings using deterministic rules.
3. Replace structured personal data such as phone, email, identity number and
   detailed address.
4. Replace entities from a private enterprise dictionary containing customer,
   candidate, project and unreleased product aliases.
5. Apply source-specific rules. HR candidate material and customer design
   material receive the strictest classification.
6. Run a post-sanitization detector over the result.
7. If the detector reports unresolved sensitive content, omit both message
   bodies for that Turn and emit only `内容因敏感性未同步`, timestamps and safe
   aggregate metadata.

The pipeline never treats an AI classifier as proof that content is safe.
Deterministic rules and the private dictionary are authoritative. A classifier
may only increase sensitivity or force omission; it cannot override a positive
deterministic match.

Every exported message records the sanitizer policy version and a SHA-256 of
the sanitized value. Synthetic canaries for every forbidden data class must be
rejected or replaced in tests and in a production preflight batch.

## 8. Incremental synchronization protocol

### 8.1 Batch envelope

Each canonical JSON Lines batch contains:

- protocol and schema version;
- sanitizer policy version;
- source instance ID;
- strictly increasing sequence number;
- creation time and expiry time;
- previous committed batch digest;
- lower and upper source watermarks;
- record count and uncompressed byte count;
- SHA-256 content digest;
- Ed25519 signature over the header and content digest.

The importer rejects unknown versions, invalid signatures, expired batches,
unexpected predecessors, sequence rollback, duplicate IDs with different
content, oversized records, excessive batch size and invalid UTF-8.

### 8.2 Atomic import

All records and the new watermark commit in one PostgreSQL transaction. An
exact replay is a successful no-op. A partial or conflicting replay fails
without advancing the watermark. Cloud APIs read only committed generations.

### 8.3 Initial backfill

The first backfill includes at most one year of in-scope data and uses the same
sanitizer and importer as incremental synchronization. It runs in bounded
batches and produces aggregate counts only. Samples are compared locally using
safe IDs and sanitized hashes; raw source content is not printed in acceptance
logs.

### 8.4 Freshness

The cloud exposes `current`, `stale` and `unavailable` replica freshness:

- current: last successful source watermark is no more than 15 minutes old;
- stale: last success is older than 15 minutes but a committed snapshot exists;
- unavailable: no valid snapshot exists.

Staleness never deletes or replaces the last successful data and never creates
a false healthy conclusion.

## 9. Cloud application behavior

Cloud mode uses the existing Platform UI and read APIs with a replica-backed
repository. It preserves Agent, Sessions, Session detail, question/answer,
attachment metadata, usage and operations views.

Cloud mode explicitly disables:

- all Review writes and feedback replay;
- Agent restart, deployment and control actions;
- source synchronization that pulls from FAE or ADMIN;
- local loopback MetaBot probing;
- attachment byte proxy and download tickets;
- any fallback to a local or remote source database.

Fleet health shown in cloud mode is the last sanitized snapshot pushed from the
local monitor. FAE public health may be shown separately, but it cannot replace
the source watermark or make stale replica data current.

## 10. Authentication and authorization

The first release binds only to loopback and is accessed through an
administrator SSH tunnel. There is no anonymous public route.

The application defines an `AuthProvider` boundary for a later DingTalk or
Feishu implementation. When public mode is enabled, the authorization model is:

- administrator: all approved Agent and Session views;
- department owner: only explicitly assigned Agents and Sessions;
- internal user: only Sessions mapped to their cloud-safe user ID.

HR is a separate permission domain and is never implied by another department
owner role. The application defaults to deny for missing identity, missing
assignment, missing role mapping or an unavailable identity provider.

## 11. Storage, encryption and retention

The dedicated PostgreSQL volume contains only sanitized replica data. Sensitive
display columns use application-level envelope encryption so a copied database
volume or backup does not expose plaintext without the separate key file. Key
files are root-owned, mode 0600 and mounted read-only only where required.

Daily database backups are encrypted to a dedicated recovery recipient before
leaving the database container. A restore drill verifies schema, row counts and
sanitized content hashes. Backup logs contain aggregate evidence only.

Session, Turn, message and attachment metadata expire one year after their
source event time. Expiry removes encrypted display data and identity links.
Irreversible, non-personal aggregate metrics may remain. Retention is applied
both to the live replica and backup generations.

## 12. Deployment layout

The cloud host uses these isolated roots:

```text
/opt/orbbec-agent-platform/releases/COMMIT_SHA/
/opt/orbbec-agent-platform/current
/etc/orbbec-agent-platform/
/var/lib/orbbec-agent-platform/postgres/
/var/lib/orbbec-agent-platform/operations/
/var/lib/orbbec-agent-platform/backups/
/var/log/orbbec-agent-platform/
```

Images are immutable and tagged by reviewed Git commit. Deployment verifies an
artifact manifest, builds or loads the new image, applies explicit replica-only
migrations, starts the candidate on a private validation listener, runs health
and synthetic sanitizer/import tests, then changes the `current` release. The
production listener remains `127.0.0.1:8080`.

The initial deployment does not edit Nginx. A later domain release will add
exact host routes for `agent.orbbec.com.cn` and `fae.orbbec.com.cn`, with HTTPS
and SSO, while leaving the website domains unchanged.

## 13. Rollback

Before mutation, deployment records the FAE container ID, image, start time,
health payload digest, Nginx configuration digest, public listeners and current
Platform state.

On Platform failure:

1. stop the candidate Platform containers;
2. restore the previous immutable Platform release pointer;
3. restore the replica database snapshot only when a migration is not backward
   compatible;
4. leave the local export queue intact for retry;
5. verify the previous Platform or the known absent state;
6. re-check every recorded FAE and Nginx invariant.

Rollback never deletes or modifies FAE data, containers, images, Nginx routes,
Langfuse services or local source data. Because the cloud database is a
replica, it may be discarded and rebuilt only through an explicit recovery
operation after encrypted backup evidence is retained.

## 14. Release sequence

1. Capture read-only cloud and local baseline facts.
2. Add cloud replica schema, repositories and cloud-mode feature gates.
3. Add deterministic sanitizer, private dictionary loader and fail-closed
   post-detector.
4. Add canonical signed batch exporter and transactional importer.
5. Add restricted SSH forced-command installer and local five-minute scheduler.
6. Add cloud Compose, immutable deploy, rollback, backup and restore tooling.
7. Run all local backend and Web UI tests.
8. Deploy Platform and database without changing Nginx.
9. Run a synthetic canary import, then the one-year sanitized backfill.
10. Verify through an SSH tunnel and enable incremental synchronization.
11. Re-check FAE, Nginx, public listeners and unrelated containers.

## 15. Acceptance criteria

The release is accepted only when all conditions hold:

1. existing backend and Web UI suites pass;
2. sanitizer tests cover every forbidden data class and stable placeholder
   behavior;
3. production synthetic canaries do not appear in the cloud database, API,
   rendered HTML, logs or backups;
4. invalid signature, replay, gap, expiry, size and schema batches fail closed;
5. exact batch replay creates no duplicate rows;
6. injected import failure leaves the prior generation and watermark unchanged;
7. the one-year backfill reconciles safe aggregate counts and hashes;
8. Sessions and Turn order match the local source for sampled safe IDs;
9. attachments expose metadata only and every byte/download route is forbidden;
10. cloud mode exposes no Review, replay, restart, release or control mutation;
11. no Platform, PostgreSQL or importer port is publicly reachable;
12. SSH-tunnel UI and all required read APIs are healthy;
13. stale synchronization preserves the last snapshot and visibly marks it;
14. encrypted backup and restore drill succeed;
15. FAE container ID, image, start time and health payload are unchanged;
16. Nginx configuration digest and existing public listener set are unchanged;
17. local source databases, attachments and MetaBot processes are unchanged;
18. deployment, synchronization, backup, restore and rollback request no
   interactive password or credential UI.

## 16. Later domain and identity release

After DNS is ready:

- `agent.orbbec.com.cn` and `fae.orbbec.com.cn` receive exact DNS records;
- Nginx uses distinct host-based routes and HTTPS certificates;
- `agent.orbbec.com.cn` proxies to the loopback Platform listener;
- `fae.orbbec.com.cn` proxies to the existing FAE listener;
- DingTalk or Feishu is implemented behind `AuthProvider`;
- the three-role authorization model and HR isolation are enabled before public
  access;
- temporary SSH-tunnel mode remains available only for break-glass operations.

This later release does not change the sanitizer, replica schema, signing,
retention, backup or one-way synchronization protocol.
