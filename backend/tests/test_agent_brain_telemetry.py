from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent_brain.telemetry import (
    BrainStepCounters,
    BrainTelemetry,
    BrainTurnSnapshot,
    evaluate_budget_matrix,
)


def _step(
    seq: int,
    *,
    resume_ordinal: int = 0,
    input_tokens: int = 25,
    cache_read_input_tokens: int = 75,
) -> BrainStepCounters:
    return BrainStepCounters(
        step_seq=seq,
        resume_ordinal=resume_ordinal,
        duration_ms=100,
        input_tokens=input_tokens,
        output_tokens=10,
        cache_creation_input_tokens=5,
        cache_read_input_tokens=cache_read_input_tokens,
        recovery_count=1 if seq == 4 else 0,
        duplicate_event_count=1 if seq == 4 else 0,
        truncation_count=0,
        omission_count=0,
    )


def _snapshot(
    *,
    wait_settlements=(
        ("post_commit", "immediate"),
        ("event_append", "pending"),
        ("reaper", "serialization_retry_exhausted"),
    ),
) -> BrainTurnSnapshot:
    return BrainTurnSnapshot(
        turn_id=uuid4(),
        model_config_version="brain-opus5-v1",
        model_id="claude-opus-5",
        prompt_version="brain-v1",
        system_prompt_sha256="a" * 64,
        task_count=3,
        batch_count=2,
        queue_duration_ms=50,
        run_duration_ms=400,
        settle_duration_ms=125,
        steps=(
            _step(1),
            _step(2, resume_ordinal=1, input_tokens=75, cache_read_input_tokens=25),
            _step(3, resume_ordinal=2, input_tokens=40, cache_read_input_tokens=60),
            _step(4, input_tokens=25, cache_read_input_tokens=75),
        ),
        outcome="resolved",
        fallback_used=False,
        reason_code=None,
        wait_settlements=wait_settlements,
    )


def test_cache_metrics_separate_resume_path() -> None:
    summary = BrainTelemetry().summarize(_snapshot())

    assert summary.continuous_steps.cache_hit_rate == pytest.approx(0.75)
    assert summary.first_waiting_agents_resume.cache_hit_rate == pytest.approx(0.25)
    assert summary.later_waiting_agents_resumes.cache_hit_rate == pytest.approx(0.60)
    assert summary.first_waiting_agents_resume.estimated_cost > 0
    assert summary.recovery_count == 1
    assert summary.duplicate_event_count == 1
    assert summary.wait_settlement_sources == {
        "post_commit": 1,
        "event_append": 1,
        "reaper": 1,
    }
    assert summary.wait_settlement_results == {
        "immediate": 1,
        "pending": 1,
        "serialization_retry_exhausted": 1,
    }
    assert summary.immediate_settlement_count == 1
    assert summary.immediate_settlement_step_count == 1
    assert summary.wait_immediate_settlement_rate == pytest.approx(1 / 3)


def test_telemetry_contains_only_bounded_counters_and_no_content() -> None:
    source = _snapshot()
    summary = BrainTelemetry().summarize(source).as_public_dict()
    serialized = json.dumps(summary, sort_keys=True)

    assert str(source.turn_id) in serialized
    assert "candidate name" not in serialized
    assert "thinking" not in serialized.lower()
    assert set(summary) == {
        "turn_id",
        "model_config_version",
        "model_id",
        "prompt_version",
        "system_prompt_sha256",
        "step_count",
        "task_count",
        "batch_count",
        "queue_duration_ms",
        "run_duration_ms",
        "settle_duration_ms",
        "continuous_steps",
        "first_waiting_agents_resume",
        "later_waiting_agents_resumes",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "recovery_count",
        "duplicate_event_count",
        "truncation_count",
        "omission_count",
        "outcome",
        "fallback_used",
        "reason_code",
        "wait_settlement_sources",
        "wait_settlement_results",
        "immediate_settlement_count",
        "immediate_settlement_step_count",
        "wait_immediate_settlement_rate",
    }


def test_telemetry_rejects_unbounded_or_invalid_counters() -> None:
    with pytest.raises(ValueError, match="Brain telemetry step invalid"):
        _step(1, input_tokens=-1)
    with pytest.raises(ValueError, match="Brain telemetry snapshot invalid"):
        BrainTurnSnapshot(**{**_snapshot().__dict__, "system_prompt_sha256": "raw"})
    with pytest.raises(ValueError, match="Brain telemetry snapshot invalid"):
        _snapshot(wait_settlements=(("unknown", "pending"),))


def test_budget_matrix_reports_exact_12_16_24_profiles_without_content() -> None:
    reports = evaluate_budget_matrix(
        {
            12: (_snapshot(),),
            16: (_snapshot(wait_settlements=(("event_append", "immediate"),)),),
            24: (_snapshot(wait_settlements=(("reaper", "pending"),)),),
        }
    )

    assert tuple(report.max_steps for report in reports) == (12, 16, 24)
    assert reports[0].turn_count == 1
    assert reports[0].observed_step_count == 4
    assert reports[0].input_tokens == 165
    assert reports[0].cache_read_input_tokens == 235
    assert reports[0].run_duration_ms == 400
    assert reports[0].settle_duration_ms == 125
    assert reports[1].wait_immediate_settlement_rate == 1.0
    serialized = json.dumps([report.as_public_dict() for report in reports])
    assert "candidate name" not in serialized
    assert "thinking" not in serialized.lower()


def test_budget_matrix_rejects_missing_or_extra_profiles() -> None:
    with pytest.raises(ValueError, match="budget evaluation matrix"):
        evaluate_budget_matrix({12: (_snapshot(),), 24: (_snapshot(),)})


def test_telemetry_has_no_provider_response_decryption_dependency() -> None:
    import app.agent_brain.telemetry as telemetry_module

    source = open(telemetry_module.__file__, encoding="utf-8").read()
    assert "ContentCodec" not in source
    assert "open_json" not in source
    assert "model_response_ciphertext" not in source


def test_http_and_cli_surfaces_do_not_reference_raw_provider_response_storage() -> None:
    backend = Path(__file__).parents[1]
    public_surfaces = tuple((backend / "app").glob("**/routes*.py")) + tuple(
        (backend / "scripts").glob("*.py")
    )

    assert public_surfaces
    for path in public_surfaces:
        source = path.read_text(encoding="utf-8")
        assert "model_response_ciphertext" not in source, path
        assert "provider-response" not in source, path
