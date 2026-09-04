# HR Position Chat Surface and Upload Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the HR position page chat-first, restore reliable browser attachment uploads, and match FAE's compact answer actions.

**Architecture:** Reuse the existing `HrPositionHeader`, `HrPositionDetailsDrawer`, and `HrPositionTaskMenu` boundaries instead of adding another workspace model. Keep the existing attachment three-step lifecycle, but make the content route validate actual streamed bytes rather than requiring a transport `Content-Length`; preserve the service-layer declared-size check.

**Tech Stack:** React 19, TypeScript, Vitest/jsdom, FastAPI/Starlette, pytest, CSS, lucide-react.

## Global Constraints

- Preserve the 50 MB single-file limit and existing object-store integrity checks.
- Do not modify FAE, other Bots, shared Nginx, authorization, or HR data models.
- Keep the current conversation component mounted while position details open and close.
- Downvote continues to require a reason and permits an optional 1,000-character comment.
- Use TDD: observe each new assertion fail before implementation.

---

### Task 1: Transport-independent attachment content upload

**Files:**
- Modify: `backend/app/attachments/conversation_routes.py`
- Test: `backend/tests/test_conversation_attachment_api.py`

**Interfaces:**
- Consumes: `AttachmentUploadService.write(owner_id, upload_id, body, content_length)`.
- Produces: `PUT /api/v1/attachments/uploads/{upload_id}/content` accepting fixed-length or chunked request bodies and passing the measured body size to the service.

- [ ] **Step 1: Write failing route tests**

Add cases showing that a valid body succeeds without `Content-Length` and with chunked transfer encoding, while an empty body and a body over `MAX_FILE_BYTES` remain rejected.

```python
response = client.request("PUT", path, content=b"payload", headers=headers)
assert response.status_code == 200
assert uploads.calls[-1][-1] == 7
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `python -m pytest backend/tests/test_conversation_attachment_api.py -k upload_content -q`
Expected: the no-length/chunked acceptance assertion fails with 411.

- [ ] **Step 3: Measure the request stream**

Remove the mandatory header/chunked rejection. Accumulate `received`, reject once it exceeds `MAX_FILE_BYTES`, reject zero bytes, and call `write(..., received)` after rewinding the staged file. If a valid numeric header is present, reject only when it exceeds `MAX_FILE_BYTES`; do not use it as the receipt.

- [ ] **Step 4: Run route and service tests**

Run: `python -m pytest backend/tests/test_conversation_attachment_api.py backend/tests/test_attachment_upload_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/attachments/conversation_routes.py backend/tests/test_conversation_attachment_api.py
git commit -m "fix(attachments): validate streamed upload bytes"
```

### Task 2: Stage-specific upload errors and compact composer attachments

**Files:**
- Modify: `webui/src/attachmentApi.ts`
- Modify: `webui/src/components/conversation/AttachmentUploader.tsx`
- Modify: `webui/src/components/conversation/AttachmentUploader.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `AttachmentApiError.status` and `AttachmentApiError.detail`.
- Produces: `attachmentUploadErrorMessage(error, stage)` and a compact uploader embedded inside both new and existing conversation composers.

- [ ] **Step 1: Write failing uploader tests**

Assert that a content-stage 409 renders `文件传输失败，请重试` rather than `Attachment API 409`, and that an empty queue does not render `未选择任何文件` or the long quota disclosure.

- [ ] **Step 2: Run the component test and confirm failure**

Run: `npm test -- --run src/components/conversation/AttachmentUploader.test.tsx`
Expected: FAIL on the new user-facing error assertion.

- [ ] **Step 3: Implement stage error mapping and compact markup**

Track `begin | content | complete | processing` in `process()`, map API/network errors to safe Chinese messages, retain raw detail only in the error object, and render the add-file control plus queue without an empty-state label.

- [ ] **Step 4: Add composer styling**

Place `.conversation-composer-attachments` and `.agent-direct-attachments` inside the composer visual boundary; render upload controls as a transparent toolbar and failed/ready items as compact file chips.

- [ ] **Step 5: Run uploader and composer tests**

Run: `npm test -- --run src/components/conversation/AttachmentUploader.test.tsx src/pages/AgentUsePage.test.tsx src/pages/ConversationPage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add webui/src/attachmentApi.ts webui/src/components/conversation/AttachmentUploader.tsx webui/src/components/conversation/AttachmentUploader.test.tsx webui/src/styles.css
git commit -m "fix(hr): recover attachment upload experience"
```

### Task 3: FAE-style answer actions

**Files:**
- Modify: `webui/package.json`
- Modify: `webui/package-lock.json`
- Modify: `webui/src/components/conversation/MessageActions.tsx`
- Modify: `webui/src/components/conversation/MessageActions.test.tsx`
- Modify: `webui/src/pages/HrWorkspace.acceptance.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Produces: icon-only controls with accessible labels `复制回答`, `有用`, and `不达标`; existing `onFeedback` payloads remain unchanged.

- [ ] **Step 1: Install the same icon library used by FAE**

Run: `npm install lucide-react@^0.562.0`
Expected: package manifest and lockfile contain `lucide-react`.

- [ ] **Step 2: Write failing action tests**

Query actions by `aria-label`, verify SVG icons exist, verify copy changes to `已复制`, and verify `不达标` opens the existing reason/comment form.

- [ ] **Step 3: Run tests and confirm failure**

Run: `npm test -- --run src/components/conversation/MessageActions.test.tsx src/pages/HrWorkspace.acceptance.test.tsx`
Expected: FAIL because the old controls expose visible text.

- [ ] **Step 4: Implement FAE action controls**

Use `Copy`, `Check`, `CircleAlert`, `ThumbsUp`, and `ThumbsDown` from `lucide-react`; use FAE's 28 px transparent icon-button treatment while retaining the existing feedback detail panel and retry action.

- [ ] **Step 5: Run action tests**

Run: `npm test -- --run src/components/conversation/MessageActions.test.tsx src/pages/HrWorkspace.acceptance.test.tsx src/pages/ConversationPage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add webui/package.json webui/package-lock.json webui/src/components/conversation/MessageActions.tsx webui/src/components/conversation/MessageActions.test.tsx webui/src/pages/HrWorkspace.acceptance.test.tsx webui/src/styles.css
git commit -m "feat(hr): align answer actions with FAE"
```

### Task 4: Chat-first HR position page

**Files:**
- Modify: `webui/src/workspaces/hr/HrPositionWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionWorkspace.test.tsx`
- Modify: `webui/src/workspaces/hr/HrR12.acceptance.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `HrPositionHeader`, `HrPositionDetailsDrawer`, `HrPositionTaskMenu`, and the existing `DirectAgentWorkspace` props `header`, `composerTools`, and `layout="focused"`.
- Produces: one persistent conversation workspace with on-demand position details and task controls.

- [ ] **Step 1: Rewrite journey assertions first**

Assert that the page has `.hr-position-bar`, no legacy metrics/tab/task bars, a focused conversation workspace, a `岗位资料` drawer opener, and a `岗位任务` composer tool. Assert opening and closing the drawer leaves the exact textarea node and its value mounted.

- [ ] **Step 2: Run position tests and confirm failure**

Run: `npm test -- --run src/workspaces/hr/HrPositionWorkspace.test.tsx src/workspaces/hr/HrR12.acceptance.test.tsx`
Expected: FAIL because the legacy header, tabs, and taskbar are still rendered.

- [ ] **Step 3: Integrate existing focused components**

Replace the legacy context header/navigation/taskbar with `HrPositionHeader`. Render `DirectAgentWorkspace` once with `layout="focused"`, pass `HrPositionTaskMenu` through `composerTools`, and render `HrPositionDetailsDrawer` as a sibling so opening it never unmounts chat state.

- [ ] **Step 4: Preserve recovery status without a permanent bar**

Pass active task status as a compact thread supplement or conditional status chip; render nothing when there is no task and only show retry UI on actual recovery failure.

- [ ] **Step 5: Apply responsive layout**

Give the position workspace a fixed-height shell under the HR top bar, a compact 64 px header, a wide conversation area, and a right-side desktop drawer/bottom-sheet mobile drawer. Remove obsolete styles for the legacy metrics, section tabs, and quick-task bar.

- [ ] **Step 6: Run HR and full web tests**

Run: `npm test -- --run src/workspaces/hr/HrPositionWorkspace.test.tsx src/workspaces/hr/HrR12.acceptance.test.tsx src/pages/HrWorkspace.acceptance.test.tsx`
Expected: PASS.

Run: `npm test`
Expected: PASS.

Run: `npm run build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add webui/src/workspaces/hr/HrPositionWorkspace.tsx webui/src/workspaces/hr/HrPositionWorkspace.test.tsx webui/src/workspaces/hr/HrR12.acceptance.test.tsx webui/src/styles.css
git commit -m "feat(hr): make position workspace chat first"
```

### Task 5: Integrated verification and release

**Files:**
- Modify only if verification exposes a scoped defect.

**Interfaces:**
- Produces: merged `master`, a Platform-only production release, and a release report satisfying the user's disk discipline.

- [ ] **Step 1: Run backend regression suite**

Run: `python -m pytest backend/tests/test_conversation_attachment_api.py backend/tests/test_attachment_upload_service.py backend/tests/test_cloud_deployment.py -q`
Expected: PASS.

- [ ] **Step 2: Review the complete diff**

Run: `git diff master...HEAD --check && git status --short && git log --oneline master..HEAD`
Expected: no whitespace errors, only scoped files, clean tracked tree.

- [ ] **Step 3: Merge locally and push only master**

Use a `--no-ff` local merge after verifying `master` has not moved. Do not push the feature branch.

- [ ] **Step 4: Enforce deployment disk gates**

Record `df -B1 / /data`, refuse release below the configured thresholds, stage only under `/data/staging/ai-agent-platform/<deployment_id>/`, and use an exact-path cleanup trap.

- [ ] **Step 5: Deploy only AI Agent Platform**

Do not modify shared Nginx, FAE, VOC, Marketing, AI ADMIN, or unrelated containers. Retain current plus two root-disk rollback releases and current plus two service images.

- [ ] **Step 6: Production acceptance**

Verify the HR page HTTP response, upload a small `.md` file through the authenticated UI when a browser session is available, confirm attachment ready/download, and confirm answer actions plus drawer behavior. Record staging cleanup, release sizes, image retention, and before/after disk state.
