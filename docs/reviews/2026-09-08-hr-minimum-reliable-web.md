# HR minimum reliable WEB — local engineering acceptance

2026-09-08. Current scope: the approved three-task minimum WEB plan, not the old
18-task migration. Task 1 is locally closed at Platform `324bd80` / MetaBot
`a8b053e`. Task 2 is implemented, locally verified and independently approved.
Task 3 local process and snapshot-UI checks are recorded below; actual MetaBot
startup shutdown review is being finalized. **No production deployment or
real-model quality acceptance.**

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

## Task 3 local engineering evidence

- MetaBot `7f38269`: exact complete/late-begin acknowledgement plus the original
  native stop proof permits deletion of matching redundant file bytes. Retained
  receipts prevent recreation after restart. Modified/unlisted files and unknown
  execution data remain untouched. Independent MetaBot review Approved.
- Input/output/actual parent-death suites: **38 passed, 18.50s**; scoped ESLint
  and TypeScript no-emit checks exit 0. This includes killing a real parent while
  its registered child survives: occupancy and no-replay hold until exact stop.
  It is a runtime-parent fixture, not a killed production MetaBot instance.
- Enabled `core-chat-v5-web-loop.test.ts` and
  `core-chat-v5-recruiting-loop.test.ts`, serial: **2 passed, 182.28s**. Actual
  API downtime during execution, independent DirectWorker death, lost acceptance
  and PDF transfer failure preserve the original execution/result. Four native
  starts for the four web failure cases; three for the recruiting business loop.
- Backend request-budget/recovery/readiness/web-HTTP suites: **30 passed,
  1 opt-in skipped, 14.80s**. The opt-in process test was separately enabled in
  the preceding MetaBot harness. The three-window shared-quota test preserves
  legacy cadence, readiness and finite result-backlog draining.
- `tests/test_hr_web_app_startup.py`: **1 passed, 2.49s**. Real `create_app`,
  disposable PG identity/session, HR context, grants, Result consumer, snapshot
  route and Position projector are assembled together. DirectWorker is created
  only through the separate factory; absent runtime readiness cannot run legacy
  execution or manufacture an answer. This configuration test injects validated
  settings and substitutes external login/object storage, not database authority.
- Web affected suites: **102 passed, 1.65s** across conversation API/page/progress
  and four HR workflow suites. Existing Node localStorage warning remains.
  The prior Task 2 production build succeeded; UI source has not changed since.
- Native bridge TypeScript build into a uniquely owned temporary directory and
  import of its compiled startup module both succeeded. The 5,164 KiB temporary
  build was moved to Trash for recovery; existing `dist`, dependencies and
  application data were not overwritten. Final bounded-shutdown changes have
  separate startup candidate tests/typecheck.

### One local snapshot-based browser walkthrough

The actual app above was exposed on owned loopback sockets, with its disposable
test session forwarded by the test-only preview helper. No production browser
session, credential, recipient or external model was used.

Verified: home composer 904 × 366 pixels at a 1728-pixel viewport; Shift+Enter
inserts a newline; Enter submits and opens the newly created conversation; the
520-pixel materials drawer is anchored to the right; Position navigation returns
to the same conversation ID; refresh retains one original user message.

This is **not** browser login/Cookie/CSRF, live SSE or real-model answer-quality
acceptance. Those authentication and streaming protocols have separate API and
component evidence. The initial preview buffered SSE in TestClient and its
teardown needed interruption of the exact owned pytest process; that invocation
is not counted as passing. The helper now explicitly rejects `/events` for this
snapshot-only use. An actual HTTP probe returned 503 promptly, and the fixed
preview exited normally after release: **1 passed, 52.91s**. All three owned
preview-control directories and the created browser tab were cleaned up.

### Local release-boundary check (not a deployment)

`bash -n deploy/cloud/deploy.sh deploy/cloud/remote-stage.sh` passed. The tracked
`backend`, `webui`, and `deploy` candidate trees contain 1,227 files / 15,112,678
bytes at Platform `e3ccf81`; no forbidden data/upload/log/index/knowledge/venv/
node_modules/database paths were found. This is source inventory, **not** an
archive/image-size estimate or a production disk-growth claim.

The existing shared Platform remote-stage script is not an HR-only activation
command, and current Compose does not define the new independent DirectWorker.
Neither was executed. A later authorized release must explicitly add that HR
worker process, retain existing attachment processing and Position projection,
and retain the signed Relay hop. Do not launch another Brain or run an attachment
retention/erasure CLI merely to obtain processing.

Pending SQL dependency order tested locally: `hr_turn_attempts.sql` →
`hr_v5_readiness.sql` → `hr_direct_dispatch.sql` →
`hr_web_result_recovery.sql` → `hr_turn_input_context.sql`. These follow the
existing numbered baseline, including the branch's 088 dependency; allocate
production numbers only from the authorized target's actual inventory.

Rollback is not changing an environment flag and reopening legacy execution.
Stop new intake, preserve pinned ownership and original launch identity, and
reconcile in-flight/uncertain attempts before switching. Do not roll back to a
build that lacks ownership exclusion. Do not drop additive ledgers or revive a
model invocation to repair a delivery.

Production prerequisites remain unchanged: `/data` for persistent growth and
deployment-ID-scoped staging; before/after `df -B1 / /data`; 25 GB preflight and
20 GB projected free-space gates; at most 75% root usage; current plus two rollback
releases/images; scoped archive retention and trap cleanup. Actual current and
rollback versions, image IDs, staging/image sizes, HTTP business acceptance and
disk deltas are **not measured**, because no production release was authorized
or performed. No shared Nginx, Office, Feishu, Team or other Bot was changed.

## Remaining activation boundaries

1. Finish the bounded startup-shutdown candidate review recorded at the top;
   this is the remaining local code checkpoint, not another architecture task.
2. Authorized activation must explicitly provision the independent DirectWorker
   and enable both cloud `PLATFORM_HR_WEB_WORKER_ENABLED=1` and local signed
   Relay `PLATFORM_WORKER_V5_ENABLED=1`. MetaBot's separate opt-in is
   `METABOT_HR_WEB_V5_ENABLED=1`, with `METABOT_HR_WEB_V5_CALLBACK_ORIGIN`,
   `METABOT_PLATFORM_ORIGIN`, and separate private
   `METABOT_HR_WEB_V5_INPUT_ROOT` / `METABOT_HR_WEB_V5_OUTPUT_ROOT` directories.
   Existing runtime PG/bridge credential files, HR model and Feishu configuration
   remain authoritative. Team's environment allowlist needs its own authorized
   configuration update; these names must not silently activate another Bot.
3. Input/native files without verifiable original ownership or stop evidence
   remain preserved. No arbitrary age-based erasure or guessed orphan deletion;
   record their disk usage under the application's `/data` inventory at activation.
4. Actual target migration allocation, HR-only release composition, compatible
   rollback versions and disk/image budgets remain a separate authorized release
   check. Pending SQL is unapplied and unnumbered; no production claim is implied
   by the local ordering and static script inspection above.
5. Real-model/production acceptance requires separately bounded authority. No
   push, merge, deploy, business sending or production migration was performed.
   Original two dirty 09-07 documents and dependency/runtime directories remain
   outside the task commits.
