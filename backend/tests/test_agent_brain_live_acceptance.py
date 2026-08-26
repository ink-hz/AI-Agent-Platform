from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"
RUNBOOK = ROOT / "docs" / "runbooks" / "agent-brain-live-collaboration-release.md"

SCENARIOS = (
    "simple_direct_answer",
    "parallel_hr_marketing",
    "progress_wakeup",
    "agent_followup",
    "agent_stop",
    "user_intervention",
    "partial_failure",
    "adapter_offline",
    "provider_refusal",
    "worker_crash_recovery",
    "thinking_stream_interruption",
    "mobile_replay",
)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_live_scenario_is_a_required_real_acceptance_gate(scenario: str) -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert f"`{scenario}`" in text
    assert "mock_events=0" in text
    assert "invariant_failures=0" in text


def test_release_uses_real_opus5_summarized_thinking_without_fallback() -> None:
    manifest = json.loads(
        (CLOUD / "brain-model.release.json").read_text(encoding="utf-8")
    )
    assert manifest["model_id"] == "claude-opus-5"
    assert manifest["thinking_type"] == "adaptive"
    assert manifest["thinking_display"] == "summarized"
    assert manifest["max_output_tokens"] == 65536
    assert "fallbacks" not in manifest


def test_release_and_rollback_do_not_mutate_admin_fae_or_nginx() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    for required in (
        "Nginx SHA256 unchanged",
        "FAE container ID/ImageID/StartedAt/RestartCount unchanged",
        "/office/?view=services",
        "Do not restart AI ADMIN or FAE",
        "migration 045 and later migrations remain",
    ):
        assert required in runbook

    scripts = "\n".join(
        (CLOUD / name).read_text(encoding="utf-8").lower()
        for name in (
            "remote-stage.sh",
            "accept.sh",
            "rollback-dingtalk-production.sh",
        )
    )
    for forbidden in (
        "docker restart ai-fae-backend",
        "docker stop ai-fae-backend",
        "docker restart ai-admin",
        "docker stop ai-admin",
    ):
        assert forbidden not in scripts
