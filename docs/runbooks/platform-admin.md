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
set -euo pipefail
git diff --check
cd webui && npm test && npm run build && npm audit --omit=dev
cd ../backend && .venv/bin/python -m pytest -q
cd .. && deploy/cloud/acceptance.sh local
```

All commands must exit zero. Commit the runbook, rerun any gate affected by the
commit, and require a clean worktree. Push the one reviewed commit atomically,
without force push:

```bash
set -euo pipefail
if [[ -n "$(git status --porcelain)" ]]; then
  echo "worktree is not clean" >&2
  exit 1
fi
git push --atomic origin \
  HEAD:refs/heads/feat/agent-public-entry \
  HEAD:refs/heads/master
git fetch origin master feat/agent-public-entry
release_head="$(git rev-parse HEAD)"
remote_master="$(git rev-parse origin/master)"
remote_feature="$(git rev-parse origin/feat/agent-public-entry)"
if [[ "$release_head" != "$remote_master" ]]; then
  echo "origin/master does not match the reviewed release" >&2
  exit 1
fi
if [[ "$release_head" != "$remote_feature" ]]; then
  echo "origin/feat/agent-public-entry does not match the reviewed release" >&2
  exit 1
fi
```

Deploy only that immutable commit with the deployment-owned mode-0600
configuration, then run production acceptance:

```bash
set -euo pipefail
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
3. Sign in as `苍渊`, open `/identity`, and wait for the managed-user rows to
   finish loading. In the same controlled owner browser session, inspect the
   existing `GET /api/v1/manage/users` response. Locate the exact record created
   by the coordinated target login and confirm that the same rendered row shows
   the expected human-readable name, `在职`, and `企业成员`. The response's
   stable internal account is operator input, not evidence: do not copy it to
   the OS clipboard, save the response, take a screenshot, or include it in a
   report.
4. Keep the exact `/identity` row selected in the owner page. Without changing
   browser identity or handing the value to another user, manually transcribe
   its stable internal account from the controlled owner response directly into
   the silent terminal `read` below. The terminal does not echo it, and the
   procedure clears the variable immediately after the query. If the owner
   response does not correlate to exactly one row, if duplicate human-readable
   names make the row ambiguous, or if the page reloads, stop and repeat the
   coordinated login and owner lookup. Never choose a row by name alone.
5. Run the controller-side sanitized state query below before assignment. If
   there is no stable internal account, ask the target to open Agent
   Platform in DingTalk again, then repeat the check. If the account is not
   active in both local and directory state, if correlation is ambiguous, or
   if more than one candidate remains, stop. Never resolve ambiguity by name.

### Controller-side sanitized state query

There is no dedicated production script for this check. As authorized root on
the Platform host, use the existing PostgreSQL container and manually type the
stable internal account into a silent terminal read. This read-only query emits
exactly five fields: display name, role, local status, directory status, and the
aggregate count of Sessions revoked by administrator-role revocation. It does
not emit the internal account or any DingTalk identity.
Both identifier-handling blocks below run `set +x` immediately before the
hidden read. Shell tracing must remain disabled until the identifier variable
has been unset or the block exits; never run `set -x` inside either block.

```bash
set -euo pipefail
platform_root=/opt/orbbec-agent-platform
environment_path="$platform_root/private/platform.env"
compose_path="$platform_root/current/deploy/cloud/compose.yaml"
compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
postgres_id="$("${compose[@]}" ps -q platform-postgres)"
if [[ -z "$postgres_id" ]]; then
  echo "platform-postgres is unavailable" >&2
  exit 1
fi

set +x
read -r -s -p "Enter verified internal account: " target_internal_id
printf '\n'
uuid_pattern='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
if [[ ! "$target_internal_id" =~ $uuid_pattern ]]; then
  unset target_internal_id uuid_pattern
  echo "stable internal account format is invalid" >&2
  exit 1
fi
read -r -p "Expected role (member/platform_admin): " expected_role
if [[ "$expected_role" != "member" && "$expected_role" != "platform_admin" ]]; then
  unset target_internal_id uuid_pattern expected_role
  echo "expected role is invalid" >&2
  exit 1
fi

role_state="$(
  {
    printf '%s\n' "$target_internal_id"
    /bin/cat <<'SQL'
with target as (
  select internal_user_id,display_name,role::text as role,status,
    locally_invalidated_at,last_confirmed_generation_id
  from platform_control.internal_users
  where internal_user_id=
    current_setting('platform.target_internal_id')::uuid
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
  (select count(*) from platform_control.web_sessions session
   where session.internal_user_id=target.internal_user_id
     and session.revoked_at is not null
     and session.revoked_reason='admin_role_revoked')
    as session_revocation_count
from target
cross join platform_control.directory_state state
where state.singleton;
SQL
  } | /usr/bin/docker exec -i "$postgres_id" /bin/bash -c '
    set -euo pipefail
    IFS= read -r target_internal_id
    export PGOPTIONS="-c platform.target_internal_id=$target_internal_id"
    unset target_internal_id
    exec /usr/bin/psql -X -A -t -F "|" -U platform_owner \
      -d agent_platform_control -v ON_ERROR_STOP=1
  '
)"
unset target_internal_id uuid_pattern

if [[ -z "$role_state" || "$role_state" == *$'\n'* ]]; then
  unset expected_role role_state
  echo "sanitized state query did not return exactly one row" >&2
  exit 1
fi
IFS='|' read -r display_name actual_role local_status directory_status \
  session_revocation_count extra_field <<<"$role_state"
if [[ -n "${extra_field:-}" || -z "$display_name" \
    || "$actual_role" != "$expected_role" \
    || "$local_status" != "active" || "$directory_status" != "active" \
    || ! "$session_revocation_count" =~ ^[0-9]+$ ]]; then
  unset expected_role role_state display_name actual_role local_status \
    directory_status session_revocation_count extra_field
  echo "sanitized role-state gate failed" >&2
  exit 1
fi
printf '%s\n' \
  'display_name|role|local_status|directory_status|session_revocation_count' \
  "$role_state"
unset expected_role role_state display_name actual_role local_status \
  directory_status session_revocation_count extra_field
```

The block aborts before PostgreSQL if the identifier format or expected role is
invalid. It sends the identifier over stdin, moves it into a transient database
session setting, and keeps it out of process arguments and SQL error text. It
prints the five-field header and one data row only after every assertion passes.
Before assignment enter expected role `member`; after assignment enter
`platform_admin`; after revocation enter `member`. Capture the reported Session
revocation count without assuming its value, then reconcile the target's actual
reauthentication behavior as described below.

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
5. Repeat the sanitized state query with expected role `platform_admin` and
   require both statuses `active`. Record the returned Session revocation count
   without assuming it is zero. If the target's prior Session is no longer
   accepted, require a fresh login; otherwise continue with the still-valid
   authenticated Session and verify its new role.
6. Verify the requested and completed assignment audit pair. The standalone
   block prints only the success marker and fails unless the user's current
   `role_audit_event_id` is the exact requested event linked by one completed
   event with the same request, target, reason, and role transition:

   ```bash
   set -euo pipefail
   platform_root=/opt/orbbec-agent-platform
   environment_path="$platform_root/private/platform.env"
   compose_path="$platform_root/current/deploy/cloud/compose.yaml"
   compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
   postgres_id="$("${compose[@]}" ps -q platform-postgres)"
   if [[ -z "$postgres_id" ]]; then
     echo "platform-postgres is unavailable" >&2
     exit 1
   fi

   set +x
   read -r -s -p \
     "Enter verified internal account: " target_internal_id
   printf '\n'
   uuid_pattern='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
   if [[ ! "$target_internal_id" =~ $uuid_pattern ]]; then
     unset target_internal_id uuid_pattern
     echo "stable internal account format is invalid" >&2
     exit 1
   fi

   assignment_audit_count="$(
     {
       printf '%s\n' "$target_internal_id"
       /bin/cat <<'SQL'
   select count(*)
   from platform_control.internal_users users
   join platform_control.audit_events requested
     on requested.audit_event_id=users.role_audit_event_id
   join platform_control.audit_events completed
     on completed.request_id=requested.request_id
    and completed.target_internal_id=requested.target_internal_id
   where users.internal_user_id=
       current_setting('platform.target_internal_id')::uuid
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
     } | /usr/bin/docker exec -i "$postgres_id" /bin/bash -c '
       set -euo pipefail
       IFS= read -r target_internal_id
       export PGOPTIONS="-c platform.target_internal_id=$target_internal_id"
       unset target_internal_id
       exec /usr/bin/psql -X -A -t -U platform_owner \
         -d agent_platform_control -v ON_ERROR_STOP=1
     '
   )"
   unset target_internal_id uuid_pattern
   if [[ "$assignment_audit_count" != "1" ]]; then
     unset assignment_audit_count
     echo "administrator assignment audit pair is missing" >&2
     exit 1
   fi
   unset assignment_audit_count
   printf '%s\n' ADMIN_ASSIGNMENT_AUDIT_OK
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
   `/identity` page. The owner first selects a separately approved eligible test
   target through the same exact-row procedure above. In the controlled owner
   page, inspect that selected record's stable internal account without copying
   it. In the administrator browser developer console, run the block below and
   manually transcribe the value into the temporary masked in-page field. Do
   not use the OS clipboard or a browser prompt. The field clears and removes
   itself before any request:

   ```javascript
   (async () => {
     let targetInternalId = "";
     let csrfValue = "";
     const form = document.createElement("form");
     const label = document.createElement("label");
     const input = document.createElement("input");
     const submit = document.createElement("button");
     form.style.cssText = "position:fixed;z-index:2147483647;top:16px;left:16px;padding:16px;background:white;color:black;border:2px solid black";
     label.textContent = "Verified test internal account: ";
     input.type = "password";
     input.autocomplete = "off";
     input.setAttribute("aria-label", "Verified test internal account");
     submit.type = "submit";
     submit.textContent = "Run owner-only boundary check";
     label.append(input);
     form.append(label, submit);
     document.body.append(form);
     input.focus();

     try {
       targetInternalId = await new Promise((resolve) => {
         form.addEventListener("submit", (event) => {
           event.preventDefault();
           const value = input.value.trim();
           input.value = "";
           form.remove();
           resolve(value);
         }, { once: true });
       });
       if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(targetInternalId)) {
         throw new Error("stable internal account format is invalid");
       }

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
       csrfValue = (await accountResponse.json()).csrf_token;
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
       if (response.status !== 403) {
         throw new Error(`expected 403, received ${response.status}`);
       }

       const verifyResponse = await fetch("/api/v1/manage/users", {
         credentials: "include",
       });
       if (!verifyResponse.ok) {
         throw new Error(`verification users ${verifyResponse.status}`);
       }
       const unchanged = (await verifyResponse.json()).users.filter(
         (user) => user.internal_user_id === targetInternalId
           && user.status === "active" && user.role === "member",
       );
       if (unchanged.length !== 1) {
         throw new Error("eligible target changed after forbidden request");
       }
       console.log("ADMIN_ASSIGNMENT_FORBIDDEN_OK");
     } finally {
       input.value = "";
       form.remove();
       targetInternalId = "";
       csrfValue = "";
     }
   })();
   ```

   This keeps the browser Session and anti-forgery value in browser memory and
   emits `ADMIN_ASSIGNMENT_FORBIDDEN_OK` only after the response is exactly
   `403` and a fresh management read proves the target remains an active
   `member`. Any request or assertion failure throws and emits no success
   marker. Clear and close the console after success. The administrator must
   not receive or use any administrator-role control.
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
