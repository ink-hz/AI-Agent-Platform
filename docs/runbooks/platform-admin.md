# Platform administrator release and operations

This runbook releases and operates the `platform_admin` role. `苍渊` remains the
sole `platform_owner`; an administrator is privileged but is not an owner or a
break-glass identity. Only the authenticated owner assigns or revokes an
administrator, and every change is audited.

Never place a DingTalk provider identifier, authorization code, Cookie, CSRF
value, secret, or encryption material in a command, screenshot, ticket, or
report. A display name is only a human confirmation cue. Never create, select,
or promote an account by display name alone: every action must target the exact
stable internal account created by a verified DingTalk login.

## Preconditions

1. Start from the reviewed release commit in a clean worktree. Confirm the
   deployment configuration and SSH key are regular, current-user-owned
   mode-0600 files without printing their contents.
2. Confirm the current release includes control migrations 024 and 025, the
   directory is fresh, and all five Platform services are healthy.
3. Confirm `苍渊` is still the single active `platform_owner`. Administrator
   assignment never replaces, demotes, or duplicates the owner.
4. Identify the intended target through a live, coordinated login. Do not
   proceed from a directory name search, screenshot, mobile number, email
   address, or browser-supplied provider value.

## Release the reviewed commit

Run the complete local gate from the repository root:

```bash
git diff --check
cd webui && npm test && npm run build && npm audit --omit=dev
cd ../backend && .venv/bin/python -m pytest -q
cd .. && deploy/cloud/acceptance.sh local
```

All commands must exit zero. Commit the runbook, rerun any gate affected by the
commit, and require a clean worktree. Push the one reviewed commit atomically,
without force push:

```bash
git push --atomic origin \
  HEAD:refs/heads/feat/agent-public-entry \
  HEAD:refs/heads/master
git fetch origin master feat/agent-public-entry
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)"
```

Deploy only that immutable commit with the deployment-owned mode-0600
configuration, then run production acceptance:

```bash
deploy/cloud/deploy.sh \
  "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"

ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 \
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  root@47.106.112.69 \
  '/opt/orbbec-agent-platform/current/deploy/cloud/accept-dingtalk-production.sh'
```

Stop unless both commands report success. Confirm all five Platform services
are healthy, port 8080 remains loopback-only, and the FAE identity and start
time are unchanged before changing any role.

## Establish the target's stable internal account

1. Have the target open Agent Platform once from DingTalk and complete the
   normal in-client sign-in. A directory listing or a matching display name
   before this login is not an eligible account.
2. Confirm the target reaches the Account page and sees the expected
   human-readable name. Do not capture or report login artifacts.
3. Using a sanitized state check, confirm the login created a stable internal
   account and correlated it to exactly one active managed-user record. The
   check may report only display name, role, local status, directory status,
   and Session revocation count; it must not emit the stable identifier used
   for correlation.
4. If there is no stable internal account, ask the target to open Agent
   Platform in DingTalk again, then repeat the check. If the account is not
   active in both local and directory state, if correlation is ambiguous, or
   if more than one candidate remains, stop. Never resolve ambiguity by name.

## Owner-driven assignment

1. Sign in as `苍渊`. On the Account page, verify the current session reports
   `平台所有者`, then open `/identity`.
2. Locate the exact managed-user record already correlated to the target's
   stable internal account. Confirm its human-readable name, `在职` status,
   and `企业成员` role. The visible name alone is not selection evidence.
3. On that exact record, select `设为平台管理员`. Do not use a direct database
   update or an administrator session.
4. Require the success message and refreshed row to show `平台管理员`. If the
   request returns `403`, `409`, or `503`, or the refreshed state differs, stop
   and investigate; do not switch to a name-matched row or retry with copied
   browser credentials.

## Target relogin

A role change can invalidate a current Session. Have the target reopen Agent
Platform. If the next request returns `401` or the login page appears, complete
a new DingTalk sign-in. Never replay an authorization code or reuse, copy, or
report a Cookie or CSRF value. Verify the Account page now shows
`平台管理员`.

## Acceptance after assignment

Use the target's newly authenticated administrator session and record only
status codes and sanitized outcomes:

1. Open `/identity` and verify the owner-equivalent read route
   `GET /api/v1/manage/users` succeeds and presents the managed-user list.
2. On one separately approved, active test member whose stable internal
   account has been verified, enter the approved reason and select
   `设为只读观察者`. Confirm the audited change succeeds. On the same stable
   record, enter the approved cleanup reason, select `撤销只读观察者`, and
   confirm the member's prior role and scope state is restored. Never choose
   this acceptance subject by display name alone.
3. Through the approved authenticated acceptance harness, attempt
   `POST /api/v1/manage/admins/{internal_user_id}` as the administrator against
   a verified eligible test target. Confirm the response is `403` and no role
   mutation or audit-request event is created. Do not print the substituted
   internal identifier or any authentication material.
4. Run the sanitized production check. It may return only display name, role,
   local status, directory status, and Session revocation count. Confirm the
   assigned target is active and has role `platform_admin`.

## Normal revocation

1. Sign in as `苍渊`, verify `平台所有者`, and open `/identity` while the current
   release still supports `platform_admin`.
2. Locate the exact previously verified stable internal account. Confirm its
   human-readable name, `在职` status, and `平台管理员` role, then select
   `撤销平台管理员`.
3. Require the success message and refreshed row to show `企业成员`. Revocation
   returns the target to `member` and invalidates all of the target's active
   Platform Sessions in the same audited change.
4. Confirm the target's next request returns `401`. Run the sanitized check and
   confirm role `member` and the expected Session revocation count. A later use
   requires a new DingTalk sign-in.

Do not edit role or Session rows manually. If revocation is indeterminate,
preserve the request evidence and reconcile it through the current release;
do not issue an unrelated second mutation.

## Mandatory gate before rollback

Before rolling back to any binary that does not recognize `platform_admin`, use
the current release and the authenticated owner to enumerate every active
administrator by stable internal account. Revoke each one with the normal
procedure above. After every revocation, confirm the row is `member`, its active
Sessions were invalidated, and the audited completion exists.

Run a final sanitized aggregate check and require zero active
`platform_admin` accounts before starting the rollback. Do not infer this from
display names, do not demote the owner, and do not attempt the cleanup after an
older binary is running. If any administrator cannot be revoked and verified,
the rollback is blocked.

## Evidence record

Record the immutable release commit, frontend and backend test counts,
migration versions 024 and 025, production acceptance result, the sanitized
role result, Session revocation count, and any remaining human login action.
Do not include raw database rows or any provider identifier, authorization
code, Cookie, CSRF value, secret, or encryption material.
