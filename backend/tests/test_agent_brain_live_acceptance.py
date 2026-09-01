from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.agent_catalog import AgentCatalogRepository

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


def test_platform_core_release_freezes_migrations_049_through_051() -> None:
    migrations = ROOT / "backend" / "control_migrations"
    expected = {
        49: "049_agent_brain_task_dependencies.sql",
        50: "050_agent_brain_task_wait_state.sql",
        51: "051_agent_brain_actions.sql",
    }

    assert {
        version: (migrations / name).name for version, name in expected.items()
    } == expected
    wait_sql = (migrations / expected[50]).read_text(encoding="utf-8")
    assert "create table platform_brain.brain_task_event_cursors" in wait_sql
    assert "drop column cursors" in wait_sql
    assert "delivered_seq" in wait_sql
    assert "create table platform_brain.agent_task_actions" in (
        migrations / expected[51]
    ).read_text(encoding="utf-8")


def test_first_release_uses_external_workspaces_for_voc_fae_and_admin() -> None:
    catalog = AgentCatalogRepository()
    voc = catalog.require("voc")
    admin = catalog.require("ai-admin-agent")
    fae = catalog.require("ai-fae-agent")

    assert voc.interaction_modes == ("external_workspace",)
    assert voc.workspace_url == "/voc/"
    assert voc.adapter_kind is None
    assert voc.execution_pool is None
    assert voc.capability_version == 2
    assert voc.dispatchable is False
    for card in (voc, admin, fae):
        assert card.interaction_modes == ("external_workspace",)
        assert card.adapter_kind is None
        assert card.execution_pool is None
        assert card.dispatchable is False


def test_release_gate_requires_core_schema_and_behavior_evidence() -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")

    for required in (
        "MIGRATIONS_049_050_051=applied",
        "WAIT_CURSOR_COLUMNS=0",
        "BRAIN_CURSOR_WATERLINE=passed",
        "PENDING_ACTION_FORCED_RECOVERY=passed",
        "TASK_PROTOCOL_ISOLATION=passed",
        "VOC_ACTION_EXACTLY_ONCE=passed",
        "V2_MISSION_RUN_WRITES=0",
        "/office/?view=services",
        "https://fae.orbbec.com.cn/",
    ):
        assert required in script
