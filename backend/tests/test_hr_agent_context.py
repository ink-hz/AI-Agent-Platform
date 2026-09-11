import hashlib
import json
from uuid import UUID, uuid4

import pytest
from app.hr_agent.context import (
    build_model_context,
    ensure_read_result_fits_summary,
    estimate_input_tokens,
)
from app.hr_agent.resources import PublishedKnowledge, ResourceReader
from app.hr_agent.tools import execute_tool
from app.hr_agent.types import HrAgentProblem, ModelContext, ToolCall, WorkPaused
from test_hr_agent_repository import (
    database as database,  # noqa: PLC0414 - pytest fixture export
)
from test_hr_agent_repository import model_context, reply, request
from test_hr_agent_repository import (
    repo as repo,  # noqa: PLC0414 - pytest fixture export
)


def publication(path, method_text="先检验假设，再比较证据。"):
    path.mkdir(exist_ok=True)
    (path / "role.md").write_text(
        "你是 HR 助理。根据任务自主选取参考知识，证据不足时说明缺口。"
    )
    (path / "method.md").write_text(method_text)
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


def unread_tool_batch(repo, resources, fence, ref):
    attempt = repo.prepare_model(fence, model_context(revision=fence.input_revision))
    repo.mark_model_sending(fence, attempt.attempt_id)
    arguments = [
        {"ref": ref, "offset": 0, "limit": 4000},
        {"ref": ref, "offset": 4000, "limit": 4000},
    ]
    operations = repo.commit_model(
        fence,
        attempt.attempt_id,
        reply(
            "",
            [
                ToolCall(str(index), "read_resource", args)
                for index, args in enumerate(arguments)
            ],
        ),
    )
    for operation, args in zip(operations, arguments, strict=True):
        repo.commit_read(fence, operation, resources.read_resource(fence, args))
    return attempt


def large_unread_tool_batch(repo, resources, fence, ref, count=3):
    attempt = repo.prepare_model(fence, model_context(revision=fence.input_revision))
    repo.mark_model_sending(fence, attempt.attempt_id)
    arguments = [
        {"ref": ref, "offset": index * 20000, "limit": 20000} for index in range(count)
    ]
    operations = repo.commit_model(
        fence,
        attempt.attempt_id,
        reply(
            "",
            [
                ToolCall(str(index), "read_resource", args)
                for index, args in enumerate(arguments)
            ],
        ),
    )
    for operation, args in zip(operations, arguments, strict=True):
        repo.commit_read(fence, operation, resources.read_resource(fence, args))
    return attempt


def tool_consumption_setup(repo, tmp_path, *, context_window_tokens=32768):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / "tool-consumption-settings")
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
            "context_window_tokens": context_window_tokens,
        },
    )
    repo.settings = settings
    root = tmp_path / "tool-consumption-release"
    ref = publication(root, "证据正文" + "x" * 7992)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    work = repo.submit(owner, request(references=[ref], budget_profile="test"), uuid4())
    fence = repo.claim("w", 60)
    producer = unread_tool_batch(repo, resources, fence, ref)
    return owner, work, fence, resources, producer


def test_soft_trigger_keeps_latest_multi_tool_batch_for_one_work_call(repo, tmp_path):
    _owner, _work, fence, resources, _producer = tool_consumption_setup(repo, tmp_path)
    selected, unconsumed = repo.read_selected_entries(fence, include_unconsumed=True)
    assert len(unconsumed) == 2, [(entry.kind, entry.entry_id) for entry in selected]

    context = build_model_context(repo, resources, fence)

    assert context.purpose == "work"
    assert sum(message["role"] == "tool" for message in context.messages) == 2


def test_summary_retries_do_not_mark_latest_tool_batch_consumed(repo, tmp_path):
    _owner, _work, fence, resources, producer = tool_consumption_setup(repo, tmp_path)
    entry = repo.read_selected_entries(fence)[0]
    provenance = {
        "derived_from": [
            {
                "entry_id": str(entry.entry_id),
                "seq": entry.seq,
                "input_revision": entry.input_revision,
            }
        ],
        "policy_revision": "test",
    }
    summary = ModelContext(
        "summary",
        ({"role": "user", "content": "摘要旧历史"},),
        (),
        (),
        20,
        fence.input_revision,
        summary_provenance=provenance,
    )
    for _ in range(2):
        attempt = repo.prepare_model(fence, summary)
        repo.mark_model_sending(fence, attempt.attempt_id)
        repo.interrupt_model(fence, attempt.attempt_id, "empty_response")
    assert producer.attempt_id != attempt.attempt_id
    uncommitted_work = repo.prepare_model(
        fence, model_context(revision=fence.input_revision)
    )
    repo.mark_model_sending(fence, uncommitted_work.attempt_id)

    context = build_model_context(repo, resources, fence)

    assert context.purpose == "work"
    assert sum(message["role"] == "tool" for message in context.messages) == 2


def test_later_committed_work_allows_consumed_tool_batch_to_compact(repo, tmp_path):
    _owner, _work, fence, resources, _producer = tool_consumption_setup(repo, tmp_path)
    direct = build_model_context(repo, resources, fence)
    attempt = repo.prepare_model(fence, direct)
    repo.mark_model_sending(fence, attempt.attempt_id)
    repo.commit_model(fence, attempt.attempt_id, reply("已直接消费工具正文"))

    context = build_model_context(repo, resources, fence)

    assert context.purpose == "summary"


def test_indivisible_unconsumed_tool_batch_blocks_with_context_reason(repo, tmp_path):
    owner, work, fence, resources, _producer = tool_consumption_setup(
        repo, tmp_path, context_window_tokens=7000
    )

    with pytest.raises(WorkPaused):
        build_model_context(repo, resources, fence)

    blocked = repo.get_work(owner, work["work_id"])
    assert blocked["state"] == "blocked"
    assert blocked["block_reason"] == "context_too_large"


@pytest.mark.parametrize("character", ["中", "😀"])
def test_read_resource_rejects_exact_receipt_that_cannot_fit_one_summary_request(
    repo, tmp_path, character
):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / "read-window-settings")
    repo.settings = replace(
        settings,
        provider_profile={
            **settings.provider_profile,
            "context_window_tokens": 32768,
        },
    )
    root = tmp_path / "read-window-release"
    ref = publication(root, character * 20000)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    work = repo.submit(
        owner,
        request(text="核对完整材料", references=[ref], budget_profile="test"),
        uuid4(),
    )
    fence = repo.claim("w", 60)
    attempt = repo.prepare_model(fence, model_context(revision=fence.input_revision))
    repo.mark_model_sending(fence, attempt.attempt_id)
    operations = repo.commit_model(
        fence,
        attempt.attempt_id,
        reply(
            "",
            [
                ToolCall("large", "read_resource", {"ref": ref, "limit": 20000}),
                ToolCall("small", "read_resource", {"ref": ref, "limit": 1000}),
            ],
        ),
    )

    rejected = execute_tool(repo, resources, fence, operations[0])
    assert rejected["error"]["code"] == "invalid_input"
    assert "smaller limit" in rejected["error"]["message"]
    with repo.transaction() as c:
        c.execute(
            "SELECT count(*) AS count FROM platform_hr_agent.read_records WHERE work_id=%s",
            (UUID(work["work_id"]),),
        )
        assert c.fetchone()["count"] == 0
    assert repo.get_work(owner, work["work_id"])["checkpoint"]["readings"] == []

    accepted = execute_tool(repo, resources, fence, operations[1])
    assert accepted["error"] is None
    assert accepted["data"]["text"] == character * 1000
    with repo.transaction() as c:
        c.execute(
            "SELECT count(*) AS count, min(read_id::text) AS read_id FROM platform_hr_agent.read_records WHERE work_id=%s",
            (UUID(work["work_id"]),),
        )
        row = c.fetchone()
        assert row["count"] == 1
        assert row["read_id"] == accepted["data"]["read_id"]
    assert repo.get_work(owner, work["work_id"])["state"] == "running"
    context = build_model_context(repo, resources, fence)
    assert context.purpose == "work"
    assert context.estimated_input_tokens + settings.budget_profile[
        "max_output_tokens"
    ] <= settings.provider_profile["context_window_tokens"]


def test_read_resource_guard_accounts_for_transactional_checkpoint_growth(
    repo, tmp_path, monkeypatch
):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / "read-boundary-settings")
    repo.settings = replace(
        settings,
        provider_profile={
            **settings.provider_profile,
            "context_window_tokens": 100000,
        },
    )
    root = tmp_path / "read-boundary-release"
    ref = publication(root, '引号"反斜杠\\控制\n😀中' * 300)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    repo.submit(
        owner,
        request(text="动态目标", references=[ref], budget_profile="test"),
        uuid4(),
    )
    fence = repo.claim("w", 60)
    attempt = repo.prepare_model(fence, model_context(revision=fence.input_revision))
    repo.mark_model_sending(fence, attempt.attempt_id)
    operation = repo.commit_model(
        fence,
        attempt.attempt_id,
        reply("", [ToolCall("boundary", "read_resource", {"ref": ref, "limit": 3000})]),
    )[0]
    observed = {}

    def reject_one_below_final_shape(*args, **kwargs):
        checkpoint = kwargs["checkpoint"]
        assert checkpoint["readings"][0]["state"] == "partial"
        assert checkpoint["readings"][0]["remaining_ranges"]
        assert len(args[4]["read_id"]) == 36
        required = ensure_read_result_fits_summary(*args, **kwargs)
        observed["required"] = required
        repo.settings = replace(
            repo.settings,
            provider_profile={
                **repo.settings.provider_profile,
                "context_window_tokens": required - 1,
            },
        )
        return ensure_read_result_fits_summary(*args, **kwargs)

    monkeypatch.setattr(
        "app.hr_agent.context.ensure_read_result_fits_summary",
        reject_one_below_final_shape,
    )

    rejected = execute_tool(repo, resources, fence, operation)

    assert rejected["error"]["code"] == "invalid_input"
    assert observed["required"] > 0
    with repo.transaction() as c:
        c.execute(
            "SELECT count(*) AS count FROM platform_hr_agent.read_records WHERE work_id=%s",
            (fence.work_id,),
        )
        assert c.fetchone()["count"] == 0
    assert repo.get_work(owner, str(fence.work_id))["checkpoint"]["readings"] == []


def test_hard_window_progressively_summarizes_large_unconsumed_batch_after_resume(
    repo, tmp_path
):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / "large-batch-settings")
    settings = replace(
        settings,
        budget_profile={
            **settings.budget_profile,
            "input_target_tokens": 50000,
            "input_trigger_tokens": 60000,
            "max_output_tokens": 2000,
        },
        provider_profile={
            **settings.provider_profile,
            "context_window_tokens": 75000,
        },
    )
    repo.settings = settings
    root = tmp_path / "large-batch-release"
    ref = publication(root, "中" * 60000)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    work = repo.submit(owner, request(references=[ref], budget_profile="test"), uuid4())
    fence = repo.claim("w", 60)
    large_unread_tool_batch(repo, resources, fence, ref)

    repo.wait_for_budget(fence)
    waiting = repo.get_work(owner, work["work_id"])
    assert waiting["state"] == "waiting_budget"
    repo.extend_budget(
        owner,
        work["work_id"],
        {
            "expected_budget_revision": waiting["budget"]["revision"],
            "addition": {
                "model_calls": 1,
                "total_tokens": 1,
                "active_seconds": 1,
            },
            "reason": "继续同一批次",
        },
        uuid4(),
    )
    fence = repo.claim("w-resume", 60)

    summaries = 0
    while True:
        context = build_model_context(repo, resources, fence)
        if context.purpose == "work":
            break
        assert len(context.summary_provenance["derived_from"]) == 1
        attempt = repo.prepare_model(fence, context)
        repo.mark_model_sending(fence, attempt.attempt_id)
        repo.commit_model(fence, attempt.attempt_id, reply("保留完整来源的阶段摘要"))
        repo.commit_summary(fence, attempt.attempt_id, context.summary_provenance)
        summaries += 1

    assert summaries == 2
    assert sum(message["role"] == "tool" for message in context.messages) == 1
    assert repo.get_work(owner, work["work_id"])["state"] == "running"


@pytest.mark.parametrize("oversize", ["prefix", "entry"])
def test_indivisible_context_overflow_blocks_with_recoverable_reason(
    repo, tmp_path, oversize
):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / ("oversize-" + oversize))
    settings = replace(
        settings,
        budget_profile={
            **settings.budget_profile,
            "input_target_tokens": 3000,
            "input_trigger_tokens": 4000,
            "max_output_tokens": 1000,
        },
        provider_profile={
            **settings.provider_profile,
            "context_window_tokens": 7000,
        },
    )
    repo.settings = settings
    root = tmp_path / ("oversize-release-" + oversize)
    ref = publication(root, "中" * 10000)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    text = "中" * 10000 if oversize == "prefix" else "当前目标"
    references = [] if oversize == "prefix" else [ref]
    work = repo.submit(
        owner,
        request(text=text, references=references, budget_profile="test"),
        uuid4(),
    )
    fence = repo.claim("w", 60)
    if oversize == "entry":
        large_unread_tool_batch(repo, resources, fence, ref, count=1)

    with pytest.raises(WorkPaused) as paused:
        build_model_context(repo, resources, fence)

    assert paused.value.view["state"] == "blocked"
    assert paused.value.view["block_reason"] == "context_too_large"
    assert repo.get_work(owner, work["work_id"])["budget"]["charged_calls"] == (
        1 if oversize == "entry" else 0
    )


def test_oversize_user_history_requires_new_work_and_preserves_old_work(repo, tmp_path):
    from dataclasses import replace

    from hr_agent_support import make_hr_settings

    settings = make_hr_settings(tmp_path / "oversize-history-settings")
    repo.settings = replace(
        settings,
        budget_profile={
            **settings.budget_profile,
            "input_target_tokens": 3000,
            "input_trigger_tokens": 4000,
            "max_output_tokens": 1000,
        },
        provider_profile={
            **settings.provider_profile,
            "context_window_tokens": 15000,
        },
    )
    root = tmp_path / "oversize-history-release"
    publication(root)
    resources = ResourceReader(
        repo, PublishedKnowledge(root), authorize_objects=lambda *a: None
    )
    repo.scope_validator = resources.validate_scope
    owner = uuid4()
    original = "中" * 10000
    old = repo.submit(owner, request(text=original, budget_profile="test"), uuid4())
    fence = repo.claim("old-1", 60)
    with pytest.raises(WorkPaused):
        build_model_context(repo, resources, fence)

    repo.append_input(
        owner,
        old["work_id"],
        {
            "expected_input_revision": 1,
            "text": "缩短后的目标",
            "objects": [],
            "references": [],
            "question_id": None,
        },
        uuid4(),
    )
    fence = repo.claim("old-2", 60)
    with pytest.raises(WorkPaused) as paused:
        build_model_context(repo, resources, fence)
    assert paused.value.view["block_reason"] == "context_too_large"

    with repo.transaction() as c:
        c.execute(
            "SELECT * FROM platform_hr_agent.entries WHERE work_id=%s AND kind='user' ORDER BY seq",
            (UUID(old["work_id"]),),
        )
        rows = c.fetchall()
    assert [
        repo._unseal("entries", row["entry_id"], "sealed_body", row)["body"]
        for row in rows
    ] == [
        original,
        "缩短后的目标",
    ]

    new = repo.submit(
        owner,
        request(text="按较小范围重新开始", budget_profile="test"),
        uuid4(),
    )
    new_fence = repo.claim("new", 60)
    context = build_model_context(repo, resources, new_fence)
    assert context.purpose == "work"
    assert repo.get_work(owner, old["work_id"])["state"] == "blocked"
    assert repo.get_work(owner, new["work_id"])["state"] == "running"


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
    assert attempt.tools == ()
    assert [message["role"] for message in attempt.messages] == [
        "system",
        "user",
        "user",
        "user",
    ]
    historical = json.loads(attempt.messages[2]["content"])["historical_records"]
    assert [record["entry_id"] for record in historical] == [
        source["entry_id"] for source in context.summary_provenance["derived_from"]
    ]
    assert all(
        set(record) == {"entry_id", "seq", "input_revision", "kind", "messages"}
        for record in historical
    )
    assert any(
        message["role"] == "tool"
        for record in historical
        for message in record["messages"]
    )
    assert context.estimated_input_tokens == estimate_input_tokens(
        context.messages, (), settings.provider_profile["tokenizer"]
    )
    assert (
        context.estimated_input_tokens + settings.budget_profile["max_output_tokens"]
        <= settings.provider_profile["context_window_tokens"]
    )
    assert attempt.messages[-1] == {
        "role": "user",
        "content": attempt.messages[0]["content"],
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
        assert (
            next_context.estimated_input_tokens
            + settings.budget_profile["max_output_tokens"]
            <= settings.provider_profile["context_window_tokens"]
        )
        assert any(message["role"] == "tool" for message in next_context.messages)


def test_summary_prefix_that_leaves_no_history_window_blocks_for_input_change(
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

    blocked = repo.get_work(owner, work["work_id"])
    assert blocked["state"] == "blocked"
    assert blocked["block_reason"] == "context_too_large"


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
