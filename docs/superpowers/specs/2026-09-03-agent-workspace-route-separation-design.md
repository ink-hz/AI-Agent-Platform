# Agent Workspace Route Separation Design

**Date:** 2026-09-03

**Status:** Approved product design; implementation planning pending

**Systems:** Agent Platform, AI ADMIN, AI FAE, VOC, HR Agent, Marketing Agent family

## 1. Decision

Agent Platform will expose each business domain as a stable, independent browser
workspace under the existing `agent.orbbec.com.cn` origin. This change separates
page and route ownership only. It does not require every workspace to move into
one repository, process, frontend build, database, or deployment unit.

The canonical product routes are:

```text
/                         Agent Brain
/agents                   Professional Agent directory; navigation only
/office/                   Administration workspace
/fae/                      FAE workspace
/voc/                      VOC workspace
/hr/                       HR workspace
/marketing/                Marketing workspace
/admin/                    Platform system administration
```

The common Platform foundation remains responsible for DingTalk enterprise
identity, the stable `internal_user_id`, route return-path validation, shared
authorization primitives, attachments where supported, audit, Agent catalog,
and the top-level product entry. Each workspace owns its pages, local navigation,
canonical links, and domain-specific management authorization.

This specification is intentionally narrower than a repository or service split.
It creates stable product boundaries so the workspaces can be developed
independently without first duplicating infrastructure.

## 2. Goals

1. Give Office, FAE, VOC, HR, and Marketing non-overlapping canonical browser
   namespaces.
2. Make `/agents` a directory that launches the correct workspace rather than a
   second place where the same Agent is used.
3. Make the root page of every business workspace the direct-use experience.
4. Put FAE and VOC management inside their own workspaces while keeping their
   management grants independent.
5. Scope visible history to the current workspace and Agent.
6. Let each workspace evolve without editing the internal pages or route tables
   of another workspace.
7. Preserve existing bookmarks through explicit, tested compatibility redirects.
8. Keep `/office/*` and the public customer-facing `https://fae.orbbec.com.cn/`
   behavior unchanged while the new internal route structure is introduced.

## 3. Non-goals

- Do not split all workspaces into new repositories or services as part of this
  change.
- Do not create another login, password, Platform Session, or identity record.
- Do not migrate or duplicate Conversation, FAE, VOC, HR, or Marketing data.
- Do not change the Agent Brain orchestration protocol or Adapter contracts.
- Do not grant FAE management access through VOC management access, or the
  reverse.
- Do not make frontend navigation visibility an authorization boundary.
- Do not replace the public FAE customer application or move it under `/fae/`.
- Do not rewrite the existing `/office/*` application in the Platform frontend.

## 4. Current State and Required Corrections

The repository currently mixes three routing shapes:

1. `/office/*` is an independently served AI ADMIN application behind the shared
   origin.
2. `/voc/*` is already routed to the standalone VOC application; old Platform
   routes redirect into it.
3. HR and the five Marketing Agents are rendered by the shared
   `/agents/{agent_id}` direct-conversation page.
4. FAE direct use currently launches the public FAE origin, while FAE operations
   are rendered under `/admin/fae/*`. The target state adds an internal FAE
   direct-use surface at `/fae/` backed by the existing FAE application and
   keeps the public origin unchanged.

The previously approved independent FAE Workbench design assigned `/fae/*` to
management. This specification supersedes only that route assignment: `/fae/`
is now the FAE direct-use entry and management moves to `/fae/manage/*`. The FAE
grant model, unique-name provisioning, per-request authorization, audit rules,
and read-only deployment safeguards remain approved and unchanged.

The existing standalone VOC route must not be moved back into the Platform SPA.
Route isolation is an ownership contract, not a requirement that every page use
the same build artifact.

## 5. Canonical Route Contract

### 5.1 Agent Brain and directory

```text
/                         Agent Brain conversation workspace
/agents                   Professional Agent directory
/admin/*                  Platform-wide system administration
```

`/agents` contains launch cards only. It must not retain a second direct-chat
implementation after compatibility routing is in place.

### 5.2 FAE workspace

```text
/fae/                                      FAE Agent direct use
/fae/conversations/{conversation_id}       FAE conversation
/fae/manage/                               FAE management overview
/fae/manage/sessions                       FAE Session list
/fae/manage/sessions/{session_key}         FAE Session detail
/fae/manage/issues                         Feedback and repair governance
/fae/manage/issues/{issue_id}              Governance item detail
/fae/manage/reports                        Analysis reports
/fae/manage/reports/{report_id}            Report detail
```

The `/fae/` browser namespace is the internal employee workspace. Its direct-use
surface is served by the existing FAE application through a dedicated internal
base path and Platform enterprise identity contract. The more-specific
`/fae/manage/*` route family is served by Platform. The external customer
application at `https://fae.orbbec.com.cn/` remains a separate public entry with
unchanged behavior and data policy.

### 5.3 VOC workspace

```text
/voc/                                      VOC Agent direct use
/voc/records                               Current user's VOC records
/voc/records/{voc_no}                      Current user's VOC detail
/voc/manage/                               VOC management overview
/voc/manage/records                        Authorized VOC management list
/voc/manage/records/{voc_no}               Authorized VOC management detail
```

The standalone VOC application remains the route owner. Query-string navigation
such as `?view=management` is replaced by canonical path routing; the
compatibility redirect remains for exactly one release.

### 5.4 HR workspace

```text
/hr/                                       HR Agent direct use
/hr/conversations/{conversation_id}        HR conversation
```

### 5.5 Marketing workspace

The five Marketing Agents share one workspace and one local Agent switcher:

```text
/marketing/                                Default Marketing entry
/marketing/prospecting                     Marketing Prospecting
/marketing/inbound                         Marketing Inbound
/marketing/voice                           Marketing Voice
/marketing/intelligence                    Marketing Intelligence
/marketing/gtm                             Marketing GTM
/marketing/{agent_slug}/conversations/{conversation_id}
```

`/marketing/` redirects with history replacement to the chosen default Agent.
The initial default is `prospecting`; changing the default later must not alter
the canonical links of the other Agents.

## 6. Workspace Registry

One bounded registry is the source of truth for browser routing and directory
launches. It is a projection of the canonical Agent catalog, not another list of
Agent capabilities.

Each entry contains only route-level facts:

```text
workspace_id
base_path
direct_agent_ids
agent_slug_by_id
route_owner
management_scope, if any
availability
```

The initial mapping is:

| Workspace | Agent IDs | Route owner | Management scope |
|---|---|---|---|
| Office | `ai-admin-agent` | AI ADMIN upstream | existing Office authorization |
| FAE direct use | `ai-fae-agent` | AI FAE upstream at `/fae/*`, excluding the more-specific management family | none |
| FAE management | `ai-fae-agent` | Platform at `/fae/manage/*` | `fae_workbench` |
| VOC | `voc` | standalone VOC upstream | `voc_management` |
| HR | `hr-bot` | Platform WebUI | none in this release |
| Marketing | five `marketing-*-bot` IDs | Platform WebUI | none in this release |

Workspace route fields belong in one module and must not be repeated in the
directory page, account page, router, and individual Agent pages. Capability
descriptions, execution pools, Adapter kinds, and latency stay in the canonical
Agent catalog.

## 7. Page Shell and Code Boundaries

All workspaces preserve a recognizable Platform identity and account entry, but
each workspace owns its local navigation.

```text
Shared product header
  Agent Brain | Professional Agents | current workspace | Platform Admin when allowed | account

FAE local navigation
  Agent | Management, when allowed

VOC local navigation
  Agent | My VOC | Management, when allowed

Marketing local navigation
  Prospecting | Inbound | Voice | Intelligence | GTM
```

Platform-hosted workspaces are organized by workspace rather than by a flat page
directory:

```text
webui/src/
  platform/                 shared shell, identity, route registry
  workspaces/
    fae/                    Platform-owned FAE management surface
    hr/                     HR direct-use surface
    marketing/              Marketing direct-use surface and switcher
  shared/                   reusable conversation, attachment and state components
```

Standalone `/office/*` and `/voc/*` applications remain in their owning
repositories. They consume the same route and identity contracts but are not
copied into `webui/src/workspaces`.

Boundary rules:

- A workspace registers pages only below its own canonical prefix.
- Workspace internals do not import another workspace's private components.
- Shared behavior moves to `shared` only when at least two workspaces actually
  need the same contract; speculative abstraction is prohibited.
- API prefixes and browser paths are defined centrally per workspace and are not
  scattered as string literals through pages and tests.
- Route ownership is explicit in Nginx and application tests. A generic
  `location /` must not silently capture a route owned by another application.

## 8. Identity and Authorization

All internal workspaces resolve the same DingTalk enterprise person to the same
Platform `internal_user_id`. A separate route never implies a separate identity.

Direct-use and management authorization remain distinct:

```text
direct use = active enterprise identity + Agent use grant
FAE management = Platform Owner OR active fae_workbench grant
VOC management = Platform Owner OR active voc_management grant
Platform admin = existing Platform role policy
```

FAE and VOC management grants are independent. Holding either grant does not
grant the other and does not imply `platform_admin`, `management_viewer`, or
Owner.

Every protected backend request re-evaluates current server-side identity and
authorization. Frontend route guards and hidden navigation provide usability
only. Revocation takes effect on the next request.

The account projection exposes bounded workspace scopes so the UI can render the
correct entries. It must not expose raw DingTalk identifiers or let the browser
submit an identity or role override.

## 9. Conversation and Data Scoping

This route change does not move systems of record. Each existing service keeps
its authoritative Conversation or business data store. Platform-hosted direct
conversations continue to use the Platform Conversation model; standalone FAE
and VOC surfaces keep their currently approved persistence and integration
contracts.

The visible history is nevertheless strictly scoped:

- `/fae/*` shows only FAE conversations available to the current subject.
- `/voc/*` shows only the current subject's VOC drafts and records unless the
  request enters the separately authorized management route.
- `/hr/*` shows only conversations permanently bound to `hr-bot`.
- each `/marketing/{slug}` page shows only conversations permanently bound to
  that selected Marketing Agent.

For Platform Conversations, the backend filter is at least:

```text
internal_user_id + direct_agent_id + conversation_status
```

A conversation cannot be rebound from one Agent to another. A conversation ID
that exists but is outside the current subject, workspace, or Agent scope returns
the existing non-enumerating not-found response.

Attachments, CSRF, audit, archive, rename, pagination, and cancellation reuse the
existing service contracts. The workspace migration must not fork those APIs or
create duplicate Session tables.

## 10. Compatibility Routing

Old links map to exactly one canonical workspace route and use history
replacement in the browser where applicable:

```text
/agents/hr-bot                                      -> /hr/
/agents/hr-bot/conversations/{id}                   -> /hr/conversations/{id}
/agents/marketing-prospecting-bot                   -> /marketing/prospecting
/agents/marketing-inbound-bot                       -> /marketing/inbound
/agents/marketing-voice-bot                         -> /marketing/voice
/agents/marketing-intelligence-bot                  -> /marketing/intelligence
/agents/marketing-gtm-bot                           -> /marketing/gtm
/agents/voc                                         -> /voc/
/agents/voc/workspace                               -> /voc/
/agents/ai-fae-agent                                -> /fae/
/admin/fae                                          -> /fae/manage/
/admin/fae/sessions...                              -> /fae/manage/sessions...
/admin/fae/issues...                                -> /fae/manage/issues...
/admin/fae/reports...                               -> /fae/manage/reports...
/admin/voc                                          -> /voc/manage/
/voc/?view=management                               -> /voc/manage/
```

Redirects preserve only allowlisted query parameters. They must reject external
origins, protocol-relative URLs, backslashes, control characters, and unrelated
paths. Redirect chains and loops are release blockers.

Compatibility API prefixes remain for exactly one release only when they execute
the same handler and authorization dependency as the canonical API. A
compatibility mount must never become a weaker authorization path.

## 11. Failure Isolation

- Each Platform-hosted workspace has its own frontend error boundary.
- Failure to load one workspace remains on that route and presents a retry
  action; it never redirects to `/admin` or another Agent.
- A disabled workspace is shown as unavailable in `/agents` without hiding or
  degrading healthy workspaces.
- An authenticated user without management scope receives a stable 403 response
  and workspace-specific permission page.
- A missing or cross-scope conversation receives a non-enumerating 404.
- An unauthenticated browser enters the existing DingTalk login flow and returns
  to the exact validated workspace path.
- FAE/VOC data freshness and read-only conditions are rendered inside their own
  workspaces, not as global Platform banners.
- `/office/*`, the external FAE origin, Agent Brain, and unrelated Nginx routes
  are included in every rollout's non-regression probes.

## 12. Implementation Sequence

1. Add the route-level Workspace Registry and exact compatibility-route tests
   without changing current page behavior.
2. Rebase the unfinished independent FAE access work onto this contract:
   preserve its grant and audit work, change its management routes from
   `/fae/*` to `/fae/manage/*`, and add the `/fae/` direct-use entry.
3. Complete VOC path routing so direct use stays at `/voc/` and management moves
   from query-string navigation to `/voc/manage/*`; preserve the existing
   standalone upstream and identity flow.
4. Move HR direct use and deep links to `/hr/*`.
5. Move the five Marketing Agents to `/marketing/*` and scope history to the
   selected Agent.
6. Convert `/agents` to a pure directory and enable the compatibility redirects.
7. Run route, identity, management-scope, history, attachment, mobile, Nginx,
   deployment, rollback, and real-session acceptance.
8. Remove old page handlers only after one compatibility release and fresh
   production evidence. Historical data is never deleted by route cleanup.

Each workspace migration is an independently testable commit and deployment
unit. A later workspace must not be required to make an earlier workspace usable.

## 13. Testing and Acceptance

### 13.1 Route tests

- every canonical workspace URL can be loaded directly and refreshed;
- `/agents` cards target only canonical workspace URLs;
- every listed legacy route maps to the exact canonical route once;
- Marketing slugs map to the expected five Agent IDs;
- unsafe or unknown return paths and redirect targets are rejected;
- Nginx assigns exactly one upstream owner to every exact route family,
  including the distinct `/fae/manage/*` and remaining `/fae/*` families; a
  generic location must not capture a more-specific workspace family.

### 13.2 Identity and authorization tests

- DingTalk login returns to the original validated workspace path;
- one enterprise subject resolves to the same `internal_user_id` across all
  internal workspaces;
- direct Agent use does not imply management access;
- FAE management and VOC management do not imply one another;
- both management scopes are enforced by backend dependencies on every request;
- revocation is effective on the next request;
- unauthorized deep links do not leak existence or content.

### 13.3 Conversation and UI tests

- HR history contains only `hr-bot` conversations;
- each Marketing tab contains only its own Agent's conversations;
- switching Marketing Agents creates or opens a correctly bound conversation;
- rename, archive, pagination, attachments, retry, cancellation, and mobile
  navigation continue to work on their canonical routes;
- FAE and VOC local management navigation appears only with the corresponding
  scope;
- failure in one workspace does not replace the global shell or another
  workspace with an error state.

### 13.4 Production non-regression evidence

- `/` remains Agent Brain and never falls through to `/admin`;
- `/office/*` responses, upstream ownership, identity behavior, and primary
  workflows are unchanged;
- `https://fae.orbbec.com.cn/` remains unchanged;
- the standalone VOC upstream remains healthy and retains route ownership;
- old bookmarks reach their canonical routes without loops;
- frontend production builds, backend suites, Nginx checks, deployment gates,
  and rollback validation pass.

## 14. Completion Definition

The work is complete only when Office, FAE, VOC, HR, and Marketing have stable,
non-overlapping browser namespaces; `/agents` is directory-only; FAE and VOC
management live below independent management subroutes with independent grants;
workspace history is correctly scoped; old links are safe compatibility routes;
and a failure or deployment in one workspace cannot silently take over another
workspace's route.
