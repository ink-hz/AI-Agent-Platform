# Task 3 platform bridge report

Date: 2026-10-27. Worktree: `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/manufacturing-workbench-20261027`.
Branch: `feat/manufacturing-workbench-20261027`. Base: `e5e07e40`.
Consumes manufacturing Task 1+2 accepted source `0b3698d` via the configured versioned source root. No manufacturing repository edits, merge, deployment or browser automation were performed in this scope. Canonical platform README is unchanged.

## Implementation

- Exported `app.manufacturing.build_manufacturing_router()` with no required arguments. Reads only `PLATFORM_MANUFACTURING_ROOT`, `PLATFORM_MANUFACTURING_DATABASE_PATH`, `PLATFORM_MANUFACTURING_RELEASE` for this feature. Expected production values remain `/opt/manufacturing`, `/app/data/manufacturing/workbench.sqlite3`, and the immutable manufacturing release SHA.
- Explicit `importlib` loading from `<root>/src/manufacturing_platform` under an isolated module name; no global PYTHONPATH or sys.path changes. Calls accepted `build_router(database_path=..., assets_dir=<root>/webui/dist, authorize=..., release=...)`. Module imports/build errors, missing assets/package or missing/relative configuration fall back to an unavailable router without preventing platform startup.
- Trusted callback takes only `request.state.auth_context` as `AuthContext`; owner/admin allowed, missing identity401, member/viewer403. Actor ID and name both use trusted stable internal UUID because AuthContext has no display name. Browser actor headers have no authority. Hard-stale reads remain allowed and mutations503.
- Central `AuthorizationService` has exact method/route tuples for access, health, catalog, assets, line, stage read/write, task create/update and native shell paths. No unrelated scope rules are broadened. Existing global Origin/CSRF checks remain unchanged.
- Known unavailable APIs return safe503, with authenticated access returning `{allowed:true,available:false,can_edit:false}`. Private namespace fallback prevents unknown API paths/methods from falling into the generic SPA even when platform identity mode is disabled. Actual router callback still requires identity in that mode.
- API namespace, assets and errors get `Cache-Control: private, no-store`. Assets remain outside the public Vite manifest. Shell-only public recognition and login-return validation add only `/manufacturing` and `/manufacturing/`; arbitrary children/query paths remain invalid. Prefixed shell CSP additionally permits the private manufacturing asset namespace for native SPA entry.
- Native page stays in existing AppShell/sidebar/header. Access probe precedes asset loading and mount. Prefix-aware API root, account CSRF, `account.hard_stale_read_only || !access.can_edit`, auth callback and dirty callback are passed using exact accepted library option names. Dedicated DOM root owns `{unmount(),isDirty()}` lifetime.
- Script promises cache captured library implementations by release asset URL, rather than later reading a mutable window global. Stylesheets load inactive; only the current live release stylesheet is active. Route retirement, StrictMode, errors and auth denial retire guard/callback/style ownership. Shared leave guard protects SPA and popstate navigation; library owns document beforeunload protection.
- Sidebar entry appears only for owner/admin plus successful `{allowed:true,available:true}` access probe. Business inner views remain library state; no extra app navigation or arbitrary route catch-all is introduced.

## RED / GREEN evidence

1. Backend feature-first suite: `17 failed,1 skipped` for absent bridge module and absent manufacturing login-return support. Implemented small adapters, then `17 passed,1 skipped`.
2. Frontend wrapper suite initially failed resolving the missing ManufacturingPage; sidebar RED asserted missing 大制造 link. Initial wrapper/sidebar GREEN:10 tests.
3. Additional preview CSP regression observed RED (private API asset namespace absent) then GREEN after precise source extension.
4. Additional disabled-namespace regression observed RED404 without identity rather than401, then GREEN after private fallback.
5. Duplicate native/library heading regression observed RED2 headings then GREEN after wrapper heading retirement on ready.
6. Independent review reported release rollback/global-loader coupling (P2). Added v1→v2→v1 and stale in-flight script/CSS tests; both observed RED with old loader. Captured implementations and active stylesheet ownership fixed both; wrapper GREEN13 tests.

## Final verification

Backend interpreter: `/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python`.

```
TEST_MANUFACTURING_RELEASE_ROOT=/Users/neo/Developer/work/AI-Manufacturing-Platform/.worktrees/workbench-v1 \
  <python> -m pytest -q backend/tests/test_manufacturing_bridge.py \
  backend/tests/test_ai_engineering_api.py backend/tests/test_r1_authorization.py \
  backend/tests/test_dingtalk_auth_api.py
```

Result: **1001 passed**,0 failures,0 skips,33.63s. Existing Starlette per-request-cookie deprecation warnings:139 (existing auth test harness). Real manufacturing package test uses actual accepted API/repository/assets, trusted actor callback, real central authorization and real Origin/CSRF middleware; records saved/read via HTTP in a disposable SQLite file.

```
<python> -m pytest -q backend/tests/test_manufacturing_bridge.py
```

Result without integration env: **57 passed,1 skipped**,0 failures,1.22s. Optional accepted-sibling integration is the only skipped test; normal bridge tests create isolated contract bundles and do not need a sibling installed. Tests cover all real Role values, all exact tuples, hard-stale writes, absent identity, forged browser actors, missing/invalid CSRF and wrong/missing Origin, private assets, denied/missing routes, bad/malformed source, prefixed shells/returns/CSP and import search-path preservation.

Actual `main.create_app` construction is tested with minimal temporary registry/contract/static fixtures and synthetic session auth, then real `AuthorizationService` with no observation grants. The isolated harness replaces middleware's default DB-free `authorization=None` before its stack builds. It asserts native router registration, unauth401, allowed access and member403. No lifespan/network is started; production identity provider/DB were not simulated as verified.

```
cd webui
npm test -- --run src/pages/ManufacturingPage.test.tsx \
  src/platform/ManufacturingNavigation.test.tsx src/AppShell.sidebar.test.tsx \
  src/panoramaNavigation.test.ts src/auth.test.ts src/router.test.ts src/documentTitle.test.ts
npm run build
```

Result: **261 passed** across7 files,0 failures. TypeScript and Vite production build exit0. Existing Vite large-chunk warning remains. Explicit built-manifest assertion confirms no manufacturing business assets appear in the public Vite manifest. `git diff --check` clean.

## Review and remaining acceptance

Independent reviewer: `/root/manufacturing_api_review`. Initial review found one P2 asset rollback issue; implementation and dedicated regressions fixed it, with re-review requested. Root owns final review approval and integration; this branch is not merged.

API/interface regressions and component tests are local engineering evidence. Browser/visual acceptance, real DingTalk login, real production directory/audit behavior, process/restart production persistence and deployment acceptance remain unexecuted here. No production container/image/Compose overlays were changed. Existing custom SDK/Git runtime overlays must be preserved by rollout tooling. ERP/MRP/MES remain relationship registration only; no external connectors are introduced.
