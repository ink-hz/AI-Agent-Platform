from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_control_plane_migration import control_database

from app.hr.models import BindPositionConversation, CreateManualPosition
from app.hr.position_intelligence_models import (
    OfficialPositionVersion,
    PositionContextVersion,
)
from app.hr.repository import HrPositionRepository
from app.hr.task_context import (
    HrTaskContextError,
    HrTaskContextProvider,
    HrTaskMaterial,
    HrTaskScope,
    PostgresHrTaskContextSource,
    canonical_hash,
)


def _records():
    now = datetime.now(UTC)
    owner_id, position_id = uuid4(), uuid4()
    official = OfficialPositionVersion(
        uuid4(), owner_id, position_id, "J11014", "算法工程师", "机器人",
        ("深圳",), "研发", "算法类", 1, "本科", "全职", "20K-30K",
        "Build the system.", "Test the system.", "sync-v1", now, "a" * 64,
        now, now, "active", "published", {"snapshot": "sync-v1"}, now,
    )
    context = PositionContextVersion(
        uuid4(), owner_id, position_id, 1, "confirmed",
        {"mission": {"text": "Deliver perception"}}, "Confirmed context",
        official.official_position_version_id, None, None, None, None, (),
        "hr-bot", "model-v1", owner_id, owner_id, now, now, 1,
    )
    material = HrTaskMaterial(
        uuid4(), position_id, "b" * 64, "ready", True,
        now + timedelta(days=1), False,
    )
    return owner_id, position_id, official, context, material


class Source:
    def __init__(self, scope):
        self.scope = scope
        self.existing = None
        self.recorded = []

    def existing_for_turn(self, owner_id, conversation_id, turn_id):
        return self.existing

    def load_for_turn(self, owner_id, conversation_id, turn_id):
        return self.scope

    def record_for_turn(self, owner_id, conversation_id, turn_id, envelope):
        self.recorded.append((owner_id, conversation_id, turn_id, envelope))
        return envelope


class CandidateProvider:
    def __init__(self):
        self.calls = []

    def for_task(self, owner_id, position_id, candidate_id, position_candidate_id):
        self.calls.append((owner_id, position_id, candidate_id, position_candidate_id))
        return SimpleNamespace(
            candidate_id=candidate_id,
            position_candidate_id=position_candidate_id,
            document_attachment_ids=(uuid4(),),
            human_feedback_ids=(uuid4(),),
            prompt_context="Candidate evidence",
        )


def test_envelope_pins_position_context_materials_and_candidate_fragment() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id, candidate_id, position_candidate_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    source = Source(HrTaskScope(
        owner_id, position_id, conversation_id, turn_id, "candidate_match",
        official, context, (material,), candidate_id, position_candidate_id,
    ))
    candidate_provider = CandidateProvider()
    provider = HrTaskContextProvider(source, candidate_provider=candidate_provider)

    envelope = provider.build_for_turn(owner_id, conversation_id, turn_id)

    assert envelope.position_id == position_id
    assert envelope.official_version_id == official.official_position_version_id
    assert envelope.context_version_id == context.context_version_id
    assert envelope.material_attachment_ids == (material.attachment_id,)
    assert envelope.candidate_id == candidate_id
    assert envelope.canonical_sha256 == canonical_hash(envelope)
    assert "Build the system." in envelope.prompt_context
    assert "Candidate evidence" in envelope.prompt_context
    assert len(source.recorded) == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"state": "quarantined"}, "material unavailable"),
        ({"active": False}, "material unavailable"),
        ({"retained_until": datetime.now(UTC) - timedelta(seconds=1)}, "material unavailable"),
        ({"erasure_pending": True}, "material unavailable"),
        ({"position_id": uuid4()}, "material scope invalid"),
    ],
)
def test_provider_rejects_unready_expired_erased_and_cross_position_materials(
    overrides, message,
) -> None:
    owner_id, position_id, official, context, material = _records()
    material = replace(material, **overrides)
    conversation_id, turn_id = uuid4(), uuid4()
    source = Source(HrTaskScope(
        owner_id, position_id, conversation_id, turn_id, "jd", official,
        context, (material,), None, None,
    ))

    with pytest.raises(HrTaskContextError, match=message):
        HrTaskContextProvider(source).build_for_turn(
            owner_id, conversation_id, turn_id
        )
    assert source.recorded == []


def test_provider_fails_closed_on_scope_mismatch_or_missing_candidate_provider() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    mismatched = Source(HrTaskScope(
        uuid4(), position_id, conversation_id, turn_id, "jd", official,
        context, (material,), None, None,
    ))
    with pytest.raises(HrTaskContextError, match="scope invalid"):
        HrTaskContextProvider(mismatched).build_for_turn(
            owner_id, conversation_id, turn_id
        )

    candidate_scope = Source(HrTaskScope(
        owner_id, position_id, conversation_id, turn_id, "candidate_match",
        official, context, (material,), uuid4(), uuid4(),
    ))
    with pytest.raises(HrTaskContextError, match="candidate context unavailable"):
        HrTaskContextProvider(candidate_scope).build_for_turn(
            owner_id, conversation_id, turn_id
        )


def test_recovery_returns_the_recorded_envelope_without_rereading_newer_context() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    source = Source(HrTaskScope(
        owner_id, position_id, conversation_id, turn_id, "jd", official,
        context, (material,), None, None,
    ))
    provider = HrTaskContextProvider(source)
    first = provider.build_for_turn(owner_id, conversation_id, turn_id)
    source.existing = first
    source.scope = replace(
        source.scope,
        context=replace(context, context_version_id=uuid4(), version_number=2),
    )

    recovered = provider.build_for_turn(owner_id, conversation_id, turn_id)

    assert recovered is first
    assert recovered.context_version_id == context.context_version_id
    assert len(source.recorded) == 1


@pytest.mark.postgres
def test_postgres_source_verifies_position_binding_and_persists_one_task_record(
    conversation_database,
    repository,
    request,
) -> None:
    environment, owner_id, _ = conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "结构工程师")
    )

    def cleanup():
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "delete from platform_hr.position_task_records "
                "where owner_internal_user_id=%s",
                (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_conversations "
                "where owner_internal_user_id=%s",
                (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.positions "
                "where owner_internal_user_id=%s",
                (owner_id,),
            )

    request.addfinalizer(cleanup)
    started = repository.start(
        owner_id, uuid4(), "请生成岗位说明",
        mode="direct_agent", direct_agent_id="hr-bot",
    )
    positions.bind_conversation(BindPositionConversation(
        owner_id, position.position_id, started.conversation.conversation_id,
        uuid4(), "created_in_position",
    ))
    source = PostgresHrTaskContextSource(
        environment["urls"]["platform_control_app"]
    )
    provider = HrTaskContextProvider(source)

    first = provider.build_for_turn(
        owner_id, started.conversation.conversation_id, started.turn.turn_id
    )
    replay = provider.build_for_turn(
        owner_id, started.conversation.conversation_id, started.turn.turn_id
    )

    assert replay == first
    assert first.position_id == position.position_id
    assert first.task_kind == "freeform"
    assert "结构工程师" in first.prompt_context
    with psycopg.connect(environment["admin"]) as connection:
        count = connection.execute(
            "select count(*) from platform_hr.position_task_records "
            "where owner_internal_user_id=%s and turn_id=%s",
            (owner_id, started.turn.turn_id),
        ).fetchone()[0]
    assert count == 1
