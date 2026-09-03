# Agent Workspace Route Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Office, FAE, VOC, HR, and Marketing stable, non-overlapping browser workspaces while making `/agents` a launch-only directory and preserving one DingTalk enterprise identity.

**Architecture:** Keep route ownership explicit: AI ADMIN continues to own `/office/*`, the standalone VOC application owns `/voc/*`, AI FAE owns the internal direct-use surface under `/fae/*`, Platform owns the more-specific `/fae/manage/*`, and Platform's React application owns `/hr/*`, `/marketing/*`, `/agents`, and `/admin/*`. A bounded Workspace Registry supplies route facts to the Platform router and directory; existing data stores and conversation APIs remain authoritative.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, FastAPI, Pydantic, PostgreSQL control migrations, pytest, Nginx, Bash deployment gates.

## Global Constraints

- Canonical routes are `/`, `/agents`, `/office/`, `/fae/`, `/fae/manage/*`, `/voc/`, `/voc/manage/*`, `/hr/`, `/marketing/*`, and `/admin/*`.
- `/agents` is navigation only; it must not remain a second direct-chat surface after the compatibility release.
- `/office/*` and `https://fae.orbbec.com.cn/` must retain their current code paths, identity behavior, data policy, and production availability.
- Route separation does not create a new repository, service, database, Session, password, or `internal_user_id`.
- FAE management requires Owner or active `fae_workbench`; VOC management requires Owner or active `voc_management`. Neither scope grants the other or any Platform-wide administrator role.
- Every protected backend request re-evaluates current identity and authorization; frontend navigation is not an authorization boundary.
- HR history is filtered by `internal_user_id + hr-bot + status`; every Marketing history view is filtered by `internal_user_id + selected marketing agent + status`.
- Existing rename, archive, pagination, attachment, retry, cancellation, CSRF, audit, and non-enumerating 404 contracts must be reused rather than forked.
- Legacy browser routes redirect exactly once with history replacement; cross-upstream redirects perform a full document navigation.
- Compatibility API mounts use the same handlers and authorization dependency as the canonical API and remain for one release only.
- No route may fall through to `/admin`, another Agent, or a generic upstream when its owning workspace is unavailable.
- Do not hardcode `天启`, `范闲`, `苍渊`, or any other production display name in source, migrations, fixtures, or deployment scripts.
- Preserve unrelated untracked files. Use local worktrees and local branches only; push `master` only after local integration and full verification.
- The unfinished worktree `.worktrees/fae-independent-access` is authoritative for migration 063 work already present there; do not recreate or overwrite it.

## Existing Work That This Plan Preserves

- Platform route design: `docs/superpowers/specs/2026-09-03-agent-workspace-route-separation-design.md` at commit `4075466`.
- Standalone VOC routing already deployed on Platform master through commit `ae454f1`.
- FAE management grant work is partially implemented, uncommitted, in `.worktrees/fae-independent-access` on `feat/fae-independent-access`.
- `docs/superpowers/plans/2026-09-01-independent-fae-workbench-access.md` remains authoritative for the grant/audit semantics in its Tasks 1–4 and 7. Its browser route Tasks 5–6 and release Tasks 8–9 are superseded by this plan because management now lives at `/fae/manage/*`, not `/fae/*`.

## File and Interface Map

### Agent Platform repository

- `webui/src/platform/workspaces.ts`: only browser-route registry; no capability text or runtime health.
- `webui/src/router.ts`: Platform-owned routes and compatibility redirects.
- `webui/src/workspaces/direct/DirectAgentWorkspace.tsx`: reusable Platform Conversation surface.
- `webui/src/workspaces/hr/HrWorkspacePage.tsx`: binds the reusable surface to `hr-bot`.
- `webui/src/workspaces/marketing/MarketingWorkspacePage.tsx`: binds the reusable surface to one of five Marketing Agents.
- `webui/src/workspaces/fae/FaeManagementWorkspace.tsx`: FAE management shell under `/fae/manage/*`.
- `backend/app/control_plane/fae_access.py`: audited FAE management scope service.
- `backend/app/fae_workbench/routes.py`: canonical `/api/fae` handlers and compatibility mount.
- `deploy/cloud/agent-domain.nginx.conf`: exact route ownership for FAE direct use versus FAE management.

### AI FAE repository

- `webui/src/runtimePaths.ts`: browser base, API base, and internal/public surface detection.
- `webui/src/routes.ts`: `/app/*` and `/fae/*` chat/deep-link parsing.
- `src/api/webui.py`: serves one relative-asset build at both `/app/*` and `/fae/*`.
- `webui/src/enterpriseIdentity.ts`: existing launch exchange plus internal Platform bootstrap.
- Existing FAE APIs remain rooted in the FAE application; Nginx maps internal browser requests from `/fae/api/*` to them.

### VOC repository

- `webui/src/routes.ts`: canonical `/voc/*` path parser and one-release query compatibility.
- `webui/src/App.tsx`: direct, records, and management page selection from the path.
- Existing VOC backend authorization and SPA fallback remain authoritative.

---

### Task 1: Centralize Platform workspace route facts

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `webui/src/platform/workspaces.ts`
- Create: `webui/src/platform/workspaces.test.ts`
- Modify: `webui/src/pages/AgentUseDirectoryPage.tsx`
- Modify: `webui/src/pages/AgentUsePage.test.tsx`

**Interfaces:**
- Consumes: Agent IDs returned by `GET /api/v1/catalog/agents`.
- Produces: `WORKSPACES`, `MARKETING_AGENT_ID_BY_SLUG`, `workspaceForAgent(agentId)`, `workspaceLaunchPath(agentId)`, and `directConversationPath(agentId, conversationId)`.

- [ ] **Step 1: Write failing registry tests**

Create `workspaces.test.ts` with exact route assertions:

```ts
import { describe, expect, it } from "vitest";
import {
  MARKETING_AGENT_ID_BY_SLUG,
  directConversationPath,
  workspaceForAgent,
  workspaceLaunchPath,
} from "./workspaces";

describe("workspace route registry", () => {
  it.each([
    ["ai-admin-agent", "/office/?view=services"],
    ["ai-fae-agent", "/fae/"],
    ["voc", "/voc/"],
    ["hr-bot", "/hr/"],
    ["marketing-prospecting-bot", "/marketing/prospecting"],
    ["marketing-inbound-bot", "/marketing/inbound"],
    ["marketing-voice-bot", "/marketing/voice"],
    ["marketing-intelligence-bot", "/marketing/intelligence"],
    ["marketing-gtm-bot", "/marketing/gtm"],
  ])("maps %s to %s", (agentId, path) => {
    expect(workspaceLaunchPath(agentId)).toBe(path);
  });

  it("keeps the five marketing slugs stable", () => {
    expect(MARKETING_AGENT_ID_BY_SLUG).toEqual({
      prospecting: "marketing-prospecting-bot",
      inbound: "marketing-inbound-bot",
      voice: "marketing-voice-bot",
      intelligence: "marketing-intelligence-bot",
      gtm: "marketing-gtm-bot",
    });
  });

  it("builds only Platform conversation deep links", () => {
    expect(directConversationPath("hr-bot", "c:1")).toBe("/hr/conversations/c%3A1");
    expect(directConversationPath("marketing-voice-bot", "c:2"))
      .toBe("/marketing/voice/conversations/c%3A2");
    expect(directConversationPath("ai-fae-agent", "c:3"))
      .toBe("/fae/conversations/c%3A3");
    expect(directConversationPath("voc", "c:4")).toBeNull();
  });

  it("rejects unknown agent ids", () => {
    expect(workspaceForAgent("unknown-agent")).toBeNull();
    expect(workspaceLaunchPath("unknown-agent")).toBeNull();
  });
});
```

- [ ] **Step 2: Run the tests and confirm the module is missing**

```bash
cd webui
npm test -- --run src/platform/workspaces.test.ts
```

Expected: FAIL because `./workspaces` does not exist.

- [ ] **Step 3: Implement the bounded registry**

Create `workspaces.ts` with this public shape:

```ts
export type WorkspaceId = "office" | "fae" | "voc" | "hr" | "marketing";
export type RouteOwner = "platform" | "ai-admin" | "ai-fae" | "voc";
export type ManagementScope = "fae_workbench" | "voc_management";

export interface WorkspaceRoute {
  workspaceId: WorkspaceId;
  basePath: string;
  routeOwner: RouteOwner;
  directAgentIds: readonly string[];
  agentSlugById: Readonly<Record<string, string>>;
  availability: "catalog";
  management: {
    basePath: string;
    routeOwner: RouteOwner;
    scope: ManagementScope;
  } | null;
}

export const MARKETING_AGENT_ID_BY_SLUG = Object.freeze({
  prospecting: "marketing-prospecting-bot",
  inbound: "marketing-inbound-bot",
  voice: "marketing-voice-bot",
  intelligence: "marketing-intelligence-bot",
  gtm: "marketing-gtm-bot",
} as const);

export const WORKSPACES: readonly WorkspaceRoute[] = Object.freeze([
  { workspaceId: "office", basePath: "/office/", routeOwner: "ai-admin", directAgentIds: ["ai-admin-agent"], agentSlugById: {}, availability: "catalog", management: null },
  { workspaceId: "fae", basePath: "/fae/", routeOwner: "ai-fae", directAgentIds: ["ai-fae-agent"], agentSlugById: {}, availability: "catalog", management: { basePath: "/fae/manage/", routeOwner: "platform", scope: "fae_workbench" } },
  { workspaceId: "voc", basePath: "/voc/", routeOwner: "voc", directAgentIds: ["voc"], agentSlugById: {}, availability: "catalog", management: { basePath: "/voc/manage/", routeOwner: "voc", scope: "voc_management" } },
  { workspaceId: "hr", basePath: "/hr/", routeOwner: "platform", directAgentIds: ["hr-bot"], agentSlugById: {}, availability: "catalog", management: null },
  { workspaceId: "marketing", basePath: "/marketing/", routeOwner: "platform", directAgentIds: Object.values(MARKETING_AGENT_ID_BY_SLUG), agentSlugById: Object.fromEntries(Object.entries(MARKETING_AGENT_ID_BY_SLUG).map(([slug, id]) => [id, slug])), availability: "catalog", management: null },
]);
```

Implement lookups from this array. `workspaceLaunchPath("ai-admin-agent")` adds only the approved `?view=services`; other paths contain no query. `directConversationPath()` returns encoded HR, Marketing, and FAE paths and returns `null` for route owners without that contract.

- [ ] **Step 4: Remove directory-local route lists**

Delete `WORKSPACE_URLS` from `AgentUseDirectoryPage.tsx` and use `workspaceLaunchPath(card.agent_id)`. Preserve the existing catalog-provided label, description, availability, order, and disabled state; the registry decides only the href.

- [ ] **Step 5: Run focused tests**

```bash
cd webui
npm test -- --run src/platform/workspaces.test.ts src/pages/AgentUsePage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add webui/src/platform/workspaces.ts webui/src/platform/workspaces.test.ts webui/src/pages/AgentUseDirectoryPage.tsx webui/src/pages/AgentUsePage.test.tsx
git commit -m "refactor(webui): centralize workspace routes"
```

---

### Task 2: Add canonical Platform routes and safe compatibility redirects

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/auth.test.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/documentTitle.test.tsx`
- Modify: `backend/app/control_plane/auth.py`
- Modify: `backend/app/control_plane/routes_auth.py`
- Modify: `backend/tests/test_web_session_security.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

**Interfaces:**
- Consumes: route helpers from `webui/src/platform/workspaces.ts`.
- Produces: canonical `hr`, `marketing-*`, and `fae-manage-*` route variants plus `LegacyRedirect.navigation: "spa" | "document"`.

- [ ] **Step 1: Add failing route and return-path cases**

Add this route matrix to `router.test.ts`:

```ts
it.each([
  ["/hr", { name: "legacy-redirect", to: "/hr/", navigation: "spa" }],
  ["/hr/", { name: "hr" }],
  ["/hr/conversations/c%3A1", { name: "hr-conversation", conversationId: "c:1" }],
  ["/marketing", { name: "legacy-redirect", to: "/marketing/prospecting", navigation: "spa" }],
  ["/marketing/", { name: "legacy-redirect", to: "/marketing/prospecting", navigation: "spa" }],
  ["/marketing/inbound", { name: "marketing", agentSlug: "inbound" }],
  ["/marketing/gtm/conversations/c-2", { name: "marketing-conversation", agentSlug: "gtm", conversationId: "c-2" }],
  ["/fae/manage/", { name: "fae-manage-overview" }],
  ["/fae/manage/sessions/s%3A1", { name: "fae-manage-session", sessionKey: "s:1" }],
  ["/fae/manage/issues/00000000-0000-4000-8000-000000000001", { name: "fae-manage-issue", issueId: "00000000-0000-4000-8000-000000000001" }],
  ["/agents/hr-bot", { name: "legacy-redirect", to: "/hr/", navigation: "spa" }],
  ["/agents/ai-fae-agent", { name: "legacy-redirect", to: "/fae/", navigation: "document" }],
  ["/admin/fae/reports", { name: "legacy-redirect", to: "/fae/manage/reports", navigation: "spa" }],
  ["/admin/voc", { name: "legacy-redirect", to: "/voc/manage/", navigation: "document" }],
])("parses %s", (path, expected) => expect(parseRoute(path)).toEqual(expected));
```

In `auth.test.ts`, accept exact canonical paths and reject malformed variants:

```ts
expect(readLoginReturnPath("?return_path=%2Ffae%2Fconversations%2Fc-1")).toBe("/fae/conversations/c-1");
expect(readLoginReturnPath("?return_path=%2Ffae%2Fmanage%2Freports%2Fr-1")).toBe("/fae/manage/reports/r-1");
expect(readLoginReturnPath("?return_path=%2Fmarketing%2Fvoice%2Fconversations%2Fc-1")).toBe("/marketing/voice/conversations/c-1");
expect(readLoginReturnPath("?return_path=%2Fmarketing%2Funknown")).toBe("/");
expect(readLoginReturnPath("?return_path=%2F%2Fevil.example")).toBe("/");
```

- [ ] **Step 2: Run the tests and confirm the canonical variants are missing**

```bash
cd webui
npm test -- --run src/router.test.ts src/auth.test.ts src/documentTitle.test.tsx
```

Expected: FAIL on the new route names and return paths.

- [ ] **Step 3: Extend the route union and parser**

Add these variants:

```ts
| { name: "hr" }
| { name: "hr-conversation"; conversationId: string }
| { name: "marketing"; agentSlug: keyof typeof MARKETING_AGENT_ID_BY_SLUG }
| { name: "marketing-conversation"; agentSlug: keyof typeof MARKETING_AGENT_ID_BY_SLUG; conversationId: string }
| { name: "fae-manage-overview" }
| { name: "fae-manage-sessions" }
| { name: "fae-manage-session"; sessionKey: string }
| { name: "fae-manage-issues" }
| { name: "fae-manage-issue"; issueId: string }
| { name: "fae-manage-reports" }
| { name: "fae-manage-report"; reportId: string }
| { name: "legacy-redirect"; to: string; navigation: "spa" | "document" }
```

Parse the more-specific conversation/detail routes before their workspace roots. Inspect the original pathname before trailing-slash normalization so `/hr` replaces to `/hr/` while `/hr/` renders the workspace. Both `/marketing` and `/marketing/` replace to `/marketing/prospecting`. Accept only the five keys in `MARKETING_AGENT_ID_BY_SLUG`. Update `routePath()` and `routeSection()` for every new variant. `/fae/*` outside `/fae/manage/*` is not a Platform SPA route and must not be added to this union.

- [ ] **Step 4: Make redirect execution match route ownership**

Add `safeLegacyWorkspaceSearch(targetPath, sourceSearch)` beside the router. It drops every query by default and preserves only one occurrence of these keys after value validation:

| Target family | Allowlisted keys |
|---|---|
| `/fae/manage/sessions` | `q`, `channel`, `sentiment`, `review_status`, `outcome`, `date_from`, `date_to`, `date_before`, `subject_key`, `has_subject`, `abnormal`, `has_latency`, `page` |
| `/fae/manage/issues` | `status`, `disposition`, `priority`, `failure_layer`, `owner`, `q`, `created_after`, `page`, or the paired `session_key` + `turn_key` |
| `/fae/manage/reports/{id}` | positive integer `version` |

Reuse the value validators already exercised by `sessionNavigation.ts`, `FaeIssuesPage.tsx`, and `FaeReportsPage.tsx`; move a validator into a shared route module instead of copying it when necessary. Duplicated keys, unknown keys, invalid enum/date/boolean/page values, unpaired Session/Turn keys, and all queries on other redirect families are dropped.

Change `LegacyRedirect` to append only that sanitized query and to use a full document navigation for other upstreams:

```tsx
function LegacyRedirect({ to, navigation }: { to: string; navigation: "spa" | "document" }) {
  const target = `${to}${safeLegacyWorkspaceSearch(to, window.location.search)}`;
  useEffect(() => {
    if (navigation === "document") {
      window.location.replace(platformPath(target));
      return;
    }
    navigate(target, { replace: true });
  }, [navigation, target]);
  return <PendingPage title="正在打开工作区" description="正在进入对应的专业 Agent。" />;
}
```

Do not call SPA `navigate()` for `/fae/*` or `/voc/*`; doing so leaves the Platform document loaded under a route owned by another application.

- [ ] **Step 5: Expand the exact login return-path allowlist**

Add anchored patterns for:

```text
/fae/
/fae/conversations/{[A-Za-z0-9:._-]+}
/fae/manage/{sessions|issues|reports}[/safe-id]
/voc/{records|manage/records}[/safe-id]
/hr/[/conversations/safe-id]
/marketing/{prospecting|inbound|voice|intelligence|gtm}[/conversations/safe-id]
```

Keep protocol-relative URLs, absolute URLs, backslashes, fragments, control characters, duplicated `return_path`, and unknown slugs rejected.

- [ ] **Step 6: Apply the same allowlist and SPA shell routes on the backend**

Replace the current VOC-only special case in `validate_return_path()` with anchored canonical workspace patterns. The backend accepts the same paths listed in Step 5 and still rejects query strings and fragments; Office remains restricted to exact `/office/` because it restores its own allowlisted view through session storage.

Add authenticated shell handlers for Platform-owned routes only:

```python
@router.get("/hr", include_in_schema=False)
@router.get("/hr/", include_in_schema=False)
@router.get("/hr/{client_path:path}", include_in_schema=False)
@router.get("/marketing", include_in_schema=False)
@router.get("/marketing/", include_in_schema=False)
@router.get("/marketing/{client_path:path}", include_in_schema=False)
@router.get("/fae/manage", include_in_schema=False)
@router.get("/fae/manage/", include_in_schema=False)
@router.get("/fae/manage/{client_path:path}", include_in_schema=False)
```

Do not mount a Platform shell at `/fae/`, `/fae/conversations/*`, or `/voc/*`; Nginx assigns those documents to AI FAE and VOC.

- [ ] **Step 7: Update document titles and run focused tests**

Use `HR Agent`, the selected Marketing name, and `FAE 工作台` titles. Then run:

```bash
cd webui
npm test -- --run src/router.test.ts src/auth.test.ts src/documentTitle.test.tsx
cd ../backend
.venv/bin/python -m pytest tests/test_web_session_security.py tests/test_dingtalk_auth_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add webui/src/router.ts webui/src/router.test.ts webui/src/auth.ts webui/src/auth.test.ts webui/src/App.tsx webui/src/documentTitle.ts webui/src/documentTitle.test.tsx backend/app/control_plane/auth.py backend/app/control_plane/routes_auth.py backend/tests/test_web_session_security.py backend/tests/test_dingtalk_auth_api.py
git commit -m "feat(webui): add canonical workspace routes"
```

---

### Task 3: Move HR and Marketing direct use onto canonical workspaces

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `webui/src/workspaces/direct/DirectAgentWorkspace.tsx`
- Create: `webui/src/shared/WorkspaceErrorBoundary.tsx`
- Create: `webui/src/shared/WorkspaceErrorBoundary.test.tsx`
- Create: `webui/src/workspaces/hr/HrWorkspacePage.tsx`
- Create: `webui/src/workspaces/marketing/MarketingWorkspacePage.tsx`
- Create: `webui/src/workspaces/marketing/MarketingWorkspacePage.test.tsx`
- Modify: `webui/src/pages/AgentUsePage.tsx`
- Modify: `webui/src/pages/AgentUsePage.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/AppShell.brain.test.tsx`

**Interfaces:**
- Consumes: existing Conversation functions in `conversationApi.ts` and `directConversationPath()` from the registry.
- Produces: `DirectAgentWorkspaceProps`, `HrWorkspacePage`, and `MarketingWorkspacePage`.

- [ ] **Step 1: Add failing workspace-scoping tests**

Define the shared component contract in tests:

```ts
export interface DirectAgentWorkspaceProps {
  account: Account;
  agentId: string;
  conversationId?: string;
  conversationPath: (conversationId: string) => string;
  header?: ReactNode;
}
```

Assert:

```ts
expect(listConversations).toHaveBeenCalledWith(expect.any(AbortSignal), undefined, 20, "hr-bot");
expect(listConversations).toHaveBeenCalledWith(expect.any(AbortSignal), undefined, 20, "marketing-inbound-bot");
expect(historyPath).toBe("/marketing/inbound/conversations/c-1");
expect(screen.getByRole("link", { name: "Voice" })).toHaveAttribute("href", "/marketing/voice");
```

Also assert switching from Inbound to Voice does not reuse or rebind the selected Inbound conversation.

- [ ] **Step 2: Run the focused tests and confirm failure**

```bash
cd webui
npm test -- --run src/pages/AgentUsePage.test.tsx src/workspaces/marketing/MarketingWorkspacePage.test.tsx src/AppShell.brain.test.tsx
```

- [ ] **Step 3: Extract the existing direct-use implementation without changing APIs**

Move the stateful implementation from `AgentUsePage.tsx` into `DirectAgentWorkspace.tsx`. Keep these calls unchanged:

```ts
listConversations(signal, before, limit, agentId, status)
startConversation(text, account.csrf_token, agentId)
renameConversation(conversationId, title, account.csrf_token)
archiveConversation(conversationId, account.csrf_token)
restoreConversation(conversationId, account.csrf_token)
```

Replace only the browser-path callback with `conversationPath`. Continue passing the existing `ConversationPageClient` into `ConversationPage`; that existing client remains responsible for message streaming, attachments, retries, cancellation, and conversation-detail authorization. Preserve pagination cursor handling, non-enumerating 404, current error copy, and archive/restore behavior.

- [ ] **Step 4: Add the HR wrapper**

```tsx
export function HrWorkspacePage(props: { account: Account; conversationId?: string }) {
  return <DirectAgentWorkspace
    account={props.account}
    agentId="hr-bot"
    conversationId={props.conversationId}
    conversationPath={(id) => `/hr/conversations/${encodeURIComponent(id)}`}
  />;
}
```

- [ ] **Step 5: Add the Marketing wrapper and local switcher**

Map `agentSlug` through `MARKETING_AGENT_ID_BY_SLUG`. Render five canonical anchor links and pass the selected Agent ID to `DirectAgentWorkspace`. Key the stateful child by Agent ID so a switch cannot carry a draft, selected conversation, or active stream into another Agent.

```tsx
<DirectAgentWorkspace
  key={agentId}
  account={account}
  agentId={agentId}
  conversationId={conversationId}
  conversationPath={(id) => `/marketing/${agentSlug}/conversations/${encodeURIComponent(id)}`}
  header={<MarketingAgentSwitcher selected={agentSlug} />}
/>
```

- [ ] **Step 6: Route the new pages and reduce the old page to compatibility-only use**

Render `HrWorkspacePage` for `hr` routes and `MarketingWorkspacePage` for Marketing routes. Old `/agents/{id}` cases must already parse as redirects for the six migrated IDs. Unknown non-migrated direct Agent IDs may keep the old page for the one-release window, but `/agents` cards must never point there.

- [ ] **Step 7: Add workspace-local error containment**

Create a class error boundary because React render failures cannot be caught by event handlers:

```tsx
export class WorkspaceErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <section role="alert">
      <h1>{this.props.title} 暂时不可用</h1>
      <p>当前工作区加载失败，其他 Agent 不受影响。</p>
      <button type="button" onClick={() => this.setState({ failed: false })}>重试</button>
    </section>;
  }
}
```

Wrap HR and Marketing separately. Test that a throwing child leaves `window.location.pathname` unchanged and renders no `/admin` or other-Agent link.

- [ ] **Step 8: Run and commit**

```bash
cd webui
npm test -- --run src/pages/AgentUsePage.test.tsx src/workspaces/marketing/MarketingWorkspacePage.test.tsx src/AppShell.brain.test.tsx src/router.test.ts
cd ..
git add webui/src/workspaces webui/src/shared/WorkspaceErrorBoundary.tsx webui/src/shared/WorkspaceErrorBoundary.test.tsx webui/src/pages/AgentUsePage.tsx webui/src/pages/AgentUsePage.test.tsx webui/src/App.tsx webui/src/AppShell.tsx webui/src/AppShell.brain.test.tsx
git commit -m "feat(webui): separate HR and Marketing workspaces"
```

---

### Task 4: Replace VOC query navigation with canonical paths

**Repository:** `/Users/neo/Developer/work/Orbbec-VOC-Agent`

**Files:**
- Create: `webui/src/routes.ts`
- Create: `webui/src/routes.test.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/api.test.ts`
- Modify: `webui/src/pages/VocWorkspacePage.test.tsx`
- Modify: `webui/src/pages/VocManagementPage.test.tsx`
- Modify: `webui/src/pages/VocWorkspacePage.tsx`
- Modify: `webui/src/pages/VocManagementPage.tsx`
- Modify: `tests/api/test_workspace_web.py`
- Modify: `tests/acceptance/test_platform_workspace_contract.py`

**Interfaces:**
- Consumes: fixed `VOC_BASE_PATH === "/voc"` and existing `voc_admin` server projection.
- Produces: `parseVocRoute(pathname, search)` and `vocRoutePath(route)`.

- [ ] **Step 1: Write failing route tests**

```ts
expect(parseVocRoute("/voc/", "")).toEqual({ name: "feedback" });
expect(parseVocRoute("/voc/records", "")).toEqual({ name: "records" });
expect(parseVocRoute("/voc/records/VOC-1", "")).toEqual({ name: "record", vocNo: "VOC-1" });
expect(parseVocRoute("/voc/manage/", "")).toEqual({ name: "management" });
expect(parseVocRoute("/voc/manage/records/VOC-1", "")).toEqual({ name: "management-record", vocNo: "VOC-1" });
expect(parseVocRoute("/voc/", "?view=management")).toEqual({ name: "legacy-management" });
expect(parseVocRoute("/voc/manage/../../admin", "")).toEqual({ name: "not-found" });
```

- [ ] **Step 2: Run and confirm failure**

```bash
cd webui
npm test -- --run src/routes.test.ts src/api.test.ts src/pages/VocWorkspacePage.test.tsx src/pages/VocManagementPage.test.tsx
```

- [ ] **Step 3: Implement the exact parser and one-release redirect**

Use discriminated routes:

```ts
export type VocRoute =
  | { name: "feedback" }
  | { name: "records" }
  | { name: "record"; vocNo: string }
  | { name: "management" }
  | { name: "management-record"; vocNo: string }
  | { name: "legacy-management" }
  | { name: "not-found" };
```

On `legacy-management`, call `history.replaceState({}, "", "/voc/manage/")` before rendering management. Replace topbar links with `/voc/`, `/voc/records`, and `/voc/manage/`. Keep the existing backend management API and `voc_admin` check unchanged.

Pass route selection into the existing pages instead of creating duplicate record pages:

```tsx
<VocWorkspacePage
  csrfToken={session.csrf_token}
  initialVocNo={route.name === "record" ? route.vocNo : undefined}
  onOpenVoc={(vocNo) => history.pushState({}, "", vocRoutePath({ name: "record", vocNo }))}
/>
<VocManagementPage
  initialVocNo={route.name === "management-record" ? route.vocNo : undefined}
  onOpenVoc={(vocNo) => history.pushState({}, "", vocRoutePath({ name: "management-record", vocNo }))}
/>
```

On `popstate`, reparse the path and restore or close the matching detail. A record outside the current user's existing backend scope continues to return the existing non-enumerating response.

- [ ] **Step 4: Preserve exact login return paths**

Change `safeCurrentVocLocation()` to allow only paths produced by `vocRoutePath()`. `loadSession()` stores that exact path and sends it as Platform `return_path`; it no longer reduces every route to `/voc/`.

```ts
const returnPath = safeCurrentVocLocation();
sessionStorage.setItem("voc:return", returnPath);
redirect(`/login?return_path=${encodeURIComponent(returnPath)}`);
```

- [ ] **Step 5: Prove backend deep-link fallback does not capture APIs**

Add assertions that `/voc/manage/`, `/voc/manage/records/VOC-1`, and `/voc/records/VOC-1` serve `index.html`, while `/voc/api/missing`, `/voc/session/missing`, `/voc/assets/missing.js`, and `/voc/health` do not fall back to the SPA.

- [ ] **Step 6: Run repository verification and commit**

```bash
cd webui
npm test -- --run src/routes.test.ts src/api.test.ts src/pages/VocWorkspacePage.test.tsx src/pages/VocManagementPage.test.tsx
npm run build
cd ..
.venv/bin/python -m pytest tests/api/test_workspace_web.py tests/api/test_voc_browser_api.py tests/acceptance/test_platform_workspace_contract.py -q
git add webui/src tests/api/test_workspace_web.py tests/acceptance/test_platform_workspace_contract.py
git commit -m "feat(webui): add canonical VOC workspace paths"
```

---

### Task 5: Serve AI FAE safely at both public `/app/*` and internal `/fae/*`

**Repository:** `/Users/neo/Developer/work/AI-FAE-Agent`

**Files:**
- Create: `webui/src/runtimePaths.ts`
- Create: `webui/src/runtimePaths.test.ts`
- Create: `webui/src/routes.ts`
- Create: `webui/src/routes.test.ts`
- Modify: `webui/vite.config.ts`
- Modify: `webui/src/api.ts`
- Modify: `webui/src/api.test.ts`
- Modify: `webui/src/enterpriseIdentity.ts`
- Modify: `webui/src/enterpriseIdentity.test.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppRender.test.tsx`
- Modify: `webui/src/AuthenticatedSessionNav.tsx`
- Modify: `webui/src/AuthenticatedSessionNav.test.tsx`
- Modify: `src/api/webui.py`
- Modify: `src/platform_identity/routes.py`
- Modify: `src/api/server.py`
- Modify: `tests/unit/test_webui_mount.py`
- Modify: `tests/unit/test_platform_identity.py`

**Interfaces:**
- Consumes: existing FAE root APIs and Platform launch endpoint `POST /api/v1/agents/ai-fae-agent/launch`.
- Produces: `faeApiPath(path)`, `faeBrowserPath(path)`, `isInternalFaeSurface()`, and deep conversation URLs.

- [ ] **Step 1: Write failing runtime-path tests**

Inject the same meta tags that the backend will add to each HTML shell:

```html
<meta name="fae-browser-base" content="/fae">
<meta name="fae-api-base" content="/fae/api">
```

Assert:

```ts
expect(faeBrowserPath("/conversations/s%3A1")).toBe("/fae/conversations/s%3A1");
expect(faeApiPath("/chat")).toBe("/fae/api/chat");
expect(isInternalFaeSurface()).toBe(true);
```

For `/app`, assert `faeApiPath("/chat") === "/chat"` and the public surface remains false.

- [ ] **Step 2: Run and confirm failure**

```bash
cd webui
npm test -- --run src/runtimePaths.test.ts src/routes.test.ts src/api.test.ts src/enterpriseIdentity.test.ts src/AuthenticatedSessionNav.test.tsx
```

- [ ] **Step 3: Build relative assets and inject a trusted runtime base**

Set Vite `base: "./"`. In `src/api/webui.py`, read the built `index.html` once and inject these tags immediately after `<head>` from server-owned constants, never from request headers:

```python
def _workspace_index(index_html: str, browser_base: str, api_base: str) -> str:
    tags = (
        f'<base href="{browser_base}/">'
        f'<meta name="fae-browser-base" content="{browser_base}">'
        f'<meta name="fae-api-base" content="{api_base}">'
    )
    return index_html.replace("<head>", f"<head>{tags}", 1)
```

Serve `/app`, `/app/`, and `/app/{safe_spa_path}` with `browser_base="/app"`, `api_base=""`. Serve `/fae`, `/fae/`, `/fae/conversations/{session_id}`, and other safe internal SPA paths with `browser_base="/fae"`, `api_base="/fae/api"`. Mount the same immutable asset directory at `/app/assets` and `/fae/assets`.

Reject fallback for any path beginning with `api/`, `assets/`, `enterprise/`, `attachments/`, or `health`. Never expose `/fae/health` through the WebUI fallback.

- [ ] **Step 4: Replace every browser API root literal**

Wrap all existing FAE requests:

```ts
await fetch(faeApiPath("/chat"), ...)
await fetch(faeApiPath("/attachments"), ...)
await fetch(faeApiPath(`/attachments/${encodeURIComponent(id)}`), ...)
await fetch(faeApiPath("/enterprise/session"), ...)
await fetch(faeApiPath("/authenticated/conversations?..."), ...)
await fetch(faeApiPath("/feedback"), ...)
```

The public `/app` meta has an empty API base, so its requests remain byte-for-byte equivalent to the current root paths.

- [ ] **Step 5: Add canonical FAE conversation routes**

Implement:

```ts
export type FaeBrowserRoute =
  | { name: "chat"; sessionId?: string }
  | { name: "review" }
  | { name: "not-found" };
```

`/fae/conversations/{session_id}` and `/app/conversations/{session_id}` restore that owned conversation. Selecting history calls `history.pushState()` with the canonical path after `fetchAuthenticatedConversation()` succeeds. `新对话` replaces the URL with the surface root. A `popstate` listener restores the selected conversation or clears to a new chat without reloading the document.

- [ ] **Step 6: Require Platform identity on the internal surface**

When no FAE enterprise Session or launch fragment exists on `/fae/*`, do not enter `public_customer`. Instead:

```ts
const accountResponse = await fetch("/api/v1/account", { credentials: "include" });
if (accountResponse.status === 401) {
  window.location.replace(`/login?return_path=${encodeURIComponent(window.location.pathname)}`);
  throw new Error("platform_login_required");
}
const account = await accountResponse.json() as { csrf_token?: unknown };
if (typeof account.csrf_token !== "string" || !account.csrf_token) {
  throw new Error("platform_account_invalid");
}
const launchResponse = await fetch("/api/v1/agents/ai-fae-agent/launch", {
  method: "POST",
  credentials: "include",
  headers: { "X-CSRF-Token": account.csrf_token },
});
```

Before navigation, store only `safeInternalFaeReturnPath(window.location)` in `sessionStorage["fae:internal-return"]`. It accepts `/fae/` and `/fae/conversations/{safe-id}` only. Validate the returned launch URL as exactly `https://agent.orbbec.com.cn/fae/#platform_launch=<safe-code>` and navigate with `window.location.replace()`. After successful exchange, remove the stored value and restore it with `history.replaceState`; malformed or absent values restore `/fae/`. Map 403 to the explicit `没有 FAE 使用权限` state; never fall back to the public customer identity on the internal surface.

- [ ] **Step 7: Permit only the two approved browser origins for FAE enterprise mutations**

Extend the route registration interface without changing its existing caller default:

```python
def register_platform_identity_routes(
    app: FastAPI,
    *,
    service: AuthenticatedSessionService,
    public_origin: str,
    additional_browser_origins: tuple[str, ...] = (),
    partner_auth_start_url: str | None = None,
) -> None:
```

Canonicalize every value with the same HTTPS origin-only validator and reject duplicates. `server.py` passes `("https://agent.orbbec.com.cn",)` as the additional internal origin. The resulting immutable set is exactly:

```text
https://fae.orbbec.com.cn
https://agent.orbbec.com.cn
```

Continue exact string comparison after canonical URL validation. Reject subdomains, ports, `null`, missing Origin on unsafe browser requests, and every other origin. `identity_capabilities` returns `partner_login_available=false` when the request Host is `agent.orbbec.com.cn`; `/partner/login` returns 404 there. Public partner login behavior remains unchanged on `fae.orbbec.com.cn`.

- [ ] **Step 8: Run focused and build tests**

```bash
cd webui
npm test -- --run src/runtimePaths.test.ts src/routes.test.ts src/api.test.ts src/enterpriseIdentity.test.ts src/AuthenticatedSessionNav.test.tsx src/AppRender.test.tsx src/brandAssets.test.ts
npm run build
cd ..
.venv/bin/python -m pytest tests/unit/test_webui_mount.py tests/unit/test_platform_identity.py tests/unit/test_enterprise_chat_ownership.py tests/unit/test_partner_chat_ownership.py -q
```

Expected: both bases pass; public `/app` request URLs remain root-relative; internal `/fae` request URLs use `/fae/api`.

- [ ] **Step 9: Commit**

```bash
git add webui src/api/webui.py src/api/server.py src/platform_identity/routes.py tests/unit/test_webui_mount.py tests/unit/test_platform_identity.py
git commit -m "feat(webui): serve the FAE workspace on an internal base"
```

---

### Task 6: Finish the audited FAE management scope foundation

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/fae-independent-access`

**Files:**
- Preserve and complete: `backend/control_migrations/063_fae_workbench_access.sql`
- Preserve and complete: `backend/tests/test_control_plane_migration.py`
- Create: `backend/app/control_plane/fae_access.py`
- Modify: `backend/app/control_plane/routes_manage.py`
- Modify: `backend/app/control_plane/routes_auth.py`
- Modify: `backend/app/fae_workbench/routes.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_governance_audit_api.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`
- Modify: `backend/tests/test_fae_workbench_api.py`
- Modify: `backend/tests/test_fae_report_api.py`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/auth.test.ts`
- Create: `webui/src/faeAccessApi.ts`
- Create: `webui/src/faeAccessApi.test.ts`
- Create: `webui/src/components/FaeAccessPanel.tsx`
- Create: `webui/src/components/FaeAccessPanel.test.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.tsx`
- Modify: `webui/src/pages/IdentityManagementPage.test.tsx`

**Interfaces:**
- Produces: account `workspace_scopes: ("fae_workbench")[]`, Owner grant/revoke endpoints, and one `_fae_workbench_context` dependency shared by both FAE API prefixes.
- Consumes later: `account.workspace_scopes.includes("fae_workbench")` for UI visibility only; backend authorization remains authoritative.

- [ ] **Step 1: Verify and commit the already-written migration before rebasing**

```bash
git status --short
cd backend
.venv/bin/python -m pytest tests/test_control_plane_migration.py -q
cd ..
git diff --check
git add backend/control_migrations/063_fae_workbench_access.sql backend/tests/test_control_plane_migration.py
git commit -m "feat(control-plane): add audited FAE workspace grants"
```

Expected: the existing v63 grant, audit, idempotency, collision, pre-login identity, and direct-table-right tests pass. Do not replace this migration with a shorter rewrite.

- [ ] **Step 2: Rebase the local worktree branch onto current local `master`**

```bash
git rebase master
```

Resolve only true overlaps. Keep mainline migration numbering and all v63 security checks. Run `tests/test_control_plane_migration.py` again after the rebase.

- [ ] **Step 3: Implement the exact Owner mutation API**

Add:

```text
GET    /api/v1/manage/fae-workbench/grants
POST   /api/v1/manage/fae-workbench/grants
DELETE /api/v1/manage/fae-workbench/grants/{internal_user_id}
```

POST accepts only `display_name`, exact reason `fae_workbench_access_approved`, and `request_id`; DELETE accepts exact reason `fae_workbench_access_revoked`, `request_id`, and `expected_row_version`. Resolve names against the active complete directory generation, persist authority by UUID, create no Session, and never promote the global role.

- [ ] **Step 4: Project only the bounded workspace scope**

Extend the strict account response with:

```python
"workspace_scopes": ["fae_workbench"] if fae_access.allows(context) else []
```

Owner receives the scope. A granted active member receives it. `platform_admin`, `management_viewer`, ordinary member, revoked user, and inactive user do not receive it unless independently granted. Authorization repository failure returns the existing fail-closed account error rather than an empty optimistic scope.

- [ ] **Step 5: Mount the FAE API once per canonical and compatibility prefix**

Keep one unprefixed handler router and one dependency:

```python
app.include_router(fae_router, prefix="/api/fae")
app.include_router(fae_router, prefix="/api/admin/fae", include_in_schema=False)
```

Both mounts must call `_fae_workbench_context`, which permits only active Owner or active FAE grant. A Platform administrator without the grant receives 403 from both prefixes. Preserve CSRF, hard-stale, audit, row-version, and `write_available` behavior.

- [ ] **Step 6: Add the Owner-only grant panel**

Expose an input labelled `花名`, a required reason, current grants, creation time, status, row version, and revoke control. The browser never accepts or displays a target UUID during grant creation. Same `request_id` replays an indeterminate request; a new request cannot create a duplicate active grant.

- [ ] **Step 7: Run the full focused authorization matrix**

```bash
cd backend
.venv/bin/python -m pytest tests/test_control_plane_migration.py tests/test_governance_audit_api.py tests/test_dingtalk_auth_api.py tests/test_fae_workbench_api.py tests/test_fae_report_api.py -q
cd ../webui
npm test -- --run src/auth.test.ts src/faeAccessApi.test.ts src/components/FaeAccessPanel.test.tsx src/pages/IdentityManagementPage.test.tsx
```

- [ ] **Step 8: Commit service, API, projection, and panel in reviewable commits**

```bash
git add backend/app backend/tests
git commit -m "feat(control-plane): manage FAE access by enterprise name"
git add webui/src
git commit -m "feat(identity): manage FAE workspace access"
```

Do not push the feature branch. Merge it locally only after Task 8's route work is ready or cherry-pick these commits into the route integration worktree.

---

### Task 7: Move FAE management pages under `/fae/manage/*`

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `webui/src/workspaces/fae/FaeManagementWorkspace.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/faeWorkbenchApi.ts`
- Modify: `webui/src/faeReportApi.ts`
- Modify: `webui/src/sessionNavigation.ts`
- Modify: `webui/src/components/fae-workbench/FaeWorkbenchShell.tsx`
- Modify: `webui/src/components/review/ReviewWorkspace.tsx`
- Modify: `webui/src/pages/FaeOverviewPage.tsx`
- Modify: `webui/src/pages/FaeSessionsPage.tsx`
- Modify: `webui/src/pages/FaeSessionDetailPage.tsx`
- Modify: `webui/src/pages/FaeIssuesPage.tsx`
- Modify: `webui/src/pages/FaeReportsPage.tsx`
- Modify: matching FAE component/page/API tests

**Interfaces:**
- Consumes: account `workspace_scopes`, canonical `/api/fae`, and `fae-manage-*` routes.
- Produces: FAE local navigation `Agent | 管理`, where `Agent` is a full-document link to `/fae/`.

- [ ] **Step 1: Change test expectations before production paths**

Use these exact browser constants:

```ts
export const FAE_DIRECT_PATH = "/fae/";
export const FAE_MANAGEMENT_PATH = "/fae/manage";
export const FAE_WORKBENCH_API_PATH = "/api/fae";
```

Update tests so overview, sessions, issues, reports, and all detail links live under `/fae/manage/*`. Assert `/admin/fae/*` returns one `legacy-redirect` to the same suffix under `/fae/manage/*`.

- [ ] **Step 2: Run the FAE frontend suite and confirm failure**

```bash
cd webui
npm test -- --run src/components/fae-workbench/FaeWorkbenchShell.test.tsx src/pages/FaeOverviewPage.test.tsx src/pages/FaeSessionsPage.test.tsx src/pages/FaeSessionDetailPage.test.tsx src/pages/FaeIssuesPage.test.tsx src/pages/FaeReportsPage.test.tsx src/faeWorkbenchApi.test.ts src/faeReportApi.test.ts src/sessionNavigation.test.ts
```

- [ ] **Step 3: Replace path literals with the canonical constants**

Set the shell links to:

```text
/fae/                         Agent
/fae/manage/                  概览
/fae/manage/sessions          Sessions
/fae/manage/issues            反馈与修复
/fae/manage/reports           分析报告
```

Use `/api/fae` for every fetch. Keep `/api/admin/fae` only in compatibility API tests and deployment probes.

- [ ] **Step 4: Enforce management scope in the Platform page selection**

```ts
const hasFaeManagement = account.role === "platform_owner"
  || account.workspace_scopes.includes("fae_workbench");
```

Use this only to select the page and navigation. The API independently applies `_fae_workbench_context`. Authenticated users without scope render a stable workspace-specific 403 page. FAE management must not appear in the generic `/admin` navigation.

- [ ] **Step 5: Preserve explicit replica read-only behavior**

Remove path-derived read-only logic from `ReviewWorkspace` and pass it explicitly:

```tsx
<ReviewWorkspace
  basePath="/fae/manage/issues"
  enforceDeploymentReadOnly
/>
```

When `write_available=false`, every mutation control is absent or disabled and the backend still rejects direct mutation attempts. FAE freshness/read-only banners render inside this workspace, not globally.

- [ ] **Step 6: Run and commit**

```bash
cd webui
npm test -- --run src/router.test.ts src/auth.test.ts src/components/fae-workbench/FaeWorkbenchShell.test.tsx src/pages/FaeOverviewPage.test.tsx src/pages/FaeSessionsPage.test.tsx src/pages/FaeSessionDetailPage.test.tsx src/pages/FaeIssuesPage.test.tsx src/pages/FaeReportsPage.test.tsx src/faeWorkbenchApi.test.ts src/faeReportApi.test.ts src/sessionNavigation.test.ts
cd ..
git add webui/src
git commit -m "feat(fae): move management under the FAE workspace"
```

---

### Task 8: Wire the internal FAE launch and exact Nginx route ownership

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/control_plane/agent_launch.py`
- Modify: `backend/tests/test_agent_launch.py`
- Modify: `webui/src/brainApi.ts`
- Modify: `webui/src/brainApi.test.ts`
- Modify: `backend/app/agent_catalog/catalog.yaml`
- Modify: `backend/tests/test_agent_catalog.py`
- Modify: `deploy/cloud/agent-domain.nginx.conf`
- Modify: `backend/tests/test_agent_domain_deployment.py`
- Modify: `backend/tests/test_cloud_deployment.py`

**Interfaces:**
- Consumes: AI FAE dual-base artifact from Task 5 and FAE management routes from Task 7.
- Produces: launch URLs at `https://agent.orbbec.com.cn/fae/#platform_launch=...` and non-overlapping Nginx owners.

- [ ] **Step 1: Write failing launch and Nginx ownership tests**

Change launch expectations to:

```text
https://agent.orbbec.com.cn/fae/#platform_launch=<single-use-code>
```

Assert Nginx has distinct exact blocks in this precedence:

```text
= /fae                         308 /fae/
= /fae/manage                  308 /fae/manage/
^~ /fae/manage/               Platform 127.0.0.1:8080
= /fae/api/chat               FAE 127.0.0.1:8000, SSE buffering off
= /fae/api/attachments        FAE 127.0.0.1:8000, 50 MB request limit
^~ /fae/api/                  FAE 127.0.0.1:8000, strip /fae/api prefix
^~ /fae/assets/               FAE 127.0.0.1:8000, immutable assets
^~ /fae/                      FAE 127.0.0.1:8000, retain URI
```

Assert `/fae/health` returns 404 and `/fae/manage/*` appears before the `/fae/*` catch-all.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_launch.py tests/test_agent_catalog.py tests/test_agent_domain_deployment.py tests/test_cloud_deployment.py -q
cd ../webui
npm test -- --run src/brainApi.test.ts
```

- [ ] **Step 3: Change only the internal enterprise launch target**

Set:

```python
_FAE_LAUNCH_BASE = "https://agent.orbbec.com.cn/fae/"
```

Update the browser parser to require exactly that origin/path, no query, and one valid `platform_launch` fragment. Keep one-time code, binding validation, expiry, and minimal identity response unchanged.

- [ ] **Step 4: Point the canonical Agent catalog entry at `/fae/`**

Change only `ai-fae-agent.workspace_url` from the public FAE URL to `/fae/`. Do this in the same deployment unit as the Nginx route, never before Task 5's FAE artifact is live.

- [ ] **Step 5: Add hardened FAE locations without editing Office or VOC blocks**

For every new FAE proxy block, overwrite `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`, and `Host`; clear `Forwarded`, `Authorization`, and hop-by-hop `Connection`; re-declare the full security header set because nested `add_header` stops inheritance. Use `Cache-Control: private, no-store` except immutable assets. Use 330-second proxy timeouts for chat and disable response/request buffering there.

For `/fae/api/*`, strip exactly one `/fae/api` prefix before proxying. Never rewrite `/fae/manage/*`; those requests retain their URI and go to Platform. Do not alter any `/office`, `/voc`, TLS, ACME, `/admin`, or generic Platform block.

- [ ] **Step 6: Run Nginx and launch tests**

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_launch.py tests/test_agent_catalog.py tests/test_agent_domain_deployment.py tests/test_cloud_deployment.py -q
cd ../webui
npm test -- --run src/brainApi.test.ts src/pages/AgentUsePage.test.tsx
cd ..
```

Expected: the repository's rendered-config tests substitute production placeholders and validate the exact location ownership; direct `nginx -t` is deferred to the staged production candidate in Task 11.

- [ ] **Step 7: Commit**

```bash
git add backend/app/control_plane/agent_launch.py backend/tests/test_agent_launch.py backend/app/agent_catalog/catalog.yaml backend/tests/test_agent_catalog.py webui/src/brainApi.ts webui/src/brainApi.test.ts deploy/cloud/agent-domain.nginx.conf backend/tests/test_agent_domain_deployment.py backend/tests/test_cloud_deployment.py
git commit -m "feat(fae): route the internal FAE workspace"
```

---

### Task 9: Make `/agents` launch-only and finish compatibility routing

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `webui/src/pages/AgentUseDirectoryPage.tsx`
- Modify: `webui/src/pages/AgentUsePage.tsx`
- Modify: `webui/src/pages/AgentUsePage.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Consumes: complete Workspace Registry and all canonical workspace routes.
- Produces: no directory card links under `/agents/{id}` for the nine catalog Agents.

- [ ] **Step 1: Add one complete directory/compatibility matrix**

Assert all nine cards point to exactly:

```text
ai-fae-agent                  /fae/
hr-bot                        /hr/
voc                           /voc/
ai-admin-agent                /office/?view=services
marketing-prospecting-bot     /marketing/prospecting
marketing-inbound-bot         /marketing/inbound
marketing-voice-bot           /marketing/voice
marketing-intelligence-bot    /marketing/intelligence
marketing-gtm-bot             /marketing/gtm
```

Assert no rendered card href starts with `/agents/`.

- [ ] **Step 2: Test every old bookmark**

Cover every route in design section 10, including deep HR/Marketing conversations, FAE management details, `/agents/voc/workspace`, `/admin/voc`, and `/voc/?view=management`. Assert exactly one redirect and no loop.

- [ ] **Step 3: Remove the duplicate direct-use entry path**

Delete migrated Agent IDs from the generic `agent`/`agent-conversation` render path. Keep only compatibility parse rules. Unknown or excluded Agent IDs return the existing not-found page and are not invented as workspaces.

- [ ] **Step 4: Make cards visibly actionable without changing grouping semantics**

Use one full-card anchor with visible title, short capability copy, availability, and `打开` affordance. Preserve FAE first, HR second, VOC third, five Marketing Agents after VOC, and Office last. Do not reintroduce forced business-category group headings.

- [ ] **Step 5: Run and commit**

```bash
cd webui
npm test -- --run src/platform/workspaces.test.ts src/pages/AgentUsePage.test.tsx src/router.test.ts src/AppShell.brain.test.tsx src/styles.test.ts
cd ..
git add webui/src
git commit -m "refactor(webui): make the Agent directory launch-only"
```

---

### Task 10: Add cross-workspace acceptance and rollback gates

**Repositories:** Agent Platform, AI FAE, and VOC

**Files:**
- Modify: `AI-Agent-Platform/deploy/cloud/accept.sh`
- Modify: `AI-Agent-Platform/backend/tests/test_cloud_deployment.py`
- Modify: `AI-Agent-Platform/backend/tests/test_agent_domain_deployment.py`
- Modify: `AI-Agent-Platform/backend/tests/test_dingtalk_auth_api.py`
- Modify: `AI-FAE-Agent/tests/unit/test_deploy_artifacts.py`
- Modify: `AI-FAE-Agent/tests/unit/test_verify_prod_script.py`
- Modify: `Orbbec-VOC-Agent/tests/deploy/test_linux_mvp_contract.py`
- Modify: `Orbbec-VOC-Agent/tests/acceptance/test_platform_workspace_contract.py`

**Interfaces:**
- Consumes: all canonical routes and authorization scopes.
- Produces: a fail-closed production route, identity, scope, and non-regression matrix.

- [ ] **Step 1: Replace old FAE browser probes with canonical management paths**

Use:

```text
/fae/
/fae/conversations/{owned-id}
/fae/manage/
/fae/manage/sessions
/fae/manage/issues
/fae/manage/reports
/api/fae/overview
/api/fae/sessions?limit=1
/api/fae/issues
/api/fae/reports/latest
```

Retain one browser compatibility probe for `/admin/fae/reports` and one API compatibility probe for `/api/admin/fae/overview`.

- [ ] **Step 2: Add the independent scope matrix**

```text
Owner                         FAE manage 200, VOC manage 200
FAE manager only              FAE manage 200, VOC manage 403
VOC manager only              FAE manage 403, VOC manage 200
platform_admin without scope  FAE manage 403, VOC follows existing independent rule
ordinary member               both management routes 403
revoked FAE grant             next FAE request 403
```

Direct-use grant failures remain independent of management scope failures.

- [ ] **Step 3: Add history and deep-link checks**

With real test identities, prove HR never returns Marketing conversations, each Marketing slug returns only its own Agent, cross-owner conversation IDs return the non-enumerating 404, FAE internal history contains only the current FAE subject, and VOC user history does not expose management records.

- [ ] **Step 4: Add route-ownership and failure-isolation checks**

Verify:

```text
/                               Platform Agent Brain
/office/*                       AI ADMIN upstream
/fae/manage/*                   Platform upstream
/fae/* excluding manage         AI FAE upstream
/voc/*                          VOC upstream
/hr/* and /marketing/*          Platform upstream
/admin/*                        Platform upstream
```

Stop or stub each non-Platform upstream in isolation and assert only its own workspace shows an error. No failure may redirect to `/admin` or change another workspace's response.

- [ ] **Step 5: Preserve explicit non-regression snapshots**

Before and after the route transaction, compare status, Location, content marker, upstream ownership, and response security headers for:

```text
https://agent.orbbec.com.cn/
https://agent.orbbec.com.cn/office/
https://agent.orbbec.com.cn/office/?view=services
https://fae.orbbec.com.cn/
https://agent.orbbec.com.cn/voc/
```

For AI FAE also compare the public release/container identity and public `/app/` chat, attachment, partner-login, and enterprise-session tests. Route separation must not deploy a separate public behavior.

- [ ] **Step 6: Run shell and focused acceptance tests**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
bash -n deploy/cloud/accept.sh deploy/cloud/deploy.sh
cd backend
.venv/bin/python -m pytest tests/test_cloud_deployment.py tests/test_agent_domain_deployment.py tests/test_dingtalk_auth_api.py -q

cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest tests/unit/test_deploy_artifacts.py tests/unit/test_verify_prod_script.py tests/unit/test_webui_mount.py tests/unit/test_platform_identity.py -q

cd /Users/neo/Developer/work/Orbbec-VOC-Agent
.venv/bin/python -m pytest tests/deploy/test_linux_mvp_contract.py tests/acceptance/test_platform_workspace_contract.py tests/api/test_workspace_web.py -q
```

- [ ] **Step 7: Commit acceptance changes in each owning repository**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
git add tests
git commit -m "test(deploy): gate the internal FAE route"

cd /Users/neo/Developer/work/Orbbec-VOC-Agent
git add tests webui/src
git commit -m "test(deploy): gate canonical VOC routes"

cd /Users/neo/Developer/work/AI-Agent-Platform
git add deploy/cloud/accept.sh backend/tests
git commit -m "test(deploy): gate workspace route ownership"
```

---

### Task 11: Integrate locally, verify all three repositories, and deploy in safe order

**Repositories:** Agent Platform, AI FAE, and VOC

**Files:**
- No new production source files.
- Runtime evidence goes only to each repository's existing ignored deployment-evidence directory.

**Interfaces:**
- Consumes: exact commits from Tasks 1–10.
- Produces: one verified `master` per repository and production evidence for the route contract.

- [ ] **Step 1: Reconcile local branches without pushing feature branches**

For each repository:

```bash
git status --short --branch
git log --oneline --decorate -12
git worktree list
```

Commit or preserve only task-owned files. Merge/cherry-pick locally onto the repository's current `master`. If another local session advanced `master`, merge that change and rerun the affected tests. Never clean unrelated untracked files.

- [ ] **Step 2: Run complete AI FAE verification first**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
.venv/bin/python -m pytest
cd webui
npm test -- --run
npm run build
cd ..
git diff --check
```

Deploy this dual-base-capable artifact while Nginx still sends no traffic to `/fae/*`. Verify public `https://fae.orbbec.com.cn/` and `/app/` before continuing.

- [ ] **Step 3: Run complete VOC verification and deploy its canonical path support**

```bash
cd /Users/neo/Developer/work/Orbbec-VOC-Agent
.venv/bin/python -m pytest
cd webui
npm test -- --run
npm run build
cd ..
git diff --check
```

Deploy VOC and verify both `/voc/` and `/voc/manage/`. Keep `?view=management` compatibility for this release.

- [ ] **Step 4: Run complete Platform verification**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/backend
.venv/bin/python -m pytest
cd ../webui
npm test -- --run
npm run build
cd ..
bash -n deploy/cloud/deploy.sh deploy/cloud/accept.sh
git diff --check
```

Run governance searches:

```bash
rg -n '(/admin/fae|/api/admin/fae|view=management|/agents/(hr-bot|marketing-|ai-fae-agent|voc))' webui/src backend/app deploy/cloud
rg -n '(天启|范闲|苍渊)' backend webui deploy
```

Expected: old routes occur only in compatibility code/tests and historical docs; display names do not occur in production source.

- [ ] **Step 5: Capture rollback targets before changing Nginx**

Record exact Platform, FAE, VOC, and AI ADMIN release/commit IDs; current Nginx hash; container IDs; restart counts; migration maximum; and the pre-change URL evidence from Task 10. Prepare an absolute-path rollback script that restores the previous Nginx file and Platform release without rolling back databases or changing AI ADMIN/FAE/VOC data.

- [ ] **Step 6: Push only repository masters and deploy Platform last**

```bash
git push origin master
```

Run this separately from each repository only after its local master is verified. Platform deploy is last because its Nginx transaction activates `/fae/*` and its directory starts linking to the new workspaces.

- [ ] **Step 7: Run real-session acceptance**

Verify all canonical routes on desktop and mobile; exact login return; FAE and VOC independent authorization; HR/Marketing scoped history; FAE attachment upload and chat streaming; FAE/VOC deep-link refresh; legacy redirects; and per-workspace failure containment. Do not output cookies, launch codes, CSRF tokens, raw DingTalk IDs, customer content, or Session transcripts.

- [ ] **Step 8: Verify rollback without executing a destructive database rollback**

Render the prior Nginx/config release in a temporary path, run structural tests and `nginx -t`, and prove the rollback target restores the pre-change owners. If live rollback becomes necessary, restore the exact Nginx backup and Platform release, reload Nginx, then re-run `/`, `/office/*`, public FAE, and VOC checks. Do not restart or downgrade AI ADMIN merely because Platform rolls back.

- [ ] **Step 9: Record completion evidence**

The release report may include these markers only after every assertion is proven:

```text
WORKSPACE_ROUTE_SEPARATION_OK=true
AGENT_DIRECTORY_LAUNCH_ONLY=true
FAE_MANAGEMENT_SCOPE_INDEPENDENT=true
VOC_MANAGEMENT_SCOPE_INDEPENDENT=true
HR_MARKETING_HISTORY_SCOPED=true
OFFICE_ROUTES_UNCHANGED=true
PUBLIC_FAE_UNCHANGED=true
```

---

## Compatibility Removal After One Release

After one full release with production evidence and no old-link failures, create a separate cleanup change that removes only:

- `/agents/{migrated-agent-id}` browser redirects;
- `/admin/fae/*` browser redirects;
- `/admin/voc` and `?view=management` browser redirects;
- `/api/admin/fae` compatibility mount.

Do not delete conversations, sessions, grants, reports, audit records, or historical URLs recorded inside immutable evidence. The cleanup needs its own tests proving canonical routes remain unchanged.
