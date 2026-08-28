from __future__ import annotations

# Pytest fixtures are imported into this module's namespace for discovery.
# ruff: noqa: F401,F811
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from app.agent_brain.action_models import (
    ActionProposal,
    canonical_action_bytes,
    proposal_digest,
    stable_action_id,
)
from app.agent_brain.action_service import (
    ActionCommandConflict,
    ActionCommandDenied,
    ActionCommandService,
)
from test_agent_brain_live_repository import live_database, seeded_live_task
from test_control_plane_migration import control_database

NOW = datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)


def _proposal(*, summary: str, impact: str) -> ActionProposal:
    task_id = uuid4()
    return ActionProposal(
        action_id=stable_action_id(task_id, 1),
        platform_task_id=task_id,
        action_seq=1,
        action_kind="voc.submit",
        summary=summary,
        impact=impact,
        parameters={"draft_id": "draft-1", "channel": "voc"},
        action_digest=proposal_digest(
            platform_task_id=task_id,
            action_seq=1,
            action_kind="voc.submit",
            parameters={"draft_id": "draft-1", "channel": "voc"},
        ),
        expires_at=NOW + timedelta(hours=2),
        execution_timeout_seconds=300,
    )


def test_digest_excludes_summary_and_impact() -> None:
    first = _proposal(summary="提交 VOC 草稿", impact="将写入正式记录")
    second = ActionProposal(
        **{
            **first.model_dump(),
            "summary": "文案已经修改",
            "impact": "展示说明已经修改",
        }
    )

    assert first.action_digest == second.action_digest


def test_action_model_rejects_digest_mismatch() -> None:
    proposal = _proposal(summary="提交 VOC 草稿", impact="将写入正式记录")
    with pytest.raises(ValueError, match="action digest mismatch"):
        ActionProposal(**{**proposal.model_dump(), "action_digest": "0" * 64})


def test_platform_digest_matches_frozen_http_contract_fixture() -> None:
    fixture = json.loads(
        (
            Path(__file__).parents[2]
            / "contracts"
            / "http_task_v1"
            / "fixtures"
            / "action_digest.json"
        ).read_text(encoding="utf-8")
    )
    expected = (
        '{"action_kind":"voc.submit","action_seq":1,"parameters":'
        '{"priority":2,"title":"机器人客户反馈"},"platform_task_id":'
        '"0d8f0764-91be-4af5-b4d8-e79d58ab3b07"}'
    ).encode()
    assert canonical_action_bytes(fixture["input"]) == expected
    assert proposal_digest(
        platform_task_id=UUID(fixture["input"]["platform_task_id"]),
        action_seq=fixture["input"]["action_seq"],
        action_kind=fixture["input"]["action_kind"],
        parameters=fixture["input"]["parameters"],
    ) == fixture["lowercase_hex"]


@pytest.mark.postgres
def test_owner_confirmation_is_exactly_once_and_non_owner_is_denied(
    live_database, seeded_live_task
) -> None:
    environment, codec, owner_id, _conversation_id, _turn_id = live_database
    _repository, _loop_repository, _loop_id, task_id, _ = seeded_live_task
    action_id = stable_action_id(task_id, 1)
    parameters = {"draft_id": "draft-1", "channel": "voc"}
    digest = proposal_digest(
        platform_task_id=task_id,
        action_seq=1,
        action_kind="voc.submit",
        parameters=parameters,
    )
    proposal = ActionProposal(
        action_id=action_id,
        platform_task_id=task_id,
        action_seq=1,
        action_kind="voc.submit",
        summary="提交 VOC 草稿",
        impact="将写入正式 VOC 记录",
        parameters=parameters,
        action_digest=digest,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        execution_timeout_seconds=300,
    )
    worker = ActionCommandService(
        environment["urls"]["platform_brain_worker"],
        content_codec=codec,
        dsn_purpose="brain",
    )
    app = ActionCommandService(
        environment["urls"]["platform_control_app"],
        content_codec=codec,
        dsn_purpose="app",
    )

    pending = worker.propose(proposal)
    assert pending.status == "pending"
    with pytest.raises(ActionCommandConflict):
        app.confirm(owner_id, action_id, "0" * 64)
    with pytest.raises(ActionCommandDenied):
        app.confirm(uuid4(), action_id, digest)

    first = app.confirm(owner_id, action_id, digest)
    second = app.confirm(owner_id, action_id, digest)
    assert first.status == second.status == "confirmed"
    assert first.execution_status == second.execution_status == "queued"
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_brain.agent_action_deliveries "
            "where action_id=%s",
            (action_id,),
        ).fetchone()[0] == 1
