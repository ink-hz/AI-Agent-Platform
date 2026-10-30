# Cloud Agent sync: controlled installation, cutover and rollback

This is a host-root deployment procedure for the exact reviewed release. It
does not authorize production evals, model calls or source business writes.
The worker asynchronously reads FAE/DAQ/ADMIN/VOC and publishes encrypted
replicas. HR/SDK remain native and office sources retain their existing path.

## Boundary and release inputs

Use a clean release directory from the accepted merged commit. Record its SHA,
image digest, migration checksum, encrypted backup receipt and ownership rows
before changing anything. Use an immutable digest or a release tag that the
registry forbids overwriting; never use `latest`. Set `PLATFORM_CLOUD_SYNC_IMAGE`
to that image. The image must include `app.cloud_sync.cli` and its `once`, `run`,
`status`, and `health` commands.

The Compose project is separately named `orbbec-agent-cloud-sync`. Its
`cloud-sync` profile is disabled by default. The only bind mount is
`/opt/orbbec-agent-platform/private/cloud-agent-sync`, read-only inside the
10001:10001 worker. Parent host directories must remain root-controlled. No
ports, host networking, Docker socket, API secret volume, office signing key or
global control credentials belong in this worker. No source credentials or
identity key may be mounted into the API/frontend.

| Source | Docker container / private DNS | DB | Source profile schema | SQL admin |
| --- | --- | --- | --- | --- |
| FAE | `ai-fae-postgres` | `ai_fae` | `public` | `ai_fae` |
| DAQ | `ai-fae-postgres` | `ai_fae` | `daq_fae` | `ai_fae` |
| ADMIN | `ai-fae-postgres` | `ai_admin` | `public` | `ai_fae` |
| VOC | `orbbec-voc-postgres-1` | `orbbec_voc` | `voc_raw/voc_core` | `postgres` |
| Replica | `orbbec-agent-platform-platform-postgres-1` / `platform-postgres` | `agent_platform` | `platform_replica` | `platform_owner` |

Source DSNs use private DNS and port5432, never the source host port5434. VOC
also grants read access to the required `voc_meta.schema_migrations.name`
installation marker. Do not use a generic `postgres` DNS alias across the three
attached networks. Verify the existing external networks are exactly
`deploy_default`, `orbbec-voc_voc_private`, and
`orbbec-agent-platform-internal`; do not recreate or modify shared networks.

## Install without starting the worker

1. Take and verify the existing encrypted platform database/config backup using
   the reviewed backup procedure (`deploy/cloud/backup.sh`, then
   `deploy/cloud/backup.sh --check`). Separately preserve encrypted source-role
   ACL/config receipts and any existing worker private directory. The existing
   backup tool's fixed scope must be checked; do not assume a new worker bind
   directory is included. Never leave plaintext DB dumps or secret archives.
   Record current `agent_sources` rows and office `source_instance_id` values in
   the private deployment receipt. Drain/check previously queued office batches.
2. Apply the reviewed migration as replica superuser, after backup:

   ```sh
   docker exec -i orbbec-agent-platform-platform-postgres-1 \
     psql -X --single-transaction -v ON_ERROR_STOP=1 -U platform_owner -d agent_platform \
     < backend/migrations/cloud_agent_sync/001_agent_sources.sql
   ```

   This migration installs DB-enforced writer ownership and the controlled
   `platform_replica.set_agent_publisher` function. Installing it takes over no
   agent. Release the updated office import/export path before ownership changes;
   it must skip cloud-owned sessions, keep office management projections, and
   scope office expiry to the office source instance.
3. As host root, provision dedicated roles and private configuration:

   ```sh
   python3 deploy/cloud/provision-agent-sync.py provision
   ```

   It creates stable, separate FAE/DAQ/ADMIN/VOC read-only passwords and a replica
   login `platform_cloud_sync_worker` belonging only to the migration-owned
   `platform_cloud_sync` group. Source logins have no memberships, no object
   ownership, and only the adapter's required column SELECT grants. Repeated
   provisioning reuses private credentials, reconciles old grants, and never
   rotates passwords or assigns ownership. Existing unsafe files/configs fail
   closed. Database scripts go through `docker exec -i ... psql` stdin; passwords
   and DSNs never enter argv or normal output. SQL statement/error-statement
   logging is disabled within the provisioning transaction. Confirm any custom
   database audit extension also excludes credential DDL before this step.
4. Root copies the existing stable32-byte identity HMAC key from the Mac into
   `identity-key` in that private directory, preserving its bytes. Root copies
   the existing replica encryption key into `replica-encryption-key`. Use a
   controlled root process that captures source bytes in memory and writes the
   private destination directly; never print either key, pass key bytes in argv,
   or create an intermediate world-readable file. Each file and generated JSON/
   DSN must be a regular non-symlink0600 file owned10001:10001; the directory is
   0700 owned10001:10001. Do not copy the office private signing key. Check key
   equality privately without exposing the bytes.
5. Confirm the API login is `platform_replica_reader`, with only
   `platform_replica_read` membership, and has neither source-role memberships nor
   replica write privileges. Inspect only mounts and environment key names,
   never dump container secret contents. The API's existing replica DSN/encryption
   secret remains separate from the new worker login/identity key.
6. Render the configuration on the release host and review the resolved image,
   mounts, resource limits and three networks before creating a stopped service:

   ```sh
   docker compose -f deploy/cloud/compose.agent-sync.yaml --profile cloud-sync config
   docker compose -f deploy/cloud/compose.agent-sync.yaml --profile cloud-sync create cloud-agent-sync
   ```

   `create` does not start the worker. Keep it stopped while preparing acceptance.
   Local tests parse YAML; the developer Mac may lack the Docker Compose plugin,
   so host rendering is a separate release gate. Do not use a shared Nginx
   configuration check for this deployment.

## Cut over one source, backfill, then enable polling

The generated `config-fae.json`, `config-daq.json`, `config-admin.json` and
`config-voc.json` each select one source; `config.json` selects all four, at a
10s interval with the existing FAE verified-subject behavior enabled. Save the
previous office source instance and exact ownership fields before each switch.
Reuse the recorded cloud worker instance identifier on subsequent releases. On
the first installation establish one stable identifier (this rollout uses
`orbbec-platform-cloud-production`) and record it in the private receipt. This
publisher identifier does not enter Session/Turn/user HMAC inputs. Keep the exact
previous office instance (`orbbec-platform-local-production` here) for rollback;
never substitute the cloud identifier when returning ownership to the office.

For FAE first (repeat for `daq`, `admin`, `voc` only after each acceptance):

```sh
python3 deploy/cloud/provision-agent-sync.py set-publisher \
  --source fae --publisher cloud --source-instance-id CLOUD_INSTANCE_FROM_RECEIPT
docker compose -f deploy/cloud/compose.agent-sync.yaml --profile cloud-sync \
  run --rm --no-deps cloud-agent-sync python -m app.cloud_sync.cli once \
  --config /run/cloud-sync/config-fae.json
docker compose -f deploy/cloud/compose.agent-sync.yaml --profile cloud-sync \
  run --rm --no-deps cloud-agent-sync python -m app.cloud_sync.cli status \
  --config /run/cloud-sync/config-fae.json
```

Only the root host helper calls `set_agent_publisher`, using SQL superuser
credentials local to the existing DB container. Raw INSERT/ownership UPDATE is
forbidden, including from application writers. The function serializes takeover
with publication and audits the ownership epoch. Provisioning itself never calls
it. DAQ's source profile kind is `daq`, while its persisted source family remains
`fae`.

For each source compare real source/cloud session and turn counts, stable session
and turn keys, user HMAC ownership, titles, complete answers, exact outcomes,
feedback and reviews through read-only queries and authorized existing APIs.
Recover the known seven-turn FAE incident by stable identity. Check that the
source-read watermark and publication advance together, unchanged payloads are
not rewritten, source failures preserve earlier data, and old queued office
batches neither overwrite nor expire cloud-owned records. Retain only approved
private receipts, not business payloads in ordinary logs. Do not fabricate a
production conversation or call a model to test this path.

After each accepted source cutover, atomically update the Mac publisher's private
`sync.env` `PLATFORM_REPLICA_EXCLUDED_AGENT_IDS` CSV to include its exact Agent ID:
`ai-fae-agent`, `ai-daq-fae-agent`, `ai-admin-agent`, or `voc`. Preserve exclusions
for sources already accepted and all unrelated settings. The updated exporter
applies this before pagination; do not reset its state, batch queue, sequence or
signature chain. Check/drain existing signed batches through the ownership-aware
importer, which acknowledges and skips cloud-owned session payloads. DB ownership
remains the write boundary while this office optimization is being installed.

After all four sources pass and the default config matches those owned sources:

```sh
docker compose -f deploy/cloud/compose.agent-sync.yaml --profile cloud-sync up -d cloud-agent-sync
docker compose -f deploy/cloud/compose.agent-sync.yaml --profile cloud-sync \
  exec -T cloud-agent-sync python -m app.cloud_sync.cli health \
  --config /run/cloud-sync/config.json
```

Verify each source independently advances and health is based on its own source
observation. Measure naturally occurring source commit→authorized API visibility
against10–30s. A source snapshot exceeding its10s budget blocks latency acceptance;
retain the failure and pursue the designed change ledger before scaling. Test
outage isolation only on disposable local databases. Browser QA belongs to the
user. Installation, passing local tests or non-empty replicas alone are not
production acceptance.

## Rollback ownership while preserving payloads

Stop the worker before any rollback; record the last source/publication state.
Before restoring office ownership, refresh the corresponding Mac mirror from the
actual cloud source and verify it is at least as current as the accepted replica.
Pause office publication while preparing this step. Remove only that Agent ID
from the private `PLATFORM_REPLICA_EXCLUDED_AGENT_IDS` CSV before resuming the
office publisher; retain exclusions for Agents still cloud-owned. Preserve its
existing export state and signature chain. Do not let an old mirror or queued
pre-cutover payload overwrite current answers after handing ownership back.


Restore the recorded office `source_instance_id`, never invent one:

```sh
docker compose -f deploy/cloud/compose.agent-sync.yaml --profile cloud-sync stop cloud-agent-sync
python3 deploy/cloud/provision-agent-sync.py set-publisher \
  --source fae --publisher office --source-instance-id PREVIOUS_OFFICE_INSTANCE_FROM_RECEIPT
```

Repeat only for sources being rolled back. The controlled function changes
ownership and clears unproven source-success state; it does not delete encrypted
session payloads. Resume the approved office publisher and verify its next valid
signed batch and source-scoped expiry. Preserve independent management/review
projections. Do not restore an old whole database over current business data.


For partial rollback keep the persistent worker stopped until root has reviewed
and atomically installed a0600 owned10001:10001 config containing only the
remaining cloud-owned sources. `config-<source>.json` can be used directly for a
single remaining source by overriding the worker command with that config path.
The all-source `config.json` must not be used after partial rollback. Preserve the
original generated config privately; provisioning rejects edits rather than
silently undoing a staged/rollback config. Verify the resumed worker's per-agent
health and office publication separately. Full rollback needs no worker restart.
Keep credentials and encrypted backups for investigation; revoke source worker
logins only after verifying no source still depends on them.

## Repair an existing VOC receipt observation grant

This operation is separate from publisher cutover and business-service release.
Use the accepted merged platform release, record its commit/image digest and
`provision-agent-sync.py` SHA256, and record the actual existing VOC/worker
release versions. Do not identify the deployed version from a checkout's HEAD.
Keep the worker stopped while preserving a private catalog/ACL receipt for
`platform_sync_voc_read`, its memberships, the three VOC data tables and their
constraints, and every existing outbox policy definition. Preserve the existing
VOC DSN/identity/encryption keys and previous source-success/publication state.
Do not record employee bodies or credentials in normal deployment logs.

The reviewed role installer owns exactly one additional policy:

```sql
CREATE POLICY platform_sync_outbox_select_policy
ON voc_core.outbox_messages FOR SELECT TO platform_sync_voc_read
USING (source_inbound_message_id IS NOT NULL);
```

It runs in the existing role/ACL transaction. Repeated installation coordinates
only this named permissive SELECT policy; a same-name policy with another command
or restrictive type aborts the transaction. Actor INSERT and employee SELECT
policies and other roles' grants remain unchanged. The role retains precisely
`source_inbound_message_id,status,content,sent_at` SELECT on outbox, no table-wide
SELECT, no writes, no membership, and NOBYPASSRLS. Unlinked notifications remain
outside its observation domain; they cannot become conversation turns.

For an existing installation use the VOC-only helper as host root:

```sh
python3 deploy/cloud/provision-agent-sync.py repair-voc-observation
```

This requires the existing private `voc-database-url` (0600, owned10001 in its
0700 directory), reuses its exact password and reconciles only the VOC login and
its owned policy. It creates no private files, rotates no unrelated credential,
does not provision other sources/replica roles, and changes no publisher. A
missing/unsafe credential fails closed. Do not run the all-source `provision`
command just to repair this observation grant.

Before resuming, use authorized owner **readonly** catalog/aggregate inspection
and a separate actual reader connection to compare the linked receipt domain,
including sent/pending/failed, at explicitly recorded times. Confirm inbound PK,
job unique/FK and outbox unique/FK are valid, immediate and match the inbound ID;
compare linked count with distinct inbound count, and orphan/duplicate counts.
Do not require reader outbox count to equal all outbox rows: unlinked rows are
intentionally excluded. No SQL with a source administrator may stand in for the
reader. Employee isolation and forbidden writes are local disposable-PG tests;
do not issue production business writes for acceptance.

Release the matching worker adapter before its next ordinary VOC-only cycle.
The adapter checks the real role/column grants, table/RLS state, precise applicable
SELECT policy and actual valid constraints within its repeatable-read/read-only
snapshot. Zero-row allowlisted reads hold ACCESS SHARE against policy/constraint
DDL through the business read, permit ordinary producer writes, and retain the
existing read budget. Additional applicable SELECT/ALL policies (including
PUBLIC or restrictive policies), changed predicates, membership/bypass/ownership
or changed one-to-one constraints fail closed as `source_visibility_unverified`.
Do not grant table SELECT to obtain an explicit LOCK and do not disable RLS.

Run only the already authorized VOC source through `config-voc.json`; preserve
stable keys and the current publisher instance. Confirm the normal snapshot
updates existing encrypted rows in place, that source and replica input totals
match, and that confirmed sent counts match only the linked sent domain. Done
jobs with no receipt remain unknown; pending/failed receipts remain pending/failed.
A failed observation must leave encrypted payloads and success/publication
watermarks intact, persist the source error/failure time and make health fail.
Local acceptance is not production acceptance. Record production checks separately.

For rollback stop the worker first and retain failure evidence. If this release
created the policy, execute only the following reviewed DDL as the controlled
source owner (stdin, transaction, no credential output):

```sql
BEGIN;
DROP POLICY platform_sync_outbox_select_policy ON voc_core.outbox_messages;
COMMIT;
```

If the policy already existed, restore only its saved definition instead of
assuming it was absent. Never drop actor/employee policies, change RLS settings,
restore an entire source database or delete replica payloads. Keep VOC polling
stopped after policy rollback: the new reader correctly rejects missing
observation capability. Do not resume an older unchecked reader and publish
invisible receipts as a fresh successful snapshot. Publisher rollback, if needed,
remains the separate controlled operation above.
