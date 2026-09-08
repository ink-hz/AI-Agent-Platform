# HR minimum reliable WEB — local engineering acceptance

2026-09-08. Current scope: the approved three-task minimum WEB plan, not the old
18-task migration. Task 1 is locally closed at Platform `324bd80` / MetaBot
`a8b053e`. Task 2 is implemented, locally verified and independently approved.
Task 3 runtime/startup, remaining process matrix, browser and release handoff are
not complete. **No production deployment or real-model quality acceptance.**

## Business evidence

The same authenticated HTTP/PG/independent Worker/signed Relay/MetaBot fixture
completed three Turns: draft JD/JR, explicit Position confirmation and a context
follow-up, then selected resume analysis/interview questions with a PDF. Both
synthetic resumes passed actual public upload, validation and scanning; only the
selected one's archive ID/SHA/order/bytes reached the owned native child, together
with exact confirmed JD/JR. No global Position-material promotion was required.

During actual output PUT failure, the answer and Turn completed while the PDF
remained pending. After killing/restarting the actual file-consuming Worker and
restoring the channel, the owner-authenticated download returned the exact PDF
bytes/SHA. One binding/upload/quota charge, one-year retention, unchanged original
answer, zero web push Deliveries and exactly three total native starts were
asserted. The native/model boundary and byte storage are substitutes; all
authority, grants, processing, identity middleware and business projections are
production code over disposable PostgreSQL.

Parent rerun: `core-chat-v5-recruiting-loop.test.ts` **1 passed, 84.96s**.
Run from the MetaBot worktree with `PLATFORM_V5_TEST_BACKEND` set to this worktree's
backend and `PLATFORM_V5_TEST_PYTHON` set to the root backend venv Python. This
rerun preceded the final scheduling-order adjustment; the final Worker-specific
suite below covers that adjustment. It is not a full `create_app` startup test.

## Fixes retained and review closure

- Original grant credentials and fixed file metadata share frozen-command/Result
  transactions. Recovery neither extends the original grant nor reopens execution.
- Turn-selected resumes use exact owner/conversation/turn bindings. Explicit
  Position tasks keep their existing promotion/scope checks. Both Python and the
  implicit database recording boundary were corrected; canonical replay remains.
- Late artifact begin rechecks database time after grant and artifact locks,
  including idempotent replay. Stale expiry classification cannot permanently
  exclude a subsequently validated ready file from its original fixed intent.
- File sweeps run separately after the main tick releases its execution locks;
  text/native reconciliation does not repeatedly collide with its own file sweep.
- MetaBot computes fixed intents before copying. Partial copy/fsync faults retain
  the original Result manifest. Missing copies may be recreated only from owned
  originals matching the fixed SHA, never from changed bytes or a new model run.
- Invalid file lists produce a fixed private runtime marker before Result. The
  authenticated Result transaction emits one generic system notice; full 128 KiB
  answers remain byte-for-byte intact. Private logs/paths are never published.
- Late PDF cards refresh by Turn, including an older pending Turn after the next
  Turn starts. Refresh also loads the durable system notice; no resubmission.
- Attachment processing claims use the set-returning function in FROM, avoiding
  PostgreSQL's repeated volatile composite evaluation.
- Published intelligence stays bounded/read-only; failed light fragments yield
  partial/unknown context, never online research or heavy full-report loading.

Independent verdicts: Platform Task 2 Approved (`p03a_ownership_review`), selected
resume context Approved (`m04b_recovery_contract`), MetaBot full Task 2 Approved
(`review_unified_metabot`). Zero remaining Critical/Important/Minor findings in
their scopes. Panorama was separately inspected by the parent with 34 passing
targeted tests. Reviews do not substitute for the execution evidence below.

## Final local commands/results

Backend cwd `backend`; Python is
`/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python`.

- `-m pytest tests/test_result_artifact_recovery_races.py tests/test_result_artifact_recovery.py tests/test_turn_result_projection.py tests/test_turn_snapshot.py tests/test_hr_direct_worker.py tests/test_hr_direct_material_transport.py tests/test_hr_task_context.py tests/test_hr_task_context_recovery.py -q`: **87 passed, 40.64s**.
- `-m pytest tests/test_hr_p0_recruiting_loop.py tests/test_hr_position_package_database.py tests/test_hr_candidate_analysis_artifact_migration.py tests/test_attachment_artifacts.py tests/test_hr_panorama_context.py tests/test_attachment_grants.py tests/test_agent_brain_result_delivery.py tests/test_attachment_worker_runtime.py -q`: **92 passed, 12.08s**.
- MetaBot existing Vitest: `core-chat-v5-input`, `core-chat-v5-output`,
  `platform-attachment-transfer`, `core-chat-v5-stream-owner`,
  `core-chat-v5-lifecycle`, `core-chat-v5-http`: **107 passed, 1 existing opt-in
  skipped, 14.31s**. Separate real three-Turn harness was enabled as above.
- MetaBot existing `tsc --noEmit --composite false --incremental false -p
  tsconfig.bridge.json` and scoped ESLint: exit 0.
- Web `npm test -- src/pages/ConversationPage.test.tsx src/conversationApi.test.ts`:
  **70 passed**. HR workspace acceptance/Position suites: **25 passed** in the
  preceding Task 2 checkpoint; no workspace component source changed thereafter.
- Web `npm run build`: exit 0; existing large-chunk warning remains. Scoped new
  Python Ruff and `git diff --check` passed. Existing unrelated lint findings are
  not relabeled clean.

## Remaining Task 3 / activation boundaries

1. Wire and test actual application/CLI startup, not only the real-router fixture.
   MetaBot `CoreChatV5Service.open` is currently explicit test composition, not
   installed into `src/index.ts`; the default runtime must not silently activate.
2. Finish the process failure matrix and owned local browser walkthrough. Keep
   API-first diagnosis; do not use production accounts or real recipients.
3. Close file-retention integration: remove only resolved copies and known-stopped
   owned inputs/native directories, prevent consumers recreating cleaned copies,
   and retain unresolved/unknown-execution bytes. No arbitrary age-based erasure.
4. HR-only deployment dry run, actual migration allocation/compatibility and
   no-double-execution rollback review. Pending SQL is unapplied and unnumbered:
   `hr_turn_attempts.sql`, `hr_v5_readiness.sql`, `hr_direct_dispatch.sql`,
   `hr_web_result_recovery.sql`, `hr_turn_input_context.sql`; ordering must be
   checked against the authorized target before a numbered release.
5. Real-model/production acceptance requires its separately bounded authority.
   No push, merge, deploy, Team/Feishu mutation, model change, Nginx change or
   production migration was performed. Original two dirty 09-07 documents and
   dependency/runtime directories remain outside the task commits.
