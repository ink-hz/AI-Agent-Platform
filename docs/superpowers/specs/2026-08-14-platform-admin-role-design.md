# Platform Admin Role Design

## 1. Goal

Add a `platform_admin` role to Agent Platform so an explicitly authorized
employee can operate and manage the platform without becoming the unique
`platform_owner`.

The first intended administrator is the active DingTalk directory member whose
display name is `西门吹雪`. Display name is discovery evidence only. The role
assignment must target the stable `internal_user_id` created from a verified
DingTalk login and must never identify the account by name, mobile number, or a
browser-supplied provider identifier.

## 2. Decisions

- `苍渊` remains the sole active `platform_owner`.
- `platform_admin` is a distinct control-plane role, not a second owner and not
  an expanded `management_viewer`.
- An administrator has full existing operational and management-data access,
  subject to the same cloud read-only restrictions that already apply to the
  owner.
- An administrator may manage `management_viewer` roles and their exact Agent
  observation scopes.
- Only the owner may assign or revoke `platform_admin`.
- An administrator may not assign, revoke, replace, demote, or otherwise mutate
  the owner or another administrator.
- Offline owner binding and owner replacement remain owner break-glass
  operations; this feature does not modify them.
- An active directory member must complete one verified DingTalk login before
  becoming an administrator. The platform does not pre-provision an account by
  matching a display name.

## 3. Role Matrix

| Capability | member | management_viewer | platform_admin | platform_owner |
| --- | --- | --- | --- | --- |
| View own account | Yes | Yes | Yes | Yes |
| Use ordinary Agent entry | Existing R1 behavior | Existing R1 behavior | Existing owner behavior | Yes |
| Read all Agent, Session, Trace, Review, and Operations data | No | Exact granted Agent only | Yes | Yes |
| Execute existing operational mutations | No | No | Yes, unless cloud mode already makes the route read-only | Yes, under the same cloud-mode limit |
| Read the sanitized governance audit | No | Yes | Yes | Yes |
| List Platform users and roles | No | No | Yes | Yes |
| Assign/revoke management viewers and Agent scopes | No | No | Yes | Yes |
| Assign/revoke platform administrators | No | No | No | Yes |
| Bind or replace platform owner | No | No | No | Offline owner process only |

`platform_admin` is privileged but is not an ownership or break-glass role.
There may be multiple active administrators. The unique active-owner database
constraint remains unchanged.

## 4. Data Model and Migration

A new ordered control migration adds `platform_admin` to
`platform_control.user_role`. Existing rows retain their roles. The unique
partial index that permits only one active `platform_owner` remains intact.

The migration adds audited, security-definer functions for administrator role
assignment and revocation. Each function must:

1. require an active `platform_owner` actor;
2. require the target to be active, directory-confirmed, and locally valid;
3. accept only a `member` target for assignment and only a `platform_admin`
   target for revocation;
4. verify the matching requested audit event and optimistic row version;
5. write the canonical before/after result;
6. increment the target row version; and
7. revoke all active target Sessions when the role is revoked.

The function grants remain restricted to the existing application database
role. Direct table mutation remains revoked.

All role parsers, audit metadata validators, directory freshness checks, and
session-authentication functions must recognize `platform_admin`. A previously
bound active administrator receives the same hard-stale continuity as the
owner and viewer: read-only management access with a critical warning, while
all mutations return `503` until directory freshness recovers.

## 5. Backend Authorization

The authorization service treats `platform_admin` like `platform_owner` for
the existing owner route set, including full unscoped management reads. Existing
cloud-mode restrictions continue to run before role allowance, so the new role
does not turn the sanitized cloud replica into a writable Review store.

Identity management has a second, narrower gate:

- user listing, viewer-role changes, and observation-scope changes accept
  `platform_admin` or `platform_owner`;
- administrator assignment and revocation accept only `platform_owner`;
- the service and database both enforce the rule, so a forged request cannot
  bypass it through a direct URL;
- hard-stale sessions cannot perform any identity mutation;
- all mutations require a valid same-origin request, CSRF token, fresh directory,
  exact reason code, request ID, and an append-only audit request before commit.

New owner-only endpoints are:

- `POST /api/v1/manage/admins/{internal_user_id}` with reason
  `admin_access_approved`;
- `DELETE /api/v1/manage/admins/{internal_user_id}` with reason
  `admin_access_revoked`.

Both are idempotent only through the existing request-ID replay contract.
Conflicting target state returns `409`; missing audit or directory dependencies
return `503`; unauthorized callers receive `403`.

## 6. Audit and Session Behavior

Administrator role changes emit requested/completed or requested/rejected
events using the existing sensitive-mutation coordinator. Audit metadata
contains internal IDs, row versions, reason codes, and role transitions only;
it never contains raw DingTalk IDs, authorization codes, Cookies, mobile
numbers, or email addresses.

Privileged reads by an administrator follow the current owner read behavior.
Role mutations and management user-list reads remain audited. Revocation of an
administrator invalidates all of that user's active Platform Sessions in the
same transaction, forcing the next request to return `401`. Reassignment then
requires a new verified login.

## 7. Frontend Behavior

The frontend account contract and strict validators add `platform_admin`.
Administrators receive the same main navigation and data pages as the owner,
plus the identity-management page. The account page labels the role `平台管理员`.

On identity management:

- the owner sees controls to assign/revoke administrators and to manage viewers;
- an administrator sees the user list and viewer/scope controls;
- administrator controls are absent for administrators;
- neither role sees an operation that can mutate the owner;
- backend authorization remains authoritative regardless of rendered controls.

The initial promotion cannot be completed until `西门吹雪` has logged in once
and therefore appears in the managed-user list with a stable internal account.
After that login, the owner performs the administrator assignment from the
identity-management page, providing the fixed audited reason.

## 8. Verification

Automated tests must prove:

- migrations upgrade both production and preview databases without changing the
  current owner or existing roles;
- more than one administrator may be active while only one owner may be active;
- owner can assign and revoke an administrator with audit-first semantics;
- administrator cannot create, revoke, or mutate administrators or the owner;
- administrator can list users and manage viewers/scopes;
- administrator can access every existing operational route allowed to the owner,
  with identical cloud-mode write restrictions;
- member and viewer behavior is unchanged;
- hard-stale administrator access is read-only and audited;
- revocation terminates active administrator Sessions;
- frontend role parsing, labels, navigation, direct-route checks, and identity
  controls match the backend matrix; and
- unknown roles remain fail-closed.

Production acceptance must verify the immutable release, database migration,
five healthy Platform services, loopback-only port 8080, unchanged FAE identity,
and the existing public login boundary. After `西门吹雪` completes one real
DingTalk login, the owner assigns the role and the acceptance check confirms only
the role and successful authorized access; it must not output provider identity
values or Session material.

## 9. Rollback

Application rollback may point `current` back to the previous immutable release,
but an old binary cannot parse `platform_admin`. Therefore production promotion
must occur only after the new release is healthy, and rollback after assigning an
administrator first demotes every `platform_admin` to `member` through an audited
new-release operation and revokes their Sessions. The enum value itself remains
in PostgreSQL; removing enum values is not part of rollback.

The feature does not modify DingTalk credentials, Nginx domain routing, public
listeners, FAE, ADMIN, MetaBot Agents, replica contents, or attachment storage.
