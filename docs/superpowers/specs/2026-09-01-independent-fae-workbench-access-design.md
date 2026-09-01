# Independent FAE Workbench Access Design

**Date:** 2026-09-01

**Status:** Approved product direction; implementation pending

**Systems:** Agent Platform WebUI, control plane identity and authorization, FAE Workbench APIs

## 1. Decision

The FAE management workbench becomes an independent product workspace at:

```text
https://agent.orbbec.com.cn/fae/
```

It remains part of Agent Platform and reuses the existing DingTalk enterprise
identity, Platform Session, FAE data services, review services, report services,
audit writer, deployment process, and public origin. It is not a new application,
database, Nginx upstream, or identity system.

The first explicitly granted FAE Workbench managers are the active DingTalk
directory members whose unique display names are `天启` and `范闲`. These names
are administrative lookup input only. Persistent authorization binds to internal
identity UUIDs and never evaluates a request by comparing a display-name string.

## 2. Goals

1. Present FAE Operations as an independent workspace instead of a subsection of
   the general Platform management center.
2. Let the Platform Owner grant and revoke complete FAE Workbench access by
   entering a unique internal display name.
3. Support granting access before the target has logged in for the first time.
4. Give an FAE Workbench manager the same FAE page and endpoint authorization as
   the Platform Owner without granting general Platform administrator authority.
5. Enforce authorization on every backend request and record sensitive reads and
   mutations in the existing audit boundary.
6. Preserve existing bookmarks during migration and leave `/office/*` and
   `https://fae.orbbec.com.cn/` unchanged.

## 3. Non-goals

- Do not create a new FAE account, password, cookie, JWT, subdomain, service, or
  identity provider.
- Do not grant `platform_admin`, `management_viewer`, or Platform Owner merely to
  enter the FAE Workbench.
- Do not authorize by browser-visible state, navigation visibility, display name,
  department name, or a client-supplied internal user ID.
- Do not hardcode `天启` or `范闲` in application code or a database migration.
- Do not change the FAE customer-facing site, FAE Agent behavior, production FAE
  database, `/office/*`, or its Nginx routing.
- Do not make a read-only replica writable. Complete FAE authorization gives the
  holder every FAE operation that the active deployment safely exposes; an
  environment-level `write_available=false` remains fail-closed for Owner and
  FAE Workbench managers alike.

## 4. Product Routes

The canonical browser routes are:

```text
/fae/                         FAE overview
/fae/sessions                 Session list
/fae/sessions/{session_key}   Session detail
/fae/issues                   Feedback and repair governance
/fae/issues/{issue_id}        Governance item detail
/fae/reports                  Analysis reports
/fae/reports/{report_id}      Report detail
```

The FAE shell retains the existing four sections: Overview, Sessions, Feedback
and Repair, and Analysis Reports. It uses the Platform brand, account chip, and
logout/session behavior, but it does not render the general management-center
navigation or the general management-center replica banner. FAE-specific
freshness and read-only states remain inside the affected FAE page.

Read-only behavior must be derived from deployment/service capability, not from
the literal browser path. The current WebUI comparison against
`/admin/fae/issues` must be removed when canonical routes move, otherwise the new
path could accidentally render mutation controls against a read-only replica.

The browser treats `/fae` as a legacy spelling and replaces it with `/fae/`.
Each current `/admin/fae...` route maps to its exact `/fae...` equivalent,
preserving safe query parameters and replacing browser history. No old route may
redirect to `/office`, the FAE public domain, or a different origin.

The canonical API prefix becomes `/api/fae`. The old `/api/admin/fae` prefix may
remain for one release as a compatibility mount, but it must execute the same
authorization dependency and handler implementation. It must not retain the old
global-management-role check as an alternate access path. After the compatibility
window, it is removed in a separately tested cleanup.

## 5. Identity and Grant Model

### 5.1 New grant

Add an append-audited control-plane grant with this logical shape:

```text
fae_workbench_grants
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

There is at most one active grant for an internal user. Grant history is retained;
revocation never deletes a row. Only the Platform Owner can grant or revoke FAE
Workbench access. FAE Workbench managers cannot delegate their own authority.

The authorization decision is:

```text
allow_fae_workbench =
  valid Platform Session
  AND active internal user
  AND (
    active Platform Owner
    OR active fae_workbench_manager grant
  )
```

`platform_admin` alone does not satisfy this decision. Platform Owner access is
the break-glass path and cannot be removed through the FAE grant UI.

### 5.2 Grant by unique display name

The Owner enters a trimmed exact display name and a required business reason.
The backend resolves it only against active members in the latest complete
DingTalk directory generation.

- Zero active matches: reject with `directory_member_not_found`.
- More than one active match: reject with `directory_name_not_unique`.
- One inactive-only match: reject with `directory_member_inactive`.
- Exactly one active match: resolve or provision the internal identity, then
  create the grant.

Search suggestions may help the Owner choose a name, but the mutation repeats
the unique lookup inside its own transaction. The browser cannot submit a hidden
UUID to bypass that lookup.

### 5.3 Grant before first login

If the unique directory member has no `internal_user_id`, an audited,
security-definer operation provisions an inactive-session-free enterprise
identity from the verified active directory row. It creates or reuses the member
role internal user and its verified provider-identity mappings, then links the
directory member to that same internal UUID. It does not create a Web Session,
elevate the global role, or accept provider identifiers from the browser.

The operation must be idempotent and must fail closed on provider-identity
collision, stale directory data, ambiguous name, key-version mismatch, or an
indeterminate audit commit. When the person later signs in, the normal DingTalk
resolver must return the pre-provisioned internal UUID.

### 5.4 Immediate revocation and employment status

Every FAE API request evaluates current server-side state. Revoking a grant takes
effect on the next request without waiting for Session expiry. Directory events
that make the user inactive continue to revoke or block the Platform Session and
therefore also block the FAE Workbench. Historical grant and audit rows remain.

## 6. Backend Authorization

Replace the FAE router's current `platform_owner/platform_admin` dependency with
one dedicated FAE authorization dependency. Apply it to both canonical and
temporary compatibility API prefixes.

Read endpoints require `allow_fae_workbench`. Mutation endpoints additionally
require:

- valid CSRF verification;
- a fresh-enough directory under the existing mutation policy;
- `write_available=true` for the active Review/FAE backend;
- the existing concurrency, evidence, replay, and lifecycle preconditions;
- successful required audit writes.

The grant must not bypass the current read-only replica protection. A manager has
complete logical FAE authority, but unavailable infrastructure or a read-only
deployment produces the same explicit read-only or 503 state seen by the Owner.

Session detail access keeps its request/outcome audit pair. Governance mutations
continue to use the verified actor `corp:{internal_user_id}`; no actor field from
the browser is trusted.

## 7. Account and Frontend Authorization

The account response exposes a bounded workspace capability, for example:

```json
{"workspace_scopes":["fae_workbench"]}
```

The frontend uses this field only to choose navigation and render a fast local
denial state. It is not the security boundary. FAE routes are allowed for the
Owner or an account with `fae_workbench`; all data requests still pass backend
authorization.

FAE Workbench managers see a direct `FAE 工作台` product navigation item. They do
not see `管理中心` unless a separate Platform role already permits it. The
Platform Owner sees both.

The identity-management page adds an Owner-only `FAE 工作台权限` section with:

- exact display-name input;
- required reason input;
- current active grants with display name and status;
- grant and revoke actions;
- explicit results for missing, ambiguous, inactive, stale, and already-granted
  targets.

The initial `天启` and `范闲` grants are created after deployment through this
same audited operation or an equivalent Owner-only CLI wrapper. They are not
seeded by migration.

## 8. Data and Audit Boundaries

The independent route does not create another copy of FAE data. It reads the
same FAE Workbench, Review, observability, and report services used today.

Audit events cover at least:

- FAE grant requested, applied, rejected, and revoked;
- pre-login enterprise identity provisioned or safely replayed;
- FAE Session detail read requested and completed/failed;
- every existing FAE governance mutation and its result.

Audit metadata contains internal UUIDs, operation IDs, stable reason codes, and
sanitized before/after state. It must not include raw DingTalk identifiers,
Session text, customer content, cookies, CSRF values, or access tokens.

## 9. Error Handling

- Unauthenticated browser navigation enters the existing DingTalk login flow and
  returns to the exact safe `/fae/*` path.
- Authenticated but unauthorized access returns 403 from every FAE API and shows
  one stable FAE-specific permission page.
- Directory hard-stale state blocks new grants and mutations. Existing Owner
  continuity follows the current Platform hard-stale policy.
- A grant mutation whose commit outcome is indeterminate is not blindly retried
  with a new operation ID. The UI refreshes current server state and offers only
  same-request replay where the mutation coordinator permits it.
- Failure to write required audit evidence fails the sensitive operation.
- FAE data unavailability is shown inside the FAE workspace and does not redirect
  to the general management center or `/office`.

## 10. Security Properties

1. Display name is a grant lookup affordance, never a request-time identity key.
2. Exact uniqueness is rechecked transactionally against the active generation.
3. Pre-login provisioning consumes only server-owned encrypted directory rows.
4. The browser cannot choose an internal UUID, global role, or Provider identity.
5. Owner-only grant functions are security-definer functions with caller-role,
   freshness, row-version, request-ID, and audit-causality checks.
6. Web and database roles receive only function execution and required read
   columns; they receive no direct grant-table mutation permission.
7. Authorization is reevaluated per API request, so revocation is immediate.
8. `/api/admin/fae` compatibility cannot become a weaker bypass.

## 11. Testing

### 11.1 Database and service tests

- exact unique active name grants before and after first login;
- zero, duplicate, inactive, stale, and key-rotation mismatch cases fail closed;
- idempotent same-request grant and revoke replay;
- different-request duplicate grants do not create a second active row;
- pre-provisioned login resolves to the same internal UUID;
- Owner bypass, explicit manager allow, platform-admin-only deny, member deny;
- immediate revocation and inactive-member denial;
- required audit failure blocks the operation;
- direct table mutation remains unavailable to app and web roles.

### 11.2 API tests

- every `/api/fae` read and mutation endpoint has the dedicated dependency;
- canonical and compatibility prefixes return identical authorization decisions;
- mutations require CSRF, freshness, writer availability, and audit success;
- unauthorized responses reveal no FAE counts, titles, Session keys, or report
  metadata;
- safe login return paths accept `/fae/*` and reject cross-origin variants.

### 11.3 WebUI tests

- `/fae/*` renders the independent FAE shell without general admin navigation;
- Owner and granted manager see the FAE product entry;
- unrelated Platform admin and member do not see or enter it;
- old `/admin/fae/*` links map exactly to `/fae/*` with safe query preservation;
- all existing FAE internal links and deep links use the canonical path;
- cloud read-only state still hides governance mutations regardless of grant;
- desktop, tablet, and mobile layouts preserve navigation and account access.

### 11.4 Production acceptance

Using real enterprise identities, verify:

1. 苍渊 can access `/fae/` through Owner continuity.
2. 天启 and 范闲 can sign in and access all FAE sections.
3. A non-granted active member receives 403 and no FAE payload.
4. Revoking one test grant blocks the next API request without logging out.
5. `/office/?view=services`, `fae.orbbec.com.cn`, the FAE container, Nginx, and
   public listeners are unchanged.
6. Existing FAE report and governance data remain available and current.

## 12. Release Sequence and Rollback

1. Recheck the highest migration number across local and remote branches before
   assigning the new migration number.
2. Add the grant schema, security-definer operations, repository/service logic,
   account capability projection, and backend authorization tests.
3. Add canonical API routing while retaining the one-release compatibility mount.
4. Move WebUI routing and internal links to `/fae/*`; add legacy browser redirects.
5. Deploy with no grants except Owner continuity and run denial tests.
6. Grant `天启` and `范闲` through the audited Owner operation.
7. Run production identity, access, revocation, data, and invariant acceptance.
8. Remove the compatibility API prefix only in a later reviewed release.

Rollback restores the prior Platform release and leaves grant history intact.
If the prior release does not understand the new grant table, the unused table is
harmless and no user receives broader Platform authority. Rollback must not alter
or restart AI ADMIN, FAE, Nginx, or the FAE customer-facing site.

## 13. Completion Criteria

The change is complete only when:

- `/fae/*` is the canonical independent FAE workspace;
- the general management-center navigation is absent from that workspace;
- the Owner can grant and revoke by exact unique DingTalk display name;
- pre-login grants resolve to the same internal identity at first login;
- 天启 and 范闲 have complete FAE Workbench authorization without general
  Platform administrator authority;
- all FAE backend endpoints enforce the dedicated authorization decision;
- revocation is immediate and all sensitive operations are audited;
- old links remain safe during the compatibility window;
- `/office/*` and the public FAE service are unchanged.
