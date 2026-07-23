# Cross-channel Sender Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a safe `Name · Department` identity on MetaBot Session rows and questions, using Feishu for the observed name and DingTalk as the authoritative department directory.

**Architecture:** MetaBot resolves the sender name from the receiving chat without delaying message delivery, then writes an idempotent `identity_observed` event to the PostgreSQL flywheel. Platform joins those protected identity rows into nullable presentation fields. AI ADMIN exports a daily, minimal DingTalk directory snapshot; Platform accepts only exact, unique, active name matches and never returns provider identifiers.

**Tech Stack:** TypeScript 5.9, Feishu Node SDK, PostgreSQL 16/PLpgSQL, Python 3.11/FastAPI/Pydantic, React 19/Vitest, DingTalk OpenAPI.

## Global Constraints

- Never expose Feishu `open_id`, Feishu `union_id`, or DingTalk staff IDs through Platform APIs or UI.
- Identity lookup and persistence must never delay, reject, or alter an Agent response.
- Use exact unique-name matching only; never transliterate, fuzzy-match, or guess a department.
- Render `Feishu User` when no name is known and `Name · Department unavailable` when only the name is known.
- Keep FAE and ADMIN Session behavior backward compatible.
- Preserve unrelated dirty-worktree changes in all repositories.

---

### Task 1: Observe Feishu Sender Names Without Blocking Delivery

**Files:**
- Modify: `/Users/neo/Developer/work/metabot-dev/src/feishu/message-sender.ts`
- Modify: `/Users/neo/Developer/work/metabot-dev/src/feishu/event-handler.ts`
- Modify: `/Users/neo/Developer/work/metabot-dev/tests/flywheel-event-handler.test.ts`
- Create: `/Users/neo/Developer/work/metabot-dev/tests/feishu-sender-identity.test.ts`

**Interfaces:**
- Consumes: `chatId`, sender `open_id`, and the receiving Bot's Feishu client.
- Produces: `MessageSender.getChatMemberDisplayName(chatId, openId): Promise<string | undefined>` and a background identity observation callback.

- [ ] **Step 1: Write failing tests** for exact member selection, pagination, 24-hour cache, lookup failure, and proof that `onMessage` runs before the lookup promise resolves.
- [ ] **Step 2: Run RED** with `npx vitest run tests/feishu-sender-identity.test.ts tests/flywheel-event-handler.test.ts`.
- [ ] **Step 3: Implement the resolver** with `client.im.v1.chatMembers.getWithIterator({ params: { member_id_type: 'open_id', page_size: 100 }, path: { chat_id: chatId } })`, exact `member_id === openId`, trimmed `name`, a per-instance 24-hour cache, and sanitized failure logging.
- [ ] **Step 4: Start enrichment after normal delivery** using `void resolver(...).then(...)`; never await it in the inbound handler and never log name or IDs.
- [ ] **Step 5: Run GREEN** with the two focused test files and `npm run build:bridge`.
- [ ] **Step 6: Commit** only the four listed files with `feat: observe Feishu sender names`.

---

### Task 2: Persist Idempotent Identity Observations in PostgreSQL

**Files:**
- Modify: `/Users/neo/Developer/work/metabot-dev/src/flywheel/envelope.ts`
- Modify: `/Users/neo/Developer/work/metabot-dev/src/flywheel/index.ts`
- Modify: `/Users/neo/Developer/work/metabot-dev/tests/flywheel-envelope.test.ts`
- Modify: `/Users/neo/Developer/work/metabot-dev/tests/flywheel-recorder.test.ts`
- Create: `/Users/neo/Developer/work/Orbbec-Agent-Team/flywheel/migrations/012_sender_identity_observation.sql`

**Interfaces:**
- Consumes: the existing `RecordEventInput` with sender identifiers and `display_name`.
- Produces: `FlywheelRecorder.recordIdentityObserved(input)` and event type `identity_observed`.

- [ ] **Step 1: Write failing recorder tests** requiring an `identity_observed` envelope with an empty payload and no message content.
- [ ] **Step 2: Run RED** with `npx vitest run tests/flywheel-envelope.test.ts tests/flywheel-recorder.test.ts tests/flywheel-event-handler.test.ts`.
- [ ] **Step 3: Extend the typed recorder contract** and route the event through the existing redactor and bounded queue.
- [ ] **Step 4: Add migration `012`** that replaces `flywheel_api.ingest_event`, accepts `identity_observed`, requires an existing Feishu external identity, updates only non-empty `display_name` and safe attributes on both union/open rows, preserves `department`, and inserts an idempotent trace event without inserting a message.
- [ ] **Step 5: Apply migration twice** with the owner Keychain URL, then run a synthetic SQL transaction proving duplicate observations do not create identities or messages.
- [ ] **Step 6: Commit** MetaBot and Agent-Team separately.

---

### Task 3: Add Safe Sender Fields to Platform's Canonical Read Model

**Files:**
- Create: `/Users/neo/Developer/work/AI-Agent-Platform/backend/migrations/002_sender_identity_views.sql`
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/backend/app/observability/models.py`
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/backend/app/observability/repository.py`
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/backend/tests/test_observability_repository.py`

**Interfaces:**
- Produces nullable Turn fields `sender_name`, `sender_department`, `sender_identity_status`; Session fields `participant_count`, `primary_sender_name`, `primary_sender_department`, `sender_identity_status`.

- [ ] **Step 1: Write failing repository mapping tests** for resolved, name-only, unavailable, direct, and deterministic latest group participant cases.
- [ ] **Step 2: Run RED** with `.venv/bin/pytest tests/test_observability_repository.py -q`.
- [ ] **Step 3: Create replacement views** that choose the best non-empty Feishu identity per `sender_user_id`, count distinct participants, and never project provider subjects.
- [ ] **Step 4: Extend Pydantic models/mappers** with nullable presentation fields and status literals only.
- [ ] **Step 5: Apply the migration twice**, run backend tests, and query `information_schema` to prove raw ID column names are absent from `platform_read.sessions` and `platform_read.turns`.
- [ ] **Step 6: Commit** the migration, models, repository, and tests.

---

### Task 4: Render Identity on Session Surfaces

**Files:**
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/webui/src/api.ts`
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/webui/src/pages/SessionsPage.tsx`
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/webui/src/pages/SessionDetailPage.tsx`
- Modify: `/Users/neo/Developer/work/AI-Agent-Platform/webui/src/styles.css`
- Modify: matching Vitest files under `/Users/neo/Developer/work/AI-Agent-Platform/webui/src/`

**Interfaces:**
- Consumes: only the safe presentation fields from Task 3.
- Produces: direct `Name · Department`, group `Name · Department + N people`, and per-question identity labels.

- [ ] **Step 1: Write failing UI tests** for resolved, name-only, unavailable, group, and per-question rendering; assert raw identifier keys and sample values never render.
- [ ] **Step 2: Run RED** with `npm test`.
- [ ] **Step 3: Add one formatter** returning `Feishu User`, `Name · Department unavailable`, or `Name · Department`; reuse it across all Session surfaces.
- [ ] **Step 4: Add compact identity styling** that does not reduce question/answer content width on narrow layouts.
- [ ] **Step 5: Run GREEN** with `npm test && npm run build`.
- [ ] **Step 6: Commit** the frontend files.

---

### Task 5: Synchronize the Minimal DingTalk Directory Daily

**Files:**
- Create: `/Users/neo/Developer/work/AI-ADMIN-Agent/src/integrations/dingtalk_directory.py`
- Create: `/Users/neo/Developer/work/AI-ADMIN-Agent/scripts/sync_dingtalk_directory.py`
- Create: `/Users/neo/Developer/work/AI-ADMIN-Agent/tests/unit/test_dingtalk_directory.py`
- Extend: `/Users/neo/Developer/work/AI-Agent-Platform/backend/app/sync_remote/` importer/config/models and tests
- Create: `/Users/neo/Developer/work/AI-Agent-Platform/backend/migrations/003_dingtalk_directory.sql`

**Interfaces:**
- Produces rows containing only `staff_id`, `display_name`, `departments`, `active`, `source_updated_at`, and `source_synced_at`.
- Produces exact unique active-name matches into protected identity attributes; no provider IDs enter public views.

- [ ] **Step 1: Write failing ADMIN tests** proving pagination, minimal-field projection, idempotency, and `restricted` on permission denial.
- [ ] **Step 2: Implement the directory client and snapshot writer** using existing DingTalk credentials, with no phone, email, avatar, title, or unrelated fields.
- [ ] **Step 3: Write failing Platform importer/matcher tests** for one match, zero matches, duplicate names, inactive employees, and Unicode/whitespace normalization without transliteration.
- [ ] **Step 4: Implement protected snapshot tables and matching** with `match_method=exact_unique_name` and source timestamps.
- [ ] **Step 5: Add a daily production timer** only after a read-only permission probe succeeds; if permission is denied, deploy code disabled and report `restricted` without changing Sessions.
- [ ] **Step 6: Commit** AI ADMIN and Platform separately.

---

### Task 6: Deploy and Verify End to End

**Files:**
- Modify only versioned deployment files required by the preceding tasks.

**Interfaces:**
- Produces a live Platform response containing presentation identity only.

- [ ] **Step 1: Run full suites**: MetaBot `npm run test:bridge && npm run build:bridge`; Platform backend `.venv/bin/pytest -q`; WebUI `npm test && npm run build`; ADMIN focused and full unit suites.
- [ ] **Step 2: Back up and deploy MetaBot sources**, restart only the selected Bot instance used for a real verification message, and verify message delivery is unaffected.
- [ ] **Step 3: Apply Platform migrations and restart `com.orbbec.ai-agent-platform`**.
- [ ] **Step 4: Verify API privacy** by recursively scanning Session JSON keys/values for `open_id`, `union_id`, `staff_id`, and known provider ID prefixes.
- [ ] **Step 5: Verify product behavior**: a resolvable Feishu sender shows a name; after an exact unique DingTalk match it shows `Name · Department`; unresolved identities show the approved fallback.
- [ ] **Step 6: Push all verified commits** to each repository's `origin/master` while preserving unrelated local changes.
