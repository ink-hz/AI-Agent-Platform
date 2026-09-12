"""D1 regressions against the deployed v6/v7 persisted turn-scope shape."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_conversation_context import _complete_mission
from test_control_plane_migration import control_database  # noqa: F401
from test_hr_candidate_database import _seed_candidate_scope
from test_hr_turn_scope_v6_database import (
    repository as repository,  # noqa: PLC0414 - helper re-export
)
from test_hr_turn_scope_v6_database import (
    scoped_database as scoped_database,  # noqa: PLC0414 - pytest fixture export
)

from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_models import ConversationTurnSubmission
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_repository import ConversationRepositoryNotFound
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


def _scoped_submission(
    text: str,
    scope: HrTurnScope,
    *,
    protocol: str,
    active_attachment_id=None,
) -> ConversationTurnSubmission:
    selected = (active_attachment_id,) if active_attachment_id is not None else ()
    return ConversationTurnSubmission(
        text,
        active_attachment_ids=selected,
        hr_scope=scope,
        input_result_refs=() if protocol == "v7" else None,
    )


def _assert_current_turn_only(builder, conversation_id, turn_id, current):
    for build in (builder.build, builder.build_direct):
        context = build(conversation_id, turn_id)
        assert context.summary is None
        assert [message.content for message in context.messages] == [current]
    assert builder.compaction_candidate(conversation_id, turn_id) is None


def _is_hr_v6_mutant(tmp_path):
    source_path = Path(__file__).parents[1] / "app/agent_brain/conversation_context.py"
    source = source_path.read_text()
    original = (
        'row["user_seq"] - 1 if is_hr_agent\n'
        '                else conversation.summary_through_seq\n'
        '            )\n'
        '            summary = None if is_hr_agent else conversation.summary'
    )
    narrowed = (
        'row["user_seq"] - 1 if is_hr_v6\n'
        '                else conversation.summary_through_seq\n'
        '            )\n'
        '            summary = None if is_hr_v6 else conversation.summary'
    )
    assert source.count(original) == 1
    mutant_path = tmp_path / "conversation_context_is_hr_v6_mutant.py"
    mutant_path.write_text(source.replace(original, narrowed))
    module_name = "conversation_context_is_hr_v6_mutant"
    spec = importlib.util.spec_from_file_location(module_name, mutant_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module.ConversationContextBuilder


@pytest.mark.postgres
def test_persisted_legacy_hr_turn_without_scope_excludes_history_and_summary(
    scoped_database, tmp_path,
):
    environment = scoped_database
    owner = _seed_candidate_scope(environment)["owner"]
    repo = repository(environment)
    first = repo.start(
        owner,
        uuid4(),
        A_FACT,
        mode="direct_agent",
        direct_agent_id="fae-bot",
    )
    _complete(environment, repo, first.turn.turn_id, A_ANALYSIS)
    second = repo.append_turn(
        owner, first.conversation.conversation_id, uuid4(), B_FACT
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.conversations set direct_agent_id='hr-bot' "
            "where conversation_id=%s",
            (first.conversation.conversation_id,),
        )
        rows = connection.execute(
            "select hr_input_context from platform_control.conversation_turns "
            "where conversation_id=%s order by created_at,turn_id",
            (first.conversation.conversation_id,),
        ).fetchall()
    assert rows == [(None,), (None,)]

    conversation_id = first.conversation.conversation_id
    turn_id = second.turn.turn_id
    _assert_current_turn_only(
        ConversationContextBuilder(repo), conversation_id, turn_id, B_FACT
    )
    mutant = _is_hr_v6_mutant(tmp_path)(repo)
    with pytest.raises(AssertionError):
        _assert_current_turn_only(mutant, conversation_id, turn_id, B_FACT)

    repo.store_summary(conversation_id, turn_id, 2, MIXED_SUMMARY)
    _assert_current_turn_only(
        ConversationContextBuilder(repo), conversation_id, turn_id, B_FACT
    )
    mutant = _is_hr_v6_mutant(tmp_path)(repo)
    with pytest.raises(AssertionError):
        _assert_current_turn_only(mutant, conversation_id, turn_id, B_FACT)


@pytest.mark.postgres
@pytest.mark.parametrize("build_method", ["build", "build_direct"])
@pytest.mark.parametrize("summarized", [False, True])
@pytest.mark.parametrize("protocol", ["v6", "v7"])
def test_same_position_candidate_a_history_and_mixed_summary_do_not_cross_into_candidate_b(
    scoped_database, build_method, summarized, protocol,
):
    environment = scoped_database
    seeded = _seed_candidate_scope(environment)
    owner = seeded["owner"]
    position = seeded["position"]
    candidate_a, candidate_b, relation_a, relation_b, attachment_b = (
        uuid4() for _ in range(5)
    )
    attachment_a = seeded["attachment"]
    repo = repository(environment)
    conversation = repo.ensure_direct_conversation_shell(
        owner,
        uuid4(),
        direct_agent_id="hr-bot",
        title="D1 candidate attachment isolation",
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_attachments.attachments set conversation_id=%s "
            "where attachment_id=%s",
            (conversation.conversation_id, attachment_a),
        )
        connection.execute(
            "insert into platform_attachments.attachments(attachment_id,"
            "owner_internal_user_id,conversation_id,source_kind,"
            "original_name_ciphertext,"
            "original_name_key_version,object_ref_ciphertext,object_ref_key_version,"
            "immutable_locator,sha256,state,ready_at) values"
            "(%s,%s,%s,'user_input',%s,1,%s,1,%s,%s,'ready',now())",
            (
                attachment_b,
                owner,
                conversation.conversation_id,
                b"n" * 29,
                b"o" * 29,
                f"etag:d1-candidate-b-{attachment_b}",
                b"b" * 32,
            ),
        )
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
    scope_a = HrTurnScope(
        positionId=position,
        positionCandidateIds=(relation_a,),
        attachmentIds=(attachment_a,),
    )
    scope_b = HrTurnScope(
        positionId=position,
        positionCandidateIds=(relation_b,),
        attachmentIds=(attachment_b,),
    )
    first = repo.append_turn(
        owner,
        conversation.conversation_id,
        uuid4(),
        _scoped_submission(
            A_FACT,
            scope_a,
            protocol=protocol,
            active_attachment_id=attachment_a,
        ),
    )
    _complete(environment, repo, first.turn.turn_id, A_ANALYSIS)
    second = repo.append_turn(
        owner, first.conversation.conversation_id, uuid4(),
        _scoped_submission(
            B_FACT,
            scope_b,
            protocol=protocol,
            active_attachment_id=attachment_b,
        ),
    )
    with psycopg.connect(environment["admin"]) as connection:
        for turn, relation, attachment in (
            (first.turn, relation_a, attachment_a),
            (second.turn, relation_b, attachment_b),
        ):
            persisted = connection.execute(
                "select hr_input_context from platform_control.conversation_turns "
                "where turn_id=%s",
                (turn.turn_id,),
            ).fetchone()[0]
            scope = persisted["scope"]
            assert scope["positionId"] == str(position)
            assert scope["positionCandidateIds"] == [str(relation)]
            assert scope["attachmentIds"] == [str(attachment)]
            assert ("inputResultRefs" in persisted) is (protocol == "v7")
            assert connection.execute(
                "select array_agg(attachment_id order by attachment_id) from "
                "platform_attachments.bindings where conversation_id=%s and turn_id=%s "
                "and kind='turn_input'",
                (turn.conversation_id, turn.turn_id),
            ).fetchone()[0] == [attachment]
    if summarized:
        repo.store_summary(first.conversation.conversation_id, second.turn.turn_id, 2, MIXED_SUMMARY)

    context = getattr(ConversationContextBuilder(repo), build_method)(
        second.conversation.conversation_id, second.turn.turn_id
    )
    rendered = "\n".join(message.content for message in context.messages)
    assert context.summary is None
    assert rendered == B_FACT
    assert context.active_attachment_ids == (attachment_b,)
    assert attachment_a not in context.active_attachment_ids
    assert A_FACT not in rendered
    assert A_ANALYSIS not in rendered


@pytest.mark.postgres
@pytest.mark.parametrize("summarized", [False, True])
@pytest.mark.parametrize(
    ("current", "protocol"),
    [
        (B_FACT + "，请评估乙。", "v6"),
        ("讨论通用岗位要求。", "v6"),
        ("明确比较 A/B 的量产经历，并沿用历史中的甲乙事实。", "v7"),
    ],
)
def test_unbound_hr_turn_does_not_unlock_prior_unbound_history(
    scoped_database,
    summarized,
    current,
    protocol,
):
    environment = scoped_database
    seeded = _seed_candidate_scope(environment)
    owner = seeded["owner"]
    unbound = HrTurnScope(positionId=None, positionCandidateIds=(), attachmentIds=())
    repo = repository(environment)
    first = repo.start(
        owner,
        uuid4(),
        _scoped_submission(A_FACT, unbound, protocol=protocol),
        mode="direct_agent", direct_agent_id="hr-bot",
    )
    _complete(environment, repo, first.turn.turn_id, A_ANALYSIS)
    second = repo.append_turn(
        owner, first.conversation.conversation_id, uuid4(),
        _scoped_submission(current, unbound, protocol=protocol),
    )
    if summarized:
        repo.store_summary(first.conversation.conversation_id, second.turn.turn_id, 2, MIXED_SUMMARY)

    builder = ConversationContextBuilder(repo)
    for build in (builder.build, builder.build_direct):
        context = build(second.conversation.conversation_id, second.turn.turn_id)
        assert context.summary is None
        assert [message.content for message in context.messages] == [current]
    assert builder.compaction_candidate(second.conversation.conversation_id, second.turn.turn_id) is None


@pytest.mark.postgres
def test_long_hr_history_and_summary_remain_current_turn_only_for_compaction(
    scoped_database,
):
    environment = scoped_database
    owner = _seed_candidate_scope(environment)["owner"]
    unbound = HrTurnScope(positionId=None, positionCandidateIds=(), attachmentIds=())
    repo = repository(environment)
    first = repo.start(
        owner,
        uuid4(),
        _scoped_submission(A_FACT + "甲" * 10_000, unbound, protocol="v7"),
        mode="direct_agent",
        direct_agent_id="hr-bot",
    )
    _complete(environment, repo, first.turn.turn_id, A_ANALYSIS + "甲" * 2_000)
    current = B_FACT + "乙" * 10_000
    second = repo.append_turn(
        owner,
        first.conversation.conversation_id,
        uuid4(),
        _scoped_submission(current, unbound, protocol="v7"),
    )
    repo.store_summary(
        first.conversation.conversation_id,
        second.turn.turn_id,
        2,
        MIXED_SUMMARY,
    )

    builder = ConversationContextBuilder(repo)
    for build in (builder.build, builder.build_direct):
        context = build(second.conversation.conversation_id, second.turn.turn_id)
        assert context.summary is None
        assert [message.content for message in context.messages] == [current]
    assert builder.compaction_candidate(
        second.conversation.conversation_id,
        second.turn.turn_id,
    ) is None


@pytest.mark.postgres
def test_non_hr_history_retains_shared_summary(scoped_database):
    environment = scoped_database
    seeded = _seed_candidate_scope(environment)
    owner = seeded["owner"]
    other = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'Other','active')",
            (other,),
        )
    repo = repository(environment)
    first = repo.start(
        owner,
        uuid4(),
        "一般技术问题",
        mode="direct_agent",
        direct_agent_id="fae-bot",
    )
    _complete(environment, repo, first.turn.turn_id, "技术回答")
    with pytest.raises(ConversationRepositoryNotFound):
        repo.append_turn(other, first.conversation.conversation_id, uuid4(), "越权请求")
    second = repo.append_turn(
        owner,
        first.conversation.conversation_id,
        uuid4(),
        "继续技术讨论",
    )
    builder = ConversationContextBuilder(repo)
    context = builder.build_direct(
        first.conversation.conversation_id,
        second.turn.turn_id,
    )
    assert [message.content for message in context.messages] == [
        "一般技术问题",
        "技术回答",
        "继续技术讨论",
    ]

    repo.store_summary(
        first.conversation.conversation_id,
        second.turn.turn_id,
        2,
        "已确认技术前提",
    )
    context = builder.build_direct(
        first.conversation.conversation_id,
        second.turn.turn_id,
    )
    assert context.summary == "已确认技术前提"
    assert [message.content for message in context.messages] == ["继续技术讨论"]
