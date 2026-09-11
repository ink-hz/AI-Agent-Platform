# Task 2 report: 用户面试原文登记

## Outcome

Implemented private user-supplied interview-record registration on the candidate API. The record stores encrypted metadata and an exact material reference; exact GET reads the original attachment-backed UTF-8 text and rechecks the locked source before returning it. Registration accepts only the five specified fields, binds idempotency to the candidate, fixes authorship to `user_supplied`, and atomically marks the attachment as personal.

Migration 101 adds `candidate_interview_records`, upgrades `personal_materials` to exactly one legacy-item or interview-record registration origin, and grants only SELECT/INSERT plus a narrow `lock_user_input_source` function. Readiness verifies the new table, migration receipt SHA, table privileges, and function execute privilege.

## TDD evidence

- RED: `backend/.venv/bin/pytest -q backend/tests/test_hr_agent_interview_records.py -x` failed with `ModuleNotFoundError: app.hr_agent.interview_records`.
- GREEN/focused: `backend/.venv/bin/pytest -q backend/tests/test_hr_agent_interview_records.py` — 5 passed.
- Related real PostgreSQL/API regression: `backend/.venv/bin/pytest -q backend/tests/test_hr_agent_interview_records.py backend/tests/test_hr_agent_candidate_routes.py backend/tests/test_hr_agent_candidate_intake.py backend/tests/test_hr_agent_proposals.py backend/tests/test_hr_agent_foundation.py` — 79 passed in 41.08s.
- Static checks: `backend/.venv/bin/ruff check backend/tests/test_hr_agent_interview_records.py backend/app/hr_agent/interview_records.py` and `git diff --check` — passed.

The focused tests cover HTTP registration/list/exact text retrieval, encrypted-at-rest metadata/body absence, exact material ref preservation, fixed authorship, idempotent replay, extra-field injection rejection, `agent_output` refusal with zero record writes, source revocation propagation, nonblank text, RFC3339 timestamps, strict nullable optional IDs, and compatibility with existing intake-origin personal markers. The related suites cover existing personal-processing outbound gating and standard-proposal personal-source protection.

No production calls, model calls, browser validation, deployment, or UI work were performed. The root-owned `backend/tests/test_hr_agent_d_journey.py` was run but is not part of this commit: it passed the new registration and exact-read portion, then failed in its later model-stage evidence read because that work was blocked and message scope returned 403.
