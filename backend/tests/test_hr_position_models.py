from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.hr.models import (
    BindPositionConversation,
    ConfirmPositionDraft,
    CreateManualPosition,
    PositionDraftRecord,
    PositionRecord,
    PromotePositionMaterial,
    ProposePositionDraft,
)


def test_manual_position_command_normalizes_public_job_fields() -> None:
    command = CreateManualPosition(
        owner_id=uuid4(),
        position_id=uuid4(),
        client_request_id=uuid4(),
        title="  高级结构工程师  ",
        department="  研发中心 ",
        locations=(" 深圳 ", "中山"),
    )

    assert command.title == "高级结构工程师"
    assert command.department == "研发中心"
    assert command.locations == ("深圳", "中山")


@pytest.mark.parametrize("title", ["", "   ", "x" * 501])
def test_manual_position_command_rejects_invalid_title(title: str) -> None:
    with pytest.raises(ValueError, match="position title invalid"):
        CreateManualPosition(uuid4(), uuid4(), uuid4(), title)


def test_position_record_separates_official_and_internal_status() -> None:
    now = datetime.now(UTC)
    record = PositionRecord(
        position_id=uuid4(), owner_id=uuid4(), source_kind="official_site",
        official_job_id="j11014", title="光学设计工程师", department=None,
        locations=("中山",), official_status="stale", internal_status="active",
        source_version="2026-09-04T00:00:00Z", row_version=2,
        created_at=now, updated_at=now,
    )

    assert record.official_job_id == "J11014"
    assert record.official_status == "stale"
    assert record.internal_status == "active"


@pytest.mark.parametrize("job_id", ["J1", "11014", "J-11014", "J1234567890123"])
def test_official_position_rejects_noncanonical_job_id(job_id: str) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="official job id invalid"):
        PositionRecord(
            uuid4(), uuid4(), "official_site", job_id, "岗位", None, (),
            "active", "active", None, 1, now, now,
        )


def test_manual_position_rejects_official_fields() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="position source fields invalid"):
        PositionRecord(
            uuid4(), uuid4(), "manual", "J11014", "岗位", None, (),
            "active", "active", None, 1, now, now,
        )


def test_position_draft_requires_resolution_only_after_confirmation() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="draft resolution invalid"):
        PositionDraftRecord(
            uuid4(), uuid4(), "historical_conversation", "conversation:1",
            None, "结构工程师", {}, {}, "history-v1", "proposed", uuid4(),
            1, now, now,
        )


def test_position_draft_command_bounds_evidence_and_rule_version() -> None:
    command = ProposePositionDraft(
        owner_id=uuid4(), draft_id=uuid4(), client_request_id=uuid4(),
        source_kind="new_conversation", source_key="conversation:abc",
        source_conversation_id=uuid4(), title="  算法工程师 ",
        proposal={"mission": "3D 感知"}, evidence={"message_seq": 1},
        discovery_rule_version="interactive-v1",
    )

    assert command.title == "算法工程师"
    assert command.evidence == {"message_seq": 1}


def test_commands_reject_boolean_row_versions_and_invalid_binding_kind() -> None:
    with pytest.raises(ValueError, match="row version invalid"):
        ConfirmPositionDraft(uuid4(), uuid4(), uuid4(), uuid4(), True)
    with pytest.raises(ValueError, match="binding kind invalid"):
        BindPositionConversation(
            uuid4(), uuid4(), uuid4(), uuid4(), "candidate_pipeline"
        )


def test_material_promotion_requires_distinct_uuid_identifiers() -> None:
    shared = uuid4()
    with pytest.raises(ValueError, match="material identifiers invalid"):
        PromotePositionMaterial(shared, shared, shared, uuid4())
