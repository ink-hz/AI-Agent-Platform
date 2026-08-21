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

Generate the new private/public pair in the owner-only runtime directory. On the
cloud host, use only the audited maintenance CLI:

```bash
python -m app.execution_relay.register_worker add-key agentops-mac-primary /run/private/execution-worker-public-v2.json RELAY_KEY_ROTATION_2026
python -m app.execution_relay.register_worker revoke-key agentops-mac-primary worker-v1 RELAY_KEY_ROTATION_2026
```

Deploy and verify the new signer between those commands. Never overwrite a key
ID with different bytes and never revoke the old key before the new Worker has
produced an accepted heartbeat.

## Worker revocation

```bash
python -m app.execution_relay.register_worker revoke-worker agentops-mac-primary RELAY_WORKER_REVOKE_2026
```

Revocation is audited and immediately makes signed lease/upload calls return
401. It does not delete Sessions or event history.

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

For an acceptance-tagged run, use the bounded cloud command:

```bash
python -m app.execution_relay.acceptance_cli interrupt RUN_UUID
```

For ordinary production jobs, request cancellation through the controlled
repository/API path. A database owner may use the following only after recording
an incident and proving the run cannot be recovered:

```sql
update platform_control.execution_jobs
set status='interrupted', terminal_at=now(), updated_at=now()
where run_id='RUN_UUID' and status=any(array['dispatching','dispatched','running']);
```

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

Removal requires the literal flag `--confirm-remove-agent-execution-worker` in
the operator wrapper. After backing up and revoking the cloud Worker, stop and
remove only its LaunchAgent and execute, as the PostgreSQL cluster owner:

```sql
drop database agent_execution_worker;
drop role agent_execution_worker_runtime;
drop role agent_execution_worker_migrator;
drop role agent_execution_worker_owner;
```

The removal procedure never stops the PostgreSQL service, never removes a
PostgreSQL data directory, and never drops or alters Flywheel, `postgres`,
`template0`, `template1`, Platform control, or replica databases.
