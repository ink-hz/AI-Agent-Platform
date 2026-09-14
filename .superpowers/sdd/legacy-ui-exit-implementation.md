# Legacy HR UI exit implementation

Implemented current-only HrWorkspacePage host; removed HR legacy conversation/report route unions, parsers, render cases and path generators (including shared HR direct-conversation generator). Old paths resolve not-found. Independent positions/workflow and current intelligence source/research readers remain.

Position directory now reads only real paginated positions into one compact column, retaining query/status filtering and workflow links. Removed draft reads, proposals, confirm/merge/dismiss and start-conversation actions. Candidate stage explicitly launches existing material/work panels via the owner/position-bound in-memory launch; no submission or reference conversion. Main workspace keeps server budget, current selection and auth handling. Top navigation contains only conversation, positions, intelligence; methods remain in the current workspace. Removed intelligence archive branch and archive link.

Validation:
- Red: `npm --prefix webui test -- src/legacyHrRoutes.test.ts src/workspaces/hr/hrCloudLaunch.test.ts src/workspaces/hr/HrPositionIndex.test.tsx` failed for 3 accepted legacy routes, missing panel launch and old draft API read. Same command green: 7 assertions.
- Focused route/current host/workflow/panorama/shell/auth/title tests: 256 assertions passed; one obsolete method-nav assertion updated to check current method section.
- `npm --prefix webui test`: 120 files / 1074 assertions passed; 2 retired-UI assertions remained in styles.test and HrP0Combined acceptance (reported to root to remove alongside its audited deletion work). Log `/tmp/hr-legacy-full-frontend.log`.
- Final `npm --prefix webui test -- src/workspaces/hr/HrPositionWorkflow.test.tsx src/workspaces/hr/HrLoopWorkspace.test.tsx src/workspaces/hr/HrWorkspacePage.test.tsx`: 3 files / 51 assertions passed, including both stage buttons and actual current panels under StrictMode, without submit/append.
- `npm --prefix webui test -- src/platform/workspaces.test.ts`: 13 passed after removing HR legacy direct path generation.
- `npm --prefix webui run build`: passed; existing >500 kB chunk warning. `git diff --check`: passed.

No backend/business data mutation, migration, production calls or deployment. Browser acceptance and root-owned API/test deletion follow-up are outside this subtask's verification. React jsdom logs the existing unimplemented scrollTo warning around navigation; these do not represent browser acceptance.
