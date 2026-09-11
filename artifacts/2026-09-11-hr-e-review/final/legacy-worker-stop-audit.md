# Legacy HR Bot stop audit

Operational clarification by release author: this file records repository capabilities, not an execution approval. Follow runbook §6.1 for the reviewed exit sequence: delete only `metabot-hr`, verify other-process snapshots in a fail-fast subshell, then save. The `restore-one ... stopped` capability below briefly starts the instance before stopping it and is not the selected exit command. No commands in this audit were executed.

Scope: read-only inspection of local checked-in deployment/process configuration in `Orbbec-Agent-Team`, `AI-FAE-Agent`, and the requested adjacent MetaBot location. No environment/secret file was read, no production host was contacted, and no stop/restart/save command was executed.

## Finding

The checked-in MetaBot runtime is split into one PM2 process per Bot. HR is not hosted inside a single shared `metabot` PM2 process: `deploy/metabot.runtime-contract.json` maps `hr-bot` to PM2 name `metabot-hr`, API port 9101, and its own state/config/log directories. Other Bots have distinct names (`metabot-default`, `metabot-marketing-*`, `metabot-fae`, `metabot-agent-brain`, etc.). Therefore the repository-supported way to stop only legacy HR while retaining the other Bot processes is the allowlisted single-process operation in `scripts/reliability/sanitized-pm2.sh`.

The wrapper's `restore-one ECOSYSTEM PM2_NAME stopped` path validates the ecosystem path and allowlisted PM2 name, deletes exactly that named process, starts only that ecosystem entry with PM2 `--only`, stops exactly that name, and verifies `state-one` equals `stopped`. `save` is a separate explicit operation. The wrapper runs PM2 with a fixed `agentops` identity, fixed `PM2_HOME`, sanitized proxy variables, and no inherited environment.

## Executable operator sequence

Run these only after the authorized drain proves old HR work is terminal. The commands below are sourced from the checked-in wrapper and runtime contract; they are not reported as executed.

```bash
OPS_PM2=/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh
ECOSYSTEM=/Users/agentops/AgentRuntime/metabot/ecosystem.config.cjs

sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr
sudo -n -H -u agentops /bin/bash "$OPS_PM2" restore-one "$ECOSYSTEM" metabot-hr stopped
sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr
sudo -n -H -u agentops /bin/bash "$OPS_PM2" save
```

The first and second `snapshot-except` JSON values must match for every non-HR fleet member's name, status, and restart count. The post-stop `state-one` output must be exactly `stopped`. `snapshot-except` deliberately excludes HR and checks every other allowlisted fleet name, so it is the repository-provided evidence that other Bot instances were not changed. `save` persists the selected PM2 process list/state; it must occur only after both checks pass.

If removal rather than a persisted stopped entry is the approved target, the wrapper also supports:

```bash
sudo -n -H -u agentops /bin/bash "$OPS_PM2" delete-one metabot-hr
sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr
sudo -n -H -u agentops /bin/bash "$OPS_PM2" save
```

In that variant `state-one` must be `absent`. Do not use `delete-all`, `replace-all`, bare `pm2 delete metabot`, or a broad service restart: the wrapper source shows those affect the fleet and are unnecessary for HR-only shutdown.

## Source evidence

- `Orbbec-Agent-Team/deploy/metabot.runtime-contract.json`: runtime user `agentops`; ecosystem path `/Users/agentops/AgentRuntime/metabot/ecosystem.config.cjs`; HR identity `hr-bot`; PM2 identity `metabot-hr`; port 9101; independent per-Bot paths.
- `Orbbec-Agent-Team/scripts/reliability/sanitized-pm2.sh`: allowlisted fleet, sanitized PM2 environment, strict `state-one`, `restore-one`, `snapshot-except`, `delete-one`, and `save` implementations.
- `Orbbec-Agent-Team/scripts/reliability/tests/deploy-gate.test.mjs`: fixtures and assertions treat `metabot-hr` independently, including stopped snapshots and `start-one`/`delete-one` behavior.
- `Orbbec-Agent-Team/docs/superpowers/plans/2026-07-24-hr-jd-sync.md` and `.../2026-07-27-hr-jd-sync-only-rollout.md`: operational examples explicitly restart/check only `metabot-hr` through the sanitized wrapper as `agentops`.
- `AI-FAE-Agent/deploy/docker-compose.prod.yml` and deployment scripts manage the separate FAE application stack; no checked-in mechanism there owns `metabot-hr` or requires stopping shared MetaBot peers.

The requested adjacent directory named `metabot` does not exist under `/Users/neo/Developer/work`. The local MetaBot operational contract and wrapper are present in `Orbbec-Agent-Team`; no additional independent MetaBot repository configuration was available to corroborate a second process manager.

## Operator-supplied facts still required

Before execution, the operator must confirm on the actual runtime host:

1. the host is the current MetaBot runtime host and the account authorized to use `sudo -n -H -u agentops`;
2. the deployed wrapper path and checksum correspond to the reviewed `sanitized-pm2.sh`;
3. the active ecosystem path is the reviewed `/Users/agentops/AgentRuntime/metabot/ecosystem.config.cjs` and contains exactly one `metabot-hr` entry for `hr-bot`;
4. the pre-stop `state-one` and `snapshot-except` outputs are captured as the rollback baseline;
5. the approved desired durable state is `stopped` or `absent`;
6. no separate launchd/systemd/cron watchdog outside the reviewed PM2 configuration recreates `metabot-hr`;
7. the platform Relay and non-HR PM2 instances have independent health evidence after the stop.

The repository establishes the HR-only PM2 control capability. It does not establish the current production host identity, deployed checksums, live PM2 state, external watchdog inventory, or authorization window.
