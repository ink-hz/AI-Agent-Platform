from uuid import uuid4

import pytest
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from app.hr_agent.repository import HrAgentRepository
from app.hr_agent.types import HrAgentProblem
from hr_agent_support import hr_agent_database


@pytest.fixture(scope="module")
def database():
    with hr_agent_database() as database:
        yield database


@pytest.fixture
def repo(database):
    with database.admin_connection() as connection:
        connection.execute(
            "truncate platform_hr_agent.threads, platform_hr_agent.operations cascade"
        )
    codec = ContentCodec(
        IdentityKeyring(1, "platform-content-encryption", {1: b"k" * 32})
    )
    return HrAgentRepository(database.connection, codec)


def request(**updates):
    return {
        "thread_id": None,
        "text": "梳理公开岗位需求",
        "objects": [],
        "references": [],
        "budget_profile": "calibration-test",
        **updates,
    }


def test_submit_without_position_and_replay(repo):
    owner, key = uuid4(), uuid4()
    first = repo.submit(owner, request(), key)
    assert first["state"] == "queued"
    assert first == repo.submit(owner, request(), key)
    assert repo.get_work(owner, first["work_id"]) == first
    messages = repo.list_messages(owner, first["work_id"], 0, 100)
    assert len(messages["items"]) == 1
    assert messages["items"][0]["body"] == "梳理公开岗位需求"
    assert first["checkpoint"]["discovery_state"] == "open"


def test_same_key_changed_payload_conflicts(repo):
    owner, key = uuid4(), uuid4()
    repo.submit(owner, request(), key)
    with pytest.raises(HrAgentProblem) as failure:
        repo.submit(owner, request(text="另一个问题"), key)
    assert failure.value.problem["code"] == "idempotency_conflict"


def test_owner_filter_precedes_decryption(repo):
    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())

    class BombCodec:
        def unseal_json(self, *args):
            pytest.fail("foreign owner reached decryption")

    secured = HrAgentRepository(repo.connection_factory, BombCodec())
    with pytest.raises(HrAgentProblem) as failure:
        secured.get_work(uuid4(), work["work_id"])
    assert failure.value.problem["code"] == "not_found"


def test_new_input_invalidates_old_execution_owner(repo):
    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    fence = repo.claim("worker-a", 60)
    assert str(fence.work_id) == work["work_id"]
    changed = repo.append_input(
        owner,
        work["work_id"],
        {
            "expected_input_revision": 1,
            "text": "补充新目标",
            "objects": [],
            "references": [],
            "question_id": None,
        },
        uuid4(),
    )
    assert changed["input_revision"] == 2
    assert repo.renew(fence, 60) is False


def test_cancel_and_budget_addition_preserve_prior_usage(repo):
    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    body = {
        "expected_budget_revision": 1,
        "addition": {"model_calls": 3, "total_tokens": 0, "active_seconds": 0},
        "reason": "继续本次研究",
    }
    key = uuid4()
    updated = repo.extend_budget(owner, work["work_id"], body, key)
    assert updated["budget"]["limits"]["model_calls"] == 35
    assert updated == repo.extend_budget(owner, work["work_id"], body, key)
    cancelled = repo.cancel(owner, work["work_id"], "用户取消", uuid4())
    assert cancelled["state"] == "cancelled"
    with pytest.raises(HrAgentProblem):
        repo.extend_budget(
            owner, work["work_id"], {**body, "expected_budget_revision": 2}, uuid4()
        )


def model_context(tokens=100, revision=1):
    from app.hr_agent.types import ModelContext

    return ModelContext(
        "work", ({"role": "user", "content": "test"},), (), (), tokens, revision
    )


def reply(text="回答", tools=()):
    from app.hr_agent.types import ModelReply, Usage

    return ModelReply(text, tuple(tools), "stop", Usage(None, 100, 20, "reported"))


def test_prepared_attempt_is_not_double_charged(repo):
    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    fence = repo.claim("worker", 60)
    prepared = repo.prepare_model(fence, model_context())
    assert repo.get_work(owner, work["work_id"])["budget"]["charged_calls"] == 0
    repeated = repo.prepare_model(fence, model_context())
    assert repeated.attempt_id == prepared.attempt_id
    repo.mark_model_sending(fence, prepared.attempt_id)
    repo.mark_model_sending(fence, prepared.attempt_id)
    assert repo.get_work(owner, work["work_id"])["budget"]["charged_calls"] == 1


def test_committed_final_answer_projects_without_new_model_call(repo):
    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    fence = repo.claim("worker", 60)
    prepared = repo.prepare_model(fence, model_context())
    repo.mark_model_sending(fence, prepared.attempt_id)
    repo.commit_model(fence, prepared.attempt_id, reply())
    assert repo.next_action(fence).kind == "project_answer"
    finished = repo.finish_work(fence, prepared.attempt_id)
    assert finished["state"] == "completed"
    assert finished["answer_state"] == "ended"
    assert finished["budget"]["charged_calls"] == 1
    assert repo.list_messages(owner, work["work_id"])["items"][-1]["body"] == "回答"


def test_saved_result_and_receipt_share_one_operation(repo, database):
    from app.hr_agent.types import ToolCall

    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    fence = repo.claim("worker", 60)
    prepared = repo.prepare_model(fence, model_context())
    repo.mark_model_sending(fence, prepared.attempt_id)
    payload = {
        "result_id": None,
        "expected_revision": None,
        "kind": "research",
        "title": "观察",
        "body": "公开资料支持的判断",
        "objects": [],
        "source_refs": [],
        "preceding_refs": [],
        "base_standard_ref": None,
        "changes": [],
        "basis": [],
    }
    operations = repo.commit_model(
        fence,
        prepared.attempt_id,
        reply("", [ToolCall("slot-a", "save_result", payload)]),
    )
    first = repo.execute_local_tool(fence, operations[0])
    second = repo.execute_local_tool(fence, operations[0])
    assert first == second
    assert first["status"] == "ok"
    with database.admin_connection() as connection:
        assert (
            connection.execute(
                "select count(*) from platform_hr_agent.result_revisions where created_by_operation=%s",
                (operations[0],),
            ).fetchone()[0]
            == 1
        )
    assert len(repo.get_work(owner, work["work_id"])["result_refs"]) == 1


def test_cancel_wins_before_new_side_effect(repo, database):
    from app.hr_agent.types import ToolCall

    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    fence = repo.claim("worker", 60)
    prepared = repo.prepare_model(fence, model_context())
    repo.mark_model_sending(fence, prepared.attempt_id)
    operations = repo.commit_model(
        fence,
        prepared.attempt_id,
        reply(
            "",
            [
                ToolCall(
                    "note",
                    "save_note",
                    {
                        "body": "不得写入",
                        "source_refs": [],
                        "open_questions": [],
                        "reading_targets": [],
                    },
                )
            ],
        ),
    )
    repo.cancel(owner, work["work_id"], "cancel", uuid4())
    with pytest.raises(HrAgentProblem):
        repo.execute_local_tool(fence, operations[0])
    with database.admin_connection() as connection:
        assert (
            connection.execute(
                "select count(*) from platform_hr_agent.entries where work_id=%s and kind='note'",
                (work["work_id"],),
            ).fetchone()[0]
            == 0
        )


def test_prepared_resume_checks_pinned_configuration(repo, tmp_path):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path)
    repo.settings = settings
    owner = uuid4()
    work = repo.submit(owner, request(budget_profile="test"), uuid4())
    fence = repo.claim("w", 60)
    attempt = repo.prepare_model(fence, model_context())
    repo.settings = replace(settings, configuration_revision="changed")
    with pytest.raises(HrAgentProblem):
        repo.resume_prepared(fence, attempt.attempt_id)
    assert repo.get_work(owner, work["work_id"])["budget"]["charged_calls"] == 0


def test_prepared_resume_revalidates_context_dependency(repo):
    ref = {"kind": "method", "id": "evidence", "revision": "r1", "sha256": "a" * 64}
    revoked = False

    def scope(owner, objects, refs, work_id):
        if revoked and ref in refs:
            from app.hr_agent.types import problem

            raise problem("dependency_revoked", http_status=403)

    repo.scope_validator = scope
    from dataclasses import replace

    owner = uuid4()
    repo.submit(owner, request(), uuid4())
    fence = repo.claim("w", 60)
    attempt = repo.prepare_model(fence, replace(model_context(), dependencies=(ref,)))
    revoked = True
    with pytest.raises(HrAgentProblem):
        repo.mark_model_sending(fence, attempt.attempt_id)


def test_revoked_note_checkpoint_does_not_expose_open_questions(repo):
    from dataclasses import replace

    from app.hr_agent.types import ToolCall, problem

    ref = {
        "kind": "method",
        "id": "synthetic-sensitive-scope",
        "revision": "r1",
        "sha256": "a" * 64,
    }
    revoked = False

    def scope(owner, objects, refs, work_id):
        if revoked and ref in refs:
            raise problem("dependency_revoked", http_status=403)

    repo.scope_validator = scope
    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    fence = repo.claim("w", 60)
    attempt = repo.prepare_model(fence, replace(model_context(), dependencies=(ref,)))
    repo.mark_model_sending(fence, attempt.attempt_id)
    op = repo.commit_model(
        fence,
        attempt.attempt_id,
        reply(
            "",
            [
                ToolCall(
                    "n",
                    "save_note",
                    {
                        "body": "阶段笔记",
                        "source_refs": [ref],
                        "open_questions": ["SYNTHETIC_PRIVATE_QUESTION"],
                        "reading_targets": [],
                    },
                )
            ],
        ),
    )[0]
    repo.execute_local_tool(fence, op)
    assert repo.get_work(owner, work["work_id"])["checkpoint"]["open_questions"]
    revoked = True
    assert repo.get_work(owner, work["work_id"])["checkpoint"]["open_questions"] == []
    from app.hr_agent.tools import execute_tool

    with pytest.raises(HrAgentProblem):
        execute_tool(repo, None, fence, op)


def test_tool_receipts_and_event_pages_match_wire_contract(repo):
    from app.hr_agent.types import ToolCall, validate_contract

    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    fence = repo.claim("w", 60)
    attempt = repo.prepare_model(fence, model_context())
    repo.mark_model_sending(fence, attempt.attempt_id)
    op = repo.commit_model(
        fence,
        attempt.attempt_id,
        reply(
            "",
            [
                ToolCall(
                    "q", "ask_user", {"question": "需要补充什么证据？", "options": []}
                )
            ],
        ),
    )[0]
    outcome = repo.execute_local_tool(fence, op)
    validate_contract("QuestionReceipt", outcome["data"])
    validate_contract("WorkView", repo.get_work(owner, work["work_id"]))
    validate_contract("MessagePage", repo.list_messages(owner, work["work_id"]))
    validate_contract("EventPage", repo.list_events(owner, work["work_id"]))


def test_delayed_prepared_request_cannot_spend_elapsed_budget(repo, database):
    from app.hr_agent.types import ContextRebuildRequired

    owner = uuid4()
    work = repo.submit(owner, request(), uuid4())
    fence = repo.claim("w", 60)
    attempt = repo.prepare_model(fence, model_context())
    # Database clock fault fixture: prior active interval consumed the ordinary allowance.
    with repo.transaction() as c:
        row = repo._fence(c, fence)
        budget = repo._unseal("works", row["work_id"], "sealed_budget", row)
        budget["active_seconds"] = (
            budget["limits"]["active_seconds"] - budget["reserve"]["active_seconds"]
        )
        repo._save_budget(c, row, budget)
    with pytest.raises(ContextRebuildRequired):
        repo.mark_model_sending(fence, attempt.attempt_id)
    view = repo.get_work(owner, work["work_id"])
    assert view["phase"] == "finalizing" and view["budget"]["charged_calls"] == 0


def test_object_only_note_revocation_hides_checkpoint_text(repo):
    from app.hr_agent.types import ToolCall, problem

    candidate = {"kind": "candidate", "id": str(uuid4())}
    revoked = False

    def scope(owner, objects, refs, work_id):
        if revoked and candidate in objects:
            raise problem("scope_denied", http_status=403)

    repo.scope_validator = scope
    owner = uuid4()
    work = repo.submit(owner, request(objects=[candidate]), uuid4())
    fence = repo.claim("w", 60)
    attempt = repo.prepare_model(fence, model_context())
    repo.mark_model_sending(fence, attempt.attempt_id)
    op = repo.commit_model(
        fence,
        attempt.attempt_id,
        reply(
            "",
            [
                ToolCall(
                    "n",
                    "save_note",
                    {
                        "body": "虚构个人观察",
                        "source_refs": [],
                        "open_questions": ["FAKE_PRIVATE_UNRESOLVED"],
                        "reading_targets": [],
                    },
                )
            ],
        ),
    )[0]
    repo.execute_local_tool(fence, op)
    revoked = True
    assert repo.get_work(owner, work["work_id"])["checkpoint"]["open_questions"] == []
