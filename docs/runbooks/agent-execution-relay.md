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

A cloud deploy holds the persistent root-only
`/opt/orbbec-agent-platform/private/deploy-input.lock` transaction from before
the first fixed upload through cutover. A second deploy fails before writing any
fixed `.part`. If the local deploy process is killed, the transaction is
deliberately left fail-closed. Do not remove it automatically. After confirming
there is no live deploy or `remote-stage.sh` process and auditing the exact
owner record, release only that recorded transaction with the deployed fixed
helper:

```bash
set -euo pipefail
lock_root=/opt/orbbec-agent-platform/private/deploy-input.lock
owner="$lock_root/owner.json"
helper=/opt/orbbec-agent-platform/bin/deploy-input-lock.py
[[ -d "$lock_root" && ! -L "$lock_root" && -f "$owner" && ! -L "$owner" ]]
[[ "$(/usr/bin/stat -c '%a %U' "$lock_root")" == "700 root" ]]
[[ "$(/usr/bin/stat -c '%a %U' "$owner")" == "600 root" ]]
[[ "$(/usr/bin/stat -c '%a %U' "$helper")" == "700 root" ]]
! /usr/bin/pgrep -f 'deploy/cloud/deploy.sh|/opt/orbbec-agent-platform/bin/remote-stage.sh'
release_sha="$(/usr/bin/jq -er 'if keys==["deployment_id","release_sha"] and (.release_sha|test("^[0-9a-f]{40}$")) and (.deployment_id|test("^[0-9a-f]{32}$")) then .release_sha else error("invalid") end' "$owner")"
deployment_id="$(/usr/bin/jq -er '.deployment_id' "$owner")"
"$helper" validate "$release_sha" "$deployment_id"
"$helper" release "$release_sha" "$deployment_id"
```

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
and copy only the public document to the cloud host. Never copy a private key:

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
/usr/bin/scp -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$cloud_admin_key" /Users/agentops/AgentRuntime/execution-worker-public.next.json "$cloud_admin_host:/root/execution-worker-public-worker-v2.json"
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

On the cloud host, run only the deployed root-owned helper's fixed actions. It
uses the current deployed image and exact maintenance DSN, inspects the exact
database status and public-key fingerprint before every mutation, and persists
each transition in
`/opt/orbbec-agent-platform/private/execution-worker-key-rotation-state.json`.
It accepts no command, path, DSN, or key material from environment variables:

```bash
cloud_rotator=/opt/orbbec-agent-platform/current/deploy/cloud/execution-worker-key-rotation.py
/usr/bin/test "$(/usr/bin/stat -c '%F %a %U' "$cloud_rotator")" = "regular file 700 root"
"$cloud_rotator" prepare worker-v2
"$cloud_rotator" activate worker-v2
```

`prepare` is safe to repeat after a crash before the state rename: it validates
and cleans only the exact fixed prior/staged artifacts. `activate` records
`adding` before `add-key`; if the maintenance response is lost, its next run
inspects the exact database row and fingerprint instead of adding again.

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
"$cloud_rotator" mark-accepted worker-v2
```

Only after both acceptance commands succeed, revoke the old key on the cloud
host with `register_worker revoke-key`:

```bash
"$cloud_rotator" commit worker-v2
```

Then finalize on the Mac, and remove the retained cloud backup and registration
assets on the cloud host:

```bash
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py finalize worker-v2
"$cloud_rotator" finalize worker-v2
```

If local `finalize` fails, rerun only `finalize worker-v2` until it either
completes or fails closed for operator investigation; do not run local
`rollback` after the cloud state reached `committing` or `old_revoked`.

For future deploys, do not point neo's deploy configuration into the mode-0700
`agentops` runtime. Create this public-only handoff once as root:

```bash
/usr/bin/install -d -o agentops -g staff -m 755 /Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public
```

After a successful rotation, `agentops` publishes only the canonical public
document through a fixed part and verifies its identity and fingerprint:

```bash
set -euo pipefail
umask 077
source_public=/Users/agentops/AgentRuntime/execution-worker-public.json
source_root=/Users/agentops/AgentRuntime
handoff_root=/Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public
handoff=/Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public/current.json
[[ -d "$source_root" && ! -L "$source_root" ]]
/bin/test "$([ -d "$source_root" ] && /usr/bin/stat -f '%Lp %Su' "$source_root")" = "700 agentops"
[[ -f "$source_public" && ! -L "$source_public" ]]
/bin/test "$([ -f "$source_public" ] && /usr/bin/stat -f '%Lp %Su' "$source_public")" = "600 agentops"
[[ -d "$handoff_root" && ! -L "$handoff_root" ]]
/bin/test "$([ -d "$handoff_root" ] && /usr/bin/stat -f '%Lp %Su' "$handoff_root")" = "755 agentops"
[[ ! -e "$handoff.part" && ! -L "$handoff.part" ]]
if [[ -e "$handoff" || -L "$handoff" ]]; then
  [[ -f "$handoff" && ! -L "$handoff" ]]
  /bin/test "$([ -f "$handoff" ] && /usr/bin/stat -f '%Lp %Su' "$handoff")" = "444 agentops"
fi
handoff_part_owned=0
cleanup_handoff_part() {
  if [[ "$handoff_part_owned" == "1" ]]; then
    [[ -f "$handoff.part" && ! -L "$handoff.part" ]]
    handoff_part_metadata="$(/usr/bin/stat -f '%Lp %Su' "$handoff.part")"
    [[ "$handoff_part_metadata" == "600 agentops" || "$handoff_part_metadata" == "444 agentops" ]]
    /bin/rm -f -- "$handoff.part"
  fi
}
trap cleanup_handoff_part EXIT
(set -C; : > "$handoff.part")
handoff_part_owned=1
/bin/chmod 600 "$handoff.part"
/usr/bin/install -m 600 "$source_public" "$handoff.part"
[[ -f "$handoff.part" && ! -L "$handoff.part" ]]
/bin/test "$([ -f "$handoff.part" ] && /usr/bin/stat -f '%Lp %Su' "$handoff.part")" = "600 agentops"
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python - "$source_public" "$handoff.part" worker-v2 <<'PY'
import base64, hashlib, json, pathlib, re, sys
source, staged = map(pathlib.Path, sys.argv[1:3])
expected = sys.argv[3]
left, right = source.read_bytes(), staged.read_bytes()
value = json.loads(right)
agents = ["hr-bot", "fae-bot", "marketing-prospecting-bot", "marketing-inbound-bot", "marketing-voice-bot", "marketing-intelligence-bot", "marketing-gtm-bot"]
keys = {"worker_id", "key_id", "public_key_base64url", "allowed_agent_ids"}
encoded = value.get("public_key_base64url") if isinstance(value, dict) else None
if left != right or set(value) != keys or value["worker_id"] != "agentops-mac-primary" or value["key_id"] != expected or value["allowed_agent_ids"] != agents or not isinstance(encoded, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded) is None:
    raise SystemExit(1)
public = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
if len(public) != 32 or base64.urlsafe_b64encode(public).decode().rstrip("=") != encoded:
    raise SystemExit(1)
print(hashlib.sha256(public).hexdigest())
PY
/bin/chmod 444 "$handoff.part"
/bin/mv -f "$handoff.part" "$handoff"
handoff_part_owned=0
trap - EXIT
```

As `neo`, copy that public file into a neo-owned secret boundary and verify the
same bytes, identity, and fingerprint before the atomic rename:

```bash
set -euo pipefail
umask 077
handoff=/Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public/current.json
handoff_root=/Users/Shared/OrbbecAI-Agent-Platform/execution-worker-public
neo_secret_root="/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/secrets"
neo_keyring="$neo_secret_root/execution-worker-public-keyring.json"
[[ -d "$handoff_root" && ! -L "$handoff_root" ]]
/bin/test "$([ -d "$handoff_root" ] && /usr/bin/stat -f '%Lp %Su' "$handoff_root")" = "755 agentops"
[[ -f "$handoff" && ! -L "$handoff" ]]
/usr/bin/install -d -m 700 "$neo_secret_root"
[[ -d "$neo_secret_root" && ! -L "$neo_secret_root" ]]
/bin/test "$(/usr/bin/stat -f '%Lp %Su' "$neo_secret_root")" = "700 neo"
/bin/test "$(/usr/bin/stat -f '%Lp %Su' "$handoff")" = "444 agentops"
[[ ! -e "$neo_keyring.part" && ! -L "$neo_keyring.part" ]]
if [[ -e "$neo_keyring" || -L "$neo_keyring" ]]; then
  [[ -f "$neo_keyring" && ! -L "$neo_keyring" ]]
  /bin/test "$([ -f "$neo_keyring" ] && /usr/bin/stat -f '%Lp %Su' "$neo_keyring")" = "600 neo"
fi
neo_part_owned=0
cleanup_neo_part() {
  if [[ "$neo_part_owned" == "1" ]]; then
    [[ -f "$neo_keyring.part" && ! -L "$neo_keyring.part" ]]
    /bin/test "$(/usr/bin/stat -f '%Lp %Su' "$neo_keyring.part")" = "600 neo"
    /bin/rm -f -- "$neo_keyring.part"
  fi
}
trap cleanup_neo_part EXIT
(set -C; : > "$neo_keyring.part")
neo_part_owned=1
/bin/chmod 600 "$neo_keyring.part"
/usr/bin/install -m 600 "$handoff" "$neo_keyring.part"
[[ -f "$neo_keyring.part" && ! -L "$neo_keyring.part" ]]
/bin/test "$([ -f "$neo_keyring.part" ] && /usr/bin/stat -f '%Lp %Su' "$neo_keyring.part")" = "600 neo"
/usr/bin/cmp -s "$handoff" "$neo_keyring.part"
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python - "$neo_keyring.part" worker-v2 <<'PY'
import base64, hashlib, json, pathlib, re, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
agents = ["hr-bot", "fae-bot", "marketing-prospecting-bot", "marketing-inbound-bot", "marketing-voice-bot", "marketing-intelligence-bot", "marketing-gtm-bot"]
keys = {"worker_id", "key_id", "public_key_base64url", "allowed_agent_ids"}
encoded = value.get("public_key_base64url") if isinstance(value, dict) else None
if set(value) != keys or value["worker_id"] != "agentops-mac-primary" or value["key_id"] != sys.argv[2] or value["allowed_agent_ids"] != agents or not isinstance(encoded, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded) is None:
    raise SystemExit(1)
public = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
if len(public) != 32 or base64.urlsafe_b64encode(public).decode().rstrip("=") != encoded:
    raise SystemExit(1)
print(hashlib.sha256(public).hexdigest())
PY
/bin/mv -f "$neo_keyring.part" "$neo_keyring"
neo_part_owned=0
trap - EXIT
```

Set neo's owner-only deploy configuration
`CLOUD_EXECUTION_WORKER_PUBLIC_KEYRING` to
`/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/secrets/execution-worker-public-keyring.json`.

If local activation, either acceptance command, or the fresh heartbeat and
fingerprint comparison fails after cloud `add-key`, roll back before any retry,
and only while the cloud phase is `prepared` or `accepted`.
On the Mac, inspect the persisted phase: `prepared` is aborted, while
`activating`, `active`, or `rolled_back` is recovered with rollback:

```bash
/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python /Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/rotate-worker-key.py rollback worker-v2
```

On the cloud host, restore `worker-v1` and revoke `worker-v2` with the single
rollback action. It retains the previous document until database inspection
confirms the new key is absent or revoked, then removes state last:

```bash
"$cloud_rotator" rollback worker-v2
```

After an SSH disconnect, inspect only the phase name, which contains no DSN or
key material:

```bash
/usr/bin/jq -er '.phase' /opt/orbbec-agent-platform/private/execution-worker-key-rotation-state.json
```

For transitional phases `adding`, `committing`, `restoring`, `revoking`, or
`finalizing`, run `recover`; it completes the interrupted fixed action after
exact database inspection:

```bash
"$cloud_rotator" recover worker-v2
```

Stable phases deliberately make `recover` fail: use `activate` for `prepared`,
rerun acceptance then `mark-accepted` for `cloud_active`, use `commit` for
`accepted`, and use `finalize` for `old_revoked`. Before the commit boundary
(`prepared`, `adding`, `cloud_active`, `accepted`, `restoring`, `revoking`, or
`revoked`) rollback remains available. At `committing`, `old_revoked`, or
`finalizing`, resume forward only and do not run local `rollback`.

Never overwrite a key ID with different bytes and never revoke the old key
before the accepted heartbeat and matching fingerprint gates.

## Worker revocation

```bash
/opt/orbbec-agent-platform/current/deploy/cloud/execution-worker-key-rotation.py revoke-worker
```

Revocation is audited and immediately makes signed lease/upload calls return
401. It does not delete Sessions or event history. The helper holds the same
cloud rotation lock, fails while any rotation state exists, and invokes the
fixed `register_worker revoke-worker` maintenance action.

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
