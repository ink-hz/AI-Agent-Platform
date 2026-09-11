"""D1 regressions against the deployed v6/v7 persisted turn-scope shape."""
from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_context import _complete_mission
from test_control_plane_migration import control_database  # noqa: F401
from test_hr_candidate_database import _seed_candidate_scope
from test_hr_turn_scope_v6_database import repository, scoped_database, submission  # noqa: F401

from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_models import ConversationTurnSubmission
from app.execution_relay.contracts_v6 import HrTurnScope


A_FACT = "虚构甲独有经历：紫铜海鸥项目七次冷启动"
A_ANALYSIS = "虚构甲助手分析：紫铜海鸥贡献尚待验证"
B_FACT = "虚构乙独有经历：青石机器人量产"
MIXED_SUMMARY = A_FACT + "；岗位公共要求；" + A_ANALYSIS


def _complete(environment, repo, turn_id, text):
    with psycopg.connect(environment["admin"]) as connection:
        mission_id = connection.execute(
            "select mission_id from platform_control.conversation_turns where turn_id=%s",
            (turn_id,),
        ).fetchone()[0]
    _complete_mission(environment, repo, mission_id, text)
    assert ConversationProjection(repo).project_terminal(mission_id)


@pytest.mark.postgres
@pytest.mark.parametrize("build_method", ["build", "build_direct"])
@pytest.mark.parametrize("summarized", [False, True])
def test_same_position_candidate_a_history_and_mixed_summary_do_not_cross_into_candidate_b(
    scoped_database, build_method, summarized,
):
    environment = scoped_database
    seeded = _seed_candidate_scope(environment)
    owner = seeded["owner"]
    position = seeded["position"]
    candidate_a, candidate_b, relation_a, relation_b = (uuid4() for _ in range(4))
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_hr.candidates(candidate_id,owner_internal_user_id,"
            "confirmation_request_id,stable_name,facts) values"
            "(%s,%s,%s,'A','{}'),(%s,%s,%s,'B','{}')",
            (candidate_a, owner, uuid4(), candidate_b, owner, uuid4()),
        )
        connection.execute(
            "insert into platform_hr.position_candidates(position_candidate_id,"
            "owner_internal_user_id,position_id,candidate_id,context_version_id,"
            "source_draft_id,client_request_id) values"
            "(%s,%s,%s,%s,%s,%s,%s),(%s,%s,%s,%s,%s,%s,%s)",
            (relation_a, owner, position, candidate_a, seeded["context"], seeded["draft"], uuid4(),
             relation_b, owner, position, candidate_b, seeded["context"], seeded["draft"], uuid4()),
        )
    scope_a = HrTurnScope(positionId=position, positionCandidateIds=(relation_a,), attachmentIds=())
    scope_b = HrTurnScope(positionId=position, positionCandidateIds=(relation_b,), attachmentIds=())
    repo = repository(environment)
    first = repo.start(
        owner, uuid4(), ConversationTurnSubmission(A_FACT, hr_scope=scope_a),
        mode="direct_agent", direct_agent_id="hr-bot",
    )
    _complete(environment, repo, first.turn.turn_id, A_ANALYSIS)
    second = repo.append_turn(
        owner, first.conversation.conversation_id, uuid4(),
        ConversationTurnSubmission(B_FACT, hr_scope=scope_b),
    )
    with psycopg.connect(environment["admin"]) as connection:
        for turn, relation in ((first.turn, relation_a), (second.turn, relation_b)):
            scope = connection.execute(
                "select hr_input_context->'scope' from platform_control.conversation_turns where turn_id=%s",
                (turn.turn_id,),
            ).fetchone()[0]
            assert scope["positionId"] == str(position)
            assert scope["positionCandidateIds"] == [str(relation)]
    if summarized:
        repo.store_summary(first.conversation.conversation_id, second.turn.turn_id, 2, MIXED_SUMMARY)

    context = getattr(ConversationContextBuilder(repo), build_method)(
        second.conversation.conversation_id, second.turn.turn_id
    )
    rendered = "\n".join(message.content for message in context.messages)
    assert context.summary is None
    assert rendered == B_FACT
    assert A_FACT not in rendered
    assert A_ANALYSIS not in rendered


@pytest.mark.postgres
@pytest.mark.parametrize("summarized", [False, True])
def test_unbound_hr_turn_does_not_unlock_prior_unbound_history(scoped_database, summarized):
    environment = scoped_database
    seeded = _seed_candidate_scope(environment)
    owner = seeded["owner"]
    unbound = HrTurnScope(positionId=None, positionCandidateIds=(), attachmentIds=())
    repo = repository(environment)
    first = repo.start(
        owner, uuid4(), ConversationTurnSubmission(A_FACT, hr_scope=unbound),
        mode="direct_agent", direct_agent_id="hr-bot",
    )
    _complete(environment, repo, first.turn.turn_id, A_ANALYSIS)
    second = repo.append_turn(
        owner, first.conversation.conversation_id, uuid4(),
        ConversationTurnSubmission(B_FACT, hr_scope=unbound),
    )
    if summarized:
        repo.store_summary(first.conversation.conversation_id, second.turn.turn_id, 2, MIXED_SUMMARY)

    builder = ConversationContextBuilder(repo)
    for build in (builder.build, builder.build_direct):
        context = build(second.conversation.conversation_id, second.turn.turn_id)
        assert context.summary is None
        assert [message.content for message in context.messages] == [B_FACT]
    assert builder.compaction_candidate(second.conversation.conversation_id, second.turn.turn_id) is None
