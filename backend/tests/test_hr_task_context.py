from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from test_agent_brain_conversation_repository import (
    conversation_database,  # noqa: F401
    repository,  # noqa: F401
)
from test_control_plane_migration import control_database  # noqa: F401
from test_hr_task_result_projection_database import _seed_candidate_scope

from app.hr.candidate_context import CandidateEnvelopeProvider
from app.hr.candidate_repository import CandidateRepository
from app.hr.models import (
    BindPositionConversation,
    CreateManualPosition,
    ProjectOfficialPosition,
    PromotePositionMaterial,
)
from app.hr.position_intelligence_models import (
    CreatePositionTaskRequest,
    OfficialPositionVersion,
    PositionContextVersion,
    ProjectOfficialVersion,
    candidate_task_snapshot_sha256,
)
from app.hr.position_intelligence_repository import PositionIntelligenceRepository
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
        uuid4(),
        owner_id,
        position_id,
        "J11014",
        "算法工程师",
        "机器人",
        ("深圳",),
        "研发",
        "算法类",
        1,
        "本科",
        "全职",
        "20K-30K",
        "Build the system.",
        "Test the system.",
        "sync-v1",
        now,
        "a" * 64,
        now,
        now,
        "active",
        "published",
        {"snapshot": "sync-v1"},
        now,
    )
    context = PositionContextVersion(
        uuid4(),
        owner_id,
        position_id,
        1,
        "confirmed",
        {"mission": {"text": "Deliver perception"}},
        "Confirmed context",
        official.official_position_version_id,
        None,
        None,
        None,
        None,
        (),
        "hr-bot",
        "model-v1",
        owner_id,
        owner_id,
        now,
        now,
        1,
    )
    material = HrTaskMaterial(
        uuid4(),
        position_id,
        "b" * 64,
        "ready",
        True,
        now + timedelta(days=1),
        False,
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


@dataclass(frozen=True)
class ExternalCandidateFragment:
    candidate_id: object
    position_candidate_id: object
    context_version_id: object
    document_ids: object
    document_attachment_ids: object
    human_feedback_ids: object
    prompt_context: object


class CandidateProvider:
    def __init__(self):
        self.calls = []

    def for_task(self, owner_id, position_id, candidate_id, position_candidate_id):
        self.calls.append((owner_id, position_id, candidate_id, position_candidate_id))
        document_id = uuid4()
        return ExternalCandidateFragment(
            candidate_id=candidate_id,
            position_candidate_id=position_candidate_id,
            context_version_id=self.context_version_id,
            document_ids=(document_id,),
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
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    source = Source(
        HrTaskScope(
            owner_id,
            position_id,
            conversation_id,
            turn_id,
            "candidate_match",
            official,
            context,
            (material,),
            candidate_id,
            position_candidate_id,
        )
    )
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
    assert {item.selected_reason for item in envelope.context_references} >= {
        "official_position_baseline",
        "confirmed_position_context",
        "selected_position_material",
        "candidate_snapshot",
    }
    assert next(
        item for item in envelope.context_references
        if item.selected_reason == "official_position_baseline"
    ).content_sha256 == official.content_hash
    assert "Build the system." in envelope.prompt_context
    assert "Candidate evidence" in envelope.prompt_context
    prompt = __import__("json").loads(envelope.prompt_context)
    assert prompt["official_facts"]["category"] == "研发"
    assert prompt["official_facts"]["headcount"] == 1
    assert prompt["official_facts"]["status_reason"] == "published"
    assert len(source.recorded) == 1


def test_persisted_candidate_snapshot_is_used_without_mutable_provider_read() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    candidate_id, relation_id = uuid4(), uuid4()
    document_id, attachment_id, feedback_id = uuid4(), uuid4(), uuid4()
    prompt_context = "accepted candidate evidence"
    source = Source(HrTaskScope(
        owner_id, position_id, conversation_id, turn_id, "candidate_match",
        official, context, (material,), candidate_id, relation_id,
        client_request_id=uuid4(), candidate_document_ids=(document_id,),
        candidate_document_attachment_ids=(attachment_id,),
        candidate_human_feedback_ids=(feedback_id,),
        candidate_prompt_context=prompt_context,
        candidate_snapshot_sha256=candidate_task_snapshot_sha256(
            candidate_id=candidate_id, position_candidate_id=relation_id,
            context_version_id=context.context_version_id,
            document_ids=(document_id,), document_attachment_ids=(attachment_id,),
            human_feedback_ids=(feedback_id,), prompt_context=prompt_context,
        ),
    ))

    envelope = HrTaskContextProvider(
        source, candidate_provider=FailingCandidateProvider()
    ).build_for_turn(owner_id, conversation_id, turn_id)

    assert envelope.document_attachment_ids == (attachment_id,)
    assert envelope.human_feedback_ids == (feedback_id,)
    assert prompt_context in envelope.prompt_context


def test_persisted_candidate_snapshot_fails_closed_when_legacy_or_tampered() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    base = HrTaskScope(
        owner_id, position_id, conversation_id, turn_id, "candidate_match",
        official, context, (material,), uuid4(), uuid4(),
        client_request_id=uuid4(),
    )
    with pytest.raises(HrTaskContextError, match="snapshot unavailable"):
        HrTaskContextProvider(Source(base)).build_for_turn(
            owner_id, conversation_id, turn_id
        )
    tampered = replace(
        base,
        candidate_document_ids=(uuid4(),),
        candidate_document_attachment_ids=(uuid4(),),
        candidate_prompt_context="persisted",
        candidate_snapshot_sha256="f" * 64,
    )
    with pytest.raises(HrTaskContextError, match="snapshot invalid"):
        HrTaskContextProvider(Source(tampered)).build_for_turn(
            owner_id, conversation_id, turn_id
        )


def test_candidate_tasks_require_exact_confirmed_context_and_documents() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    source = Source(
        HrTaskScope(
            owner_id,
            position_id,
            conversation_id,
            turn_id,
            "candidate_match",
            official,
            context,
            (material,),
            uuid4(),
            uuid4(),
        )
    )
    candidate = CandidateProvider()
    candidate.context_version_id = uuid4()
    with pytest.raises(HrTaskContextError, match="candidate context scope invalid"):
        HrTaskContextProvider(source, candidate_provider=candidate).build_for_turn(
            owner_id, conversation_id, turn_id
        )

    candidate.context_version_id = context.context_version_id
    candidate.for_task = lambda *args: ExternalCandidateFragment(  # type: ignore[method-assign]
        args[2], args[3], context.context_version_id, (), (), (), "candidate"
    )
    with pytest.raises(HrTaskContextError, match="candidate documents unavailable"):
        HrTaskContextProvider(source, candidate_provider=candidate).build_for_turn(
            owner_id, conversation_id, turn_id
        )


def test_candidate_fragment_rejects_structurally_invalid_fields() -> None:
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    candidate_id, position_candidate_id = uuid4(), uuid4()
    source = Source(
        HrTaskScope(
            owner_id,
            position_id,
            conversation_id,
            turn_id,
            "candidate_match",
            official,
            context,
            (material,),
            candidate_id,
            position_candidate_id,
        )
    )

    class InvalidProvider:
        def for_task(self, *_args):
            return ExternalCandidateFragment(
                candidate_id,
                position_candidate_id,
                context.context_version_id,
                ("not-a-uuid",),
                (uuid4(),),
                (),
                "candidate",
            )

    with pytest.raises(HrTaskContextError, match="candidate context scope invalid"):
        HrTaskContextProvider(
            source, candidate_provider=InvalidProvider()
        ).build_for_turn(owner_id, conversation_id, turn_id)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"state": "quarantined"}, "material unavailable"),
        ({"active": False}, "material unavailable"),
        (
            {"retained_until": datetime.now(UTC) - timedelta(seconds=1)},
            "material unavailable",
        ),
        ({"erasure_pending": True}, "material unavailable"),
        ({"position_id": uuid4()}, "material scope invalid"),
    ],
)
def test_provider_rejects_unready_expired_erased_and_cross_position_materials(
    overrides,
    message,
) -> None:
    owner_id, position_id, official, context, material = _records()
    material = replace(material, **overrides)
    conversation_id, turn_id = uuid4(), uuid4()
    source = Source(
        HrTaskScope(
            owner_id,
            position_id,
            conversation_id,
            turn_id,
            "jd",
            official,
            context,
            (material,),
            None,
            None,
        )
    )

    with pytest.raises(HrTaskContextError, match=message):
        HrTaskContextProvider(source).build_for_turn(owner_id, conversation_id, turn_id)
    assert source.recorded == []


def test_provider_fails_closed_on_scope_mismatch_or_missing_candidate_provider() -> (
    None
):
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    mismatched = Source(
        HrTaskScope(
            uuid4(),
            position_id,
            conversation_id,
            turn_id,
            "jd",
            official,
            context,
            (material,),
            None,
            None,
        )
    )
    with pytest.raises(HrTaskContextError, match="scope invalid"):
        HrTaskContextProvider(mismatched).build_for_turn(
            owner_id, conversation_id, turn_id
        )
    candidate_scope = Source(
        HrTaskScope(
            owner_id,
            position_id,
            conversation_id,
            turn_id,
            "candidate_match",
            official,
            context,
            (material,),
            uuid4(),
            uuid4(),
        )
    )
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
    source = Source(
        HrTaskScope(
            owner_id,
            position_id,
            conversation_id,
            turn_id,
            "jd",
            newer_official,
            context,
            (material,),
            None,
            None,
        )
    )

    envelope = HrTaskContextProvider(source).build_for_turn(
        owner_id, conversation_id, turn_id
    )

    assert envelope.official_version_id == newer_official.official_position_version_id
    assert envelope.context_version_id == context.context_version_id
    assert context.official_version_id == official.official_position_version_id


def test_recovery_returns_the_recorded_envelope_without_rereading_newer_context() -> (
    None
):
    owner_id, position_id, official, context, material = _records()
    conversation_id, turn_id = uuid4(), uuid4()
    source = Source(
        HrTaskScope(
            owner_id,
            position_id,
            conversation_id,
            turn_id,
            "jd",
            official,
            context,
            (material,),
            None,
            None,
        )
    )
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
    conversation_database,  # noqa: F811
    repository,  # noqa: F811
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
                "delete from platform_hr.positions where owner_internal_user_id=%s",
                (owner_id,),
            )

    request.addfinalizer(cleanup)
    turn_request_id = uuid4()
    started = repository.start(
        owner_id,
        turn_request_id,
        "请生成岗位说明",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    positions.bind_conversation(
        BindPositionConversation(
            owner_id,
            position.position_id,
            started.conversation.conversation_id,
            uuid4(),
            "created_in_position",
        )
    )
    source = PostgresHrTaskContextSource(
        environment["urls"]["platform_control_app"],
        execution_model_version="hr-runtime-v1",
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
        stored = connection.execute(
            "select count(*),min(execution_model_version) "
            "from platform_hr.position_task_records "
            "where owner_internal_user_id=%s and turn_id=%s",
            (owner_id, started.turn.turn_id),
        ).fetchone()
    assert stored == (1, "hr-runtime-v1")
    upgraded = PostgresHrTaskContextSource(
        environment["urls"]["platform_control_app"],
        execution_model_version="hr-runtime-v2",
    )
    with pytest.raises(HrTaskContextError, match="model snapshot mismatch"):
        upgraded.existing_for_turn(
            owner_id, started.conversation.conversation_id, started.turn.turn_id
        )
    with pytest.raises(HrTaskContextError, match="could not be recorded"):
        upgraded.record_for_turn(
            owner_id, started.conversation.conversation_id, started.turn.turn_id, first
        )
    with psycopg.connect(environment["admin"]) as connection:
        implicit = connection.execute(
            "select task_kind,status from platform_hr.position_task_requests "
            "where owner_internal_user_id=%s and client_request_id=%s",
            (owner_id, turn_request_id),
        ).fetchone()
    assert implicit == ("freeform", "consumed")


@pytest.mark.postgres
def test_explicit_task_uses_owned_position_material_from_another_conversation(
    conversation_database,  # noqa: F811
    repository,  # noqa: F811
    request,
) -> None:
    environment, owner_id, _ = conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "跨会话材料岗位")
    )
    source = repository.start(
        owner_id,
        uuid4(),
        "上传岗位材料",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    target_request_id = uuid4()
    target = repository.start(
        owner_id,
        target_request_id,
        "基于已选材料生成岗位说明",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    for started in (source, target):
        positions.bind_conversation(
            BindPositionConversation(
                owner_id,
                position.position_id,
                started.conversation.conversation_id,
                uuid4(),
                "created_in_position",
            )
        )
    attachment_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,object_ref_ciphertext,"
            "object_ref_key_version,immutable_locator,size_bytes,sha256,retained_until,"
            "state,ready_at) values (%s,%s,%s,'user_input',%s,1,%s,1,'version:v1',1,%s,"
            "now()+interval '1 day','ready',now())",
            (
                attachment_id,
                owner_id,
                source.conversation.conversation_id,
                b"x" * 29,
                b"y" * 29,
                b"z" * 32,
            ),
        )
    positions.promote_material(
        PromotePositionMaterial(owner_id, position.position_id, attachment_id, uuid4())
    )
    PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    ).create_task_request(
        CreatePositionTaskRequest(
            uuid4(),
            owner_id,
            position.position_id,
            target_request_id,
            "a" * 64,
            "jd",
            None,
            (attachment_id,),
        )
    )

    def cleanup() -> None:
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
                "delete from platform_hr.position_materials where attachment_id=%s",
                (attachment_id,),
            )
            connection.execute(
                "delete from platform_attachments.attachments where attachment_id=%s",
                (attachment_id,),
            )
            connection.execute(
                "delete from platform_hr.position_conversations "
                "where owner_internal_user_id=%s",
                (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.positions where owner_internal_user_id=%s",
                (owner_id,),
            )

    request.addfinalizer(cleanup)
    envelope = HrTaskContextProvider(
        PostgresHrTaskContextSource(
            environment["urls"]["platform_control_app"],
            execution_model_version="hr-runtime-v1",
        )
    ).build_for_turn(owner_id, target.conversation.conversation_id, target.turn.turn_id)

    assert envelope.task_kind == "jd"
    assert envelope.material_attachment_ids == (attachment_id,)


@pytest.mark.postgres
def test_candidate_snapshot_business_order_survives_request_context_and_record(
    conversation_database,  # noqa: F811
    repository,  # noqa: F811
    request,
) -> None:
    environment, owner_id, _ = conversation_database
    app_url = environment["urls"]["platform_control_app"]
    positions = HrPositionRepository(app_url)
    position = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "候选顺序岗位")
    )
    scope = _seed_candidate_scope(environment, owner_id, position.position_id)
    second_attachment = UUID("00000000-0000-0000-0000-000000000001")
    second_document = UUID("00000000-0000-0000-0000-000000000002")
    older_feedback = UUID("00000000-0000-0000-0000-000000000003")
    newer_feedback = UUID("ffffffff-ffff-4fff-8fff-fffffffffff3")
    analysis_id = uuid4()

    def cleanup() -> None:
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute("set local session_replication_role=replica")
            connection.execute(
                "delete from platform_hr.position_task_records "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_task_requests "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_binding_events "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_conversations "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.human_feedback "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidate_analysis_versions "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_candidates "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidate_documents "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidates "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidate_drafts "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.candidate_draft_batches "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_attachments.attachments "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "update platform_hr.positions set current_context_version_id=null "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.position_context_versions "
                "where owner_internal_user_id=%s", (owner_id,),
            )
            connection.execute(
                "delete from platform_hr.positions where owner_internal_user_id=%s",
                (owner_id,),
            )

    request.addfinalizer(cleanup)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,immutable_locator,"
            "sha256,state,ready_at) values (%s,%s,'user_input',%s,1,%s,1,"
            "'etag:second',%s,'ready',now())",
            (second_attachment, owner_id, b"n" * 29, b"o" * 29, b"s" * 32),
        )
        connection.execute(
            "insert into platform_hr.candidate_documents("
            "document_id,owner_internal_user_id,candidate_id,attachment_id,"
            "source_draft_id,document_kind,version_number,content_sha256) values ("
            "%s,%s,%s,%s,%s,'resume',2,%s)",
            (second_document, owner_id, scope["candidate"], second_attachment,
             scope["draft"], "b" * 64),
        )
        connection.execute(
            "insert into platform_hr.candidate_analysis_versions("
            "analysis_version_id,owner_internal_user_id,position_candidate_id,"
            "position_id,candidate_id,context_version_id,client_request_id,"
            "version_number,analysis_kind,result,agent_version,model_version) "
            "values (%s,%s,%s,%s,%s,%s,%s,1,'match','{}','agent','model')",
            (analysis_id, owner_id, scope["relation"], position.position_id,
             scope["candidate"], scope["context"], uuid4()),
        )
        for feedback_id, created_at, conclusion in (
            (older_feedback, "2026-01-01T00:00:00Z", "older"),
            (newer_feedback, "2026-01-02T00:00:00Z", "newer"),
        ):
            connection.execute(
                "insert into platform_hr.human_feedback("
                "feedback_id,owner_internal_user_id,position_candidate_id,"
                "analysis_version_id,client_request_id,feedback_kind,"
                "conclusion_key,reason,canonical_payload,payload_sha256,created_at) "
                "values (%s,%s,%s,%s,%s,'accepted',%s,'ordered','{}',"
                "sha256(convert_to('{}','UTF8')),%s)",
                (feedback_id, owner_id, scope["relation"], analysis_id, uuid4(),
                 conclusion, created_at),
            )
    candidate_provider = CandidateEnvelopeProvider(
        CandidateRepository(app_url),
        lambda selected_owner, selected_position, selected_context: (
            selected_owner == owner_id and selected_position == position.position_id
            and selected_context == scope["context"]
        ),
    )
    fragment = candidate_provider.for_task(
        owner_id, position.position_id, scope["candidate"], scope["relation"]
    )
    assert fragment.document_attachment_ids == (
        scope["attachment"], second_attachment,
    )
    assert fragment.document_attachment_ids != tuple(sorted(
        fragment.document_attachment_ids, key=str,
    ))
    assert fragment.human_feedback_ids == (newer_feedback, older_feedback)
    assert fragment.human_feedback_ids != tuple(sorted(
        fragment.human_feedback_ids, key=str,
    ))
    client_request_id = uuid4()
    created = PositionIntelligenceRepository(app_url).create_task_request(
        CreatePositionTaskRequest(
            uuid4(), owner_id, position.position_id, client_request_id,
            "a" * 64, "candidate_match", scope["context"], (),
            scope["candidate"], scope["relation"], fragment.document_ids,
            fragment.document_attachment_ids, fragment.human_feedback_ids,
            fragment.prompt_context,
        )
    )
    started = repository.start(
        owner_id, client_request_id, "保持候选证据业务顺序",
        mode="direct_agent", direct_agent_id="hr-bot",
    )
    positions.bind_conversation(BindPositionConversation(
        owner_id, position.position_id, started.conversation.conversation_id,
        uuid4(), "created_in_position",
    ))
    envelope = HrTaskContextProvider(PostgresHrTaskContextSource(
        app_url, execution_model_version="hr-runtime-order-v1",
    )).build_for_turn(
        owner_id, started.conversation.conversation_id, started.turn.turn_id,
    )
    assert envelope.document_attachment_ids == fragment.document_attachment_ids
    assert envelope.human_feedback_ids == fragment.human_feedback_ids
    expected_snapshot_hash = candidate_task_snapshot_sha256(
        candidate_id=scope["candidate"],
        position_candidate_id=scope["relation"],
        context_version_id=scope["context"], document_ids=fragment.document_ids,
        document_attachment_ids=fragment.document_attachment_ids,
        human_feedback_ids=fragment.human_feedback_ids,
        prompt_context=fragment.prompt_context,
    )
    with psycopg.connect(environment["admin"]) as connection:
        recorded = connection.execute(
            "select record.document_attachment_ids,record.human_feedback_ids,"
            "request.candidate_snapshot_sha256 from platform_hr.position_task_records "
            "record join platform_hr.position_task_requests request on "
            "request.owner_internal_user_id=record.owner_internal_user_id and "
            "request.client_request_id=record.client_request_id where "
            "record.owner_internal_user_id=%s and record.turn_id=%s",
            (owner_id, started.turn.turn_id),
        ).fetchone()
    assert recorded == (
        list(fragment.document_attachment_ids),
        list(fragment.human_feedback_ids),
        expected_snapshot_hash,
    )
    assert created.candidate_snapshot_sha256 == expected_snapshot_hash


@pytest.mark.postgres
def test_task_record_sql_rejects_cross_position_official_and_nonexact_turn_inputs(
    conversation_database,  # noqa: F811
    repository,  # noqa: F811
    request,
) -> None:
    environment, owner_id, _ = conversation_database
    positions = HrPositionRepository(environment["urls"]["platform_control_app"])
    target = positions.create_manual(
        CreateManualPosition(owner_id, uuid4(), uuid4(), "目标岗位")
    )
    official_position_id = uuid4()
    now = datetime.now(UTC)
    positions.project_official(
        ProjectOfficialPosition(
            owner_id,
            official_position_id,
            uuid4(),
            "J11016",
            "其他岗位",
            None,
            ("深圳",),
            "active",
            "sync-v1",
            "a" * 64,
            now,
        )
    )
    intelligence = PositionIntelligenceRepository(
        environment["urls"]["platform_control_app"]
    )
    official = intelligence.project_official_version(
        ProjectOfficialVersion(
            uuid4(),
            owner_id,
            official_position_id,
            uuid4(),
            "J11016",
            "其他岗位",
            None,
            ("深圳",),
            "研发",
            None,
            0,
            None,
            "全职",
            "面议",
            "Duty",
            "Requirement",
            "sync-v1",
            now,
            "a" * 64,
            now,
            now,
            "active",
            "published",
            {},
        )
    )
    client_request_id = uuid4()
    intelligence.create_task_request(
        CreatePositionTaskRequest(
            uuid4(),
            owner_id,
            target.position_id,
            client_request_id,
            "f" * 64,
            "freeform",
            None,
        )
    )
    started = repository.start(
        owner_id,
        client_request_id,
        "执行目标岗位任务",
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    positions.bind_conversation(
        BindPositionConversation(
            owner_id,
            target.position_id,
            started.conversation.conversation_id,
            uuid4(),
            "created_in_position",
        )
    )
    statement = (
        "select (platform_hr.create_position_task_record_v78("
        "%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,%s::uuid[],%s::uuid[],"
        "%s,%s,%s,%s,%s)).*"
    )
    values = (
        uuid4(),
        owner_id,
        target.position_id,
        client_request_id,
        "freeform",
        official.official_position_version_id,
        None,
        [],
        None,
        None,
        [],
        [],
        started.conversation.conversation_id,
        started.turn.turn_id,
        "target prompt",
        "1" * 64,
        "hr-runtime-v1",
    )
    with (
        psycopg.connect(environment["urls"]["platform_control_app"]) as connection,
        pytest.raises(psycopg.errors.SerializationFailure),
    ):
        connection.execute(statement, values)
    with (
        psycopg.connect(environment["urls"]["platform_control_app"]) as connection,
        pytest.raises(psycopg.errors.UniqueViolation),
    ):
        connection.execute(statement, (*values[:4], "jd", None, *values[6:]))

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "delete from platform_hr.position_task_requests "
            "where owner_internal_user_id=%s and client_request_id=%s",
            (owner_id, client_request_id),
        )
    with (
        psycopg.connect(environment["urls"]["platform_control_app"]) as connection,
        pytest.raises(psycopg.errors.NoDataFound),
    ):
        connection.execute(statement, (*values[:4], "jd", None, *values[6:]))
    intelligence.create_task_request(
        CreatePositionTaskRequest(
            uuid4(),
            owner_id,
            target.position_id,
            client_request_id,
            "f" * 64,
            "freeform",
            None,
        )
    )

    attachment_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,object_ref_ciphertext,"
            "object_ref_key_version,immutable_locator,size_bytes,sha256,retained_until,"
            "state,ready_at) values (%s,%s,%s,'user_input',%s,1,%s,1,'version:v1',1,%s,"
            "now()+interval '1 day','ready',now())",
            (
                attachment_id,
                owner_id,
                started.conversation.conversation_id,
                b"x" * 29,
                b"y" * 29,
                b"z" * 32,
            ),
        )
        connection.execute(
            "insert into platform_attachments.bindings("
            "binding_id,attachment_id,owner_internal_user_id,kind,conversation_id,turn_id) "
            "values (%s,%s,%s,'turn_input',%s,%s)",
            (
                uuid4(),
                attachment_id,
                owner_id,
                started.conversation.conversation_id,
                started.turn.turn_id,
            ),
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
    with (
        psycopg.connect(environment["urls"]["platform_control_app"]) as connection,
        pytest.raises(psycopg.errors.NoDataFound),
    ):
        connection.execute(statement, exact_values)
