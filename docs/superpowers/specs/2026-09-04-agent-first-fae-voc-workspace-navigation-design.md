# Agent-First FAE and VOC Workspace Navigation Design

**Date:** 2026-09-04

**Status:** Approved product design; implementation planning pending

**Systems:** Agent Platform, AI FAE Agent, VOC Agent

## 1. Decision

FAE and VOC are Agent products first. Their canonical workspace roots open the
Agent's direct-use experience; management is a secondary capability exposed by
a small navigation entry only to an authorized enterprise user.

```text
https://fae.orbbec.com.cn/       FAE Agent external-use entry
https://agent.orbbec.com.cn/fae/ FAE Agent internal-use entry
/fae/manage/                     FAE management workbench

/voc/                            VOC Agent direct-use entry
/voc/manage/                     VOC management workbench
```

The two FAE entries use the same FAE application build, backend behavior, chat,
attachments, and conversation UI. They are not two implementations, and the
internal route does not wrap or embed the public route. Authentication and
authorization remain appropriate to each origin.

The management workbenches stay separate pages. They are discoverable from the
Agent workspace but are never embedded as management content inside the normal
Agent task flow.

## 2. Relationship to Existing Designs

This specification narrows and clarifies the browser experience defined by
`2026-09-03-agent-workspace-route-separation-design.md`:

- `/fae/` and `/voc/` are direct-use roots;
- `/fae/manage/` and `/voc/manage/` remain the management families;
- the public FAE origin and internal FAE path deliberately share one FAE
  application implementation and product experience;
- management access is represented as a conditional affordance inside that
  shared experience.

It does not change the grant, audit, data, report, issue, Session, or deployment
rules in the existing FAE and VOC designs. Where the older independent FAE
workbench design assigned management directly to `/fae/`, the later route
separation design and this specification supersede that route assignment:
management is `/fae/manage/`.

## 3. Goals

1. Make opening an Agent immediately useful instead of landing users in an
   operations or administration page.
2. Keep one FAE product implementation across both FAE origins.
3. Let authorized internal users find management without adding a global
   management-first navigation hierarchy.
4. Keep external customers and partners unaware of internal management routes.
5. Preserve server-side authorization as the only security boundary.
6. Give FAE and VOC the same predictable navigation grammar.

## 4. Non-goals

- Do not merge the FAE Agent UI and the Platform FAE management workbench into
  one frontend bundle.
- Do not use an iframe.
- Do not redirect `/fae/` to `fae.orbbec.com.cn` or the public origin back to
  the Platform origin.
- Do not copy cookies, browser sessions, or local storage across origins.
- Do not expose management APIs, reports, Sessions, issues, or grants through
  the public FAE origin.
- Do not create a third FAE or VOC landing page.
- Do not change FAE Agent answers, data policy, attachments, or model behavior.
- Do not change VOC capture, draft confirmation, or record ownership semantics.

## 5. Product Navigation

### 5.1 FAE external entry

`https://fae.orbbec.com.cn/` renders the FAE Agent usage workspace. It supports
the same product interaction surface as the internal FAE workspace: direct
conversation, attachments, conversation history where the active identity
permits it, and the same visual components and release version.

Public-customer and partner identity behavior stays unchanged. The page never
requests internal management capability and never renders a management link.

### 5.2 FAE internal entry

`https://agent.orbbec.com.cn/fae/` renders the same FAE Agent application under
its internal base path. Platform DingTalk login and the existing one-time launch
exchange establish the enterprise-bound FAE Session.

The normal workspace remains focused on conversation. A compact `管理工作台`
entry appears in the top-right account/navigation area only when Platform says
the active enterprise subject may use the FAE workbench. The entry performs a
normal top-level navigation to `/fae/manage/`.

The entry is not shown while capability is loading, unavailable, absent, or
revoked. Hiding it is only a usability decision; direct requests to management
remain protected by Platform authorization.

### 5.3 FAE management

`/fae/manage/` remains owned and served by Agent Platform. Its local header
contains a `返回 FAE Agent` link to `/fae/`. Existing sections remain:

```text
overview | sessions | feedback and repair | analysis reports
```

The management route never falls through to the FAE upstream. The FAE direct
route never renders Platform management data.

### 5.4 VOC

`/voc/` renders the VOC Agent usage page: capture customer feedback, organize a
draft, let the user review and confirm it, and view the current user's records.

The top-right `管理工作台` entry is shown only when the existing VOC Session
projection contains the management capability. It navigates to `/voc/manage/`.
The management page contains a `返回 VOC Agent` link to `/voc/`.

The existing `?view=management` spelling remains only as the already-approved
compatibility redirect; it is not a third product route.

## 6. FAE Management Capability Projection

The public and internal FAE pages must keep the same FAE identity contract.
Management navigation is therefore not added to the FAE launch token and is not
trusted from FAE local storage.

On the internal `/fae/*` origin only, the FAE frontend requests a bounded
same-origin Platform navigation projection after enterprise identity succeeds:

```json
{
  "management_workspace_url": "/fae/manage/"
}
```

An unauthorized subject receives the same successful shape with
`management_workspace_url: null`. The endpoint returns no raw DingTalk
identifier, internal UUID, department, phone number, role, CSRF value, grant
record, or reason for denial. It uses the current Platform Session and evaluates
the current FAE workbench grant on every request.

The FAE frontend accepts only `null` or the exact relative path
`/fae/manage/`. Absolute URLs, another origin, query strings, fragments, encoded
path tricks, and any other path fail closed to a hidden entry.

The public FAE origin never calls this endpoint. Origin detection uses the
existing trusted browser-base configuration, not a query parameter or
client-supplied mode.

## 7. Identity and Authorization Boundaries

```text
FAE direct use on public origin
  public customer or approved partner identity

FAE direct use on Platform origin
  active DingTalk enterprise identity + FAE use authorization

FAE management
  active Platform Session + Platform Owner or active fae_workbench grant

VOC direct use
  active DingTalk enterprise identity + VOC use authorization

VOC management
  active enterprise identity + existing VOC management capability
```

The management navigation projection grants nothing. Every management page and
API request continues to apply the existing server-side authorization. A stale
button after revocation may lead to a friendly 403 page but cannot expose data.

FAE cookies stay host-scoped. “Same application” means one codebase, release,
feature contract, and UI; it does not mean copying an authenticated browser
session between `fae.orbbec.com.cn` and `agent.orbbec.com.cn`.

## 8. Route Ownership

Nginx keeps the more-specific Platform management family ahead of the FAE
upstream family:

```text
location ^~ /fae/manage/  -> Agent Platform
location ^~ /fae/         -> AI FAE Agent
location ^~ /voc/         -> VOC Agent, including its own management router
```

The canonical no-trailing-slash forms `/fae`, `/fae/manage`, `/voc`, and
`/voc/manage` redirect once to their trailing-slash roots while preserving only
already-approved safe query parameters.

Route ownership is asserted in deployment acceptance tests so a later Nginx
release cannot silently send `/fae/manage/*` into the FAE Agent or send
`/fae/*` into the Platform SPA.

## 9. Error Handling

- Unauthenticated internal navigation enters Platform login and safely returns
  to the original FAE or VOC route.
- An authenticated user without management permission can continue using the
  Agent. Direct management navigation produces a friendly 403 with a return
  link; it does not replace the whole workspace with a generic startup failure.
- A management-navigation projection failure hides the optional entry and does
  not block FAE chat, attachments, or history.
- A core identity failure is shown distinctly from a management capability
  failure and offers retry/login actions without discarding drafts.
- FAE or VOC management data unavailability stays inside the management page and
  cannot make the direct-use Agent homepage unavailable.
- Public FAE identity behavior remains unchanged when Platform management is
  unavailable.

## 10. Implementation Boundaries

### Agent Platform

- provide the minimal FAE navigation-projection endpoint;
- retain `/fae/manage/*` route and API authorization;
- add `返回 FAE Agent` to the FAE workbench shell;
- preserve explicit Nginx route ownership and acceptance assertions.

### AI FAE Agent

- render one compact management entry on the internal base path when the exact
  projection permits it;
- never request or render that entry on the public origin;
- keep one component tree and one build for public and internal use;
- preserve normal document navigation between independently owned bundles.

### VOC Agent

- retain `/voc/` as the usage root;
- keep the management link conditional on the existing capability;
- add or retain reciprocal `返回 VOC Agent` navigation;
- distinguish login, permission, identity-service, and transient startup errors.

## 11. Verification

Automated tests must prove:

1. both FAE origins render the same Agent usage components and supported
   features from the same source tree;
2. public and partner FAE modes never call the Platform navigation endpoint and
   never render `管理工作台`;
3. internal FAE without the grant renders the complete Agent page without the
   management entry;
4. internal FAE with the grant renders exactly one entry to `/fae/manage/`;
5. invalid or unavailable navigation projections fail closed without blocking
   Agent use;
6. `/fae/manage/` and every management API still reject unauthorized requests;
7. FAE management renders `返回 FAE Agent` and returns to `/fae/`;
8. `/voc/` is the VOC usage page for both ordinary and management-authorized
   users;
9. VOC management entry visibility follows the server projection, and direct
   unauthorized management access returns the permission state;
10. VOC management renders `返回 VOC Agent`;
11. route ownership survives direct refresh for every canonical route;
12. no management response or API is reachable through the public FAE origin.

Production acceptance uses real enterprise identities for one permitted and one
unpermitted account, plus an external/partner FAE session. It verifies page
content, link visibility, direct-route authorization, conversation continuity,
attachments, history, mobile layout, and rollback without exposing Session or
Cookie values in logs.

## 12. Release Sequence

1. Add and verify the bounded Platform navigation projection without changing
   existing FAE behavior.
2. Release FAE UI support while keeping the entry fail-closed.
3. Add reciprocal return navigation to the existing FAE management shell.
4. Align VOC startup errors and reciprocal navigation without changing VOC data
   semantics.
5. Run cross-origin FAE and internal VOC acceptance.
6. Deploy the route-owner acceptance assertions and complete a rollback drill.

Each repository remains independently rollbackable. Rolling back the optional
navigation entry must not roll back FAE chat, VOC submission, management data,
identity grants, or database migrations.
