import hashlib
import json
from uuid import uuid4

import pytest
from test_hr_agent_repository import (
    database as database,  # noqa: PLC0414 - pytest fixture export
)
from test_hr_agent_repository import model_context, reply, request
from test_hr_agent_repository import (
    repo as repo,  # noqa: PLC0414 - pytest fixture export
)

from app.hr_agent.context import build_model_context
from app.hr_agent.resources import PublishedKnowledge, ResourceReader
from app.hr_agent.types import HrAgentProblem, ModelContext, ToolCall, WorkPaused


def publication(path):
    path.mkdir(exist_ok=True)
    (path / "role.md").write_text(
        "你是 HR 助理。根据任务自主选取参考知识，证据不足时说明缺口。"
    )
    (path / "method.md").write_text("先检验假设，再比较证据。")
    sha = lambda name: hashlib.sha256((path / name).read_bytes()).hexdigest()
    ref = {
        "kind": "method",
        "id": "evidence",
        "revision": "r1",
        "sha256": sha("method.md"),
    }
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "release_id": "r1",
                "role": {"path": "role.md", "sha256": sha("role.md")},
                "resources": [
                    {
                        "ref": ref,
                        "path": "method.md",
                        "title": "证据推理",
                        "description": "适用于存在竞争解释的问题",
                        "objects": [],
                    }
                ],
            }
        )
    )
    return ref


def test_published_refs_are_exact_and_reject_symlink(tmp_path):
    ref = publication(tmp_path)
    knowledge = PublishedKnowledge(tmp_path)
    assert "检验假设" in knowledge.read(ref)
    (tmp_path / "method.md").write_text("已替换")
    with pytest.raises(HrAgentProblem):
        knowledge.read(ref)
    (tmp_path / "method.md").unlink()
    (tmp_path / "method.md").symlink_to(tmp_path / "role.md")
    with pytest.raises(HrAgentProblem):
        PublishedKnowledge(tmp_path)


def test_candidate_b_excludes_a_message_and_summary(repo):
    repo.scope_validator = lambda *args: None
    a = {"kind": "candidate", "id": str(uuid4())}
    b = {"kind": "candidate", "id": str(uuid4())}
    owner = uuid4()
    work = repo.submit(owner, request(text="A个人事实", objects=[a]), uuid4())
    fence = repo.claim("w", 60)
    entries = repo.read_selected_entries(fence)
    provenance = {
        "derived_from": [
            {
                "entry_id": str(e.entry_id),
                "seq": e.seq,
                "input_revision": e.input_revision,
            }
            for e in entries
        ],
        "policy_revision": "test",
    }
    ctx = ModelContext(
        "summary",
        ({"role": "user", "content": "总结A"},),
        (),
        (),
        100,
        1,
        summary_provenance=provenance,
    )
    attempt = repo.prepare_model(fence, ctx)
    repo.mark_model_sending(fence, attempt.attempt_id)
    repo.commit_model(fence, attempt.attempt_id, reply("A摘要个人事实"))
    repo.commit_summary(fence, attempt.attempt_id, provenance)
    repo.append_input(
        owner,
        work["work_id"],
        {
            "expected_input_revision": 1,
            "text": "评估B",
            "objects": [b],
            "references": [],
            "question_id": None,
        },
        uuid4(),
    )
    current = repo.claim("w", 60)
    selected = repo.read_selected_entries(current)
    assert [e.body["body"] for e in selected] == ["评估B"]
    assert all(e.kind != "summary" for e in selected)


def scoped_summary_from_public_history(repo, owner, work, fence, ref, objects):
    public = repo.read_selected_entries(fence)[0]
    repo.append_input(
        owner,
        work["work_id"],
        {
            "expected_input_revision": fence.input_revision,
            "text": "只在当前摘要前缀中的敏感范围",
            "objects": objects,
            "references": [ref],
            "question_id": None,
        },
        uuid4(),
    )
    fence = repo.claim("w", 60)
    provenance = {
        "derived_from": [
            {
                "entry_id": str(public.entry_id),
                "seq": public.seq,
                "input_revision": public.input_revision,
            }
        ],
        "policy_revision": "scope-summary-v1",
    }
    context = ModelContext(
        "summary",
        ({"role": "user", "content": "摘要"},),
        (),
        (ref,),
        100,
        fence.input_revision,
        summary_provenance=provenance,
    )
    attempt = repo.prepare_model(fence, context)
    repo.mark_model_sending(fence, attempt.attempt_id)
    repo.commit_model(fence, attempt.attempt_id, reply("敏感摘要"))
    repo.commit_summary(fence, attempt.attempt_id, provenance)
    return fence


def test_summary_inherits_current_object_and_reference_scope(repo, tmp_path):
    root = tmp_path / "scoped-summary-release"
    ref = publication(root)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    candidate_b = {"kind": "candidate", "id": str(uuid4())}
    candidate_a = {"kind": "candidate", "id": str(uuid4())}
    work = repo.submit(owner, request(text="公开旧历史"), uuid4())
    fence = repo.claim("w", 60)
    fence = scoped_summary_from_public_history(
        repo, owner, work, fence, ref, [candidate_b]
    )
    repo.append_input(
        owner,
        work["work_id"],
        {
            "expected_input_revision": fence.input_revision,
            "text": "切换到候选人A",
            "objects": [candidate_a],
            "references": [],
            "question_id": None,
        },
        uuid4(),
    )

    current = repo.claim("w", 60)
    assert all(e.kind != "summary" for e in repo.read_selected_entries(current))


def test_summary_is_omitted_after_current_prefix_reference_is_revoked(repo, tmp_path):
    root = tmp_path / "revoked-summary-release"
    ref = publication(root)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    work = repo.submit(owner, request(text="公开旧历史"), uuid4())
    fence = repo.claim("w", 60)
    fence = scoped_summary_from_public_history(repo, owner, work, fence, ref, [])
    repo.append_input(
        owner,
        work["work_id"],
        {
            "expected_input_revision": fence.input_revision,
            "text": "不再选择材料",
            "objects": [],
            "references": [],
            "question_id": None,
        },
        uuid4(),
    )
    (root / "method.md").write_text("已撤销的不同内容")

    current = repo.claim("w", 60)
    assert all(e.kind != "summary" for e in repo.read_selected_entries(current))


def test_context_contains_paired_tool_request_and_result(repo, tmp_path):
    publication(tmp_path)
    resources = ResourceReader(
        repo, PublishedKnowledge(tmp_path), authorize_objects=lambda *args: None
    )
    owner = uuid4()
    repo.submit(owner, request(), uuid4())
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
                        "body": "已读结论",
                        "source_refs": [],
                        "open_questions": [],
                        "reading_targets": [],
                    },
                )
            ],
        ),
    )[0]
    repo.execute_local_tool(fence, op)
    context = build_model_context(repo, resources, fence)
    for i, message in enumerate(context.messages):
        if message["role"] == "tool":
            assert (
                context.messages[i - 1]["tool_calls"][0]["id"]
                == message["tool_call_id"]
            )
    assert any(m["role"] == "tool" for m in context.messages)
    assert context.estimated_input_tokens > 0


def note(repo, fence, body):
    attempt = repo.prepare_model(fence, model_context(revision=fence.input_revision))
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
                        "body": body,
                        "source_refs": [],
                        "open_questions": ["尚待证据"],
                        "reading_targets": [],
                    },
                )
            ],
        ),
    )[0]
    repo.execute_local_tool(fence, op)
    return op


def test_explicit_comparison_allows_both(repo, tmp_path):
    publication(tmp_path)
    resources = ResourceReader(
        repo, PublishedKnowledge(tmp_path), authorize_objects=lambda *a: None
    )
    repo.scope_validator = lambda *args: None
    a = {"kind": "candidate", "id": str(uuid4())}
    b = {"kind": "candidate", "id": str(uuid4())}
    owner = uuid4()
    work = repo.submit(owner, request(text="A个人事实", objects=[a]), uuid4())
    fence = repo.claim("w", 60)
    note(repo, fence, "A的阶段观察")
    repo.append_input(
        owner,
        work["work_id"],
        {
            "expected_input_revision": 1,
            "text": "显式比较A和B",
            "objects": [a, b],
            "references": [],
            "question_id": None,
        },
        uuid4(),
    )
    current = repo.claim("w", 60)
    context = build_model_context(repo, resources, current)
    text = json.dumps(context.messages, ensure_ascii=False)
    assert "A个人事实" in text and "显式比较A和B" in text
    assert any(m["role"] == "tool" for m in context.messages)


def test_previous_work_entries_are_not_imported_into_new_work(repo):
    owner = uuid4()
    first = repo.submit(owner, request(text="其他工作里的个人事实"), uuid4())
    second = repo.submit(
        owner, request(thread_id=first["thread_id"], text="当前公开问题"), uuid4()
    )
    repo.cancel(owner, first["work_id"], "停止", uuid4())
    fence = repo.claim("w", 60)
    assert str(fence.work_id) == second["work_id"]
    assert [e.body["body"] for e in repo.read_selected_entries(fence)] == [
        "当前公开问题"
    ]


def test_note_cannot_erase_personal_scope(repo):
    repo.scope_validator = lambda *a: None
    a = {"kind": "candidate", "id": str(uuid4())}
    owner = uuid4()
    work = repo.submit(owner, request(objects=[a]), uuid4())
    fence = repo.claim("w", 60)
    note(repo, fence, "个人事实不能变成通用知识")
    repo.append_input(
        owner,
        work["work_id"],
        {
            "expected_input_revision": 1,
            "text": "只讨论通用要求",
            "objects": [],
            "references": [],
            "question_id": None,
        },
        uuid4(),
    )
    new = repo.claim("w", 60)
    assert all(e.kind not in ("note", "tool") for e in repo.read_selected_entries(new))


def test_proactive_summary_preserves_sources_and_checkpoint(repo, tmp_path):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / "settings")
    settings = replace(
        settings,
        budget_profile={
            **settings.budget_profile,
            "input_target_tokens": 20000,
            "input_trigger_tokens": 24000,
        },
    )
    repo.settings = settings
    root = tmp_path / "release"
    ref = publication(root)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    work = repo.submit(
        owner,
        request(
            text="继续当前获准研究目标",
            objects=[position],
            references=[ref],
            budget_profile="test",
        ),
        uuid4(),
    )
    fence = repo.claim("w", 60)
    read_attempt = repo.prepare_model(fence, model_context())
    repo.mark_model_sending(fence, read_attempt.attempt_id)
    read_args = {"ref": ref, "offset": 0, "limit": 5}
    operation = repo.commit_model(
        fence,
        read_attempt.attempt_id,
        reply("", [ToolCall("read", "read_resource", read_args)]),
    )[0]
    repo.commit_read(fence, operation, resources.read_resource(fence, read_args))
    for i in range(4):
        note(repo, fence, "重要证据正文" + str(i) + "x" * 4200)
    context = build_model_context(repo, resources, fence)
    assert context.purpose == "summary"
    assert context.summary_provenance["derived_from"]
    before = repo.get_work(owner, work["work_id"])
    assert before["checkpoint"]["readings"][0]["remaining_ranges"]
    assert before["checkpoint"]["open_questions"] == ["尚待证据"]
    attempt = repo.prepare_model(fence, context)
    summary_state = json.loads(attempt.messages[1]["content"])
    assert summary_state == {
        "current_goal": "继续当前获准研究目标",
        "objects": [position],
        "selected_references": [ref],
        "checkpoint": before["checkpoint"],
    }
    repo.mark_model_sending(fence, attempt.attempt_id)
    repo.commit_model(
        fence, attempt.attempt_id, reply("阶段观察：保留尚待证据的问题。")
    )
    repo.commit_summary(fence, attempt.attempt_id, context.summary_provenance)
    after = repo.get_work(owner, work["work_id"])
    assert {k: v for k, v in after["checkpoint"].items() if k != "revision"} == {
        k: v for k, v in before["checkpoint"].items() if k != "revision"
    }
    assert after["budget"]["charged_calls"] == before["budget"]["charged_calls"] + 1
    assert after["state"] == "running" and after["answer_state"] == "none"
    assert not any(
        m["kind"] == "assistant"
        for m in repo.list_messages(owner, work["work_id"])["items"]
    )
    next_context = build_model_context(repo, resources, fence)
    assert next_context.purpose in ("summary", "work")
    if next_context.purpose == "work":
        assert next_context.estimated_input_tokens <= 20000


def test_summary_prefix_that_leaves_no_history_window_waits_for_budget(
    repo, tmp_path
):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / "settings-small-window")
    settings = replace(
        settings,
        budget_profile={
            **settings.budget_profile,
            "input_target_tokens": 2000,
            "input_trigger_tokens": 3000,
            "max_output_tokens": 1000,
        },
        provider_profile={
            **settings.provider_profile,
            "context_window_tokens": 2200,
        },
    )
    repo.settings = settings
    root = tmp_path / "release-small-window"
    publication(root)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    work = repo.submit(
        owner,
        request(text="目标" + "x" * 2400, budget_profile="test"),
        uuid4(),
    )
    fence = repo.claim("w", 60)

    with pytest.raises(WorkPaused):
        build_model_context(repo, resources, fence)

    assert repo.get_work(owner, work["work_id"])["state"] == "waiting_budget"


def test_mixed_summary_missing_originals_is_omitted(repo, database):
    owner = uuid4()
    repo.submit(owner, request(), uuid4())
    fence = repo.claim("w", 60)
    # Simulate old/corrupt imported provenance: service never creates this shape.
    with repo.transaction() as c:
        row = repo._fence(c, fence)
        repo._entry(
            c,
            row,
            "summary",
            {"body": "不可信混合摘要"},
            provenance={
                "derived_from": [
                    {"entry_id": str(uuid4()), "seq": 1, "input_revision": 1}
                ],
                "policy_revision": "old",
            },
        )
    assert not any(e.kind == "summary" for e in repo.read_selected_entries(fence))


def test_nested_summary_checks_all_originals_and_covers_transitive_history(
    repo, database
):
    owner = uuid4()
    repo.submit(owner, request(), uuid4())
    fence = repo.claim("w", 60)
    original = repo.read_selected_entries(fence)[0]

    def provenance(entry, seq):
        return {
            "derived_from": [{"entry_id": str(entry), "seq": seq, "input_revision": 1}],
            "policy_revision": "test",
        }

    with repo.transaction() as c:
        work = repo._fence(c, fence)
        first = repo._entry(
            c,
            work,
            "summary",
            {"body": "第一层"},
            provenance=provenance(original.entry_id, original.seq),
        )
        repo._entry(
            c, work, "summary", {"body": "第二层"}, provenance=provenance(first, 2)
        )
    assert [e.body["body"] for e in repo.read_selected_entries(fence)] == ["第二层"]
    with database.admin_connection() as connection:
        connection.execute(
            "DELETE FROM platform_hr_agent.entries WHERE entry_id=%s",
            (original.entry_id,),
        )
    assert repo.read_selected_entries(fence) == ()


def test_result_discovery_continues_across_repository_pages(repo, tmp_path):
    publication(tmp_path)
    resources = ResourceReader(
        repo, PublishedKnowledge(tmp_path), authorize_objects=lambda *a: None
    )
    owner = uuid4()
    repo.submit(owner, request(), uuid4())
    fence = repo.claim("w", 60)
    calls = [
        ToolCall(
            str(i),
            "save_result",
            {
                "result_id": None,
                "expected_revision": None,
                "kind": "research",
                "title": str(i),
                "body": "虚构观察",
                "objects": [],
                "source_refs": [],
                "preceding_refs": [],
                "base_standard_ref": None,
                "changes": [],
                "basis": [],
            },
        )
        for i in range(51)
    ]
    attempt = repo.prepare_model(fence, model_context())
    repo.mark_model_sending(fence, attempt.attempt_id)
    for op in repo.commit_model(fence, attempt.attempt_id, reply("", calls)):
        repo.execute_local_tool(fence, op)
    page = resources.list_resources(fence, {"kinds": ["result"]})
    assert len(page["items"]) == 30 and page["next_cursor"]
    second = resources.list_resources(
        fence, {"kinds": ["result"], "cursor": page["next_cursor"]}
    )
    assert len(second["items"]) == 21 and second["next_cursor"] is None
    assert len({i["ref"]["id"] for i in page["items"] + second["items"]}) == 51
