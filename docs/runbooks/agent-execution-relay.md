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
`prepare` writes it only after all three next assets are complete. The prepare
and activate actions never use an interactive credential store and never
expose secret material.
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
set -euo pipefail
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
rotation_lock="$platform_root/private/execution-worker-key-rotation.lock"
rotation_state="$platform_root/private/execution-worker-key-rotation.state"
rotation_state_part="$platform_root/private/execution-worker-key-rotation.state.part"
write_cloud_rotation_phase() {
  next_phase="$1"
  expected_phase="$2"
  case "$next_phase:$expected_phase" in
    prepared:absent|accepted:prepared|committing:accepted|old_revoked:committing) ;;
    *) return 1 ;;
  esac
  /usr/bin/test ! -e "$rotation_state_part"
  if [[ "$expected_phase" = absent ]]; then
    /usr/bin/test ! -e "$rotation_state"
  else
    /usr/bin/test "$expected_phase" = "$(/usr/bin/sed -n '2s/^phase=//p' "$rotation_state")"
    /usr/bin/test "$(/bin/cat "$rotation_state")" = "$(printf '%s\n' schema_version=1 "phase=$expected_phase" from_key_id=worker-v1 to_key_id=worker-v2)"
  fi
  printf '%s\n' schema_version=1 "phase=$next_phase" from_key_id=worker-v1 to_key_id=worker-v2 >"$rotation_state_part"
  /bin/chmod 600 "$rotation_state_part"
  /bin/mv -f "$rotation_state_part" "$rotation_state"
}
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$platform_root/private")" = "700 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$maintenance_dsn")" = "600 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$worker_keyring")" = "600 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' /root/execution-worker-public-v2.json)" = "600 root"
umask 077
exec 9>>"$rotation_lock"
/usr/bin/flock -n 9
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$rotation_lock")" = "600 root"
/usr/bin/test ! -e "$registration_root"
/usr/bin/test ! -e "$worker_keyring_previous"
/usr/bin/test ! -e "$worker_keyring_part"
/usr/bin/test ! -e "$rotation_state"
/usr/bin/test ! -e "$rotation_state_part"
/usr/bin/install -o root -g root -m 600 "$worker_keyring" "$worker_keyring_previous"
/usr/bin/install -d -o root -g root -m 700 "$registration_root"
/usr/bin/install -o root -g root -m 600 /root/execution-worker-public-v2.json "$registration_root/worker.json"
maintenance=(/usr/bin/docker run --rm --pull=never --network orbbec-agent-platform-internal --user 0:0 -v "$platform_root/private:/run/control-secrets:ro" -v "$registration_root:/run/worker-registration:ro" -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url "$image" python -m app.execution_relay.register_worker)
write_cloud_rotation_phase prepared absent
# Audited maintenance action: register_worker add-key.
"${maintenance[@]}" add-key agentops-mac-primary /run/worker-registration/worker.json RELAY_KEY_ROTATION_2026
/usr/bin/install -o root -g root -m 600 "$registration_root/worker.json" "$worker_keyring_part"
/bin/mv -f "$worker_keyring_part" "$worker_keyring"
```

Keep this cloud shell open: file descriptor 9 holds the cloud rotation lock
through the local activation and either cloud finalize or cloud rollback. Do
not start a deploy, a second rotation, or a worker removal while it is held.

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
write_cloud_rotation_phase accepted prepared
```

Only after both acceptance commands succeed, revoke the old key on the cloud
host with `register_worker revoke-key`:

```bash
write_cloud_rotation_phase committing accepted
"${maintenance[@]}" revoke-key agentops-mac-primary worker-v1 RELAY_KEY_ROTATION_2026
write_cloud_rotation_phase old_revoked committing
```

Then finalize on the Mac, and remove the retained cloud backup and registration
assets on the cloud host:

```bash
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py finalize worker-v2
/bin/rm -f -- "$worker_keyring_previous"
/bin/rm -f -- "$registration_root/worker.json"
/bin/rmdir -- "$registration_root"
/bin/rm -f -- "$rotation_state"
/usr/bin/flock -u 9
exec 9>&-
```

If local `finalize` fails, rerun only `finalize worker-v2` until it either
completes or fails closed for operator investigation; do not run local
`rollback` after the cloud state reached `committing` or `old_revoked`.

Before any later Platform deploy, set the owner-only deploy configuration's
`CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING` to the activated canonical current
document `/Users/agentops/AgentRuntime/execution-worker-public.json`. This
prevents a later deploy from writing the retired `worker-v1` document back to
the cloud keyring.

If local activation, either acceptance command, or the fresh heartbeat and
fingerprint comparison fails after cloud `add-key`, roll back before any retry,
and only while the cloud phase is `prepared` or `accepted`.
On the Mac, inspect the persisted phase: `prepared` is aborted, while
`activating`, `active`, or `rolled_back` is recovered with rollback:

```bash
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py rollback worker-v2
```

On the cloud host, atomically restore the retained `worker-v1` keyring, revoke
the failed new key, and remove only the bounded registration directory:

```bash
cloud_rotation_phase="$(/usr/bin/sed -n '2s/^phase=//p' "$rotation_state")"
case "$cloud_rotation_phase" in prepared|accepted) ;; *) exit 1 ;; esac
/bin/mv -f "$worker_keyring_previous" "$worker_keyring"
"${maintenance[@]}" revoke-key agentops-mac-primary worker-v2 RELAY_KEY_ROTATION_ROLLBACK_2026
/bin/rm -f -- "$registration_root/worker.json"
/bin/rmdir -- "$registration_root"
/bin/rm -f -- "$rotation_state"
/usr/bin/flock -u 9
exec 9>&-
```

After an SSH disconnect, the old descriptor is released but the persistent
state remains. Do not rerun initial preparation. Open a new root shell, rebuild
`release`, `environment`, `compose`, `image`, `maintenance`, and the fixed path
variables exactly as above, then reacquire and validate before choosing exactly
one recovery direction:

```bash
set -euo pipefail
platform_root=/opt/orbbec-agent-platform
release="$(/usr/bin/readlink -f "$platform_root/current")"
environment="$platform_root/private/platform.env"
compose=(/usr/bin/docker compose --env-file "$environment" -f "$release/deploy/cloud/compose.yaml")
api_id="$("${compose[@]}" ps -q platform-api)"
image="$(/usr/bin/docker inspect --format '{{.Config.Image}}' "$api_id")"
registration_root="$platform_root/private/execution-worker-key-rotation"
worker_keyring="$platform_root/private/execution-worker-public-keyring.json"
worker_keyring_previous="$platform_root/private/execution-worker-public-keyring.previous.json"
worker_keyring_part="$platform_root/private/execution-worker-public-keyring.json.part"
rotation_lock="$platform_root/private/execution-worker-key-rotation.lock"
rotation_state="$platform_root/private/execution-worker-key-rotation.state"
rotation_state_part="$platform_root/private/execution-worker-key-rotation.state.part"
maintenance=(/usr/bin/docker run --rm --pull=never --network orbbec-agent-platform-internal --user 0:0 -v "$platform_root/private:/run/control-secrets:ro" -v "$registration_root:/run/worker-registration:ro" -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url "$image" python -m app.execution_relay.register_worker)
write_cloud_rotation_phase() {
  next_phase="$1"
  expected_phase="$2"
  case "$next_phase:$expected_phase" in
    accepted:prepared|committing:accepted|old_revoked:committing) ;;
    *) return 1 ;;
  esac
  /usr/bin/test ! -e "$rotation_state_part"
  /usr/bin/test "$expected_phase" = "$(/usr/bin/sed -n '2s/^phase=//p' "$rotation_state")"
  /usr/bin/test "$(/bin/cat "$rotation_state")" = "$(printf '%s\n' schema_version=1 "phase=$expected_phase" from_key_id=worker-v1 to_key_id=worker-v2)"
  printf '%s\n' schema_version=1 "phase=$next_phase" from_key_id=worker-v1 to_key_id=worker-v2 >"$rotation_state_part"
  /bin/chmod 600 "$rotation_state_part"
  /bin/mv -f "$rotation_state_part" "$rotation_state"
}
umask 077
exec 9>>"$rotation_lock"
/usr/bin/flock -n 9
/usr/bin/test -f "$rotation_state"
/usr/bin/test ! -e "$rotation_state_part"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$rotation_lock")" = "600 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$rotation_state")" = "600 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$worker_keyring_previous")" = "600 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$registration_root")" = "700 root"
/usr/bin/test "$(/usr/bin/stat -c '%a %U' "$registration_root/worker.json")" = "600 root"
cloud_rotation_phase="$(/usr/bin/sed -n '2s/^phase=//p' "$rotation_state")"
/usr/bin/test "$(/bin/cat "$rotation_state")" = "$(printf '%s\n' schema_version=1 "phase=$cloud_rotation_phase" from_key_id=worker-v1 to_key_id=worker-v2)"
(/usr/bin/cmp -s "$worker_keyring" "$worker_keyring_previous" || /usr/bin/cmp -s "$worker_keyring" "$registration_root/worker.json")
case "$cloud_rotation_phase" in
  prepared|accepted)
    cloud_recovery=resume-forward # Set exactly resume-forward or resume-rollback.
    ;;
  committing|old_revoked)
    # The old key may already be revoked: resume-forward only.
    cloud_recovery=resume-forward
    ;;
  *) exit 1 ;;
esac
case "$cloud_recovery" in
  resume-forward)
    if [[ "$cloud_rotation_phase" = prepared ]]; then
      # add-key is idempotent only for this exact key ID and public key bytes.
      "${maintenance[@]}" add-key agentops-mac-primary /run/worker-registration/worker.json RELAY_KEY_ROTATION_2026
      /usr/bin/test ! -e "$worker_keyring_part"
      /usr/bin/install -o root -g root -m 600 "$registration_root/worker.json" "$worker_keyring_part"
      /bin/mv -f "$worker_keyring_part" "$worker_keyring"
      # Keep descriptor 9 open; rerun local/cloud acceptance, then advance to accepted.
    elif [[ "$cloud_rotation_phase" = accepted ]]; then
      write_cloud_rotation_phase committing accepted
      "${maintenance[@]}" revoke-key agentops-mac-primary worker-v1 RELAY_KEY_ROTATION_2026
      write_cloud_rotation_phase old_revoked committing
    else
      key_status="$(
        /usr/bin/docker run --rm -i --pull=never --network orbbec-agent-platform-internal --user 0:0 \
          -v "$platform_root/private:/run/control-secrets:ro" \
          -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url \
          "$image" python - <<'PY'
import psycopg
from app.execution_relay.register_worker import _secret_file

with psycopg.connect(_secret_file()) as connection:
    rows = connection.execute(
        "select key_id,status from platform_control.execution_worker_keys "
        "where worker_id='agentops-mac-primary' "
        "and key_id in ('worker-v1','worker-v2') order by key_id"
    ).fetchall()
print("\n".join(f"{key_id}={status}" for key_id, status in rows))
PY
      )"
      if [[ "$cloud_rotation_phase" = committing ]]; then
        if [[ "$key_status" = $'worker-v1=active\nworker-v2=active' ]]; then
          "${maintenance[@]}" revoke-key agentops-mac-primary worker-v1 RELAY_KEY_ROTATION_2026
        else
          /usr/bin/test "$key_status" = $'worker-v1=revoked\nworker-v2=active'
        fi
        write_cloud_rotation_phase old_revoked committing
      else
        /usr/bin/test "$key_status" = $'worker-v1=revoked\nworker-v2=active'
      fi
      # old_revoked requires local finalize and cloud cleanup; it must never use resume-rollback.
    fi
    ;;
  resume-rollback)
    case "$cloud_rotation_phase" in prepared|accepted) ;; *) exit 1 ;; esac
    # Ensure a possibly interrupted add completed with the exact bytes, then undo it.
    "${maintenance[@]}" add-key agentops-mac-primary /run/worker-registration/worker.json RELAY_KEY_ROTATION_2026
    /usr/bin/test ! -e "$worker_keyring_part"
    /usr/bin/install -o root -g root -m 600 "$worker_keyring_previous" "$worker_keyring_part"
    /bin/mv -f "$worker_keyring_part" "$worker_keyring"
    "${maintenance[@]}" revoke-key agentops-mac-primary worker-v2 RELAY_KEY_ROTATION_ROLLBACK_2026
    /bin/rm -f -- "$registration_root/worker.json"
    /bin/rmdir -- "$registration_root"
    /bin/rm -f -- "$worker_keyring_previous"
    /bin/rm -f -- "$rotation_state"
    /usr/bin/flock -u 9
    exec 9>&-
    ;;
  *) exit 1 ;;
esac
```

If a disconnect occurred before the state rename, `rotation_state` is absent,
so no database mutation was reached. Rebuild the variables above, reacquire
descriptor 9, and clean only the fully bounded pre-mutation artifacts:

```bash
set -euo pipefail
umask 077
exec 9>>"$rotation_lock"
/usr/bin/flock -n 9
/usr/bin/test ! -e "$rotation_state"
/usr/bin/test "$(/usr/bin/stat -c '%F %a %U' "$rotation_state_part")" = "regular file 600 root"
/usr/bin/test "$(/usr/bin/stat -c '%F %a %U' "$worker_keyring_previous")" = "regular file 600 root"
/usr/bin/test "$(/usr/bin/stat -c '%F %a %U' "$registration_root/worker.json")" = "regular file 600 root"
/usr/bin/cmp -s "$worker_keyring" "$worker_keyring_previous"
/bin/rm -f -- "$rotation_state_part"
/bin/rm -f -- "$worker_keyring_previous"
/bin/rm -f -- "$registration_root/worker.json"
/bin/rmdir -- "$registration_root"
/usr/bin/flock -u 9
exec 9>&-
```

Restart initial preparation only after that block succeeds. Any other
state/part combination fails closed and is an incident; do not delete or
overwrite it.

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

The wrapper acquires the same persistent rotation lock before its role,
membership, ACL, and cross-database dependency preflight, and holds it through
the final unlink. It removes only the dedicated LaunchAgent, execution-worker
key/public/DSN, dedicated logs, fixed rotation next/previous/state assets, and
known acceptance residual files. The owner-only mode-0600 lock file is retained
so no concurrent process can acquire a replacement inode. It then executes
only:

```sql
drop database agent_execution_worker;
drop role agent_execution_worker_runtime;
drop role agent_execution_worker_migrator;
drop role agent_execution_worker_owner;
```

The removal procedure never stops the PostgreSQL service, never removes a
PostgreSQL data directory, and never drops or alters Flywheel, `postgres`,
`template0`, `template1`, Platform control, or replica databases.
