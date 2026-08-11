# Cloud Platform sanitized replica runbook

This runbook operates the read-only cloud replica of AI Agent Platform. It does
not publish a public Platform route. Access is through an SSH tunnel until
domain authentication is delivered.

## Non-negotiable boundaries

- Never change the existing FAE container, its port, Nginx, Langfuse, or the
  existing public listener set.
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

Success is exactly:

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

## Later domain and identity release

After DNS and identity approval, add exact records for
`agent.orbbec.com.cn` and `fae.orbbec.com.cn`. Nginx will use separate HTTPS
virtual hosts: `agent.orbbec.com.cn` to loopback Platform and
`fae.orbbec.com.cn` to the existing FAE listener. Add DingTalk (or Feishu)
behind the Platform `AuthProvider` and enable the three-role authorization and
HR isolation model before any public Platform access. The sanitizer, signing,
encryption, retention, backup, and one-way synchronization protocol stay
unchanged.
