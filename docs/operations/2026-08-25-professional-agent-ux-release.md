# Professional Agent UX production release

Date: 2026-08-25  
Status: application and producer released; authenticated browser acceptance pending

## Scope

This release improves the employee-facing HR and Marketing Agent experience:

- public answers use the typed `core_chat_result_v2` contract;
- member pages no longer expose Mission IDs, raw statuses, Bot IDs, or diagnostic details;
- professional Agent navigation, scoped history, rename/archive/restore, feedback, progress, and responsive layout are available;
- authorized Management Center diagnostics remain separate from employee use pages.

AI ADMIN and FAE were frozen boundaries for this release. No AI ADMIN code,
`/office/*` route, shared Nginx configuration, or FAE application was changed.

## Release identifiers

| Component | Before | After |
|---|---|---|
| MetaBot | `ccf41cdd15a31177396918c6b1f0775c4e2011fd` | `350abdf45d281b109439fdb27605852b95f706f9` |
| Agent Platform | `4788f17086712d1095906d79b01e594efe9f9b6e` | `e7dc86b97e62962ee9ff27fef7134cad70daa27e` |

MetaBot rollout backup:

```text
/Users/agentops/AgentRuntime/backups/api-loopback-20260825T031200Z-350abdf45d281b109439fdb27605852b95f706f9
```

## Verification evidence

### Source verification

- Platform backend: `2796 passed, 1 skipped`.
- Platform WebUI: `45` files and `374` tests passed.
- Platform production WebUI build passed.
- MetaBot root package, excluding nested Git worktrees: `1004 passed, 1 skipped`.
- MetaBot workspaces: CLI `36`, MetaMemory `42`, Skill Hub `6`, Server `359` tests passed.
- MetaBot bridge build passed.
- Changed MetaBot files pass ESLint. The repository-wide lint command still reports four pre-existing errors in unrelated legacy test files; this release did not alter those files.
- Shell syntax and `git diff --check` passed.

The unqualified MetaBot root test command discovers independent repositories
under `.worktrees/` and reports six test-less spike files. The release test used
the explicit exclusion `--exclude '**/.worktrees/**'`; no production test failed.

### Producer and Worker

- HR and all five Marketing processes are online on MetaBot release `350abdf`.
- Each of the six business Agent capability endpoints advertises
  `core_chat_result_v2`.
- The local execution Worker is online on loopback port `9120` and its strict
  HR v2 contract assertion passed.
- Public HR probe: completed successfully, `publicAnswerMarkdown` present,
  forbidden internal prefix absent.
- Internal Agent Brain probe: completed successfully, `outputText` present,
  `publicAnswerMarkdown` absent.
- Probe output recorded only contract booleans; it did not record answer text,
  internal output, credentials, or provider payloads.

### Cloud Platform

- Cloud deploy returned:

```text
CLOUD_PLATFORM_DEPLOY_OK release=e7dc86b97e62962ee9ff27fef7134cad70daa27e mode=dingtalk
```

- All six Compose services are running and healthy: API, Brain worker,
  DingTalk Stream, directory worker, loopback proxy, and PostgreSQL.
- Control migration `044` is applied.
- Public health endpoint returns `status=ok`.
- Unauthenticated `/` redirects to `/login`, never `/admin`.

After this release completed, the independent DingTalk multi-application login
work advanced production to `2f6147151fc51189673ddcdabc26e26edc7d0fde`.
That commit is a descendant of `e7dc86b` and therefore contains this entire
Professional Agent UX release. Platform health, the local Worker v2 gate, FAE
invariance, and the canonical Office route were rechecked against the later
production release.

### Frozen-route and FAE invariance

The canonical administrative portal remains:

```text
https://agent.orbbec.com.cn/office/?view=services
```

Before and after Platform deployment it returned HTTP 200, retained the exact
effective URL, and returned byte-identical HTML.

FAE before and after evidence is identical:

```text
container_id=113dd1c870e82a36719ea992f351f66916fbf1556842ea784ea6c1d858cad22e
image_id=sha256:c65c63944523aa5d1029ffc915320f146f883f94ce121ad5c2b5e05f907d9161
started_at=2026-08-14T02:14:39.751109793Z
restart_count=0
```

`https://fae.orbbec.com.cn/` remained healthy.

## Pending authenticated acceptance

No controllable signed-in browser was available during the release window, and
the ephemeral `agentops` cross-cloud acceptance SSH configuration was absent.
Therefore the following checks are intentionally not marked complete:

- real member HR follow-up in the same conversation;
- five-card Marketing switching and Agent-scoped history;
- rename/archive/restore and structured feedback through the production UI;
- authorized Management Center inspection of the corresponding runs;
- mobile-width and DingTalk mobile-client behavior.

These are UI acceptance items, not known deployment failures. Infrastructure,
producer contract, direct public/internal execution, frozen routes, migration,
and cloud health passed.

## Rollback

Rollback order is Platform first, then MetaBot only if required. Migration 044
is additive and may remain applied.

Platform rollback (after confirming no active `metabot_local` jobs):

```bash
ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 root@47.106.112.69 \
  /opt/orbbec-agent-platform/current/deploy/cloud/rollback-dingtalk-production.sh
```

The rollback baseline must retain and recheck
`/office/?view=services`; it must not restart FAE.

MetaBot HR/Marketing rollback, only after Platform rollback:

```bash
sudo -n -u agentops /usr/bin/env -i \
  HOME=/Users/agentops USER=agentops LOGNAME=agentops \
  PATH=/Users/agentops/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  PM2_HOME=/Users/agentops/.pm2 TMPDIR=/Users/agentops/AgentRuntime/tmp \
  /bin/zsh -c 'cd /Users/agentops && \
    /Users/agentops/AgentRuntime/deploy-tools/attachment_archive_agentops_stage.sh \
      restore-release /Users/agentops/AgentRuntime/metabot-releases \
      /Users/agentops/AgentRuntime/metabot-releases/releases/ccf41cdd15a31177396918c6b1f0775c4e2011fd && \
    /Users/agentops/AgentRuntime/deploy-tools/attachment_archive_agentops_stage.sh \
      restart-business-bots'
```

After any rollback, re-run the Office, FAE, process-online, and public-answer
contract checks before reopening employee use.
