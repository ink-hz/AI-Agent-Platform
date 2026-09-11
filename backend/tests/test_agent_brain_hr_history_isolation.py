"""D1 regressions: disposable PostgreSQL and fictional candidate data only."""
from __future__ import annotations

import json
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_context import _complete_mission
from test_agent_brain_conversation_repository import conversation_database, repository  # noqa: F401
from test_control_plane_migration import control_database  # noqa: F401
from test_hr_task_result_projection_database import _seed_candidate_scope

from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_repository import ConversationRepositoryNotFound
from app.agent_brain.conversation_service import ConversationCommandService
from app.hr.candidate_context import CandidateEnvelopeProvider
from app.hr.candidate_repository import CandidateRepository
from app.hr.context import HrPositionScope
from app.hr.models import CreateManualPosition
from app.hr.position_intelligence_repository import PositionIntelligenceRepository
from app.hr.position_intelligence_service import PositionIntelligenceService
from app.hr.repository import HrPositionRepository
from app.hr.task_context import HrTaskContextProvider, PostgresHrTaskContextSource
from app.hr.task_repository import PostgresHrPositionTaskRepository
from app.hr.task_service import HrPositionTaskNotFound, HrPositionTaskService


A_FACT = "虚构甲独有经历：紫铜海鸥项目七次冷启动"
A_ANALYSIS = "虚构甲助手分析：紫铜海鸥贡献尚待验证"
B_FACT = "虚构乙独有经历：青石机器人量产"


def _complete(environment, repository, conversation_id, turn_id, text):
    # Local model-result fixture; projection itself is the real repository path.
    with psycopg.connect(environment["admin"]) as connection:
        mission_id = connection.execute(
            "select mission_id from platform_control.conversation_turns where turn_id=%s",
            (turn_id,),
        ).fetchone()[0]
    _complete_mission(environment, repository, mission_id, text)
    assert ConversationProjection(repository).project_terminal(mission_id)


@pytest.fixture
def candidate_tasks(conversation_database, repository):
    environment, owner, other_owner = conversation_database
    url = environment["urls"]["platform_control_app"]
    positions = HrPositionRepository(url)
    position = positions.create_manual(CreateManualPosition(owner, uuid4(), uuid4(), "虚构结构岗位"))
    a = _seed_candidate_scope(environment, owner, position.position_id)
    b = {name: uuid4() for name in ("candidate", "relation", "document", "attachment", "batch", "draft")}
    b["context"] = a["context"]
    with psycopg.connect(environment["admin"]) as connection:
        # Fixture object seeding only: real task intake/context checks run below.
        connection.execute(
            "insert into platform_attachments.attachments(attachment_id,owner_internal_user_id,"
            "source_kind,original_name_ciphertext,original_name_key_version,object_ref_ciphertext,"
            "object_ref_key_version,immutable_locator,sha256,state,ready_at) values "
            "(%s,%s,'user_input',%s,1,%s,1,%s,%s,'ready',now())",
            (b["attachment"], owner, b"n" * 29, b"o" * 29, f"etag:fiction-{b['attachment']}", b"b" * 32),
        )
        connection.execute(
            "insert into platform_hr.candidate_draft_batches(batch_request_id,owner_internal_user_id,"
            "position_id,attachment_ids) values(%s,%s,%s,array[%s]::uuid[])",
            (b["batch"], owner, position.position_id, b["attachment"]),
        )
        connection.execute(
            "insert into platform_hr.candidate_drafts(draft_id,owner_internal_user_id,position_id,"
            "attachment_id,batch_request_id,client_request_id,state,extracted_facts,row_version) "
            "values(%s,%s,%s,%s,%s,%s,'confirmed','{}',2)",
            (b["draft"], owner, position.position_id, b["attachment"], b["batch"], uuid4()),
        )
        connection.execute(
            "insert into platform_hr.candidates(candidate_id,owner_internal_user_id,"
            "confirmation_request_id,stable_name,facts) values(%s,%s,%s,%s,'{}')",
            (b["candidate"], owner, uuid4(), B_FACT),
        )
        connection.execute(
            "update platform_hr.candidates set stable_name=%s where candidate_id=%s",
            (A_FACT, a["candidate"]),
        )
        connection.execute(
            "insert into platform_hr.candidate_documents(document_id,owner_internal_user_id,candidate_id,"
            "attachment_id,source_draft_id,document_kind,version_number,content_sha256) "
            "values(%s,%s,%s,%s,%s,'resume',1,%s)",
            (b["document"], owner, b["candidate"], b["attachment"], b["draft"], "b" * 64),
        )
        connection.execute(
            "insert into platform_hr.position_candidates(position_candidate_id,owner_internal_user_id,"
            "position_id,candidate_id,context_version_id,source_draft_id,client_request_id) "
            "values(%s,%s,%s,%s,%s,%s,%s)",
            (b["relation"], owner, position.position_id, b["candidate"], a["context"], b["draft"], uuid4()),
        )
    candidates = CandidateEnvelopeProvider(
        CandidateRepository(url),
        lambda selected_owner, selected_position, selected_context: (
            selected_owner == owner and selected_position == position.position_id
            and selected_context == a["context"]
        ),
    )
    tasks = HrPositionTaskService(
        PositionIntelligenceService(PositionIntelligenceRepository(url)),
        ConversationCommandService(repository, v2_enabled=True),
        HrPositionScope(positions), PostgresHrPositionTaskRepository(url),
        candidate_validator=candidates,
    )
    builder = ConversationContextBuilder(
        repository,
        hr_task_context_provider=HrTaskContextProvider(
            PostgresHrTaskContextSource(url, execution_model_version="local-d1-fixture"),
            candidate_provider=candidates,
        ),
    )
    try:
        yield tasks, builder, position.position_id, a, b
    finally:
        with psycopg.connect(environment["admin"]) as connection:
            for table in (
                "position_task_records", "position_task_requests", "position_candidates",
                "candidate_documents", "candidates", "candidate_drafts", "candidate_draft_batches",
            ):
                connection.execute(f"delete from platform_hr.{table} where owner_internal_user_id=%s", (owner,))
            connection.execute("update platform_hr.positions set current_context_version_id=null where position_id=%s", (position.position_id,))
            connection.execute("alter table platform_hr.position_context_versions disable trigger all")
            connection.execute("delete from platform_hr.position_context_versions where owner_internal_user_id=%s", (owner,))
            connection.execute("alter table platform_hr.position_context_versions enable trigger all")
            for table in ("position_binding_events", "position_conversations", "positions"):
                connection.execute(f"delete from platform_hr.{table} where owner_internal_user_id=%s", (owner,))


@pytest.mark.postgres
@pytest.mark.parametrize("build_method", ["build", "build_direct"])
@pytest.mark.parametrize("summarized", [False, True])
def test_same_position_candidate_switch_omits_a_and_keeps_verified_b(
    conversation_database, repository, candidate_tasks, build_method, summarized,
):
    environment, owner, other_owner = conversation_database
    tasks, builder, position, a, b = candidate_tasks

    def start(candidate, conversation_id=None):
        return tasks.start(
            owner_id=owner, position_id=position, request_id=uuid4(), task_kind="candidate_match",
            context_version_id=a["context"], material_ids=(), conversation_id=conversation_id,
            candidate_id=candidate["candidate"], position_candidate_id=candidate["relation"],
        )

    first = start(a)
    with pytest.raises(HrPositionTaskNotFound):
        tasks.start(
            owner_id=other_owner, position_id=position, request_id=uuid4(), task_kind="candidate_match",
            context_version_id=a["context"], material_ids=(), conversation_id=first.conversation_id,
            candidate_id=b["candidate"], position_candidate_id=b["relation"],
        )
    assert A_FACT in builder.build(first.conversation_id, first.turn_id).hr_position_context.prompt_context
    _complete(environment, repository, first.conversation_id, first.turn_id, A_ANALYSIS)
    # A user facts may be in ordinary conversation, not only a structured task.
    followup = repository.append_turn(owner, first.conversation_id, uuid4(), A_FACT)
    _complete(environment, repository, first.conversation_id, followup.turn.turn_id, A_ANALYSIS)
    second = start(b, first.conversation_id)
    if summarized:
        repository.store_summary(first.conversation_id, second.turn_id, 4, A_FACT + "；岗位公共要求；" + A_ANALYSIS)
    context = getattr(builder, build_method)(second.conversation_id, second.turn_id)
    rendered = json.dumps({
        "summary": context.summary,
        "messages": [message.content for message in context.messages],
        "position": context.hr_position_context.prompt_context,
    }, ensure_ascii=False)
    assert A_FACT not in rendered
    assert A_ANALYSIS not in rendered
    assert context.summary is None
    assert len(context.messages) == 1
    assert "候选人材料生成匹配分析" in context.messages[0].content
    assert B_FACT in rendered
    assert context.hr_position_context.candidate_id == b["candidate"]
    assert context.hr_position_context.context_version_id == a["context"]
    assert context.hr_position_context.document_attachment_ids == (b["attachment"],)
    assert a["attachment"] not in context.active_attachment_ids


@pytest.mark.postgres
@pytest.mark.parametrize("summarized", [False, True])
@pytest.mark.parametrize("current", [
    B_FACT + "，请评估乙。",
    "讨论通用岗位要求。",
    "明确比较甲乙的量产经历，仅使用本轮材料：甲=铜舟设计；乙=石桥调试。",
])
def test_unbound_hr_and_explicit_comparison_never_unlock_unproven_history(
    conversation_database, repository, summarized, current,
):
    environment, owner, _ = conversation_database
    first = repository.start(owner, uuid4(), A_FACT, mode="direct_agent", direct_agent_id="hr-bot")
    _complete(environment, repository, first.conversation.conversation_id, first.turn.turn_id, A_ANALYSIS)
    second = repository.append_turn(owner, first.conversation.conversation_id, uuid4(), current)
    if summarized:
        repository.store_summary(first.conversation.conversation_id, second.turn.turn_id, 2, A_FACT + "；通用岗位摘要")
    builder = ConversationContextBuilder(repository)
    for build in (builder.build, builder.build_direct):
        context = build(first.conversation.conversation_id, second.turn.turn_id)
        assert context.summary is None
        assert [message.content for message in context.messages] == [current]
    assert builder.compaction_candidate(first.conversation.conversation_id, second.turn.turn_id) is None


@pytest.mark.postgres
def test_hr_does_not_send_large_old_history_to_compaction(conversation_database, repository):
    environment, owner, _ = conversation_database
    first = repository.start(owner, uuid4(), A_FACT + "甲" * 10000, mode="direct_agent", direct_agent_id="hr-bot")
    _complete(environment, repository, first.conversation.conversation_id, first.turn.turn_id, A_ANALYSIS + "甲" * 2000)
    second = repository.append_turn(owner, first.conversation.conversation_id, uuid4(), B_FACT + "乙" * 10000)
    assert ConversationContextBuilder(repository).compaction_candidate(first.conversation.conversation_id, second.turn.turn_id) is None


@pytest.mark.postgres
def test_non_hr_preserves_shared_summary_and_owner_rejection(conversation_database, repository):
    environment, owner, other = conversation_database
    first = repository.start(owner, uuid4(), "一般技术问题", mode="direct_agent", direct_agent_id="fae-bot")
    _complete(environment, repository, first.conversation.conversation_id, first.turn.turn_id, "技术回答")
    with pytest.raises(ConversationRepositoryNotFound):
        repository.append_turn(other, first.conversation.conversation_id, uuid4(), "越权请求")
    second = repository.append_turn(owner, first.conversation.conversation_id, uuid4(), "继续技术讨论")
    builder = ConversationContextBuilder(repository)
    context = builder.build_direct(first.conversation.conversation_id, second.turn.turn_id)
    assert [message.content for message in context.messages] == ["一般技术问题", "技术回答", "继续技术讨论"]
    repository.store_summary(first.conversation.conversation_id, second.turn.turn_id, 2, "已确认技术前提")
    context = builder.build_direct(first.conversation.conversation_id, second.turn.turn_id)
    assert context.summary == "已确认技术前提"
    assert [message.content for message in context.messages] == ["继续技术讨论"]
