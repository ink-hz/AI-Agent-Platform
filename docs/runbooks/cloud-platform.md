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

The formal release runs five Compose services: PostgreSQL, Platform API,
loopback proxy, directory/event worker, and DingTalk Stream consumer. Only the
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

The DingTalk internal application must also have the
`查询钉钉HRM个人信息的权限` capability. Gender is read from the
organization-maintained Smart HR roster field named exactly `性别`; the standard
contact-directory detail response and its optional extension map are not a
gender authority. The release probe fails closed when the HR permission is
absent, the field metadata is ambiguous, or any active employee has a missing
or invalid roster value.

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
still present. Platform deploys first; the AI ADMIN strict consumer remains on
its previous release until every gate below passes. Before Platform
publish/cutover, run the gender probe inside the directory container so its
file-backed secrets stay inside that service:

```bash
environment_path=/opt/orbbec-agent-platform/private/platform.env
compose_path=/opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml
compose=(docker compose --env-file "$environment_path" -f "$compose_path")
directory_id="$("${compose[@]}" ps -q platform-directory)"
test -n "$directory_id" || exit 1
test "$(docker inspect --format '{{.State.Health.Status}}' "$directory_id")" = healthy || exit 1
gender_probe_json="$(docker exec "$directory_id" python -m app.control_plane.gender_probe)" || exit 1
python3 -c \
  'import json,sys; sys.exit(0 if json.loads(sys.stdin.read()).get("ready") is True else 1)' \
  <<<"$gender_probe_json" || exit 1
unset gender_probe_json
```

Both the container command and the JSON `ready` boolean are fail-closed gates.
Do not print the captured JSON or load any secret on the controller. Wait for a
completed active directory generation with source schema version exactly `2`.
Before Nginx is changed, `publish-dingtalk-production.sh` runs one consistent SQL snapshot through `docker exec` against the candidate PostgreSQL container.
That single release gate includes the owner-bootstrap-aware owner count, one
completed active schema-v2 generation fresh within eight hours, a recent
healthy directory-event heartbeat, and coverage. Its fixed aggregate format is
`owner:fresh_generation:heartbeat:active:valid:null_invalid`; it requires
`active > 0`, `active = valid`, `null_invalid = 0`, and every active member to
satisfy `gender in ('male','female')`. Its coverage segment remains the fixed
aggregate `active:valid:null_invalid`, and the null/invalid count is zero. The
acceptance script rechecks the same single snapshot after cutover. Acceptance evidence must contain only fixed
aggregate counts/status. It must not contain employee names, gender values,
provider identifiers, mobile numbers, ciphertext, raw rows, or provider payloads.

The pre-cutover bootstrap may have zero active owners only when publish is
invoked with the explicit `--allow-unbound-owner` flag. After the documented
owner-binding step, formal post-cutover acceptance requires exactly one active
owner; the bootstrap flag recorded in cutover state does not relax formal
acceptance.

After the probe and complete schema-v2 reconciliation pass, verify the
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
/opt/orbbec-agent-platform/current/deploy/cloud/publish-dingtalk-production.sh \
  /opt/orbbec-agent-platform/current
/opt/orbbec-agent-platform/current/deploy/cloud/accept-dingtalk-production.sh
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

Deploy the AI ADMIN strict consumer only after Platform publish and the
authenticated account proof have passed. Rollback AI ADMIN first, then perform
the Platform compatibility rollback; retain the synchronized nullable column
and do not delete directory data.

## Agent Brain opt-in release

The use entry is a separate fail-closed release gate. Keep the management root
available until every dependency is ready, and execute the following order
without skipping or combining a gate:

1. migrations with Brain disabled;
2. local `agent-brain-bot` on `127.0.0.1:9110`;
3. Worker allowlist and key registration;
4. cloud image with Brain disabled;
5. relay canary;
6. enable Brain;
7. switch `/` from the management entry to Agent 大脑.

The cloud environment flag is `PLATFORM_AGENT_BRAIN_ENABLED`. It is absent or
`0` during migration, image and relay validation. The authenticated root then
redirects to `/admin`; only an explicit value of `1` enables Mission APIs and
the use root. The relay remains separately controlled by
`PLATFORM_EXECUTION_RELAY_ENABLED=1`.

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
access to `/admin`, a real `hr-bot` ChildRun, stored event parity, Markdown
rendering in a fresh headless browser process, unauthorized Agent denial,
explicit interruption after a Worker stop, and no duplicate ChildRun after
Worker restart. Readiness and child-run discovery poll at five-second intervals
with fixed total time limits; do not replace them with a busy loop.

The evidence file may contain only release SHAs, container IDs and start times,
worker key ID, Mission IDs, run IDs, event sequences, listener addresses, FAE
probe results and rollback paths.
Do not record prompts, answers, cookies, DingTalk IDs, or secrets. Keep the file
at mode `0600`.
Every successful gate writes an immutable UUID-suffixed evidence generation and
atomically updates the configured evidence path to the newest generation. If a
current evidence file already exists, it is first retained as a separate
mode-`0600` previous generation. This allows `accept` and `restore` to rerun
without overwriting the release or rollback evidence used by the preceding
step.

Rollback sets the feature flag to `0`, recreates only Platform API/loopback,
and verifies `/admin`, Sessions, Review and Operations before it reports
success. Do not drop migration 032 or 033. Do not delete Mission data. The rollback
never restarts or modifies FAE or local MetaBots. The FAE container identity,
configuration and separate FAE domain/IP Nginx routes remain byte-for-byte
invariant; only the Agent Platform server block is intentionally replaced.
After the rollback exercise passes, `restore` returns the
reviewed release to the enabled state.

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
