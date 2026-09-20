# Panorama platform-role review

Reviewed 2026-09-20, read-only against base `496077d492aca7955095423ac6a6689e41067639` and its uncommitted diff in `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/panorama-admin-role`.

## Verdict

Ready to merge. No critical, important, or minor actionable findings in this bounded change. This is a local code review, not deployment or browser acceptance.

## Evidence

- `backend/app/ai_engineering/routes.py:40` checks trusted `AuthContext.role` for platform_admin/platform_owner. Index, document, and asset handlers each invoke the check before exposing content; the boolean probe grants no authority. `backend/app/control_plane/authorization.py:441` independently enforces the same role set for the exact content route templates.
- Current roles are refreshed on every protected request: unchanged middleware calls `auth.authenticate`, `backend/app/control_plane/auth.py:885` calls `authenticate_web_session_v22`, and `backend/control_migrations/025_platform_admin_mutations.sql:133` returns current `internal_users.role`. The new same-session demotion tests verify the endpoint behavior with the provider boundary faked; this review did not execute a live database demotion.
- No global middleware gate, shared account contract, subapp route, login-return policy, or proxy configuration changed. `/office/`, `/hr/`, `/voc/`, and `/fae/` retain their existing access paths. Tests verify account and HR shell access after demotion, plus all four accepted return paths; they do not constitute live subapp deployment acceptance.
- `webui/src/App.tsx:108` now selects the existing direct panorama mode for home. `/brain` remains explicit. The reusable frontend capability gate and its content authorization-failure handling remain unchanged; members receive the existing denied view without loading Brain as a homepage fallback.
- The private allowlist implementation/configuration was removed with no remaining runtime references. The private content snapshot and integrity verification are unchanged. Updated design documentation matches the user's administrator-role decision and correctly separates local verification from release/browser acceptance.

## Verification

Independently ran `PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q backend/tests/test_ai_engineering_api.py`: **14 passed in 1.51s**. `git diff --check` passed. Inspected all changed code/tests/docs and existing session SQL, middleware, authorization, and frontend gate surrounding the change. The implementer separately confirmed broader backend **936 passed**, frontend **196 passed across four files**, and production frontend build **exit 0**; those checks were not duplicated during this review.
