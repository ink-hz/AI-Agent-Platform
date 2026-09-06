# HR 招聘情报直接发布实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除重复文本审批门禁，在保留版本、Bundle、磁盘、锁、回滚和验收保护的前提下，将已验收 HR 招聘情报代码与 Bundle 发布到生产。

**Architecture:** 文本授权只存在于运行手册和对应契约测试，因此以最小变更更新这两处；实际发布继续复用现有平台签名 Release 和不可变 Bundle 导入脚本。合并后从干净的 detached release worktree 发布，避免根工作树中用户自有未跟踪文件影响或进入 Release。

**Tech Stack:** Markdown、pytest、Git worktree、Bash、Docker Compose、SSH、PostgreSQL

## Global Constraints

- Owner 明确发出“上线”指令即构成发布授权，不再要求复制 SHA/Bundle ID 两行文本。
- 自动读取并核对完整 40 位 Git SHA 和 Bundle UUID；不得删除 Bundle 验签、磁盘门禁、发布锁、回滚或业务验收。
- 只推送 `master`，不推送特性分支。
- 不提交或删除 `backend/.venv`、`webui/node_modules`、`tmp/` 或根工作树其他用户文件。
- 不修改共享 Nginx 路由，不重启 FAE、VOC、行政、Marketing 或其他独立应用。
- staging 只使用 `/data/staging/orbbec-agent-platform/<deployment_id>/` 对应的精确目录；发布结束后验证清空。

---

### Task 1: Remove the duplicate text approval contract

**Files:**
- Modify: `backend/tests/test_hr_panorama_deployment.py`
- Modify: `docs/runbooks/hr-intelligence-bundle.md`

**Interfaces:**
- Consumes: Owner 的明确“上线”指令。
- Produces: 运行手册中的直接发布授权规则；不改变 `verify_import_bundle()`、`deploy.sh` 或 `import-hr-intelligence.sh` 的技术校验。

- [ ] **Step 1: Write the failing contract test**

将原有 `APPROVE_HR_BUNDLE_ID` 断言替换为：

```python
assert "Owner 明确发出上线指令即构成发布授权" in runbook
assert "APPROVE_RELEASE_SHA" not in runbook
assert "APPROVE_HR_BUNDLE_ID" not in runbook
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_panorama_deployment.py`

Expected: FAIL because the old runbook still requires the two approval variables.

- [ ] **Step 3: Implement the minimal runbook change**

Replace the Owner approval section with the explicit direct authorization rule. State that tooling derives the release SHA from the pushed `master` and the Bundle ID from the verified Bundle directory; retain every technical gate and rollback rule.

- [ ] **Step 4: Run focused verification**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_hr_panorama_deployment.py tests/test_hr_intelligence_architecture_boundary.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_hr_panorama_deployment.py docs/runbooks/hr-intelligence-bundle.md
git commit -m "docs(hr): allow direct owner release"
```

### Task 2: Reverify and integrate the complete feature branch

**Files:**
- No source files beyond Task 1.
- Preserve all unrelated untracked files.

**Interfaces:**
- Consumes: `feat/hr-agent-markdown-intelligence` and Bundle `2b49ecc4-42fe-45ae-80ac-26891f42ac6c`.
- Produces: one local `master` merge commit and one pushed `origin/master` SHA.

- [ ] **Step 1: Reverify the final feature branch**

Run:

```bash
cd backend
./.venv/bin/pytest -q
cd ../webui
npm test -- --run
npm run build
cd ..
git diff --check
```

Expected: backend and frontend pass, build succeeds, and no tracked changes remain.

- [ ] **Step 2: Reverify the immutable Bundle and retrieval gate**

Run:

```bash
cd backend
./.venv/bin/python -m tools.hr_intelligence.cli verify \
  --bundle "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/2b49ecc4-42fe-45ae-80ac-26891f42ac6c" --strict
./.venv/bin/python -m tools.hr_intelligence.cli evaluate-retrieval \
  --bundle "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/2b49ecc4-42fe-45ae-80ac-26891f42ac6c" \
  --cases "$PWD/tools/hr_intelligence/retrieval_eval_cases.v1.json"
```

Expected: 3,437 jobs, Manifest SHA-256 `5d446fe136a3fe2d3d1bb06873f9584e4a357f9546e9f66e686e14950dab98a3`, and 40/40 retrieval acceptance.

- [ ] **Step 3: Merge without disturbing the dirty root worktree**

In `/Users/neo/Developer/work/AI-Agent-Platform`, confirm untracked files do not collide, fetch `origin/master`, merge current `origin/master` into the feature branch if it advanced, rerun affected verification, then run:

```bash
git merge --no-ff feat/hr-agent-markdown-intelligence \
  -m "merge: add HR Agent markdown intelligence"
```

Expected: a merge commit containing the complete feature branch; user-owned untracked files remain unchanged.

- [ ] **Step 4: Push only master**

Run: `git push origin master`

Expected: `origin/master` exactly equals local `master`; the feature branch is not pushed.

### Task 3: Deploy the platform and import the verified Bundle

**Files:**
- Create only a temporary clean detached Git worktree for the pushed release SHA.
- Create production Release/staging artifacts only through existing scripts.

**Interfaces:**
- Consumes: pushed `origin/master`, an existing mode-0600 deploy environment, and the verified local Bundle.
- Produces: current platform Release and current HR Intelligence Bundle in production.

- [ ] **Step 1: Resolve configuration and capture pre-deploy state**

Locate the existing deployment environment without printing secrets. From it, read only the host/key paths needed by the scripts. Capture:

```bash
df -B1 / /data
readlink -f /opt/orbbec-agent-platform/current
du -sh /opt/orbbec-agent-platform/releases/*
docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
```

Expected: root free space at least 25 GB, projected root free space at least 20 GB, and no active Platform deployment lock.

- [ ] **Step 2: Create a clean detached release worktree and publish**

Create a detached worktree at the pushed `master` SHA, ensure `git status --porcelain` is empty and `HEAD == origin/master`, then run its existing `deploy/cloud/deploy.sh <absolute-deploy-env>`.

Expected: `CLOUD_PLATFORM_DEPLOY_OK release=<full-sha> mode=dingtalk`.

- [ ] **Step 3: Import the exact Bundle**

Run from the same release worktree:

```bash
deploy/cloud/import-hr-intelligence.sh \
  "<absolute-deploy-env>" \
  "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/2b49ecc4-42fe-45ae-80ac-26891f42ac6c"
```

Expected: local and remote checksum verification pass, the importer accepts the expected Bundle ID, and the exact staging directory is removed.

- [ ] **Step 4: Run production acceptance and inspect HR behavior**

Verify the public HR page and API return successful HTTP responses, the current database publication identifies Bundle `2b49ecc4-42fe-45ae-80ac-26891f42ac6c`, and an HR task query retrieves non-empty Markdown context with provenance. Confirm FAE and `/office/` remain HTTP 200 and their container identity/start time did not change.

- [ ] **Step 5: Capture the mandatory release report**

Record before/after `df`, Release sizes, current and two rollback versions, archived/deleted versions, empty staging, current/two rollback Docker images, business HTTP checks, root net growth, and whether any unrelated app or shared Nginx changed.

- [ ] **Step 6: Remove only the temporary release worktree**

After all acceptance checks pass, remove the exact detached worktree with `git worktree remove <exact-path>`. Do not delete any repository, Bundle, Release or user-owned untracked file.
