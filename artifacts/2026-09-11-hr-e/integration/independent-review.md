# Independent D1 and cutover-gate review

Date: 2026-09-11

Review type: bounded, read-only source review and targeted local tests
Worktree HEAD at review: `5aa83e5fc64eeb398b64890496cbdf98fad8d249`

## Finding

No blocking issue was found within the requested D1 context-isolation and cutover-gate scope.

`ConversationContextBuilder._load` classifies the HR path only for `direct_agent` conversations whose direct agent is `hr-bot`. It selects history after `user_seq - 1` and discards the shared summary for those turns, so `build` and `build_direct` receive only the current HR user message. The non-HR path continues to use `summary_through_seq` and the stored summary.

The same-position regression creates two distinct candidate UUIDs and two distinct `position_candidates` relation UUIDs under the same position. It persists candidate A scope and content, then candidate B scope and content, optionally writes a mixed summary containing A-only user and assistant facts, and verifies B-only rendered context with no summary for both builders.

The cutover gate permits a missing table/singleton only for the legacy lane and rejects cloud admission. The public `hr_agent_database` fixture applies the HR Web and HR Agent migrations, explicitly initializes the gate, and by default transitions it to cloud; callers can select legacy initialization.

## Commands and observed results

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_agent_brain_hr_history_isolation.py backend/tests/test_agent_brain_conversation_context.py::test_non_hr_direct_context_bounds_120_persisted_messages_without_losing_current_input backend/tests/test_agent_brain_conversation_context.py::test_non_hr_turn_never_receives_candidate_parser_input backend/tests/test_hr_execution_cutover.py::test_missing_gate_table_preserves_legacy_only
```

Observed: `9 passed in 3.50s`.

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_hr_execution_cutover.py backend/tests/test_hr_agent_foundation.py
```

Observed: `61 passed in 23.83s`.

Retention correction by the integration owner: the matching raw outputs are present in `backend/independent-d1-gate-review.log` and `backend/independent-cutover-fixture-review.log` (9/3.50s and 61/23.83s respectively). The reviewer originally reported them absent; the files were subsequently checked before staging.

## Review bounds and limitation

This was not a whole-E suite, deployment, production, network, model, or business-quality test. It did not review unrelated integration changes.

The same-position test materially distinguishes candidates and their position relations, but it does not independently query and assert the persisted candidate-B `hr_input_context`. Because the implemented policy is current-turn-only for every HR turn, a scope-serialization defect could escape this particular isolation assertion. Frozen current-turn scope/provider/attachment behavior is outside this test's direct assertions.

## Reviewed tree fingerprint

SHA-256 values were computed after the test runs:

```text
72a5b207b25316f72606b1d69239ce4cc4d7bf00877706f46acc575efa6edcf9  backend/app/agent_brain/conversation_context.py
b694c3e30dc44594c7b2cf0c2ae809e4b00989ac100423d58b5a069fd47fe20f  backend/tests/test_agent_brain_hr_history_isolation.py
1d933bfcc5069597d3f44174b24a46f3386987e4522bdbe5dd6af977d2389684  backend/app/hr_agent/cutover.py
62225ed04238f229b90e613ac204c3447d374419f505f6b3ea2c446a477fdbea  backend/tests/test_hr_execution_cutover.py
9ed9458af30ef76134546adffb92fd9db6202a61a0c600642d65c9838a6b8a53  backend/tests/hr_agent_support.py
5a0b7a0767f35c49b6f67a1896f53d28e877c26048ad2f9324fe386894fbc247  backend/tests/test_agent_brain_conversation_context.py
```

Integration follow-up after this review: the D1 test now also queries each persisted turn scope and asserts the common position plus distinct A/B relation IDs. `backend/d1-persisted-scopes.log` records 6 passed/1.79s. The reviewed production context/gate behavior was not changed by that test addition; the test fingerprint above remains the earlier review snapshot.
