# Task 1 report: 候选人成果可发现与准确续作

## Outcome

- `save_result` now inserts `result_links` in the same transaction for the object set already verified and frozen by the work. The model still cannot add an object outside the current work scope.
- Candidate confirmation adds a candidate link for the exact draft only after the intake item, source, result reference, and explicit create/link-existing choice have passed the existing checks. The public `link_result` candidate guard is unchanged.
- Candidate result discovery includes historical `candidate_documents` that predate candidate links. The generic result list continues to show the result's current revision. `read_candidate.documents[*].result_ref` remains the immutable confirmed revision and is re-authorized/read exactly, so a later revision does not replace the confirmed draft.
- `read_candidate` returns sorted `position_ids` after re-authorizing every position. Revoked position access therefore blocks the candidate response.

## Tests and evidence

TDD red run before production edits:

```text
backend/.venv/bin/pytest -q \
  backend/tests/test_hr_agent_repository_views.py::test_save_result_automatically_links_server_verified_work_objects \
  backend/tests/test_hr_agent_candidate_intake.py::test_loop_narrative_human_confirm_and_encrypted_replay \
  backend/tests/test_hr_agent_candidate_intake.py::test_optional_owned_position_relation_is_persisted

3 failed in 4.00s
```

The failures were the missing position/candidate result projections and missing `position_ids`. A second red test updated a result after intake and initially observed the newer revision where the confirmed exact reference was expected; the final policy was clarified so generic lists use current while candidate documents stay pinned.

Focused green checks:

```text
backend/.venv/bin/pytest -q \
  backend/tests/test_hr_agent_candidate_intake.py::test_confirmed_candidate_discovers_exact_draft_after_later_revision \
  backend/tests/test_hr_agent_candidate_routes.py::test_batch_http_confirmed_candidate_and_scope \
  backend/tests/test_hr_agent_repository_views.py::test_save_result_automatically_links_server_verified_work_objects \
  backend/tests/test_hr_agent_repository_views.py::test_link_rejects_unrelated_candidate_and_rolls_back

4 passed in 4.86s
```

Relevant real disposable PostgreSQL and HTTP/database regression:

```text
backend/.venv/bin/pytest -q \
  backend/tests/test_hr_agent_repository_views.py \
  backend/tests/test_hr_agent_candidate_intake.py \
  backend/tests/test_hr_agent_candidate_routes.py \
  backend/tests/test_hr_agent_candidate_erasure.py \
  backend/tests/test_hr_agent_routes.py \
  backend/tests/test_hr_agent_research_result.py \
  backend/tests/test_hr_task_result_projection_database.py

48 passed in 25.91s
```

The regression covers thread/position/candidate discovery, exact confirmed document references after a later revision, pre-link historical candidate documents, create and link-existing confirmation, idempotent replay without duplicate links, foreign-owner rejection, arbitrary cross-candidate link rejection, source erasure, and position revocation.

## Design tradeoffs

- Result links store result identity rather than revision. Consequently generic object lists follow `results.current_revision`, matching the existing list contract. Candidate confirmation precision lives in `candidate_documents.result_ref`; callers use that exact reference for continuation.
- Historical confirmed documents are included in candidate result discovery through an owner/candidate-scoped `EXISTS` fallback. This avoids rewriting old rows or backfilling mutable links during reads.
- Link insertion is centralized in a small repository helper. Candidate confirmation may use it only after its stronger intake checks, while general `link_result` keeps its original restriction that a candidate must already occur in the result document objects.

## Boundaries and incomplete items

- Backend repository, candidate service, and focused tests only. No UI, root architecture documents, migration, production, deployment, or model calls were changed or run.
- Browser and production acceptance were not applicable to this backend task and were not run.

---

# 2026-10-30 Task 1: authenticated independent Web-entry projection

The preceding HR report is retained as historical evidence. This section records the platform navigation Task 1 from `.superpowers/sdd/task-1-brief.md` and `docs/superpowers/specs/2026-10-30-platform-navigation-consolidation-design.md`.

## Outcome and version

Implemented in commit `6fe25633` (`feat(catalog): project authorized independent Web entries`) on branch `refactor/platform-navigation-20261030`, from clean task start `64b53179a0b17f640924df2fabc739908a6c04da`. `git ls-remote origin refs/heads/master` confirmed remote mainline `4fa65dee6627f300eb97a93402417ed66b216a85`. The spec separately records actual previous production release `c6ee87f25e4562ee9ce84a4508491a3a089dbd4e`; production was not queried or changed.

Only `backend/app/agent_catalog/web_entries.py`, `backend/app/agent_catalog/routes.py`, and `backend/tests/test_agent_catalog_web_entries.py` contain implementation/test changes. No existing tests needed edits. The existing authenticated route adds `web_entry` only to cards returned by `permitted_catalog_for_user_id`. It preserves all existing serialized values and `dispatchable`.

The finite `MappingProxyType` publication table provides exactly `ai-fae-agent:/fae/`, `ai-daq-fae-agent:/daq/`, `hr-bot:/hr/`, `voc:/voc/`, and `ai-admin-agent:/office/?view=services`. Unpublished identifiers return `None`, serialized to explicit JSON null. Each published projection is a fresh object. No URL inference, alias, mutable shared result, or sibling repository import is used. HR's legacy MetaBot execution contract remains separate and unchanged.

## Baseline, red, and green evidence

Commands ran from `/Users/neo/.codex/worktrees/0791/AI-Agent-Platform`. The worktree has no virtualenv, so all used the existing original-checkout Python without installing or upgrading dependencies. `PYTHONPATH=backend` and `-c backend/pytest.ini` explicitly select worktree code and existing test configuration.

Baseline before new tests or production edits:

```sh
PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/tests/test_agent_catalog.py backend/tests/test_agent_use_authorization.py backend/tests/test_agent_brain_api.py -m 'not postgres' -q
```

Result: **75 passed, 5 deselected, 38 warnings in 1.26s**, exit 0. The warnings are pre-existing Starlette per-request cookie deprecations in `test_agent_brain_api.py`.

Red after adding only the new test file:

```sh
PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/tests/test_agent_catalog_web_entries.py -q
```

Result: **22 failed, 3 passed in 0.50s**, exit 1. Fifteen HTTP contract cases failed for the missing `web_entry` field (KeyError/assertion); seven projection helper cases failed because the new module did not exist. Existing anonymous, empty-grant, and unavailable-grant boundaries passed. Collection succeeded; these were expected absent-feature failures.

Green after implementation:

```sh
PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/tests/test_agent_catalog_web_entries.py backend/tests/test_agent_catalog.py backend/tests/test_agent_use_authorization.py backend/tests/test_agent_brain_api.py -m 'not postgres' -q
```

Result: **100 passed, 5 deselected, 38 warnings in 1.22s**, exit 0. This includes all **25 new tests**. Same existing cookie deprecation warnings as baseline; no new-test warnings.

Additional affected authorization classification and catalog mount checks:

```sh
PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/tests/test_dingtalk_auth_api.py::test_agent_brain_routes_are_exact_authenticated_self_service_routes backend/tests/test_agent_brain_deployment.py::test_catalog_router_is_mounted_outside_the_brain_repository_gate -q
```

Result: **109 passed in 1.05s**, exit 0. This parametrized registry check covers existing self-service route classifications for all four roles; it is not an SSO end-to-end acceptance test. `git diff --check` and `git diff --cached --check` also exited 0.

Final scoped runs total **209 passed, 0 failed, 5 PostgreSQL cases deliberately deselected**. Baseline and red figures are separate observations, not added to the final passing count.

## Identity and test boundaries

New HTTP tests run the real FastAPI catalog route and real `AgentUseAuthorization`, including the full canonical catalog, `resolve_agent_use_decision_v41` query construction, returned-row validation, authorization filtering, and JSON serialization. A test-only middleware injects an already authenticated `AuthContext`; a connection double supplies boolean decisions at the PostgreSQL boundary. Tests assert the exact authenticated user's UUID is passed with all canonical IDs, and exercise member, management viewer, platform admin, and platform owner with the same limited grants. Role names do not synthesize additional card access. No grants means a real 200 empty list; a database-boundary failure means 503. Anonymous access returns 401 before authorization queries.

This is local in-process HTTP/API engineering coverage, not real staff login, real PostgreSQL decision-function execution, signed-cookie issuance, independent service authentication, or business-quality acceptance. Existing Brain HTTP tests retain their existing identity/security middleware path. No identity, API auth, database roles, schema, grants, or business records were modified.

## Spec and quality self-review

- All five exact publication paths, DAQ inclusion, and all five Marketing nulls are tested through HTTP. HR has `/hr/` while retaining `workspace_url:null`, MetaBot adapter ID/kind, direct/delegation modes, pool, concurrency, and dispatchability.
- Every pre-existing public field on all ten cards is compared against the original catalog serializer plus `dispatchable`; original source card serialization remains unchanged.
- Unknown IDs, legacy aliases, absolute/protocol-relative URL strings, and a path-like traversal identifier receive no publication. The function accepts only an agent ID and never takes a caller-supplied path. Mutating a returned object cannot alter the fixed publication table.
- Authenticated filtering occurs before projection; no new catalog, callable adapter, dispatch, runtime, model, budget, fallback, or retry behavior was introduced. This is additive presentation metadata, so Agent execution architecture/workflow documents require no behavior update.
- Changes are scoped and small: one immutable mapping, one typed projection function, one route serialization addition. Existing cache-control and explicit authorization-unavailable behavior remain.

Concerns/remaining acceptance: independent task spec/quality review is still required before Task 2. Publication is configured availability, not a live health assertion. PostgreSQL acceptance was not rerun because authorization logic/database behavior was unchanged; 5 existing database cases were explicitly deselected. Process-fault tests, frontend components/build, browser acceptance, production acceptance, SSH, Nginx, and deployment were not executed for this backend task. Real model calls: **0**. Production business writes: **0**. No unresolved failing scoped tests.
