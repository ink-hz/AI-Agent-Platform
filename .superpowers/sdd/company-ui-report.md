# Company UI implementation report

## Scope

Implemented Task 2 of `2026-09-08-hr-intelligence-company-consumption.md`: the runtime-validated company API client, latest company/topic reading workspace, lazy paginated jobs, explicit reference actions, HR shell label, focused styles, and affected tests. Legacy `insightVersionId` remains accepted but does not select historical content.

## TDD evidence

- RED: `src/hrCompanyIntelligenceApi.test.ts` initially failed because the new parser/client module did not exist. Follow-up RED runs proved platform-prefix/cache behavior and rejection of non-numeric metric buckets.
- RED: replacement `HrPanoramaWorkspace.test.tsx` initially failed all five new company/topic behaviors against the legacy report/history workspace. Follow-up RED tests reproduced cross-unit duplicate fact ID contamination and missing `popstate` synchronization.
- RED: `HrWorkspaceShell.test.tsx` failed while the navigation still read `全景分析`.
- GREEN: focused component/API/acceptance run completed with 20 passing tests across five files.

## Validation

- Runtime parser directly consumes the backend real-bundle-derived fixtures: all company summaries, full Insta360 detail including official non-job evidence, missing-metrics Scantech detail, and an Insta360 job page.
- Component/API regression: `npm test -- --run src/hrCompanyIntelligenceApi.test.ts src/workspaces/hr/HrPanoramaWorkspace.test.tsx src/workspaces/hr/HrWorkspaceShell.test.tsx src/workspaces/hr/HrPanorama.acceptance.test.tsx src/workspaces/hr/HrP0Combined.acceptance.test.tsx` — 5 files, 20 tests passed.
- Production build: `npm run build` — TypeScript and Vite build passed. Vite retained its existing large-chunk advisory.
- Browser acceptance was not run by this task agent; the integration owner prepared the final browser pass.
- No production calls, model calls, deployment, or business-message sends were performed.

## Review fixes

- Evidence lookup uses `(unit_id, fact_id)` identity, preventing local IDs from crossing units.
- Company detail requests are pinned to the directory `bundle_id`, abort stale requests, and follow browser Back/Forward state.
- Source URLs accept only HTTP(S); metric buckets require numeric counts; missing metrics remain visibly unknown rather than zero.
- Selected claim, company, and job-page reference text is preserved in full. Aggregate budget enforcement stays in the shared reference formatter.
- Topic copy states the current business limitation without exposing implementation rules.
