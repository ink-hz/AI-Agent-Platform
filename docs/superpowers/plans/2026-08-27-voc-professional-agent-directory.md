# VOC Professional Agent Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the existing VOC workspace to the authorized professional Agent directory for every active enterprise member.

**Architecture:** Keep `backend/app/agent_catalog/catalog.yaml` as the directory source of truth. Add `voc` as an external-workspace-only card, extend the database authorization allowlist in a new immutable migration, and allow only the exact same-origin `/agents/voc/workspace` link in the React directory. Production receives an audited `all_members` grant through the existing grant function.

**Tech Stack:** Python 3.12, Pydantic 2, PostgreSQL 17/PLpgSQL, Pytest, React 19, TypeScript, Vitest, Docker Compose, Bash.

## Global Constraints

- VOC appears after HR and before the five Marketing Agents.
- `voc` uses only `external_workspace`; it has no Adapter and is never Brain-dispatchable.
- The only accepted workspace URL is `/agents/voc/workspace`.
- Access remains subject to backend `agent_use_grants`; frontend visibility is not authorization.
- The production grant target is `all_members` and must be created by the audited grant function.
- Do not modify the VOC service, VOC database, AI ADMIN `/office`, FAE, MetaBot, or Brain runtime.

---

### Task 1: Canonical VOC Catalog and Authorization Allowlist

**Files:**
- Modify: `backend/tests/test_agent_catalog.py`
- Modify: `backend/tests/test_control_plane_migration.py`
- Create: `backend/tests/test_voc_agent_catalog_migration.py`
- Modify: `backend/app/agent_catalog/models.py`
- Modify: `backend/app/agent_catalog/catalog.yaml`
- Create: `backend/control_migrations/048_voc_agent_catalog_authorization.sql`

**Interfaces:**
- Consumes: `load_agent_catalog()` and `platform_control.has_agent_use_scope_v29(uuid, text)`.
- Produces: canonical Agent ID `voc`, an external workspace card, and authorization evaluation for `voc`.

- [ ] **Step 1: Write failing Catalog tests**

Add `"voc"` to `EXPECTED_IDS`, rename the exact-count test to nine Agents, and extend the external-mode assertions:

```python
def test_catalog_contains_exactly_the_nine_product_agents() -> None:
    cards = load_agent_catalog()
    assert set(CANONICAL_AGENT_IDS) == EXPECTED_IDS
    assert tuple(card.agent_id for card in cards) == CANONICAL_AGENT_IDS

def test_catalog_expresses_direct_delegated_and_external_modes_explicitly() -> None:
    repository = AgentCatalogRepository()
    for agent_id in EXPECTED_IDS - {"ai-admin-agent", "ai-fae-agent", "voc"}:
        assert repository.require(agent_id).dispatchable is True
    voc = repository.require("voc")
    assert voc.interaction_modes == ("external_workspace",)
    assert voc.workspace_url == "/agents/voc/workspace"
    assert voc.adapter_kind is None
    assert voc.dispatchable is False
```

Create the migration test:

```python
from pathlib import Path

MIGRATION = Path(__file__).parents[1] / "control_migrations" / "048_voc_agent_catalog_authorization.sql"

def test_voc_authorization_migration_extends_the_canonical_allowlist() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "create or replace function platform_control.has_agent_use_scope_v29" in sql
    assert "'voc'" in sql
    assert "Canonical nine-Agent authorization allowlist" in sql
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_agent_catalog.py \
  backend/tests/test_voc_agent_catalog_migration.py
```

Expected: failures because `voc` and migration 048 do not exist.

- [ ] **Step 3: Implement the Catalog card and strict URL**

Insert `"voc"` after `"hr-bot"` in `CANONICAL_AGENT_IDS` and add:

```python
_WORKSPACE_URLS = {
    "ai-admin-agent": "/office/?view=services",
    "ai-fae-agent": "https://fae.orbbec.com.cn/",
    "voc": "/agents/voc/workspace",
}
```

Insert this card after HR in `catalog.yaml`:

```yaml
  - agent_id: voc
    display_name: VOC 洞察助手
    domain_group: 客户洞察
    mission: 将客户反馈整理为可编辑 VOC 草稿，确认后入库，并查看和补充本人记录。
    capabilities: [整理客户反馈为结构化草稿, 提交本人 VOC, 查看和补充本人记录]
    exclusions: [不代替用户确认并提交草稿, 不读取无权访问的他人 VOC]
    required_inputs: [客户反馈原文, 企业登录身份]
    example_tasks: [把客户反馈整理成 VOC 草稿, 查看并补充我提交的 VOC]
    interaction_modes: [external_workspace]
    workspace_url: /agents/voc/workspace
    supports_persistent_session: false
    supports_followup_message: false
    supports_progress_events: false
    supports_thinking_summary: false
    supports_cancel: false
    supports_attachments: false
    typical_latency_seconds: 30
    capability_version: 1
```

- [ ] **Step 4: Create migration 048**

Create the immutable migration with this complete definition; do not edit released migration 043:

```sql
create or replace function platform_control.has_agent_use_scope_v29(
  selected_user_id uuid,
  selected_agent_id text
) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  with active_member as (
    select state.active_generation_id, member.member_key
    from platform_control.internal_users users
    join platform_control.directory_state state on state.singleton
    join platform_control.directory_generations generation
      on generation.generation_id = state.active_generation_id
     and generation.status = 'complete'
    join platform_control.directory_members member
      on member.generation_id = state.active_generation_id
     and member.internal_user_id = users.internal_user_id
     and member.status = 'active'
    where users.internal_user_id = selected_user_id
      and users.status = 'active'
      and users.locally_invalidated_at is null
      and selected_agent_id in (
        'hr-bot',
        'voc',
        'marketing-prospecting-bot',
        'marketing-inbound-bot',
        'marketing-voice-bot',
        'marketing-intelligence-bot',
        'marketing-gtm-bot',
        'ai-admin-agent',
        'ai-fae-agent'
      )
  )
  select exists (
    select 1
    from active_member member
    join platform_control.agent_use_grants grant_row
      on grant_row.agent_id = selected_agent_id
     and grant_row.revoked_at is null
    where (
      grant_row.target_kind = 'all_members'
      or (
        grant_row.target_kind = 'user'
        and grant_row.target_internal_user_id = selected_user_id
      )
      or (
        grant_row.target_kind = 'department'
        and grant_row.include_descendants
        and exists (
          select 1
          from platform_control.member_departments membership
          join platform_control.department_closure closure
            on closure.generation_id = membership.generation_id
           and closure.descendant_department_key = membership.department_key
           and closure.ancestor_department_key = grant_row.target_department_key
          where membership.generation_id = member.active_generation_id
            and membership.member_key = member.member_key
        )
      )
    )
  )
$function$;

comment on function platform_control.has_agent_use_scope_v29(uuid, text) is
  'Canonical nine-Agent authorization allowlist, revised by migration 048.';
```

`create or replace function` preserves the existing function privileges while changing only the canonical ID allowlist.

- [ ] **Step 5: Run backend tests and verify GREEN**

Run:

```bash
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_agent_catalog.py \
  backend/tests/test_agent_catalog_migration.py \
  backend/tests/test_voc_agent_catalog_migration.py \
  backend/tests/test_control_plane_migration.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add backend/app/agent_catalog backend/control_migrations/048_voc_agent_catalog_authorization.sql \
  backend/tests/test_agent_catalog.py backend/tests/test_voc_agent_catalog_migration.py
git commit -m "feat(catalog): add VOC professional Agent"
```

---

### Task 2: VOC Directory Card, Ordering, and URL Fail-Closed Behavior

**Files:**
- Modify: `webui/src/pages/AgentUsePage.test.tsx`
- Modify: `webui/src/pages/AgentUseDirectoryPage.tsx`

**Interfaces:**
- Consumes: `AgentCapabilityCard` with `agent_id="voc"`, `interaction_modes=["external_workspace"]`, and `workspace_url="/agents/voc/workspace"`.
- Produces: a clickable `data-agent-kind="voc"` card after HR and before Marketing.

- [ ] **Step 1: Write failing directory tests**

Add the fixture:

```typescript
const vocCard: AgentCapabilityCard = {
  ...adminCard, agent_id: "voc", display_name: "VOC 洞察助手", domain_group: "客户洞察",
  workspace_url: "/agents/voc/workspace",
};
```

Include it in the directory test input and assert the exact order and secure link:

```typescript
expect(cards.map((node) => node.getAttribute("href"))).toEqual([
  "https://fae.orbbec.com.cn/",
  "/agents/hr-bot",
  "/agents/voc/workspace",
  "/agents/marketing-gtm-bot",
  "/office/?view=services",
]);
expect(container.querySelector("a[href='/agents/voc/workspace']")?.getAttribute("data-agent-kind")).toBe("voc");
```

Change the malicious URL test to poison `vocCard.workspace_url` and verify no malicious anchor is rendered.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd webui && npm test -- src/pages/AgentUsePage.test.tsx
```

Expected: order/link assertions fail because VOC is not allowlisted or typed.

- [ ] **Step 3: Implement the card contract**

Update the directory constants and kind selection:

```typescript
const WORKSPACE_URLS: Readonly<Record<string, string>> = Object.freeze({
  "ai-admin-agent": "/office/?view=services",
  "ai-fae-agent": "https://fae.orbbec.com.cn/",
  "voc": "/agents/voc/workspace",
});
const AGENT_ORDER = Object.freeze([
  "ai-fae-agent", "hr-bot", "voc",
  "marketing-prospecting-bot", "marketing-inbound-bot", "marketing-voice-bot",
  "marketing-intelligence-bot", "marketing-gtm-bot", "ai-admin-agent",
]);
type AgentKind = "fae" | "hr" | "voc" | "marketing" | "admin";

function agentKind(card: AgentCapabilityCard): AgentKind {
  if (card.agent_id === "ai-fae-agent") return "fae";
  if (card.agent_id === "ai-admin-agent") return "admin";
  if (card.agent_id === "voc") return "voc";
  return card.domain_group === "Marketing" ? "marketing" : "hr";
}
```

- [ ] **Step 4: Run frontend tests and build**

Run:

```bash
cd webui
npm test -- src/pages/AgentUsePage.test.tsx src/router.test.ts src/auth.test.ts
npm run build
```

Expected: all tests and the TypeScript/Vite build pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add webui/src/pages/AgentUseDirectoryPage.tsx webui/src/pages/AgentUsePage.test.tsx
git commit -m "feat(web): list VOC after HR"
```

---

### Task 3: Cloud Acceptance, Audited All-Member Grant, and Release

**Files:**
- Create: `backend/tests/test_voc_agent_directory_deployment.py`
- Modify: `deploy/cloud/accept.sh`

**Interfaces:**
- Consumes: authenticated member Catalog API, migration 048, and `platform_control.grant_agent_use_scope_v29`.
- Produces: a production-visible VOC card for every active member with an immutable audit event.

- [ ] **Step 1: Write the failing acceptance-source test**

Create:

```python
from pathlib import Path

ACCEPT = Path(__file__).parents[2] / "deploy" / "cloud" / "accept.sh"

def test_authenticated_member_acceptance_requires_voc_catalog_access() -> None:
    script = ACCEPT.read_text(encoding="utf-8")
    assert '"voc" not in agents' in script
    assert '"marketing-gtm-bot" in agents' in script
```

The production assertion itself must require both HR and VOC while retaining the existing Marketing denial:

```python
agents={item.get("agent_id") for item in json.load(open(sys.argv[1],encoding="utf-8")).get("agents",[])}
if "hr-bot" not in agents or "voc" not in agents or "marketing-gtm-bot" in agents:
    raise SystemExit(1)
```

- [ ] **Step 2: Run the acceptance static tests and verify RED**

Run:

```bash
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_voc_agent_directory_deployment.py
```

Expected: failure because `accept.sh` does not contain the VOC requirement.

- [ ] **Step 3: Implement the acceptance assertion and verify locally**

Apply the assertion from Step 1, then run:

```bash
bash -n deploy/cloud/accept.sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_voc_agent_directory_deployment.py \
  backend/tests/test_cloud_deployment.py \
  backend/tests/test_cloud_acceptance_policy.py
```

Expected: shell syntax and selected tests pass.

- [ ] **Step 4: Run the complete release gate**

Run:

```bash
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
cd webui && npm test && npm run build
```

Expected: backend and frontend suites pass with zero failures; build exits 0.

- [ ] **Step 5: Commit and push**

```bash
git add backend/tests/test_voc_agent_directory_deployment.py deploy/cloud/accept.sh
git commit -m "test(deploy): require VOC in member catalog"
git push -u origin feat/voc-agent-directory
```

- [ ] **Step 6: Merge to master and deploy with the existing cloud release script**

Merge the reviewed branch without rewriting unrelated local master work, push that exact commit to `origin/master`, then run:

```bash
deploy/cloud/deploy.sh "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"
```

Record the resulting release commit, image, container start time, migration 048 success, and health state. Do not restart FAE or AI ADMIN.

- [ ] **Step 7: Create the audited production all-member grant**

Resolve the current active `platform_owner` internal UUID from the control database. Generate separate UUIDv4 values for the grant and audit request, then call:

```sql
select platform_control.grant_agent_use_scope_v29(
  :grant_id::uuid,
  'voc',
  'all_members',
  null,
  null,
  false,
  :owner_internal_user_id::uuid,
  'VOC_ALL_MEMBERS_20260827',
  :request_id::uuid
);
```

Use the production control database secret file without printing its contents. Verify one active `all_members` grant for `voc` and one matching `agent_use_scope_granted` audit event.

- [ ] **Step 8: Verify production behavior**

Run the authenticated production acceptance suite, then verify:

- `/agents` returns 200 for a real member session;
- `/api/v1/catalog/agents` includes `voc` for that member;
- the rendered link is exactly `/agents/voc/workspace` and follows HR;
- the VOC page loads and refreshes under the same Platform identity;
- `/office/?view=services` and `https://fae.orbbec.com.cn/` remain unchanged;
- `voc` is absent from Brain-dispatchable Agent results.

- [ ] **Step 9: Record rollback evidence**

Record the previous Platform image and release. Rollback means restoring that image and invoking the audited revoke function for the `voc` grant; it must not delete VOC records or stop the VOC service.
