# R1.2 workbench/resources report

## Commits

- `3b381e9 feat(hr): expose exact position resources`
- `ab25921 feat(hr): backfill historical position resources`
- `9bde984 feat(hr): add R1.2 web contracts`
- `dc03d14 feat(hr): add position intelligence panels`
- `78fb0b7 feat(hr): deliver the R1.2 business workspace`

## TDD evidence

RED was recorded before each implementation task:

- Resource API tests initially failed because `app.hr.resource_models` and resource routing did not exist.
- Backfill tests initially failed because `app.hr.resource_backfill` did not exist.
- Web contract tests initially failed because `hrR12Api` and `hr-position-section` routes did not exist.
- Panel tests initially failed because all three panel modules did not exist.
- Workspace acceptance initially failed because quick tasks were not composed into the position workspace.

GREEN commands and exact results:

- `/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_hr_position_resources.py tests/test_hr_position_resource_api.py` — `6 passed`.
- `/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_hr_resource_backfill.py` — `2 passed`.
- `cd webui && npm test -- --run src/hrR12Api.test.ts src/router.test.ts` — `61 passed`; `npm run build` passed.
- `cd webui && npm test -- --run src/workspaces/hr/HrPositionContextPanel.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx src/workspaces/hr/HrPositionResourcesPanel.test.tsx` — `3 passed`.
- `cd webui && npm test -- --run src/workspaces/hr/HrPositionWorkspace.test.tsx src/workspaces/hr/HrR12.acceptance.test.tsx` — `5 passed`; `npm run build` passed.
- Final backend regression: `tests/test_hr_position_resource*.py tests/test_hr_resource_backfill.py tests/test_hr_position_import_cli.py` — `11 passed`.
- Final frontend regression: `src/hrR12Api.test.ts src/workspaces/hr` — `23 passed`; `npm run build` passed.
- `git diff --check` passed.

## Concerns and integration handoff

- This branch deliberately does not mount `build_hr_resource_router`, connect historical discovery to the shared import CLI, or render section routes from `App.tsx`; those are owned by the integration task.
- The R1.2 context/candidate client paths are typed against the frozen interface and await the position/candidate subsystem routers at integration.
- Existing `HrPositionIndex.test.tsx` emits jsdom's `Window.scrollTo` warning during the broad workspace suite; all tests passed and this branch does not alter that unrelated test.
- The isolated worktree lacks `backend/.venv` and `webui/node_modules`; verification used the preserved root interpreter and a temporary symlink to the root frontend dependencies, removed before completion.

## Final status

DONE_WITH_CONCERNS — all assigned resource/backfill and HR React workspace tasks are committed and green; application/router/CLI composition remains intentionally for the integration owner.
