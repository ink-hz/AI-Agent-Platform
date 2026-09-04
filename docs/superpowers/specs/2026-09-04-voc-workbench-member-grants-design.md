# VOC Workbench Member Grants Design

**Date:** 2026-09-04

**Status:** Approved product direction

**Systems:** Agent Platform identity/control plane, VOC extension authorization

## 1. Decision

VOC management access keeps the existing global-role path and adds an independent
per-member grant path. The effective policy is:

```text
allow_voc_management =
  valid active Platform identity
  AND (
    role in {platform_owner, platform_admin, management_viewer}
    OR active voc_workbench_manager grant
  )
```

The first explicitly granted member is the active directory member whose unique
display name is `稻夫`. The name is administrative lookup input only. Persistent
authorization binds to the resolved Platform internal-user UUID and never checks
a request by display name.

`稻夫` remains a `member`. His existing independent FAE Workbench grant remains
unchanged, so he receives FAE and VOC management access without receiving general
Platform management authority.

## 2. Boundaries

- Add a VOC-specific grant; do not reuse or broaden an FAE grant.
- Do not assign `management_viewer`, `platform_admin`, or `platform_owner` to a
  member merely to grant VOC access.
- Do not hardcode a display name or user UUID in application authorization code.
- Do not change FAE authorization, FAE grant rows, `/office/*`, Nginx, or another
  application's services.
- A hidden management link is not authorization. Every VOC management API request
  repeats the server-side decision.
- Ordinary VOC submission and self-read permissions remain unchanged.

## 3. Grant Model

Control migration `075` adds `platform_control.voc_workbench_grants`, mirroring
the established FAE grant lifecycle:

```text
grant_id
internal_user_id
permission = manager
created_by_internal_user_id
created_at
created_audit_event_id
revoked_at
revoked_by_internal_user_id
revoked_audit_event_id
row_version
```

Only one active VOC grant may exist per internal user. Revocation marks the row;
it never deletes history. Grant and revoke operations require Platform Owner,
CSRF protection, a fresh directory, a unique active directory display-name
match, optimistic row-version checks, idempotent operation IDs, and matching
requested/completed audit events. Lookup failures and unavailable control or
audit storage fail closed.

Owner-only management endpoints mirror the existing FAE endpoints:

```text
GET    /api/v1/manage/voc-workbench/grants
POST   /api/v1/manage/voc-workbench/grants
DELETE /api/v1/manage/voc-workbench/grants/{internal_user_id}
```

The initial production grant for `稻夫` must use this audited grant operation.
It must not be inserted directly or embedded in migration `075`.

## 4. Runtime Authorization

A dedicated `VocWorkbenchAccessService` evaluates the current role and current
grant state. Existing global management roles remain allowed. A granted member
receives `voc.read_all` in the signed VOC identity projection and passes the
Platform VOC admin BFF guard. The same decision controls the session field that
shows the VOC management entry.

Grant revocation or employee deactivation takes effect on the next request,
without waiting for the browser Session to expire. Hard-stale identity behavior
remains read-only and follows the existing global-role path; grants do not bypass
identity invalidation, CSRF, private-service bearer validation, upstream
capability checks, or VOC audit writes.

## 5. Tests

Tests must prove:

1. `platform_owner`, `platform_admin`, and `management_viewer` retain VOC
   management access.
2. An active `member` with only a VOC grant gets `voc.read_all`, sees the
   management entry, and can call each VOC management read API.
3. An ungranted member gets no management entry and receives 403 from the same
   APIs, including hand-constructed URLs.
4. FAE-only, VOC-only, and combined FAE-plus-VOC grants remain independent.
5. Revoked, inactive, locally invalidated, ambiguous-name, stale-directory, and
   storage-failure cases fail closed.
6. Grant and revoke operations are owner-only, CSRF-protected, idempotent,
   row-version checked, and audit-linked.
7. Production acceptance confirms `稻夫` remains `member`, keeps the existing FAE
   grant, gains one active VOC grant, and no unrelated service or Nginx process is
   restarted.

## 6. Rollout and Rollback

Deploy the Platform code and migration through the normal Platform release lock,
then create the initial audited VOC grant and validate the VOC page and admin API
using the real member Session. The migration is forward-only. Application
rollback leaves the inert grant table and history intact; it does not delete the
grant, revert migration `075`, alter the existing FAE grant, or restart unrelated
services.
