from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.position_intelligence_models import (
    ConfirmContextModules,
    CreateContextDraft,
    HrPositionContextEnvelope,
    OfficialPositionVersion,
    PositionContextVersion,
)


def test_context_draft_requires_exact_baselines_and_normalizes_modules() -> None:
    position_id, official_id = uuid4(), uuid4()
    command = CreateContextDraft(
        owner_id=uuid4(),
        context_version_id=uuid4(),
        position_id=position_id,
        base_context_version_id=None,
        official_version_id=official_id,
        modules={"talent_profile": {"summary": " 结构能力 "}},
        summary="  首版画像  ",
        client_request_id=uuid4(),
        source_material_attachment_ids=(uuid4(),),
    )

    assert command.position_id == position_id
    assert command.official_version_id == official_id
    assert command.summary == "首版画像"


def test_context_commands_reject_unknown_modules_and_invalid_confirmation() -> None:
    with pytest.raises(ValueError, match="context modules invalid"):
        CreateContextDraft(
            uuid4(), uuid4(), uuid4(), None, None,
            {"candidate_pipeline": {}}, "draft", uuid4(),
        )
    with pytest.raises(ValueError, match="confirmed modules invalid"):
        ConfirmContextModules(
            uuid4(), uuid4(), uuid4(), uuid4(), None, 1, (), uuid4(),
        )


def test_official_version_preserves_complete_published_facts() -> None:
    now = datetime.now(UTC)
    record = OfficialPositionVersion(
        official_position_version_id=uuid4(), owner_id=uuid4(),
        position_id=uuid4(), official_job_id="j11014", title="算法工程师",
        department="机器人", locations=("深圳",), category="研发",
        subcategory="算法类", headcount=1, degree="本科",
        employment_type="全职", salary="20K-30K", duty="Build.",
        requirement="Test.", source_version="sync-v1",
        source_changed_at=now, content_hash="a" * 64,
        first_observed_at=now, last_observed_at=now,
        official_status="active", status_reason="published",
        evidence={"snapshot_sha256": "b" * 64}, created_at=now,
    )

    assert record.official_job_id == "J11014"
    assert record.duty == "Build."
    assert record.requirement == "Test."


def test_context_version_rejects_mutable_or_inconsistent_state() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="context confirmation invalid"):
        PositionContextVersion(
            context_version_id=uuid4(), owner_id=uuid4(), position_id=uuid4(),
            version_number=1, state="confirmed", modules={"mission": {}},
            summary="summary", official_version_id=None,
            base_context_version_id=None, source_conversation_id=None,
            source_turn_id=None, source_artifact_version_id=None,
            source_material_attachment_ids=(), agent_id=None,
            model_version=None, created_by=uuid4(), confirmed_by=None,
            created_at=now, confirmed_at=None, row_version=1,
        )


def test_envelope_is_frozen_and_uses_exact_contract() -> None:
    envelope = HrPositionContextEnvelope(
        position_id=uuid4(), official_version_id=None,
        context_version_id=None, task_kind="jd",
        material_attachment_ids=(), candidate_id=None,
        position_candidate_id=None, document_attachment_ids=(),
        human_feedback_ids=(), prompt_context="Position facts",
        canonical_sha256="a" * 64,
    )

    assert envelope.task_kind == "jd"
    with pytest.raises(Exception):
        envelope.task_kind = "jr"  # type: ignore[misc]
