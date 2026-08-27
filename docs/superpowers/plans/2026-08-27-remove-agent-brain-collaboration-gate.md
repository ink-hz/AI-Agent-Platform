# Remove Agent Brain Collaboration Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permanently delete the extra Agent Brain Collaboration release gate so authenticated users always enter the real Brain workspace and real service failures are shown explicitly.

**Architecture:** Keep the two core Brain compatibility switches (`PLATFORM_AGENT_BRAIN_ENABLED` and `PLATFORM_AGENT_BRAIN_V2_ENABLED`) and remove every configuration, UI, and deployment dependency on `PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED`. The authenticated shell always renders the Brain workspace; the existing Conversation API remains the authoritative availability boundary and its explicit 503 is projected as a retryable user-visible failure.

**Tech Stack:** Python 3.11, FastAPI, PostgreSQL, React 18, TypeScript, Vitest, Pytest, Docker Compose, Bash.

## Global Constraints

- Delete `PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED`; do not rename or replace it.
- Delete the “Agent 大脑正在准备” product state and all `brain-preparing` headers or metadata.
- Retain `PLATFORM_AGENT_BRAIN_ENABLED` and `PLATFORM_AGENT_BRAIN_V2_ENABLED` as the only core compatibility switches.
- A real Brain failure must be explicit and retryable; never redirect to Professional Agents and never silently downgrade.
- Do not modify Nginx or restart AI ADMIN, FAE, or VOC.
- Preserve the user's untracked files and unrelated worktree changes.

---

### Task 1: Remove the backend Collaboration configuration and shell gate

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/control_plane/routes_auth.py`
- Modify: `backend/tests/test_config.py`
- Modify: `backend/tests/test_cloud_config.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`
- Modify: `backend/tests/test_agent_brain_deployment.py`

**Interfaces:**
- Consumes: `Config.agent_brain_enabled: bool`, `Config.agent_brain_v2_enabled: bool`.
- Produces: `build_auth_router(...)` without an `agent_brain_enabled` parameter; `build_conversation_router(..., brain_enabled=config.agent_brain_enabled)`.

- [ ] **Step 1: Write failing configuration and shell tests**

In `backend/tests/test_config.py`, replace Collaboration defaults with an assertion that the field no longer exists even when a legacy environment variable is present:

```python
monkeypatch.setenv("PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED", "0")
config = load_config()
assert not hasattr(config, "agent_brain_collaboration_enabled")
```

In `backend/tests/test_dingtalk_auth_api.py`, change the authenticated-root assertions to require an ordinary identity shell with no release-state header or Brain mode metadata:

```python
root = client.get("/", cookies=cookies, follow_redirects=False)
assert root.status_code == 200
assert "x-platform-entry-state" not in root.headers
assert "platform-agent-brain-mode" not in root.text
```

In `backend/tests/test_agent_brain_deployment.py`, require the source to contain neither the config property nor `brain_use_enabled`:

```python
source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
assert "agent_brain_collaboration_enabled" not in source
assert "brain_use_enabled" not in source
```

- [ ] **Step 2: Run focused backend tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest -q \
  backend/tests/test_config.py \
  backend/tests/test_cloud_config.py \
  backend/tests/test_dingtalk_auth_api.py \
  backend/tests/test_agent_brain_deployment.py
```

Expected: FAIL because the field, shell metadata, preparing header, and source gate still exist.

- [ ] **Step 3: Remove the backend mechanism**

Apply these exact behavior changes:

```python
# config.py
# Delete Config.agent_brain_collaboration_enabled.
# Delete its environment parsing.
# Delete the validation branch named
# "Agent Brain collaboration requires Agent Brain V2".
```

```python
# main.py
# Delete brain_use_enabled entirely.
# Pass config.agent_brain_enabled to build_conversation_router.
# Stop passing an Agent Brain release flag to build_auth_router.
```

```python
# routes_auth.py
def _shell_response(
    opened: OpenedPublicAsset,
    *,
    csp: str,
    asset_base: str,
) -> Response:
    # Keep identity-mode injection and public asset rewriting.
    # Delete platform-agent-brain-mode injection.

def build_auth_router(
    auth,
    *,
    static_dir: str,
    public_assets: frozenset[str],
    detailed_health,
) -> APIRouter:
    # An authenticated GET / always returns application_shell().
    # Never add X-Platform-Entry-State.
```

Delete `test_live_collaboration_requires_brain_v2`; it tests a removed input rather than a valid product invariant.

- [ ] **Step 4: Run focused backend tests and verify GREEN**

Run the Step 2 command.

Expected: all selected tests PASS with zero failures.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/config.py backend/app/main.py \
  backend/app/control_plane/routes_auth.py \
  backend/tests/test_config.py backend/tests/test_cloud_config.py \
  backend/tests/test_dingtalk_auth_api.py \
  backend/tests/test_agent_brain_deployment.py
git commit -m "fix(brain): remove collaboration availability gate"
```

---

### Task 2: Delete the preparing page and expose real retryable failures

**Files:**
- Delete: `webui/src/pages/BrainPreparingPage.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/pages/BrainPage.tsx`
- Modify: `webui/src/pages/BrainPage.test.tsx`
- Modify: `webui/src/cloudMode.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `ConversationApiError(status: number, detail: unknown)` from `webui/src/conversationApi.ts`.
- Produces: `brainUnavailable(error: unknown): boolean` local helper and retry UI that preserves the existing `ConversationSubmission` idempotency key.

- [ ] **Step 1: Write failing WebUI tests**

Replace the preparation-page test in `webui/src/cloudMode.test.tsx` with a test that adds a legacy disabled meta tag and still requires the real composer:

```tsx
const legacy = document.createElement("meta");
legacy.name = "platform-agent-brain-mode";
legacy.content = "disabled";
document.head.append(legacy);
// Render authenticated App.
expect(container.querySelector("#brain-heading")?.textContent).toBe("Agent 大脑");
expect(container.querySelector("#brain-request")).not.toBeNull();
expect(container.textContent).not.toContain("Agent 大脑正在准备");
```

Add a `BrainPage.test.tsx` case whose retained submission rejects with:

```tsx
new ConversationApiError(503, { detail: "Agent Brain unavailable" })
```

Assert:

```tsx
expect(container.textContent).toContain("Agent 大脑暂不可用");
expect(container.querySelector<HTMLTextAreaElement>("#brain-request")?.value)
  .toBe("找视觉人才");
// Clicking retry reuses the same submission object.
expect(createSubmission).toHaveBeenCalledTimes(1);
expect(send).toHaveBeenCalledTimes(2);
```

- [ ] **Step 2: Run focused WebUI tests and verify RED**

Run:

```bash
cd webui
npm test -- --run src/cloudMode.test.tsx src/pages/BrainPage.test.tsx
```

Expected: FAIL because the legacy meta still selects `BrainPreparingPage` and the failure copy is generic.

- [ ] **Step 3: Implement the minimal WebUI removal**

Make `App.tsx` unconditional for an authenticated Brain route:

```tsx
case "brain": return account
  ? <BrainWorkspacePage account={account} />
  : <PendingPage title="Agent 大脑" description="请启用企业身份后使用。" />;
```

Delete the `BrainPreparingPage` import/file and delete `agentBrainShellEnabled()` from `auth.ts`.

In `BrainPage.tsx`, import `ConversationApiError`, keep the existing retained submission, and distinguish the explicit failure:

```tsx
function brainUnavailable(error: unknown): boolean {
  return error instanceof ConversationApiError
    && error.status === 503
    && typeof error.detail === "object"
    && error.detail !== null
    && "detail" in error.detail
    && error.detail.detail === "Agent Brain unavailable";
}
```

Store `"unavailable" | "generic" | null` instead of a boolean. Render “Agent 大脑暂不可用。请稍后使用同一次请求重试。” for the first state and retain the existing generic network copy for the second state. Both states use the same retry button and retained operation.

Remove only CSS selectors that exclusively target `.brain-preparing`; do not restyle the Brain workspace.

- [ ] **Step 4: Run focused WebUI tests and verify GREEN**

Run the Step 2 command.

Expected: all selected tests PASS with zero failures.

- [ ] **Step 5: Build the WebUI**

Run:

```bash
cd webui
npm run build
! rg -n "Agent 大脑正在准备|顶层调度能力尚未正式启用" dist
```

Expected: build exits 0 and both strings are absent from `dist`.

- [ ] **Step 6: Commit Task 2**

```bash
git add webui/src/App.tsx webui/src/auth.ts webui/src/pages/BrainPage.tsx \
  webui/src/pages/BrainPage.test.tsx webui/src/cloudMode.test.tsx \
  webui/src/styles.css webui/src/pages/BrainPreparingPage.tsx
git commit -m "fix(web): delete agent brain preparing state"
```

---

### Task 3: Remove the dead flag from deploy, rollback, acceptance, and runbooks

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/remote-stage.sh`
- Modify: `deploy/cloud/accept.sh`
- Modify: `deploy/cloud/rollback-dingtalk-production.sh`
- Modify: `docs/runbooks/agent-brain-live-collaboration-release.md`
- Modify: `docs/runbooks/cloud-platform.md`
- Modify: `backend/tests/test_agent_brain_deployment.py`

**Interfaces:**
- Consumes: persisted `PLATFORM_AGENT_BRAIN_ENABLED` and `PLATFORM_AGENT_BRAIN_V2_ENABLED` values.
- Produces: `platform.env` containing only image, cloud auth mode, direct-agent flag, Brain flag, and Brain V2 flag.

- [ ] **Step 1: Write failing deployment governance tests**

Add one repository-wide assertion in `test_agent_brain_deployment.py`:

```python
for path in (
    CLOUD / "compose.yaml",
    CLOUD / "remote-stage.sh",
    CLOUD / "accept.sh",
    CLOUD / "rollback-dingtalk-production.sh",
):
    text = path.read_text(encoding="utf-8")
    assert "PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED" not in text
    assert "brain-preparing" not in text.lower()
```

Update existing exact assertions so `remote_feature` writes and verifies only the two core Brain flags.

- [ ] **Step 2: Run deployment tests and verify RED**

Run:

```bash
backend/.venv/bin/pytest -q \
  backend/tests/test_agent_brain_deployment.py \
  backend/tests/test_execution_worker_deployment.py
```

Expected: FAIL on each remaining Collaboration environment reference and rollback preparing assertion.

- [ ] **Step 3: Delete deployment references**

Make these exact changes:

```yaml
# compose.yaml platform-api environment
# Delete PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED.
```

```bash
# remote-stage.sh
# Delete read/default/validate/export of Collaboration.
# Write platform.env with DIRECT_AGENT, AGENT_BRAIN, and AGENT_BRAIN_V2 only.
```

```bash
# accept.sh
# remote_feature changes only AGENT_BRAIN and AGENT_BRAIN_V2.
# Delete Collaboration inspect assertions.
# A disabled-core rollback still requires authenticated GET / == 200,
# but must not require X-Platform-Entry-State.
```

```bash
# rollback-dingtalk-production.sh
# Delete all Collaboration environment editing.
# Keep safe intake shutdown through the two core flags.
```

Revise both runbooks so they state that the extra gate was removed on 2026-08-27 and that true service failures are explicit. Do not rewrite historical migration facts.

- [ ] **Step 4: Run deployment tests and shell syntax checks**

Run:

```bash
backend/.venv/bin/pytest -q \
  backend/tests/test_agent_brain_deployment.py \
  backend/tests/test_execution_worker_deployment.py
bash -n deploy/cloud/remote-stage.sh
bash -n deploy/cloud/accept.sh
bash -n deploy/cloud/rollback-dingtalk-production.sh
```

Expected: all tests PASS and every `bash -n` exits 0.

- [ ] **Step 5: Commit Task 3**

```bash
git add deploy/cloud/compose.yaml deploy/cloud/remote-stage.sh \
  deploy/cloud/accept.sh deploy/cloud/rollback-dingtalk-production.sh \
  docs/runbooks/agent-brain-live-collaboration-release.md \
  docs/runbooks/cloud-platform.md \
  backend/tests/test_agent_brain_deployment.py
git commit -m "chore(deploy): delete brain collaboration switch"
```

---

### Task 4: Full verification, release, and production acceptance

**Files:**
- Verify only; do not modify source files in this task.

**Interfaces:**
- Consumes: Tasks 1–3 commits.
- Produces: one production release where no runtime or deployment artifact contains the removed switch.

- [ ] **Step 1: Run the full relevant backend suite**

Run:

```bash
backend/.venv/bin/pytest -q \
  backend/tests/test_config.py \
  backend/tests/test_cloud_config.py \
  backend/tests/test_dingtalk_auth_api.py \
  backend/tests/test_agent_brain_deployment.py \
  backend/tests/test_agent_brain_live_acceptance.py \
  backend/tests/test_agent_brain_conversations.py \
  backend/tests/test_execution_worker_deployment.py
```

Expected: PASS with zero failures.

- [ ] **Step 2: Run the complete WebUI suite and build**

Run:

```bash
cd webui
npm test -- --run
npm run build
! rg -n "Agent 大脑正在准备|顶层调度能力尚未正式启用" dist
```

Expected: all Vitest tests PASS, build exits 0, and deleted copy is absent.

- [ ] **Step 3: Run repository governance checks**

Run:

```bash
git diff --check HEAD~3..HEAD
! rg -n "PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED|agent_brain_collaboration_enabled|brain-preparing|Agent 大脑正在准备" \
  backend/app backend/tests webui/src deploy/cloud docs/runbooks
```

Expected: no whitespace errors and no live-code/runbook references.

- [ ] **Step 4: Record production invariants before deploy**

Using read-only SSH checks, record hashes/identities for:

```text
AI ADMIN: MainPID, ActiveEnterTimestampMonotonic, NRestarts
FAE: container ID, image ID, StartedAt, RestartCount
VOC: container ID, image ID, StartedAt, RestartCount
Nginx: nginx -T SHA256
```

Expected: every baseline is non-empty and the three public pages return 200.

- [ ] **Step 5: Push and deploy with the Platform deployment script**

Run:

```bash
git push origin master
deploy/cloud/deploy.sh \
  '/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env'
```

Do not run an Agent Brain rollback action and do not modify Nginx.

Expected: deployment reports healthy Platform API, loopback, Brain Worker, directory, and stream services.

- [ ] **Step 6: Verify production behavior and invariants**

Verify:

```text
platform.env has no PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED
platform-api container environment has no removed variable
GET /login shell has no platform-agent-brain-mode meta
authenticated GET / returns the Agent 大脑 composer, never the preparing page
one real authenticated Brain conversation reaches a durable terminal state
/office/?view=services == 200
https://fae.orbbec.com.cn/ == 200
/agents/voc/workspace == 200 for an authorized session
AI ADMIN, FAE, VOC, and Nginx baselines are unchanged
```

Expected: every assertion passes. If any fails, roll back only the Platform release; do not recreate the deleted gate.

- [ ] **Step 7: Report the verified release**

Do not add a release report file to Git. Report only the release SHA, pass/fail status, and invariant booleans to the user; do not include secrets, cookies, prompts, model output, or production identifiers.
