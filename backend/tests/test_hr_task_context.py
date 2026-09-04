from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from app.hr.models import (
    BindPositionConversation,
    CreateManualPosition,
    ProjectOfficialPosition,
)
from app.hr.position_intelligence_models import (
    CreatePositionTaskRequest,
    OfficialPositionVersion,
    PositionContextVersion,
    ProjectOfficialVersion,
)
from app.hr.position_intelligence_repository import PositionIntelligenceRepository
from app.hr.repository import HrPositionRepository
from app.hr.task_context import (
    CandidateEnvelopeFragment,
    HrTaskContextError,
    HrTaskContextProvider,
    HrTaskMaterial,
    HrTaskScope,
    PostgresHrTaskContextSource,
    canonical_hash,
)
from test_agent_brain_conversation_repository import (
    conversation_database,
    repository,
)
from test_control_plane_migration import control_database


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
        return CandidateEnvelopeFragment(
            candidate_id=candidate_id,
            position_candidate_id=position_candidate_id,
            context_version_id=self.context_version_id,
            document_attachment_ids=(uuid4(),),
            human_feedback_ids=(uuid4(),),
            prompt_context="Candidate evidence",
        )


class FailingCandidateProvider:
    def for_task(self, *_args):
        raise RuntimeError("candidate repository detail")


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
    candidate_provider.context_version_id = context.context_version_id
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
    prompt = __import__("json").loads(envelope.prompt_context)
    assert prompt["official_facts"]["category"] == "研发"
    assert prompt["official_facts"]["headcount"] == 1
    assert prompt["official_facts"]["status_reason"] == "published"
    assert len(source.recorded) == 1


def test_candidate_tasks_require_exact_confirmed_context_and_documents() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    source = Source(HrTaskScope(
        owner_id, position_id, conversation_id, turn_id, "candidate_match",
        official, context, (material,), uuid4(), uuid4(),
    ))
    candidate = CandidateProvider()
    candidate.context_version_id = uuid4()
    with pytest.raises(HrTaskContextError, match="candidate context scope invalid"):
        HrTaskContextProvider(source, candidate_provider=candidate).build_for_turn(
            owner_id, conversation_id, turn_id
        )

    with pytest.raises(ValueError, match="candidate documents unavailable"):
        CandidateEnvelopeFragment(
            source.scope.candidate_id, source.scope.position_candidate_id,
            context.context_version_id, (), (), "candidate",
        )


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
    with pytest.raises(HrTaskContextError, match="candidate context unavailable"):
        HrTaskContextProvider(
            candidate_scope, candidate_provider=FailingCandidateProvider()
        ).build_for_turn(owner_id, conversation_id, turn_id)


def test_new_official_facts_do_not_invalidate_confirmed_internal_context() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    newer_official = replace(
        official,
        official_position_version_id=uuid4(),
        content_hash="c" * 64,
        source_version="sync-v2",
    )
    source = Source(HrTaskScope(
        owner_id, position_id, conversation_id, turn_id, "jd", newer_official,
        context, (material,), None, None,
    ))

    envelope = HrTaskContextProvider(source).build_for_turn(
        owner_id, conversation_id, turn_id
    )

    assert envelope.official_version_id == newer_official.official_position_version_id
    assert envelope.context_version_id == context.context_version_id
    assert context.official_version_id == official.official_position_version_id

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
                "delete from platform_hr.position_task_requests "
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
    turn_request_id = uuid4()
    started = repository.start(
        owner_id, turn_request_id, "请生成岗位说明",
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
    with psycopg.connect(environment["admin"]) as connection:
        implicit = connection.execute(
            "select task_kind,status from platform_hr.position_task_requests "
            "where owner_internal_user_id=%s and client_request_id=%s",
            (owner_id, turn_request_id),
        ).fetchone()
    assert implicit == ("freeform", "consumed")


@pytest.mark.postgres
def test_task_record_sql_rejects_cross_position_official_and_nonexact_turn_inputs(
    conversation_database, repository, request,
) -> None:
    environment, owner_id, _ = conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    target = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "目标岗位")
    )
    official_position_id = uuid4()
    now = datetime.now(UTC)
    positions.project_official(ProjectOfficialPosition(
        owner_id, official_position_id, uuid4(), "J11016", "其他岗位", None,
        ("深圳",), "active", "sync-v1", "a" * 64, now,
    ))
    intelligence = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    official = intelligence.project_official_version(ProjectOfficialVersion(
        uuid4(), owner_id, official_position_id, uuid4(), "J11016", "其他岗位",
        None, ("深圳",), "研发", None, 0, None, "全职", "面议", "Duty",
        "Requirement", "sync-v1", now, "a" * 64, now, now, "active",
        "published", {},
    ))
    client_request_id = uuid4()
    intelligence.create_task_request(CreatePositionTaskRequest(
        uuid4(), owner_id, target.position_id, client_request_id,
        "f" * 64, "freeform", None,
    ))
    started = repository.start(
        owner_id, client_request_id, "执行目标岗位任务",
        mode="direct_agent", direct_agent_id="hr-bot",
    )
    positions.bind_conversation(BindPositionConversation(
        owner_id, target.position_id, started.conversation.conversation_id,
        uuid4(), "created_in_position",
    ))
    statement = (
        "select (platform_hr.create_position_task_record_v69("
        "%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,%s::uuid[],%s::uuid[],"
        "%s,%s,%s,%s)).*"
    )
    values = (
        uuid4(), owner_id, target.position_id, client_request_id, "freeform",
        official.official_position_version_id, None, [], None, None, [], [],
        started.conversation.conversation_id, started.turn.turn_id,
        "target prompt", "1" * 64,
    )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as connection:
        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(statement, values)
    with psycopg.connect(environment["urls"]["platform_control_app"]) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(statement, (*values[:4], "jd", None, *values[6:]))

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "delete from platform_hr.position_task_requests "
            "where owner_internal_user_id=%s and client_request_id=%s",
            (owner_id, client_request_id),
        )
    with psycopg.connect(environment["urls"]["platform_control_app"]) as connection:
        with pytest.raises(psycopg.errors.NoDataFound):
            connection.execute(statement, (*values[:4], "jd", None, *values[6:]))
    intelligence.create_task_request(CreatePositionTaskRequest(
        uuid4(), owner_id, target.position_id, client_request_id,
        "f" * 64, "freeform", None,
    ))

    attachment_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,object_ref_ciphertext,"
            "object_ref_key_version,immutable_locator,size_bytes,sha256,retained_until,"
            "state,ready_at) values (%s,%s,%s,'user_input',%s,1,%s,1,'version:v1',1,%s,"
            "now()+interval '1 day','ready',now())",
            (attachment_id, owner_id, started.conversation.conversation_id,
             b"x" * 29, b"y" * 29, b"z" * 32),
        )
        connection.execute(
            "insert into platform_attachments.bindings("
            "binding_id,attachment_id,owner_internal_user_id,kind,conversation_id,turn_id) "
            "values (%s,%s,%s,'turn_input',%s,%s)",
            (uuid4(), attachment_id, owner_id,
             started.conversation.conversation_id, started.turn.turn_id),
        )
    def cleanup():
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "delete from platform_attachments.bindings where attachment_id=%s",
                (attachment_id,),
            )
            connection.execute(
                "delete from platform_attachments.attachments where attachment_id=%s",
                (attachment_id,),
            )
            connection.execute(
                "delete from platform_hr.position_conversations where conversation_id=%s",
                (started.conversation.conversation_id,),
            )
    request.addfinalizer(cleanup)
    exact_values = (*values[:5], None, *values[6:])
    with psycopg.connect(environment["urls"]["platform_control_app"]) as connection:
        with pytest.raises(psycopg.errors.NoDataFound):
            connection.execute(statement, exact_values)
