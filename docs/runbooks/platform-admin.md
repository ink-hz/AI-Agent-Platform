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
test -z "$(git status --porcelain)"
git push --atomic origin \
  HEAD:refs/heads/feat/agent-public-entry \
  HEAD:refs/heads/master
git fetch origin master feat/agent-public-entry
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)"
test "$(git rev-parse HEAD)" = \
  "$(git rev-parse origin/feat/agent-public-entry)"
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
3. In the target's authenticated browser developer console, copy only the
   stable internal account to the clipboard. The command returns no account
   payload and does not print the value:

   ```javascript
   copy((await fetch("/api/v1/account", { credentials: "include" })
     .then((response) => {
       if (!response.ok) throw new Error(`account ${response.status}`);
       return response.json();
     })).internal_user_id)
   ```

   Pass that value directly to the authorized owner/controller for the hidden
   prompts below. Do not place it in shell history, screenshots, or the final
   report. Close the target's developer console after copying it.
4. Sign in as `苍渊`, open `/identity`, and wait for the managed-user rows to
   finish loading. In the owner browser developer console, run the following
   correlation step and paste the target's stable internal account only into
   the prompt. It finds the exact API record, verifies the eligible state, and
   outlines the corresponding rendered row without printing the identifier:

   ```javascript
   const targetInternalId = prompt("Paste verified internal account");
   const managedResponse = await fetch("/api/v1/manage/users", {
     credentials: "include",
   });
   if (!managedResponse.ok) {
     throw new Error(`managed users ${managedResponse.status}`);
   }
   const managedUsers = (await managedResponse.json()).users;
   const matches = managedUsers
     .map((user, index) => ({ user, index }))
     .filter(({ user }) => user.internal_user_id === targetInternalId);
   if (matches.length !== 1) throw new Error("stable account mismatch");
   const { user, index } = matches[0];
   if (user.status !== "active" || user.role !== "member") {
     throw new Error("target is not an active member");
   }
   const rows = [...document.querySelectorAll(".identity-users article")];
   if (rows.length !== managedUsers.length || !rows[index]) {
     throw new Error("managed rows changed; reload and repeat");
   }
   rows.forEach((row) => { row.style.outline = ""; });
   rows[index].style.outline = "4px solid #ffbf00";
   rows[index].scrollIntoView({ block: "center" });
   console.table([{
     display_name: user.display_name,
     role: user.role,
     local_status: user.status,
   }]);
   ```

   The console output is limited to display name, role, and local status.
   Clear and close the console after confirming the outlined row. If the row
   count changes or any check fails, reload `/identity` and start again; never
   fall back to selecting a name-matched row.
5. Run the controller-side sanitized state query below before assignment. If
   there is no stable internal account, ask the target to open Agent
   Platform in DingTalk again, then repeat the check. If the account is not
   active in both local and directory state, if correlation is ambiguous, or
   if more than one candidate remains, stop. Never resolve ambiguity by name.

### Controller-side sanitized state query

There is no dedicated production script for this check. As authorized root on
the Platform host, use the existing PostgreSQL container and paste the stable
internal account into a silent prompt. This read-only query emits exactly five
fields: display name, role, local status, directory status, and the Session
revocation count from the most recent completed administrator-role event. It
does not emit the internal account or any DingTalk identity.

```bash
platform_root=/opt/orbbec-agent-platform
environment_path="$platform_root/private/platform.env"
compose_path="$platform_root/current/deploy/cloud/compose.yaml"
compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
postgres_id="$("${compose[@]}" ps -q platform-postgres)"
test -n "$postgres_id"

read -r -s -p "Paste verified internal account: " target_internal_id
/usr/bin/printf '\n'
[[ "$target_internal_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]

/usr/bin/docker exec -i "$postgres_id" /usr/bin/psql -X -A -F '|' \
  -P footer=off -U platform_owner -d agent_platform_control \
  -v ON_ERROR_STOP=1 -v target_internal_id="$target_internal_id" <<'SQL'
with target as (
  select internal_user_id,display_name,role::text as role,status,
    locally_invalidated_at,last_confirmed_generation_id
  from platform_control.internal_users
  where internal_user_id=:'target_internal_id'::uuid
), latest_admin_event as (
  select (event.sanitized_before_after->>'session_revocation_count')::integer
    as session_revocation_count
  from platform_control.audit_events event
  join target on event.target_internal_id=target.internal_user_id::text
  where event.event_type in (
      'admin_role_assignment_completed','admin_role_revocation_completed'
    ) and event.result='completed'
  order by event.occurred_at desc,event.audit_event_id desc
  limit 1
)
select target.display_name,target.role,
  case when target.status='active' and target.locally_invalidated_at is null
    then 'active' else 'inactive' end as local_status,
  case when target.last_confirmed_generation_id=state.active_generation_id
      and exists (
        select 1 from platform_control.directory_members member
        where member.generation_id=state.active_generation_id
          and member.internal_user_id=target.internal_user_id
          and member.status='active'
      )
    then 'active' else 'inactive' end as directory_status,
  coalesce(latest_admin_event.session_revocation_count,0)
    as session_revocation_count
from target
cross join platform_control.directory_state state
left join latest_admin_event on true
where state.singleton;
SQL

unset target_internal_id
```

Require exactly one data row. Before assignment it must report the confirmed
display name, role `member`, and both statuses `active`. After assignment and
after any revocation, repeat the same query by pasting the same verified value
at the silent prompt and require the expected role and revocation count.

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
5. Repeat the sanitized state query and require role `platform_admin`, both
   statuses `active`, and Session revocation count `0`.
6. In the same authorized root shell, with `postgres_id` retained from the
   state query and the stable internal account supplied again only through the
   silent prompt, verify the requested and completed assignment audit pair.
   The query prints only the success marker and fails unless the user's current
   `role_audit_event_id` is the exact requested event linked by one completed
   event with the same request, target, reason, and role transition:

   ```bash
   read -r -s -p \
     "Paste verified internal account: " target_internal_id
   /usr/bin/printf '\n'
   [[ "$target_internal_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]

   assignment_audit_count="$(/usr/bin/docker exec -i "$postgres_id" \
     /usr/bin/psql -X -A -t -U platform_owner -d agent_platform_control \
     -v ON_ERROR_STOP=1 -v target_internal_id="$target_internal_id" <<'SQL'
   select count(*)
   from platform_control.internal_users users
   join platform_control.audit_events requested
     on requested.audit_event_id=users.role_audit_event_id
   join platform_control.audit_events completed
     on completed.request_id=requested.request_id
    and completed.target_internal_id=requested.target_internal_id
   where users.internal_user_id=:'target_internal_id'::uuid
     and users.role='platform_admin'
     and requested.event_type='admin_role_assignment_requested'
     and requested.result='requested'
     and requested.reason_code='admin_access_approved'
     and completed.event_type='admin_role_assignment_completed'
     and completed.result='completed'
     and completed.reason_code='admin_access_approved'
     and completed.sanitized_before_after->>'linked_audit_event_id'
       =requested.audit_event_id::text
     and completed.sanitized_before_after->>'previous_role'='member'
     and completed.sanitized_before_after->>'new_role'='platform_admin';
SQL
   )"
   unset target_internal_id
   test "$assignment_audit_count" = "1"
   echo ADMIN_ASSIGNMENT_AUDIT_OK
   ```

   Stop unless this prints `ADMIN_ASSIGNMENT_AUDIT_OK`.

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
3. Verify the owner-only boundary from the administrator's authenticated
   `/identity` page. First acquire a separately approved eligible test target's
   stable internal account with the same no-output copy and correlation steps
   above. In the administrator browser developer console, run the following
   command and paste that value only into the prompt:

   ```javascript
   (async () => {
     const targetInternalId = prompt("Paste verified test internal account");
     const usersResponse = await fetch("/api/v1/manage/users", {
       credentials: "include",
     });
     if (!usersResponse.ok) {
       throw new Error(`managed users ${usersResponse.status}`);
     }
     const matches = (await usersResponse.json()).users.filter(
       (user) => user.internal_user_id === targetInternalId
         && user.status === "active" && user.role === "member",
     );
     if (matches.length !== 1) throw new Error("eligible target mismatch");
     const accountResponse = await fetch("/api/v1/account", {
       credentials: "include",
     });
     if (!accountResponse.ok) {
       throw new Error(`account ${accountResponse.status}`);
     }
     const csrfValue = (await accountResponse.json()).csrf_token;
     const response = await fetch(
       `/api/v1/manage/admins/${encodeURIComponent(targetInternalId)}`,
       {
         method: "POST",
         credentials: "include",
         headers: {
           Accept: "application/json",
           "Content-Type": "application/json",
           "X-CSRF-Token": csrfValue,
         },
         body: JSON.stringify({ reason: "admin_access_approved" }),
       },
     );
     console.log(JSON.stringify({ admin_assignment_status: response.status }));
   })();
   ```

   This keeps the browser Session and anti-forgery value in browser memory and
   prints only `{"admin_assignment_status":403}`. Clear and close the console,
   refresh `/identity`, and confirm the eligible test target remains `member`.
   The administrator must not receive or use any administrator-role control.
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
