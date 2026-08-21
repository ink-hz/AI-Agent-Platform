# Agent execution relay runbook

This runbook operates the local `agentops-mac-primary` execution Worker and the
cloud relay control plane. User-facing Chat routes remain disabled. User-facing
Agent Brain routes remain disabled. Run commands as `agentops` on the Mac or as
the documented cloud administrator; never use Keychain or an interactive
password prompt.

## Release gate

Create an owner-only JSON config at
`/Users/agentops/AgentRuntime/private/acceptance-config.json` containing only
`schema_version`, the fixed cloud administrator host, and the absolute SSH key
path. Both the directory and file must remain mode 0700/0600. Run:

```bash
/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/accept.sh \
  /Users/agentops/AgentRuntime/private/acceptance-config.json
```

Success is exactly:

```text
AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 public_ports_added=0 duplicate_dispatches=0
```

Any other output or exit status is a failed release gate. `accept.sh` performs
live health, signed execution, crash recovery, revocation, Session, FAE, replica,
fingerprint and listener checks; it does not consume operator-authored evidence.

## Status

```bash
/bin/launchctl print "gui/$(/usr/bin/id -u)/com.orbbec.agent-execution-worker"
/usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN
/usr/sbin/lsof -nP -iTCP:9101-9108 -sTCP:LISTEN
```

The Worker PID may listen only on `127.0.0.1:9120`; 9101-9108 must have no
listener. Cloud status is checked through the release gate rather than by
printing credentials.

## Logs

```bash
/usr/bin/tail -n 200 /Users/agentops/AgentRuntime/log/execution-worker.out.log
/usr/bin/tail -n 200 /Users/agentops/AgentRuntime/log/execution-worker.err.log
```

Do not paste secret files, DSNs, callback tokens, signed headers or event bodies
into tickets.

## Key rotation

Run one rotation at a time. This example rotates `worker-v1` to the strict
target `worker-v2`; later targets use the same positive `worker-vN` form
without leading zeroes. On the `agentops` Mac, prepare the fixed next assets
and copy only the public document to the cloud host:

```bash
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py prepare worker-v2
/usr/bin/test "$(/usr/bin/stat -f '%Lp %Su' /Users/agentops/AgentRuntime/private/execution-worker-ed25519.next.key)" = "600 agentops"
/usr/bin/test "$(/usr/bin/stat -f '%Lp %Su' /Users/agentops/AgentRuntime/execution-worker-public.next.json)" = "600 agentops"
acceptance_config=/Users/agentops/AgentRuntime/private/acceptance-config.json
read -r cloud_admin_host cloud_admin_key < <(
  cd /Users/agentops/AgentRuntime/platform/backend
  .venv/bin/python - "$acceptance_config" <<'PY'
from pathlib import Path
import sys
from app.execution_relay.acceptance_orchestrator import load_config
path = Path(sys.argv[1])
config = load_config(path, private_root=path.parent)
print(config.cloud_admin_host, config.cloud_admin_key)
PY
)
/usr/bin/scp -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$cloud_admin_key" /Users/agentops/AgentRuntime/execution-worker-public.next.json "$cloud_admin_host:/root/execution-worker-public-v2.json"
```

The fixed local transaction state is
`/Users/agentops/AgentRuntime/private/execution-worker-key-rotation-state.json`.
The prepare and activate actions never use an interactive credential store and
never expose secret material.
If the rotation is abandoned before cloud `add-key`, delete only the validated
fixed next assets with:

```bash
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py abort worker-v2
```

On the cloud host, use the currently deployed Platform image, internal network,
and exact maintenance DSN
`/opt/orbbec-agent-platform/private/control-maintenance-database-url`. The
registration document has its own mode-0700 parent because the maintenance CLI
validates both parent and file:

```bash
platform_root=/opt/orbbec-agent-platform
release="$(/usr/bin/readlink -f "$platform_root/current")"
environment="$platform_root/private/platform.env"
compose=(/usr/bin/docker compose --env-file "$environment" -f "$release/deploy/cloud/compose.yaml")
api_id="$("${compose[@]}" ps -q platform-api)"
image="$(/usr/bin/docker inspect --format '{{.Config.Image}}' "$api_id")"
maintenance_dsn="$platform_root/private/control-maintenance-database-url"
registration_root="$platform_root/private/execution-worker-key-rotation"
worker_keyring="$platform_root/private/execution-worker-public-keyring.json"
worker_keyring_previous="$platform_root/private/execution-worker-public-keyring.previous.json"
worker_keyring_part="$platform_root/private/execution-worker-public-keyring.json.part"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$platform_root/private")" = "700 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$maintenance_dsn")" = "600 root"
/usr/bin/install -d -o root -g root -m 700 "$registration_root"
/usr/bin/install -o root -g root -m 600 /root/execution-worker-public-v2.json "$registration_root/worker.json"
maintenance=(/usr/bin/docker run --rm --pull=never --network orbbec-agent-platform-internal --user 0:0 -v "$platform_root/private:/run/control-secrets:ro" -v "$registration_root:/run/worker-registration:ro" -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url "$image" python -m app.execution_relay.register_worker)
# Audited maintenance action: register_worker add-key.
"${maintenance[@]}" add-key agentops-mac-primary /run/worker-registration/worker.json RELAY_KEY_ROTATION_2026
/usr/bin/test ! -e "$worker_keyring_previous"
/usr/bin/install -o root -g root -m 600 "$worker_keyring" "$worker_keyring_previous"
/usr/bin/install -o root -g root -m 600 "$registration_root/worker.json" "$worker_keyring_part"
/bin/mv -f "$worker_keyring_part" "$worker_keyring"
```

Back on the `agentops` Mac, atomically activate the prepared identity. The
wrapper stops the LaunchAgent before replacing the canonical private key,
public document and plist; an immediate failure restores all three and the
exact previous loaded state:

```bash
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py activate worker-v2
/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/accept.sh /Users/agentops/AgentRuntime/private/acceptance-config.json
```

Gate01-08 reads the canonical public document, verifies the installed plist and
private key use the same `worker-v2`, and queries that exact registered key and
fresh heartbeat. On the cloud host, independently verify the canonical keyring,
database key, fingerprint, and heartbeat:

```bash
/opt/orbbec-agent-platform/current/deploy/cloud/accept-dingtalk-production.sh
```

Only after both acceptance commands succeed, revoke the old key on the cloud
host with `register_worker revoke-key`:

```bash
"${maintenance[@]}" revoke-key agentops-mac-primary worker-v1 RELAY_KEY_ROTATION_2026
```

Then finalize on the Mac, and remove the retained cloud backup and registration
assets on the cloud host:

```bash
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py finalize worker-v2
/bin/rm -f -- "$worker_keyring_previous"
/bin/rm -f -- "$registration_root/worker.json"
/bin/rmdir -- "$registration_root"
```

Before any later Platform deploy, set the owner-only deploy configuration's
`CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING` to the activated canonical current
document `/Users/agentops/AgentRuntime/execution-worker-public.json`. This
prevents a later deploy from writing the retired `worker-v1` document back to
the cloud keyring.

If local activation, either acceptance command, or the fresh heartbeat and
fingerprint comparison fails after cloud `add-key`, roll back before any retry.
On the Mac, the state file exists only after an activation that needs explicit
rollback:

```bash
if /usr/bin/test -f /Users/agentops/AgentRuntime/private/execution-worker-key-rotation-state.json; then
  /Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py rollback worker-v2
fi
```

On the cloud host, atomically restore the retained `worker-v1` keyring, revoke
the failed new key, and remove only the bounded registration directory:

```bash
/bin/mv -f "$worker_keyring_previous" "$worker_keyring"
"${maintenance[@]}" revoke-key agentops-mac-primary worker-v2 RELAY_KEY_ROTATION_ROLLBACK_2026
/bin/rm -f -- "$registration_root/worker.json"
/bin/rmdir -- "$registration_root"
```

Never overwrite a key ID with different bytes and never revoke the old key
before the accepted heartbeat and matching fingerprint gates.

## Worker revocation

```bash
platform_root=/opt/orbbec-agent-platform
release="$(/usr/bin/readlink -f "$platform_root/current")"
environment="$platform_root/private/platform.env"
compose=(/usr/bin/docker compose --env-file "$environment" -f "$release/deploy/cloud/compose.yaml")
api_id="$("${compose[@]}" ps -q platform-api)"
image="$(/usr/bin/docker inspect --format '{{.Config.Image}}' "$api_id")"
maintenance_dsn="$platform_root/private/control-maintenance-database-url"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$platform_root/private")" = "700 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$maintenance_dsn")" = "600 root"
/usr/bin/docker run --rm --pull=never --network orbbec-agent-platform-internal --user 0:0 -v "$platform_root/private:/run/control-secrets:ro" -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url "$image" python -m app.execution_relay.register_worker revoke-worker agentops-mac-primary RELAY_WORKER_REVOKE_2026
```

Revocation is audited and immediately makes signed lease/upload calls return
401. It does not delete Sessions or event history.
The container command above invokes `register_worker revoke-worker`.

## Backup

Back up only the dedicated local PostgreSQL database:

```bash
/opt/homebrew/opt/postgresql@17/bin/pg_dump --format=custom --file=/Users/agentops/AgentRuntime/private/agent_execution_worker.dump agent_execution_worker
```

Keep the dump mode 0600 in a mode-0700 directory.

## Restore

Stop only the execution Worker, restore into a separately prepared empty
`agent_execution_worker` database, reapply `worker_schema.sql`, and verify the
exact runtime grants before restart:

```bash
/bin/launchctl bootout "gui/$(/usr/bin/id -u)/com.orbbec.agent-execution-worker"
/opt/homebrew/opt/postgresql@17/bin/pg_restore --exit-on-error --clean --if-exists --dbname=agent_execution_worker /Users/agentops/AgentRuntime/private/agent_execution_worker.dump
```

Never restore over Flywheel or another Platform database.

## Stuck job

Inspect the dedicated local state without printing payloads:

```sql
select run_id, agent_id, state, leased_at, dispatched_at, terminal_at
from execution_worker.local_runs
order by leased_at desc limit 50;

select run_id, count(*) event_count,
       count(*) filter (where delivered_at is null) undelivered_count
from execution_worker.event_outbox
group by run_id order by run_id;
```

`dispatching|dispatched|running` jobs are never requeued automatically. An
unknown dispatch result must never cause another MetaBot POST. Recovery either
resumes upload from the same local outbox or terminalizes that same run as
`interrupted`.

## Explicit interruption

`acceptance_cli` is permitted only while the release gate has created an active acceptance environment with
`PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ENABLED=1`, its marker, database URL, and
content keyring. Inside that bounded environment the orchestrator executes:

```bash
docker exec --user 10001:10001 -e PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ENABLED=1 -e PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ROOT=/run/secrets/execution-relay-acceptance -e PLATFORM_EXECUTION_RELAY_ACCEPTANCE_MARKER_FILE=/run/secrets/execution-relay-acceptance/enabled -e PLATFORM_CONTROL_DATABASE_URL_FILE=/run/secrets/execution-relay-acceptance/control-database-url -e PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE=/run/secrets/execution-relay-acceptance/content-keyring PLATFORM_API_CONTAINER python -m app.execution_relay.acceptance_cli interrupt RUN_UUID
```

Never use that CLI for an ordinary production job or reconstruct its acceptance
environment. Production interruption must use the owner-authorized control-plane
cancel workflow that calls `ExecutionRelayRepository.request_cancel`; a successful
request records `cancel_requested=true`, and the active Worker observes it through
its signed heartbeat. If that controlled workflow is unavailable, record the
incident and leave the job and outbox intact rather than issuing owner SQL or
forcing `status='interrupted'`.

Never change a terminal run and never create a replacement run automatically.

## Restart

```bash
/bin/launchctl kickstart -k "gui/$(/usr/bin/id -u)/com.orbbec.agent-execution-worker"
```

After restart, run Status and confirm the same undelivered outbox drains.

## Rollback

Boot out the Worker, restore the previously reviewed code/config/key files, then
bootstrap the saved LaunchAgent plist. Do not roll back the local database past
an accepted MetaBot dispatch. If compatibility is uncertain, leave the Worker
stopped and explicitly terminalize affected runs as `interrupted`.

## Removal

Removal requires an existing verified custom-format backup at the exact
mode-0600 path shown below and the literal confirmation flag. After revoking the
cloud Worker, run the owner-only wrapper as `agentops`:

```bash
/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/remove.sh \
  /Users/agentops/AgentRuntime/private/postgres-owner-dsn \
  /Users/agentops/AgentRuntime/private/agent_execution_worker.dump \
  --confirm-remove-agent-execution-worker
```

The wrapper completes its role, membership, ACL, and cross-database dependency
preflight before bootout or unlink. It removes only the dedicated LaunchAgent,
execution-worker key/public/DSN, dedicated logs, fixed rotation
next/previous/state/lock assets, and known acceptance residual files. It then
executes only:

```sql
drop database agent_execution_worker;
drop role agent_execution_worker_runtime;
drop role agent_execution_worker_migrator;
drop role agent_execution_worker_owner;
```

The removal procedure never stops the PostgreSQL service, never removes a
PostgreSQL data directory, and never drops or alters Flywheel, `postgres`,
`template0`, `template1`, Platform control, or replica databases.
