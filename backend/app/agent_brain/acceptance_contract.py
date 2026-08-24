from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sys
from types import MappingProxyType
from typing import Iterable, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


@dataclass(frozen=True, slots=True)
class AcceptanceScenario:
    scenario_id: str
    automated_test: str
    expected_outcome: str
    proves_v2_mission_write_zero: bool = True


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    scenario_id: str
    passed: bool
    v2_mission_run_writes: int
    safe_diagnostics: str


def _scenario(
    scenario_id: str,
    automated_test: str,
    expected_outcome: str,
) -> AcceptanceScenario:
    return AcceptanceScenario(scenario_id, automated_test, expected_outcome)


ACCEPTANCE_SCENARIOS: Mapping[str, AcceptanceScenario] = MappingProxyType(
    {
        item.scenario_id: item
        for item in (
            _scenario(
                "direct_answer",
                "tests/test_agent_brain_v2_acceptance.py::test_acceptance_direct_answer_writes_no_mission",
                "Brain completes a Turn without a professional task when appropriate.",
            ),
            _scenario(
                "one_agent",
                "tests/test_agent_brain_loop_runtime.py::test_reference_adapter_slice_survives_worker_recreation",
                "One delegated task is settled and synthesized once.",
            ),
            _scenario(
                "two_agent_batch",
                "tests/test_agent_brain_loop_runtime.py::test_three_task_batch_creates_only_one_resume_step",
                "A whole task batch settles before exactly one Brain wake-up.",
            ),
            _scenario(
                "two_round_replan",
                "tests/test_agent_brain_v2_acceptance.py::test_acceptance_two_round_replan_is_bounded_and_durable",
                "A resumed Loop can delegate another bounded round before submission.",
            ),
            _scenario(
                "success_plus_timeout",
                "tests/test_agent_brain_v2_acceptance.py::test_acceptance_success_plus_timeout_settles_one_batch",
                "Mixed terminal task outcomes remain explicit in one paired batch.",
            ),
            _scenario(
                "metabot_offline",
                "tests/test_agent_brain_metabot_adapter.py::test_metabot_adapter_returns_fast_unavailable_when_worker_is_offline",
                "Only the local Adapter reports unavailable; the Brain remains usable.",
            ),
            _scenario(
                "provider_interruption",
                "tests/test_agent_brain_v2_budget.py::test_step_budget_forces_submission_failure_with_distinct_reason",
                "A Provider interruption is visible and never silently changes model.",
            ),
            _scenario(
                "provider_refusal",
                "tests/test_agent_brain_v2_budget.py::test_provider_refusal_skips_retry_and_writes_explicit_fallback",
                "A Provider refusal is classified once without protocol retry.",
            ),
            _scenario(
                "crash_recovery",
                "tests/test_agent_brain_v2_recovery.py::test_crash_before_model_commit_reclaims_step_without_duplicate_calls",
                "Expired leases recover from append-only state without duplicate effects.",
            ),
            _scenario(
                "duplicate_replay",
                "tests/test_agent_brain_loop_repository.py::test_replayed_tool_call_creates_one_task",
                "Duplicate tool and event delivery is idempotent.",
            ),
            _scenario(
                "concurrent_turn",
                "tests/test_agent_brain_conversation_repository.py::test_concurrent_start_replays_one_atomic_result",
                "A Conversation admits only one non-terminal Turn.",
            ),
            _scenario(
                "waiting_user_resume",
                "tests/test_agent_brain_loop_runtime.py::test_waiting_user_pauses_budget_and_resumes_same_turn_once",
                "Waiting for a person pauses the 900-second active budget.",
            ),
            _scenario(
                "authorization_revoked",
                "tests/test_agent_brain_loop_runtime.py::test_live_revocation_fails_loop_before_model",
                "A real effective deny terminates the Loop before another model call.",
            ),
            _scenario(
                "generation_refresh",
                "tests/test_agent_brain_runtime_registry.py::test_directory_generation_change_does_not_change_effective_hash",
                "A harmless directory generation refresh does not terminate work.",
            ),
            _scenario(
                "capability_changed",
                "tests/test_agent_brain_runtime_registry.py::test_capability_change_rejects_new_task_without_revoking_loop",
                "A changed capability rejects only a new task with an explicit result.",
            ),
            _scenario(
                "forced_submission",
                "tests/test_agent_brain_v2_budget.py::test_task_budget_forces_submit_with_unchanged_tools",
                "Budget exhaustion forces submit_answer without changing tool bytes.",
            ),
            _scenario(
                "zero_tool_retry",
                "tests/test_agent_brain_v2_budget.py::test_zero_tool_response_retries_once_then_uses_protocol_fallback",
                "A zero-tool response gets one correction then an explicit fallback.",
            ),
            _scenario(
                "parallel_overflow",
                "tests/test_agent_brain_tool_protocol.py::test_delegate_batch_accepts_first_four_and_pairs_every_call",
                "Only the first four task calls execute and all calls receive results.",
            ),
            _scenario(
                "long_context",
                "tests/test_agent_brain_context_policy.py::test_long_brain_context_has_explicit_model_visible_truncation_marker",
                "Long context is bounded with a model-visible truncation marker.",
            ),
            _scenario(
                "attachment_minimization",
                "tests/test_agent_brain_context_policy.py::test_child_agent_receives_only_explicit_excerpt_and_allowed_attachments",
                "A child receives only selected text and authorized attachments.",
            ),
        )
    }
)


def build_sanitized_evidence(
    results: Iterable[AcceptanceResult],
    *,
    manifest_sha256: str,
    system_prompt_sha256: str,
    provider_evidence_sha256: str,
    reviewer_id: str,
    reviewer_decision: str,
    fae_managed_files_unchanged: bool,
) -> dict[str, object]:
    """Build only the content-free release evidence allowed in Git-adjacent tools."""

    rows = tuple(results)
    expected = tuple(ACCEPTANCE_SCENARIOS)
    valid = (
        tuple(row.scenario_id for row in rows) == expected
        and all(type(row.passed) is bool and row.passed for row in rows)
        and all(row.v2_mission_run_writes == 0 for row in rows)
        and all(row.safe_diagnostics == "passed" for row in rows)
        and all(
            _SHA256.fullmatch(value)
            for value in (
                manifest_sha256,
                system_prompt_sha256,
                provider_evidence_sha256,
            )
        )
        and isinstance(reviewer_id, str)
        and _REVIEWER_ID.fullmatch(reviewer_id)
        and reviewer_decision == "approved"
        and fae_managed_files_unchanged is True
    )
    if not valid:
        raise ValueError("acceptance evidence invalid")
    return {
        "schema_version": 1,
        "scenario_count": len(rows),
        "passed_count": len(rows),
        "scenario_ids": expected,
        "v2_mission_run_writes": 0,
        "manifest_sha256": manifest_sha256,
        "system_prompt_sha256": system_prompt_sha256,
        "provider_evidence_sha256": provider_evidence_sha256,
        "reviewer_id": reviewer_id,
        "reviewer_decision": reviewer_decision,
        "fae_managed_files_unchanged": True,
    }


def main(argv: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    if selected == ["pytest-args"]:
        for scenario in ACCEPTANCE_SCENARIOS.values():
            print(scenario.automated_test)
        return 0
    if selected == ["json"]:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "scenario_count": len(ACCEPTANCE_SCENARIOS),
                    "scenarios": tuple(ACCEPTANCE_SCENARIOS),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
