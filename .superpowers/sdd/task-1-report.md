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
