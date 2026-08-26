from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.agent_brain.acceptance_contract import (
    ACCEPTANCE_SCENARIOS,
    AcceptanceResult,
    build_sanitized_evidence,
)
from app.agent_brain.loop_models import NormalizedTaskResult
from app.agent_brain.loop_repository import AgentTaskEventInput
from app.agent_brain.model_adapter import BrainModelResponse, BrainUsage
from test_agent_brain_loop_repository import loop_database, loop_repository, seeded_loop
from test_agent_brain_loop_runtime import (
    _delegate_response,
    _runtime,
    _submit_response,
)
from test_control_plane_migration import control_database


ROOT = Path(__file__).parents[2]


EXPECTED_SCENARIOS = (
    "direct_answer",
    "one_agent",
    "two_agent_batch",
    "two_round_replan",
    "success_plus_timeout",
    "metabot_offline",
    "provider_interruption",
    "provider_refusal",
    "crash_recovery",
    "duplicate_replay",
    "concurrent_turn",
    "waiting_user_resume",
    "authorization_revoked",
    "generation_refresh",
    "capability_changed",
    "forced_submission",
    "zero_tool_retry",
    "parallel_overflow",
    "long_context",
    "attachment_minimization",
)


@pytest.mark.parametrize("scenario", EXPECTED_SCENARIOS)
def test_v2_acceptance_scenario_contract_is_complete(scenario: str) -> None:
    contract = ACCEPTANCE_SCENARIOS[scenario]

    assert contract.scenario_id == scenario
    assert contract.automated_test.startswith("tests/test_agent_brain_")
    assert contract.expected_outcome
    assert contract.proves_v2_mission_write_zero is True


def test_acceptance_matrix_has_no_unreviewed_or_duplicate_scenarios() -> None:
    assert tuple(ACCEPTANCE_SCENARIOS) == EXPECTED_SCENARIOS
    assert len(set(ACCEPTANCE_SCENARIOS)) == 20


def test_scenario_references_resolve_to_real_test_functions() -> None:
    for contract in ACCEPTANCE_SCENARIOS.values():
        relative, function_name = contract.automated_test.split("::", 1)
        source = (ROOT / "backend" / relative).read_text(encoding="utf-8")
        assert f"def {function_name}(" in source


def _delegation(tool_id: str, objective: str) -> BrainModelResponse:
    block = dict(_delegate_response().content_blocks[1])
    block["id"] = tool_id
    block["input"] = {**block["input"], "objective": objective}
    return BrainModelResponse(
        provider_request_id=f"msg_{tool_id}",
        content_blocks=(block,),
        stop_reason="tool_use",
        usage=BrainUsage(input_tokens=100, output_tokens=20),
    )


@pytest.mark.postgres
def test_acceptance_direct_answer_writes_no_mission(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop

    assert _runtime(loop_repository, _submit_response()).advance_one() is True
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select loop.status,"
            "(select count(*) from platform_brain.agent_tasks where loop_id=%s),"
            "(select count(*) from platform_control.missions where turn_id=loop.turn_id),"
            "(select count(*) from platform_control.mission_runs run join "
            "platform_control.missions mission on mission.mission_id=run.mission_id "
            "where mission.turn_id=loop.turn_id) "
            "from platform_brain.brain_loops loop where loop.loop_id=%s",
            (loop_id, loop_id),
        ).fetchone() == ("completed", 0, 0, 0)


@pytest.mark.postgres
def test_acceptance_two_round_replan_is_bounded_and_durable(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop

    assert _runtime(
        loop_repository, _delegation("toolu_round_1", "第一轮专业判断")
    ).advance_one()
    assert _runtime(loop_repository).dispatch_one()
    assert _runtime(
        loop_repository, _delegation("toolu_round_2", "第二轮补充验证")
    ).advance_one()
    assert _runtime(loop_repository).dispatch_one()
    assert _runtime(loop_repository, _submit_response()).advance_one()

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select loop.status,loop.step_count,loop.task_count,"
            "(select count(*) from platform_control.missions where turn_id=loop.turn_id) "
            "from platform_brain.brain_loops loop where loop.loop_id=%s",
            (loop_id,),
        ).fetchone() == ("completed", 3, 2, 0)


@pytest.mark.postgres
def test_acceptance_success_plus_timeout_settles_one_batch(
    loop_database, loop_repository, seeded_loop
) -> None:
    environment, *_unused = loop_database
    loop_id, _snapshot_id = seeded_loop
    first = _delegation("toolu_mixed_1", "成功任务").content_blocks[0]
    second = _delegation("toolu_mixed_2", "超时任务").content_blocks[0]
    response = BrainModelResponse(
        provider_request_id="msg_mixed",
        content_blocks=(first, second),
        stop_reason="tool_use",
        usage=BrainUsage(input_tokens=100, output_tokens=30),
    )
    assert _runtime(loop_repository, response).advance_one()
    with psycopg.connect(environment["admin"]) as connection:
        task_ids = tuple(
            row[0]
            for row in connection.execute(
                "select task.task_id from platform_brain.agent_tasks task join "
                "platform_brain.brain_tool_calls call on "
                "call.brain_tool_call_id=task.brain_tool_call_id "
                "where task.loop_id=%s order by call.tool_index",
                (loop_id,),
            ).fetchall()
        )
    assert len(task_ids) == 2
    now = datetime.now(timezone.utc)
    outcomes = (
        ("completed", "专业任务完成"),
        ("timed_out", "专业任务超时"),
    )
    for task_id, (status, summary) in zip(task_ids, outcomes, strict=True):
        assert loop_repository.append_task_event(
            AgentTaskEventInput(
                task_id=task_id,
                seq=1,
                event_type=f"agent.{status}",
                created_at=now,
                payload={"status": status},
                terminal_status=status,
                result=NormalizedTaskResult(
                    status=status,
                    summary=summary,
                    deliverables=(),
                    evidence=(),
                    limitations=(),
                    attachment_refs=(),
                ),
            )
        )
    # Live collaboration delegates non-blockingly. The old blocking batch
    # settler must not create a second resume step after task terminals land.
    assert loop_repository.settle_batch(loop_id) is False
    assert _runtime(loop_repository, _submit_response()).advance_one()
    with psycopg.connect(environment["admin"]) as connection:
        statuses = connection.execute(
            "select task.status from platform_brain.agent_tasks task join "
            "platform_brain.brain_tool_calls call on "
            "call.brain_tool_call_id=task.brain_tool_call_id "
            "where task.loop_id=%s order by call.tool_index",
            (loop_id,),
        ).fetchall()
        mission_count = connection.execute(
            "select count(*) from platform_control.missions mission join "
            "platform_brain.brain_loops loop on loop.turn_id=mission.turn_id "
            "where loop.loop_id=%s",
            (loop_id,),
        ).fetchone()[0]
    assert statuses == [("completed",), ("timed_out",)]
    assert mission_count == 0


def test_sanitized_evidence_is_content_free_and_requires_every_gate() -> None:
    results = tuple(
        AcceptanceResult(
            scenario_id=scenario,
            passed=True,
            v2_mission_run_writes=0,
            safe_diagnostics="passed",
        )
        for scenario in EXPECTED_SCENARIOS
    )

    evidence = build_sanitized_evidence(
        results,
        manifest_sha256="a" * 64,
        system_prompt_sha256="b" * 64,
        provider_evidence_sha256="c" * 64,
        reviewer_id="business-reviewer-01",
        reviewer_decision="approved",
        fae_managed_files_unchanged=True,
    )

    assert evidence["schema_version"] == 1
    assert evidence["scenario_count"] == 20
    assert evidence["passed_count"] == 20
    assert evidence["v2_mission_run_writes"] == 0
    assert evidence["fae_managed_files_unchanged"] is True
    rendered = repr(evidence).lower()
    assert "prompt_text" not in rendered
    assert "answer_markdown" not in rendered
    assert "provider_response" not in rendered


def test_evidence_rejects_failure_missing_scenario_or_v1_write() -> None:
    complete = [
        AcceptanceResult(scenario, True, 0, "passed")
        for scenario in EXPECTED_SCENARIOS
    ]
    mutations = (
        complete[:-1],
        [*complete[:-1], AcceptanceResult(EXPECTED_SCENARIOS[-1], False, 0, "failed")],
        [*complete[:-1], AcceptanceResult(EXPECTED_SCENARIOS[-1], True, 1, "write")],
    )

    for results in mutations:
        with pytest.raises(ValueError, match="acceptance evidence invalid"):
            build_sanitized_evidence(
                results,
                manifest_sha256="a" * 64,
                system_prompt_sha256="b" * 64,
                provider_evidence_sha256="c" * 64,
                reviewer_id="business-reviewer-01",
                reviewer_decision="approved",
                fae_managed_files_unchanged=True,
            )


def test_release_script_and_runbook_freeze_the_v2_evidence_contract() -> None:
    script = (ROOT / "deploy/cloud/accept.sh").read_text(encoding="utf-8")
    runbook_path = ROOT / "docs/runbooks/agent-brain-v2-acceptance.md"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert runbook_path.exists()
    runbook = runbook_path.read_text(encoding="utf-8")
    for required in (
        "AGENT_BRAIN_V2_ACCEPTANCE_OK",
        "provider_probe",
        "reference-recovery.passed",
        "provider-evidence.sha256",
        "quality-review.json",
        "FAE_MANAGED_FILES_UNCHANGED=true",
        "V2_MISSION_RUN_WRITES=0",
        "metabot_local",
        "continuous_step",
        "first_waiting_agents_resume",
        "later_waiting_agents_resume",
    ):
        assert required in script or required in runbook
    assert "agent-brain-v2-acceptance.md" in readme


def test_acceptance_script_never_fabricates_required_evidence() -> None:
    script = (ROOT / "deploy/cloud/accept.sh").read_text(encoding="utf-8")

    assert "touch \"$reference_evidence\"" not in script
    assert "echo REFERENCE_RECOVERY=passed >" not in script
    assert "echo QUALITY_REVIEW=approved >" not in script
    assert "raw_provider_response" not in script
    assert "thinking_text" not in script


def test_v2_rollback_verifies_loop_history_without_legacy_mission_ids() -> None:
    script = (ROOT / "deploy/cloud/accept.sh").read_text(encoding="utf-8")

    assert "brain_v2=true" in script
    assert "V2_ROLLBACK_HISTORY_PRESERVED=true" in script
    assert "from platform_brain.brain_loops" in script
    assert "AGENT_BRAIN_V2_ROLLBACK_OK" in script
