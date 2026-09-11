# B2b HR Loop interface implementation report

Date: 2026-09-10. Worktree: `.worktrees/hr-cloud-loop-a1`. Scope: webui only; canonical architecture/workflow documents, backend, real-model work and browser acceptance remain root-owned.

## Delivered

- Independent opt-in `/hr/agent`, optional validated `position`/`work` UUID context; existing HR default/direct routes retained. Discoverable Hannah trial navigation and per-position entry. AppShell, title, access reporting and login return allowlist integrated. Login resume accepts only unique validated work/position IDs, rejects unknown/repeated/unsafe parameters.
- Real cookie/CSRF/platformPath API client; server-provided budget profile/limits. No model calls, fake product services or browser localStorage private drafts.
- Free text starts without a position; existing HrPositionPicker and real attachment begin/content/complete client reused. File inspection and parsing poll independently of worker. PDF/DOCX parse uses retained mutation identity; full/partial/failed/rejected/deleted/expired states clearly distinguished. Coverage notes visible, partial/scanned content never called complete.
- Exact published methods read before adding refs; existing safe Markdown renderer displays full uses/boundaries/sources. Independent method/thread selection generations stop late earlier choices replacing later choices.
- Cursor-paged threads, works, messages/events and result lists. Work resume reloads server-checked input objects/references/text; continuation serializes only the AppendInput contract. Poll batches never overlap within a selection. Each poll rereads visible history from cursor 0 through all pages to recheck older-message revocations, then replaces the snapshot. Account/route remount and selection/revocation epochs discard stale outstanding responses. Failed reads clear private content; retry resets restoration revision and restores same-revision refs correctly.
- SavedResult body is reused from thread and position queries; exact revision download uses authenticated Blob and revokes object URL. User-selected position linking calls real links endpoint. State, finalizing phase, tool-error distinctions, recovery, reading gaps and pending questions shown without raw tool arguments.
- Proposal checkboxes start empty; confirmation sends only selected changes, exact proposal and its unchanged baseline revision. Conflict shows reread/revised-proposal affordances. Current standards shown separately. Remove/replace shows original formal text only when fetched current standard exactly matches proposal base; otherwise those destructive checkboxes are disabled with request-revision guidance. This avoids inventing old text where no historical-standard HTTP endpoint exists.
- Budget pause shows measured counters/quality and current limits, requires explicit nonzero additional quantities plus continuation reason. Budget revision and key frozen across retry even if another client updates the work. No automatic extension/cancellation; stale-directory/busy write controls enforced.

## Verification

- TDD: observed initial API pagination / component behavior / route / position entry failures before implementation; later reproduced and fixed new-work object leakage, delayed success after revocation, method selection race, exact destructive standard display, budget retry rebasing, terminal upload labeling and login return rejection.
- Targeted API/React/router regression: 9 files, 118 passing tests. Command from webui: `npm test -- src/hrLoopApi.test.ts src/workspaces/hr/HrLoopWorkspace.test.tsx src/workspaces/hr/HrWorkspaceShell.test.tsx src/router.test.ts src/workspaces/hr/HrPositionIndex.test.tsx src/documentTitle.test.tsx src/accessEventReporter.test.tsx src/AppShell.brain.test.tsx src/App.hrPositionSection.test.tsx`.
- Refined same-revision recovery regression rerun: 17/17 workspace tests passed, including failure after content had already been restored, reload and subsequent append retaining exact refs.
- Auth regression after login fix: `npm test -- src/auth.test.ts`: 100/100 passed. Aggregate relevant files cover 218 tests (118 plus 100).
- Latest `npm run build`: exit 0; TypeScript and Vite production bundle passed. Existing large chunk warning remains; no dependency manifest changes.
- `git diff --check` passed. Existing HrPositionIndex test emits jsdom `Window.scrollTo` unsupported warning; tests pass.
- Independent static review completed by `review_b2b`; two P1 and three P2 findings handled above. No unresolved review finding intentionally deferred.

## Boundaries / remaining acceptance

These are component tests with mocked HR API/attachment boundaries plus HTTP-client fetch contract tests. They are not real HTTP server, real model or business-quality acceptance. No production access, business message or model call made by this task. Root owns actual API/DB and real-process validation, full model journey, final desktop/mobile browser upload/scroll/layout checks and production decisions.

The backend unauthenticated shell/login routing for `/hr/agent` remains root-owned; frontend login return allowlist is ready. Existing standard API exposes current standard only, so a destructive proposal against an unavailable older base requires a revised proposal before selection. Network retry keys are held in memory for current intent; browser reload restores persisted work via its URL, not an unsent private draft.

## Final-review follow-up (2026-09-10)

- Structured `result.basis` now renders a separate Chinese “基准性质” section, independent of model-written body. Confirmed-standard and official-original sources describe the saved exact-version basis; user-temporary basis shows the originating accepted input number and remains explicitly unconfirmed. Raw IDs/revisions are not displayed.
- Security/revocation errors now additionally remove private thread/history navigation, selected position/name, draft, methods, notice, continuation reason/budget and in-memory mutation intents. This reset occurs in the security-error path only; ordinary work-selection clearing behavior is unchanged. Delayed successful reads remain blocked by the existing epoch barrier.
- Real material references use kind `material`; `:text` and `:original` receive body/original labels. A user can explicitly add the fetched current standard's exact reference with “带此标准讨论”.
- Method preview omits leading YAML frontmatter as presentation only. Relative source-file links are readable plain text; public HTTP(S) links remain safe links. Source text, exact reference and submitted identity remain unchanged. Implemented in dedicated `HrLoopMethodPreview.tsx`, leaving the shared Markdown renderer unchanged.
- Observed seven targeted failures before implementing these fixes. Verified temporary/confirmed/official basis without prose labels, complete private-view reset for both 403 and 410 plus delayed responses, method preview/source-link behavior, real material labeling and exact-standard addition.
- Final combined command (the original 9 files plus `src/auth.test.ts`) passed **10 files / 225 tests**, including **24 workspace tests**. Latest `npm run build` exit 0; `git diff --check` clean. Existing jsdom scrollTo and Vite chunk-size warnings remain unchanged. No backend edits, model calls, production actions or browser interactions in this follow-up; root performs final browser screenshots.

## Documentation recheck (2026-09-11)

- Re-ran the exact final combined command, with this complete 10-file list: `npm test -- src/hrLoopApi.test.ts src/workspaces/hr/HrLoopWorkspace.test.tsx src/workspaces/hr/HrWorkspaceShell.test.tsx src/router.test.ts src/workspaces/hr/HrPositionIndex.test.tsx src/documentTitle.test.tsx src/accessEventReporter.test.tsx src/AppShell.brain.test.tsx src/App.hrPositionSection.test.tsx src/auth.test.ts`. Result: **10 files / 225 tests passed**, including **24 workspace tests**.
- Ran `npm test` once: **126 files / 1,145 tests passed; 3 tests failed**, all in the pre-existing `src/styles.test.ts`: `never renders visible text below the approved minimum` (`11 < 11.5`), `renders HR as a calm full-height recruiting workspace instead of a card dashboard` (expects `background: #eef1f4`), and `keeps the position conversation primary with on-demand responsive controls` (expects literal `248px` grid columns).
- No CSS or visual-contract test was changed. `webui/src/styles.css` hashes to `ceeb217b73162287e638ebc33560cf67901c4fb7`, exactly the `ac965b9` documentation-review baseline blob. The same jsdom `Window.scrollTo` and Vite chunk-size warnings remain warnings.

## Mobile scroll browser follow-up

Root browser found the shared `.hr-workspace-body { overflow:hidden }` prevented wheel scrolling through the document-style loop on 390×844. Added a loop-only direct-child selector `.hr-workspace-body:has(> .hr-loop)` with vertical auto overflow, hidden horizontal overflow and contained vertical overscroll. Existing chat selectors and behavior unchanged. `npm --prefix webui run build` passed and `git diff --check` passed. No mirrored CSS test added for this reversible scoped change; root verifies actual upward/downward scrolling in browser after rebuilding.
