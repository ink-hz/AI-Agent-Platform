# Cloud Platform and DingTalk identity runbook

The local-to-cloud execution prerequisite is operated separately through the
[Agent execution relay runbook](agent-execution-relay.md). Its release gate must
pass before any user-facing Chat or Agent Brain route is enabled.

This runbook operates the read-only cloud replica and the production DingTalk
identity boundary of AI Agent Platform. The public employee entry is
`https://agent.orbbec.com.cn/`; PostgreSQL and the Platform upstream remain
private.

## Non-negotiable boundaries

- Never change the existing FAE container, its port, its Nginx server blocks,
  Langfuse, or its legacy IP behavior. The formal cutover may modify only the
  Agent HTTPS server's shared authentication and root `location /`.
- The Platform API binds only to `127.0.0.1:8080`; PostgreSQL and the forced
  importer have no host port.
- The data flow is one way: local source to a sanitized, signed batch to the
  cloud. The cloud cannot read or call back into the local source.
- Attachment bytes, download coordinates, original names, raw user IDs, source
  paths, credentials, customer designs, and unsanitized content never enter the
  cloud replica.
- Do not start a real backfill until the private sanitizer dictionary has been
  reviewed. An empty dictionary is not approval.
- Every secret file and acceptance evidence file is an owner-only regular file
  with mode `0600`; its parent directory uses mode `0700`.

## Formal DingTalk production release

The formal release runs six Compose services: PostgreSQL, Platform API,
loopback proxy, directory/event worker, DingTalk Stream consumer, and the
private durable Agent Brain worker. Only the
loopback proxy binds a host port, exactly `127.0.0.1:8080`. The API and both
workers have outbound access but no published port. The API uses the
`platform_control_app` and append-only audit roles; the directory and Stream
processes use separate least-privilege roles and separate secret volumes.

Before deploying, these root-owned mode-0600 files must exist under
`/opt/orbbec-agent-platform/private`:

```text
dingtalk-app-key
dingtalk-agent-id
dingtalk-corp-id
dingtalk-app-secret
dingtalk-owner-userid
backup-recovery-x25519.pub
```

The deployment generates production control DSNs and independent versioned
identity keyrings. Generate the content keyring once, validate it with the
production codec, and put a second encrypted copy in the approved offline backup:

```bash
backend/.venv/bin/python deploy/cloud/generate-content-keyring.py \
  /absolute/private/content-encryption-keyring
```

The deploy environment references private files by absolute path only:

```text
CLOUD_CONTENT_ENCRYPTION_KEYRING=/absolute/private/content-encryption-keyring
CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING=/absolute/private/execution-worker-public.json
```

The keyring parent is mode `0700` and the file is mode `0600`. Back up the
content keyring before first deployment; losing it makes encrypted Missions
unreadable. The generator never prints key material. Deployment never reads
macOS Keychain and never accepts credentials through process arguments. Do not
print or copy secret contents into shell history.

Run the reviewed clean release through the normal deploy command. Success is:

```text
CLOUD_PLATFORM_DEPLOY_OK release=<commit> mode=dingtalk
```

Deployment starts the formal services while the existing root Basic Auth is
still present. Apply Platform migrations 039/040 first without publishing the
new account projection, then deploy and verify the AI ADMIN compatibility bridge
that accepts both legacy and additive account contracts. Before Platform
publish/cutover, run the employee-profile aggregate probe inside the directory container so its
file-backed secrets stay inside that service:

```bash
environment_path=/opt/orbbec-agent-platform/private/platform.env
compose_path=/opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml
compose=(docker compose --env-file "$environment_path" -f "$compose_path")
directory_id="$("${compose[@]}" ps -q platform-directory)"
test -n "$directory_id" || exit 1
test "$(docker inspect --format '{{.State.Health.Status}}' "$directory_id")" = healthy || exit 1
profile_probe_json="$(docker exec "$directory_id" python -m app.control_plane.employee_profile_probe)" || exit 1
python3 -c 'import json,sys; p=json.load(sys.stdin); active=p["active_employee_count"]; raise SystemExit(not (active > 0 and p["primary_department_present_count"] == active))' \
  <<<"$profile_probe_json" || exit 1
unset profile_probe_json
```

Both the container command and the aggregate completeness checks are fail-closed gates.
Do not print the captured JSON or load any secret on the controller. Wait for a
completed active directory generation with source schema version exactly `3`.
Before Nginx is changed, `publish-dingtalk-production.sh` runs one consistent SQL snapshot through `docker exec` against the candidate PostgreSQL container.
That single release gate includes the owner-bootstrap-aware owner count, one
completed active schema-v3 generation fresh within eight hours and a recent
healthy directory-event heartbeat. Its fixed aggregate format is
`owner:fresh_generation:heartbeat`. The separate employee-profile aggregate requires
`active_employee_count > 0` and
`primary_department_present_count = active_employee_count`. Generation validation
proves authoritative employee-count agreement and required display names, so
nickname completeness is 100% and primary-department completeness is 100%.
The real-name and mobile coverage are reported through `real_name_present_count`
and `mobile_present_count` without a completeness requirement; gender coverage is not a lodging release gate
because AI ADMIN supports locked local fallback. The
acceptance script rechecks the same single snapshot after cutover. Acceptance evidence must contain only fixed
aggregate counts/status. It must not contain employee names, gender values,
provider identifiers, mobile numbers, ciphertext, raw rows, or provider payloads.

The pre-cutover bootstrap may have zero active owners only when publish is
invoked with the explicit `--allow-unbound-owner` flag. After the documented
owner-binding step, formal post-cutover acceptance requires exactly one active
owner; the bootstrap flag recorded in cutover state does not relax formal
acceptance.

After the probe and complete schema-v3 reconciliation pass, verify the
directory/event heartbeat. Bind the sole owner with the exact private DingTalk
userid; never select the owner by display name:

```bash
/opt/orbbec-agent-platform/current/deploy/cloud/bind-production-owner.sh \
  approver_one approver_two BACKUP_REFERENCE INITIAL_OWNER_BINDING
```

The command performs a dry run, creates a 15-minute authenticated receipt, and
then consumes the same receipt for the audited mutation. The two approver
identifiers must be distinct stable lowercase operator identifiers. The backup
and incident references must be uppercase stable references.

After owner binding, publish only the root identity boundary:

```bash
/opt/orbbec-agent-platform/current/deploy/cloud/run-dingtalk-production-cutover.sh \
  /opt/orbbec-agent-platform/releases/<EXPECTED_RELEASE_SHA> \
  <EXPECTED_RELEASE_SHA> /root/private/platform-controlled-account-cookie
```

The Nginx transaction removes the old shared Platform Basic Auth, replaces only
the Platform root location, removes the obsolete DingTalk preview include, and
preserves `/admin`, its independent authentication, TLS, ACME, and unrelated
locations byte-for-byte. It uses 360-second proxy timeouts and overwrites all
trusted forwarding headers.

Rollback restores the exact pre-cutover Nginx file, prior immutable release,
and matching environment; it stops only Platform services and never restarts
FAE:

```bash
/opt/orbbec-agent-platform/current/deploy/cloud/rollback-dingtalk-production.sh
```

Do not delete the cutover state or pre-cutover release until the acceptance
window closes.

The controlled-account cookie file must be a root-owned, mode-`0600`, regular
file containing only the approved account's session-cookie value. Acceptance
checks the exact account schema and emits only field-presence booleans. Both
publish and acceptance run under the same existing deployment/action lock, bind
evidence to the expected release, verify every application container embeds that
exact release SHA, and recheck the current release and container IDs. If formal
acceptance fails after publication, the coordinator invokes the fixed rollback
under the same lock before releasing it.

Deploy the remaining AI ADMIN migration/backend/UI only after Platform publish
and the authenticated account proof have passed. Roll back in reverse order:
AI ADMIN feature release, Platform projection, AI ADMIN compatibility bridge;
retain synchronized nullable columns and do not delete directory data.

## Unified employee entry and AI ADMIN `/office` boundary

The Agent domain has one Nginx owner: Agent Platform. Its HTTPS server block
must retain these explicit path owners:

```text
/                     Platform employee-use entry
/agents/*             Platform professional Agent workspaces
/admin and /admin/*   Platform management center -> 127.0.0.1:8080
/office and /office/* AI ADMIN -> 127.0.0.1:8011
```

Do not rely on the fallback `location /` to own `/admin`; both `/admin` and
`^~ /admin/` are explicit Platform locations so a later application cannot
silently capture the path. AI ADMIN releases must not install or edit this
server block. The `/office` cutover is performed only by
`publish-office-path-migration.sh` under the shared Platform action lock.

The default request-body limit remains 1 MB. Only the exact AI ADMIN feedback
upload endpoint is 12 MB. Platform currently has no browser upload endpoint,
so no broad 50 MB exception is installed; add one only together with a real,
authenticated, exact Platform upload route and its tests. Every `/office`
location that overrides `add_header` repeats the complete HSTS, nosniff,
frame, referrer, CSP and permissions header set. `/office/health` is a public
404 owned by the Admin-path acceptance contract, not a Platform health API.

AI ADMIN authenticates each request through the loopback-only minimal subject
endpoint and receives only `internal_user_id`, `display_name`, and `active`.
It must independently enforce strict Host/Origin and CSRF checks for every
mutation. A same-site request originating at `fae.orbbec.com.cn` must not be
accepted as an Admin write merely because the browser attached the Platform
Session Cookie.

Before control migration 042, deployment automatically runs the read-only
classification preflight against both production and preview control
databases. To run the same gate manually without exposing rows:

```bash
release=/opt/orbbec-agent-platform/current
platform_env=/opt/orbbec-agent-platform/private/platform.env
compose="$release/deploy/cloud/compose.yaml"
postgres="$(docker compose --env-file "$platform_env" -f "$compose" ps -q platform-postgres)"
"$release/deploy/cloud/preflight-execution-job-kind.sh" \
  "$postgres" agent_platform_control
"$release/deploy/cloud/preflight-execution-job-kind.sh" \
  "$postgres" agent_platform_control_preview
```

The only acceptable outputs begin with
`EXECUTION_JOB_KIND_PREFLIGHT_OK`. Orphaned `execution_jobs` or an unknown
Mission phase stops the release and prints only aggregate classification
evidence. Never coalesce an unknown row to `legacy_brain` merely to pass the
gate.

The normal staged release enables direct professional-Agent use with
`PLATFORM_DIRECT_AGENT_ENABLED=1` while keeping both Brain flags at `0`. The
API process runs a mode-filtered V1 relay scheduler that can claim only
`direct_agent` Missions in this state. It cannot claim or advance a Brain
Mission. The authenticated `/` route still renders the real Agent 大脑
workspace; a Brain submission fails explicitly with HTTP 503 while intake is
disabled. It never switches to a release-state preparation page. Authorized HR
and Marketing workspaces remain usable.

Before compatibility rollback, `rollback-dingtalk-production.sh` verifies that
no `metabot_local` job remains queued, leased, dispatched, or running. A
non-zero count returns `ROLLBACK_BLOCKED_ACTIVE_METABOT_LOCAL`; wait for the
tasks to settle or explicitly terminate them through the supported stop path,
then retry. Never let an older orchestrator reinterpret an in-flight V2 job.
The Nginx rollback baseline after the office cutover is the post-migration
configuration containing `/office`; every Platform rollback must recheck
`/office/?view=services` as well as `/admin`, `/`, and FAE invariance.

## Agent Brain opt-in release

The use entry is a separate fail-closed release gate. Keep the management root
available until every dependency is ready, and execute the following order
without skipping or combining a gate:

1. migrations with Brain disabled;
2. deterministic legacy Mission-to-Conversation backfill with zero quarantine;
3. local `agent-brain-bot` on `127.0.0.1:9110`;
4. Worker allowlist and key registration;
5. cloud image with Brain disabled;
6. relay canary;
7. start the private durable worker with V2 intake disabled;
8. run the real Provider probe and reference crash-recovery acceptance;
9. atomically enable Brain and V2 intake;
10. verify `/` remains the Agent 大脑 workspace and a real Turn completes.

The cloud environment flags are `PLATFORM_AGENT_BRAIN_ENABLED` and
`PLATFORM_AGENT_BRAIN_V2_ENABLED`. Both are absent or `0` during migration,
image, Provider, recovery, and relay validation. They move to `1` in the same
mode-0600 environment-file replacement. The authenticated root never
redirects to `/admin` or a preparation shell. Only an explicit value of `1`
enables Brain Mission APIs. The workspace itself remains visible and reports
runtime unavailability explicitly. Direct professional-Agent routes are controlled independently by
`PLATFORM_DIRECT_AGENT_ENABLED=1`. The relay remains separately controlled by
`PLATFORM_EXECUTION_RELAY_ENABLED=1`.

Before staging, place the configured Provider key at
`/opt/orbbec-agent-platform/private/brain-provider-api-key` as a root-owned,
non-symlink, mode-0600 regular file. It is copied only into the
`platform-brain-secrets` volume as UID 10001. The Brain service does not receive
the DingTalk AppSecret, publishes no port, uses a read-only filesystem, drops
all Linux capabilities, and has `no-new-privileges`. Its database credential is
the dedicated `platform_brain_worker` DSN.

Stage B is refused unless the evidence resolves to exactly:

```text
PROVIDER_PROBE=passed
REFERENCE_RECOVERY=passed
V1_NONTERMINAL_MISSIONS=0
V2_MISSION_RUN_WRITES=0
LOCAL_WORKER_ACCEPTS=metabot_local
FAE_MANAGED_FILES_UNCHANGED=true
```

The Provider evidence JSON and `provider-evidence.sha256` live under the
root-only `/opt/orbbec-agent-platform/private/agent-brain-v2` directory. They
contain capability booleans, version hashes, request IDs and token counters,
but no key, Prompt text, user content, raw model response, or Adapter payload.
The reference-recovery marker is produced only by the acceptance procedure;
creating it manually bypasses the release contract and is prohibited.

Rollback replaces both Brain flags with `0`, stops the private worker, and
preserves every `platform_brain` row. It never creates a V1 Mission for an
active V2 Turn. Existing non-terminal V2 work remains visible as interrupted
unless the same V2 worker release is restored.

The cloud stage runs `python -m app.agent_brain.conversation_backfill` after
control migrations and before any control-plane consumer starts. It uses only
the maintenance DSN and the content-encryption keyring, locks legacy rows in
bounded `FOR UPDATE SKIP LOCKED` batches, and never rewrites Mission content.
Each legacy Mission receives deterministic Conversation, Message, and Turn
identifiers; rerunning after a committed batch creates no duplicate history.
The release proceeds only when the command prints exactly:

```text
AGENT_BRAIN_CONVERSATION_BACKFILL_OK scanned=<n> created=<n> quarantined=0
```

Any non-zero quarantine or non-exact output fails staging while Brain remains
disabled. Investigate the protected source row and content-key configuration;
do not delete the Mission, fabricate ownership from a name, or enable Brain to
work around the gate. Compatibility rollback disables the Conversation entry
but retains both the original Mission and its additive Conversation links.

Provision the local Worker from Neo's reviewed checkout before creating the
acceptance inputs:

```bash
deploy/local-execution-worker/provision.sh
```

This wrapper uses Neo's private PostgreSQL socket only for a temporary
bootstrap role, leaves only the narrow SCRAM runtime HBA rule, and installs the
Worker as `agentops` without a password, GUI, Keychain or copied SSH key. The
Worker is the fixed PM2 process `orbbec-agent-execution-worker`, launched only
through
`/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/worker-pm2.sh`
and the fixed `execution-worker.ecosystem.config.cjs`; the provision path does
not call `launchctl`. Before mutation it records the exact prior Worker state
(`absent`, `online`, or `stopped`) and an owner-only copy of the existing PM2
dump. Failure restores both. Success first proves that the PM2 PID is the sole
`127.0.0.1:9120` listener, that Agent Brain is byte-for-byte process invariant,
and that every PM2 process other than Brain and this Worker is invariant; only
then does it run `pm2 save` and commit the receipt.

The agentops release stage runs with `umask 077`, so the real Git archive
extracts `worker-pm2.sh` as owner-only mode `0700` and the ecosystem config as
owner-only mode `0600`. Stage, prepare, and the PM2 wrapper require those exact
modes; group/other access or a mode mismatch fails before Worker mutation.
The wrapper does not execute npm's standard `.npm-global/bin/pm2` symlink. It
uses the fixed regular package executable at
`.npm-global/lib/node_modules/pm2/bin/pm2` after verifying agentops ownership,
execute permission, no symlink at the canonical file, and no group/other-write
permission on its package ancestors.
After PM2 starts the Worker, provision allows dependency import and socket bind
up to a fixed 60-second deadline, polling every 5 seconds. Every poll must
still return the exact PM2 Worker identity. PM2 `launching` is the only startup
phase that may wait without probing the listener; once online, only an absent
9120 listener is retryable. `waiting restart`, errored, stopped, absent or
ambiguous PM2 state, a non-loopback or duplicate listener, or a listener PID
different from the PM2 PID fails immediately. The deadline begins as soon as
the installer returns from PM2 start and is checked again before accepting a
listener; failure restores the prior Worker state and PM2 dump.
Rollback restoration of a previously online Worker applies the same strict
identity mapping and a separate fixed 60-second `launching`-to-`online` wait;
failed PM2 states or expiration fail the rollback instead of claiming that the
prior state was restored.

Create a private JSON acceptance config and four private browser-input files.
The config, the member and owner Cookie header files, the HR acceptance prompt,
the interruption prompt and the evidence destination must be owner-only regular
files with mode `0600`. The acceptance identities must include a
real DingTalk test member and a different owner or platform administrator. Before enablement,
require a pre-created `hr-bot` grant for the member and deliberately do not grant
`marketing-gtm-bot`.

Each Cookie file contains exactly the two browser cookies
`__Host-platform_session` and `__Host-platform_csrf` on one line. The script
derives mode-`0600` curl/CDP inputs, supplies the required production Origin,
and never places either value in command-line arguments or evidence. The JSON
config uses schema version `2` and has exactly these absolute-path fields:
`member_cookie_file`, `owner_cookie_file`, `hr_prompt_file`,
`interruption_prompt_file`, the agentops-owned mode-`0600`
`relay_acceptance_config`, and `evidence_file`. Cloud root access is fixed inside the Neo-owned release coordinator
to `/Users/neo/.ssh/orbbec_aliyun_ed25519`. That key is never copied to or made
readable by `agentops`.

Before release, persist predeclared `grant_id` and `request_id` UUIDs with the
authenticated owner/member account results (or their stable
`internal_user_id` values) in a private JSON file. Apply the audited
`acceptance-grant` maintenance helper in the deployed container. It grants only
`hr-bot`, verifies `marketing-gtm-bot` remains denied, and rejects names or
DingTalk provider identifiers.

The private document is schema version `1` with exact keys `actor`, `member`,
`grant_id`, and `request_id`. `actor` and `member` are either stable UUID
strings or the corresponding `/api/v1/account` results. Run the helper through
a one-use input directory owned by the image runtime UID. Both the mounted
directory and files must satisfy the helper's `0700`/`0600` ownership checks;
mounting a root-owned `0600` file directly is not sufficient:

```bash
grant_input=/opt/orbbec-agent-platform/private/acceptance-grant-input
trap 'rm -rf -- "$grant_input"' EXIT
release="$(readlink -f /opt/orbbec-agent-platform/current)"
platform_env=/opt/orbbec-agent-platform/private/platform.env
compose="$release/deploy/cloud/compose.yaml"
api="$(docker compose --env-file "$platform_env" -f "$compose" ps -q platform-api)"
PLATFORM_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$api")"
[[ "$PLATFORM_IMAGE" =~ ^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,255}$ ]]
install -d -m 0700 -o 10001 -g 10001 "$grant_input"
install -m 0600 -o 10001 -g 10001 \
  /absolute/private/acceptance-grant.json "$grant_input/grant.json"
install -m 0600 -o 10001 -g 10001 \
  /opt/orbbec-agent-platform/private/control-maintenance-database-url \
  "$grant_input/maintenance-database-url"
docker run --rm --read-only --user 10001:10001 \
  --network orbbec-agent-platform-internal \
  -v "$grant_input":/run/input:ro \
  -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/input/maintenance-database-url \
  "$PLATFORM_IMAGE" python -m app.agent_brain.acceptance_grant \
  /run/input/grant.json
rm -rf -- "$grant_input"
trap - EXIT
```

Run the staged gate from the reviewed release checkout:

```bash
deploy/cloud/accept.sh /absolute/private/agent-brain-acceptance.json preflight
deploy/cloud/accept.sh /absolute/private/agent-brain-acceptance.json release
deploy/cloud/accept.sh /absolute/private/agent-brain-acceptance.json rollback
deploy/cloud/accept.sh /absolute/private/agent-brain-acceptance.json restore
```

`release` performs enablement and real acceptance in one fail-closed process;
there is no standalone enable action. Any enablement or acceptance failure
restores `PLATFORM_AGENT_BRAIN_ENABLED=0` and therefore the management root.
The `accept` action is only for an additional rerun of the same gate and also
disables Brain on failure. `restore` repeats the complete release gate after a
successful rollback instead of enabling the flag without acceptance.
Before the flag can become `1`, `release` runs the existing ten-gate execution
relay acceptance as `agentops` and requires its exact fixed success marker.
That canary runs while Brain remains `0`; a missing config or any non-exact
result leaves the management entry active.

All mutating actions hold both a local private-directory lock and the same
root-owned cloud private-directory lock used by `deploy/cloud/deploy.sh` for
their full lifecycle. The common remote lock is acquired atomically before
either workflow acquires or changes deployment input, so a concurrent Brain
action or cloud deploy fails closed. An unclean shutdown deliberately leaves a
stale lock for explicit operator audit; never delete it merely to retry.

### Stale lock recovery

Treat the common lock as stale only after confirming that local
`deploy/cloud/deploy.sh` and `deploy/cloud/accept.sh` processes and their remote
SSH/stage processes do not hold either lock open. On the cloud host, read and
record the owner token, lock ownership and timestamps; then compare the current
and previous deployment pointers and Brain feature state with the most recent
deployment and acceptance evidence. Also recheck Platform health and the FAE
invariance snapshot. If a cutover or remote operation remains uncertain, stop
and investigate instead of clearing the lock.

After the operator records that audit in the deployment incident log, move only
the exact
`/opt/orbbec-agent-platform/private/agent-brain-action.lock` directory to a
token-named tombstone, verify its `owner` file still contains the recorded
token, and remove that file and the now-empty tombstone. Never use a recursive
delete or a wildcard. Run `preflight` again before retrying any mutation.

The acceptance checks the member use root, member denial at `/admin`, owner
access to `/admin`, and owner Conversation metrics. It then creates one
HR-oriented Brain Conversation, waits for the first real `hr-bot` ChildRun,
posts a follow-up to the same Conversation, and requires exactly two Turns,
four Messages, two linked Missions, monotonic persisted events, and two distinct
completed `hr-bot` runs. Replaying either POST and resuming SSE after the final
sequence must create no duplicate Turn or ChildRun. The gate also checks
Markdown rendering on the Conversation page, unauthorized direct-Agent denial,
explicit interruption after a Worker stop, and no duplicate ChildRun after
Worker restart. Readiness and child-run discovery poll at five-second intervals
with fixed total time limits; do not replace them with a busy loop.

The evidence file may contain only release SHAs, container IDs and start times,
worker key ID, Conversation/Turn/Mission/run IDs, event sequences and counts,
listener addresses, FAE probe results and rollback paths.
Do not record prompts, answers, cookies, DingTalk IDs, or secrets. Keep the file
at mode `0600`.
Every successful gate writes an immutable UUID-suffixed evidence generation and
atomically updates the configured evidence path to the newest generation. If a
current evidence file already exists, it is first retained as a separate
mode-`0600` previous generation. This allows `accept` and `restore` to rerun
without overwriting the release or rollback evidence used by the preceding
step.

Rollback sets the feature flag to `0`, recreates only Platform API/loopback,
and verifies `/admin`, Sessions, Review, Operations, and the exact retained
two-Turn Conversation shape before it reports success. Do not drop migrations
032 through 038. Do not delete Conversation, Message, Turn, Mission, or run
data. The rollback never restarts or modifies FAE or local MetaBots. The FAE
container identity and configuration remain unchanged.
The separate FAE domain/IP Nginx routes remain byte-for-byte invariant;
only the Agent Platform server block is intentionally replaced. After the
rollback exercise passes, `restore` returns
the reviewed release to the enabled state, appends a third Turn to the same
Conversation exactly once, and then repeats the complete release gate.

## One-time local preparation

Use an explicit private directory outside the repository:

```bash
private_root="/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica"
mkdir -p "$private_root"
chmod 700 "$private_root"
deploy/cloud/bootstrap-keys.sh "$private_root"
```

The command is idempotent. Re-running it must keep the same key fingerprints.
The backup recovery private key remains local and is never copied to the cloud.

Create `$private_root/deploy.env` with mode `0600`:

```bash
CLOUD_ADMIN_HOST=root@47.106.112.69
CLOUD_ADMIN_KEY=/Users/neo/.ssh/orbbec_aliyun_ed25519
CLOUD_SIGNING_PUBLIC_KEY=/absolute/private/path/replica-signing-public.key
CLOUD_BACKUP_PUBLIC_KEY=/absolute/private/path/backup-recovery-x25519.pub
CLOUD_CONTENT_ENCRYPTION_KEYRING=/absolute/private/path/content-encryption-keyring
CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING=/absolute/private/path/execution-worker-public.json
CLOUD_BASELINE_FILE=/absolute/private/path/cloud-baseline.sha256
CLOUD_ACCEPTANCE_EVIDENCE_FILE=/absolute/private/path/acceptance-evidence.env
```

Create the private sanitizer dictionary at
`$private_root/sanitizer-private.yaml`. It has this schema:

```yaml
customers: []
candidates: []
projects: []
products: []
addresses: []
```

Populate every group with approved aliases from the retained one-year source
window, then set mode `0600`. This private sanitizer dictionary is never
committed, uploaded, or logged. If its coverage cannot be approved, deploy the
empty Platform infrastructure but stop before synthetic import and backfill.

## Pre-deployment gate and baseline

From a clean, reviewed `master` checkout at the release commit:

```bash
deploy/cloud/acceptance.sh local
deploy/cloud/acceptance.sh capture-baseline "$private_root/deploy.env"
```

The baseline stores only SHA-256 facts for FAE identity, image, start time,
health, Nginx configuration, and public listeners. The acceptance gate never
prints their values.

## Deploy

```bash
deploy/cloud/deploy.sh "$private_root/deploy.env"
```

Legacy SSH-tunnel deployments reported:

```text
CLOUD_PLATFORM_DEPLOY_OK release=<commit> mode=ssh-tunnel
```

The deploy script uses `BatchMode=yes`, verifies the release manifest, creates
an immutable release, migrates only `platform_replica`, starts the loopback API,
and requires the captured FAE/Nginx/listener facts to remain unchanged. A
failure automatically restores the prior Platform API release.

## Restricted import transport

Copy only the `AUTHORIZED_KEY=...` line printed by `bootstrap-keys.sh` into the
cloud account's `authorized_keys`. Preserve every existing line. The new key is
restricted to `forced-import.sh` with no PTY, forwarding, agent, or arbitrary
command capability.

Create `$private_root/sync.env` with mode `0600`. It contains the absolute local
source DSN file, private sanitizer dictionary, identity/signing key, queue and
state paths, backend Python, log directory, restricted SSH host and key. Use a
stable production source ID distinct from `synthetic-acceptance`.

Run one empty export before a real backfill and verify the batch locally. The
five-minute sync command is:

```bash
deploy/cloud/push-replica.sh "$private_root/sync.env"
```

It deletes a queued batch only after receiving the exact authenticated import
acknowledgement. Failure leaves the queue intact for retry.

## Synthetic canary, cleanup, and one-year backfill

Generate a signed, already-sanitized batch whose raw fixture covers phone,
email, ID number, credential, URL, path, user ID, customer, candidate, project,
product, address, and attachment name. Import it with the restricted key. Scan
the cloud database, APIs, rendered HTML, service logs, and encrypted backup for
the fixture canaries; record only pass/fail evidence, never matching content.

```bash
set -a
source "$private_root/sync.env"
set +a
canary_batch="$private_root/synthetic-canary.jsonl"
cd backend
.venv/bin/python -m app.cloud_replica.cli canary --output "$canary_batch"
ssh -i "$CLOUD_SYNC_SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes "$CLOUD_SYNC_SSH_HOST" < "$canary_batch"
```

Before any real data, run the guarded cleanup inside the cloud API image:

```bash
ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 -o BatchMode=yes \
  root@47.106.112.69 '/usr/bin/docker compose \
    --env-file /opt/orbbec-agent-platform/private/platform.env \
    -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml \
    run --rm --no-deps -T \
    -v orbbec-agent-platform-import-secrets:/run/import-secrets:ro \
    -e PLATFORM_REPLICA_DATABASE_URL_FILE=/run/import-secrets/replica-database-url \
    -e PLATFORM_REPLICA_ENCRYPTION_KEY_FILE=/run/import-secrets/replica-encryption-key \
    platform-api python -m app.cloud_replica.cli reset-test-generation \
    --source-instance-id synthetic-acceptance'
rm -f -- "$canary_batch"
```

The transaction refuses to run if any other generation exists. Confirm zero
Sessions and Agents. Reset the separate local synthetic state, switch to the
production source ID, and then run bounded batches until the one-year watermark
reaches the captured upper bound. Reconcile safe session/turn counts, safe
hashes, and sampled Turn order without exporting original IDs or text.

## SSH tunnel and freshness verification

Open the SSH tunnel:

```bash
ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 \
  -o BatchMode=yes -o IdentitiesOnly=yes \
  -N -L 8080:127.0.0.1:8080 root@47.106.112.69
```

Open `http://127.0.0.1:8080/`, `/agents`, and `/sessions`, then sample Session
detail pages. Confirm Markdown rendering, reverse chronological ordering, safe
attachment metadata without buttons, no Review navigation, and no mutation
controls. Required read APIs must succeed through the SSH tunnel.

For freshness, stop only the local scheduler long enough to exceed 900 seconds.
The last snapshot must remain visible and be marked stale. Restart sync, require
one successful import, and confirm freshness returns without losing history.

## Encrypted backup and restore drill

The cloud timer runs the encrypted backup daily. Manual execution is:

```bash
ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 -o BatchMode=yes \
  root@47.106.112.69 /opt/orbbec-agent-platform/current/deploy/cloud/backup.sh
```

Copy one `.orb` backup to an approved recovery host without changing it. Run the
restore drill with the local recovery private key:

```bash
deploy/cloud/restore-drill.sh /absolute/path/replica.orb \
  "$private_root/backup-recovery-x25519.key"
```

The approved recovery host must have Docker. The drill streams decrypted bytes
directly into a disposable PostgreSQL container and never creates a plaintext
dump file. Record `RESTORE_DRILL_OK=1` only after this succeeds.

## Enable five-minute sync

After the backfill and restore drill pass:

```bash
deploy/install-cloud-sync-launchagent.sh \
  "$private_root/sync.env" \
  /Users/neo/Library/LaunchAgents/com.orbbec.ai-agent-platform-cloud-sync.plist
```

Verify the LaunchAgent interval is 300 seconds, wait for one successful batch,
and confirm the source watermark advances. This is the five-minute sync gate.

## Final acceptance

Create the private evidence file named by `CLOUD_ACCEPTANCE_EVIDENCE_FILE`:

```bash
CANARY_ABSENT=1
SYNTHETIC_RESET_OK=1
BACKFILL_RECONCILED=1
ORDER_SAMPLES_MATCH=1
TUNNEL_APIS_OK=1
STALE_STATE_OK=1
RESTORE_DRILL_OK=1
LOCAL_SOURCE_UNCHANGED=1
FIVE_MINUTE_SYNC_OK=1
```

Set mode `0600`, then run:

```bash
deploy/cloud/acceptance.sh final "$private_root/deploy.env"
```

Acceptance succeeds only with
`CLOUD_PLATFORM_ACCEPTANCE_OK release=<commit> criteria=18`.

## Rollback

The deployment script automatically rolls back a failed candidate. For a later
manual rollback, first retain an encrypted backup and the local export queue.
Select the prior immutable release, stop only `platform-loopback` and `platform-api`, atomically restore
`/opt/orbbec-agent-platform/current` and its matching private environment, then
start only the prior `platform-api` and `platform-loopback`. Do not remove or recreate PostgreSQL unless
a separately approved restore operation requires it. Re-run the FAE, Nginx,
public-listener, loopback API, and SSH tunnel checks after rollback.

Never restart, recreate, or edit FAE, Nginx, Langfuse, local source databases,
attachments, or MetaBot as part of Platform rollback.

## Historical domain and identity notes

The following describes the design that preceded the formal DingTalk release.
The DNS records now exist. Keep these notes only for migration history: add exact records for
`agent.orbbec.com.cn` and `fae.orbbec.com.cn`. Nginx will use separate HTTPS
virtual hosts: `agent.orbbec.com.cn` to loopback Platform and
`fae.orbbec.com.cn` to the existing FAE listener. Add DingTalk (or Feishu)
behind the Platform `AuthProvider` and enable the three-role authorization and
HR isolation model before any public Platform access. The sanitizer, signing,
encryption, retention, backup, and one-way synchronization protocol stay
unchanged.

The DingTalk identity runtime uses two different HMAC keyrings. Provider
identity lookup and browser Session material use
`PLATFORM_IDENTITY_HMAC_KEYRING_FILE`; abuse-control buckets use the dedicated
mode-0600 `PLATFORM_RATE_LIMIT_HMAC_KEYRING_FILE` with purpose
`rate-limit-hmac`. Never point both settings at the same file. Ordinary
provider identity key rotation must leave the rate-limit keyring unchanged, so
live callback, login, global exchange, and per-user buckets remain continuous.
Rotate the rate-limit keyring only as a separate reviewed maintenance change,
after the prior bucket TTL has elapsed or with an explicit overlap migration;
changing its active key/version immediately creates a new digest namespace and
is not an ordinary identity-key rotation step.

## Legacy temporary administrator public entry

Until DingTalk or Feishu identity is implemented, the sanitized cloud replica
may be published at `https://agent.orbbec.com.cn` behind HTTPS Basic Auth for a
small administrator group. This is not employee identity, department access,
or HR authorization. Do not distribute the shared credential broadly. The
publication never uses Keychain and does not expose port 8080.

Create a private password file outside Git in the existing cloud-replica
private directory. The password must contain 32–128 ASCII letters, digits,
underscores, or hyphens. Generate it without printing it:

```bash
private_root="/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica"
password_file="$private_root/agent-basic-auth-password"
umask 077
openssl rand -base64 48 | tr '+/' '_-' | tr -d '=\n' > "$password_file"
printf '\n' >> "$password_file"
chmod 600 "$password_file"
```

Add these owner-only values to a dedicated mode 0600 configuration file, for
example `$private_root/agent-domain.env`:

```bash
CLOUD_ADMIN_HOST=root@47.106.112.69
CLOUD_ADMIN_KEY=/Users/neo/.ssh/orbbec_aliyun_ed25519
AGENT_DOMAIN=agent.orbbec.com.cn
AGENT_PUBLIC_IP=47.106.112.69
AGENT_BASIC_AUTH_USER=agentadmin
AGENT_BASIC_AUTH_PASSWORD_FILE=/absolute/private/path/agent-basic-auth-password
```

From a clean reviewed release, publish the route:

```bash
deploy/cloud/publish-agent-domain.sh "$private_root/agent-domain.env"
```

Success is exactly:

```text
AGENT_DOMAIN_PUBLISH_OK domain=agent.orbbec.com.cn
```

The publisher verifies the A record with the default resolver and AliDNS, then
pins acceptance traffic to `AGENT_PUBLIC_IP`; this prevents a stale local
resolver cache from turning a successful publication into a false failure.

The remote installer stores only a salted password hash in Nginx. Plaintext
remains only in the protected local password file so an administrator can
retrieve it deliberately. It is never printed, passed in argv, stored in Git,
or loaded from Keychain.

Acceptance requires HTTP 308, anonymous HTTPS 401, authenticated HTML and read
APIs, `cloud-replica`, `read_only=true`, `auth=basic-auth`, TLS 1.2/1.3, a
loopback-only 8080 listener, healthy Certbot renewal, unchanged FAE domain and
legacy IP responses, and unchanged FAE container identity.

For credential rotation, replace the local password file atomically with a new
valid mode 0600 value and run the same publisher. Each run creates a new
root-owned backup and `/root/rollback-agent-domain-<UTC>.sh`. Run the latest
rollback script over the existing administrator SSH connection if publication
must be withdrawn; it restores the prior Nginx files and Platform auth mode and
restarts only Platform API and its loopback proxy.

The temporary public entry does not approve the one-year backfill or
five-minute synchronization. Those remain separate gates: the private sanitizer dictionary,
canary scan, reconciliation, stale-state test, restore drill, and local scheduler
must pass before real Session data is considered available or current.

## Office recipient resolver scoped release (`office_recipient_resolver_release`)

此能力只能在已完成常规不可变 release 后单独启用。通用 `deploy.sh` 明确拒绝
`PLATFORM_OFFICE_RECIPIENT_BEARER`、`PLATFORM_OFFICE_RECIPIENT_BEARER_FILE` 和
`PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED`，防止私有解析能力随普通发布被隐式开启。

1. 在 owner-only 终端创建共享秘密文件，不经过标准输出：

   ```bash
   umask 077
   install -o 10001 -g 10001 -m 600 /dev/null /opt/orbbec-agent-platform/private/platform-office-recipient-bearer
   openssl rand -hex 32 > /opt/orbbec-agent-platform/private/platform-office-recipient-bearer
   chown 10001:10001 /opt/orbbec-agent-platform/private/platform-office-recipient-bearer
   chmod 600 /opt/orbbec-agent-platform/private/platform-office-recipient-bearer
   ```

   文件必须是容器服务账号 UID 10001、GID 10001 所有的普通非符号链接、mode 0600、至少
   32 字节；root 所有的 mode 0600 文件无法被容器读取，必须阻断发布。不得打印、复制到工单、
   放入 argv 或提交仓库。AI ADMIN 通过独立的受保护部署步骤读取同一份秘密。

2. 记录当前两个目标容器的 Container ID、Image ID、StartedAt、RestartCount、配置摘要和
   mounts 摘要。确认当前 release 包含
   `backend/control_migrations/058_office_recipient_directory.sql` 与
   `backend/control_migrations/059_office_recipient_directory_department_order.sql`，随后用既有
   owner migration runner 依次应用并验证 058、059；不得手工粘贴或改写 SQL。

   早期 scoped feature release 曾以 053、054 发布同一组 Office 函数；主线随后已占用 053–057，
   因此合并后的正式编号固定为 058、059。若目标库的 migration ledger 已记录早期 Office
   053、054，必须先停止通用 migrator，核对旧 SQL checksum 与函数定义，再在受控维护窗口迁移
   ledger 到 058、059，之后才能应用主线 053–057；不得让 checksum guard 失败后继续部署。

3. 在目标机的 protected environment 中只设置：

   ```dotenv
   PLATFORM_OFFICE_RECIPIENT_BEARER_SOURCE_FILE=/opt/orbbec-agent-platform/private/platform-office-recipient-bearer
   ```

   使用 base Compose 与显式 override，仅重建两个目标服务：

   ```bash
   docker compose --env-file /opt/orbbec-agent-platform/private/platform.env \
     -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml \
     -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.office-recipient-directory.yaml \
     config --services
   docker compose --env-file /opt/orbbec-agent-platform/private/platform.env \
     -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml \
     -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.office-recipient-directory.yaml \
     up -d --no-deps platform-api platform-loopback
   ```

4. 从目标机验证回环请求加正确 bearer 成功；缺失 bearer、错误 bearer 和非本地 peer 均返回
   404。响应必须为 `Cache-Control: no-store`，搜索响应不得包含解密后的收件人 ID，resolve
   响应只在这个服务间边界返回最小字段。

5. 回退时先停止 AI ADMIN 新通知准备，确认没有依赖解析器的可认领发送，再用 base Compose
   配置重建这两个服务，使 feature flag 恢复为 0。保留 migration 058、059 和目录数据，不删除表；
   重新核对两个目标容器与公开页面状态。
