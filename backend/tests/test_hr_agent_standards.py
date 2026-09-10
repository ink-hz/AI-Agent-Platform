from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_hr_agent_proposals as proposal_support
from app.hr_agent.types import HrAgentProblem, validate_contract
from test_hr_agent_proposals import proposal_args, save

database = proposal_support.database
repo = proposal_support.repo


def confirmation(proposal, selected=None, expected=None):
    return {
        "proposal_ref": proposal["ref"],
        "selected_change_ids": selected
        if selected is not None
        else [c["change_id"] for c in proposal["changes"]],
        "expected_standard_revision": expected,
    }


def saved(repo, owner, position, **updates):
    args = proposal_args(position)
    args.update(updates)
    result = save(repo, owner, args)
    assert result["status"] == "ok", result
    return result["data"]


def test_partial_confirmation_preserves_items_and_old_exact_revision(repo):
    from app.hr_agent.standards import StandardService

    service = StandardService(repo)
    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    proposal = saved(repo, owner, position)
    first = service.confirm(owner, position["id"], confirmation(proposal), uuid4())
    validate_contract("StandardView", first)
    a, b = first["items"]
    proposal2 = saved(
        repo,
        owner,
        position,
        base_standard_ref=first["ref"],
        basis=[
            {"kind": "confirmed_standard", "ref": first["ref"], "input_revision": None}
        ],
        changes=[
            {
                "action": "replace",
                "target_item_id": a["item_id"],
                "text": "进一步明确责任",
            },
            {"action": "remove", "target_item_id": b["item_id"], "text": None},
            {"action": "add", "target_item_id": None, "text": "新增可选职责"},
        ],
    )
    key = uuid4()
    request = confirmation(
        proposal2, [proposal2["changes"][0]["change_id"]], first["ref"]["revision"]
    )
    second = service.confirm(owner, position["id"], request, key)
    assert second["items"] == [{"item_id": a["item_id"], "text": "进一步明确责任"}, b]
    assert service.current(owner, position["id"]) == second
    assert service.read(owner, first["ref"]) == first
    assert service.confirm(owner, position["id"], request, key) == second
    with pytest.raises(HrAgentProblem) as error:
        service.confirm(owner, position["id"], request, uuid4())
    assert error.value.http_status == 409
    assert (
        error.value.problem["details"]["current_revision"] == second["ref"]["revision"]
    )
    request["expected_standard_revision"] = second["ref"]["revision"]
    with pytest.raises(HrAgentProblem):
        service.confirm(owner, position["id"], request, uuid4())


def test_first_confirmation_race_is_serialized(repo):
    from app.hr_agent.standards import StandardService

    service = StandardService(repo)
    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    proposals = [saved(repo, owner, position), saved(repo, owner, position)]

    def confirm(proposal):
        try:
            return service.confirm(
                owner, position["id"], confirmation(proposal), uuid4()
            )
        except HrAgentProblem as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(confirm, proposals))
    assert sum(isinstance(o, dict) for o in outcomes) == 1
    error = next(o for o in outcomes if isinstance(o, HrAgentProblem))
    assert error.http_status == 409
    assert (
        error.problem["details"]["current_revision"]
        == service.current(owner, position["id"])["ref"]["revision"]
    )


def test_invalid_selection_and_permission_revocation_never_write(repo):
    from app.hr_agent.standards import StandardService
    from app.hr_agent.types import problem

    service = StandardService(repo)
    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    proposal = saved(repo, owner, position)
    for selected in [[], [str(uuid4())], [proposal["changes"][0]["change_id"]] * 2]:
        with pytest.raises(HrAgentProblem) as error:
            service.confirm(
                owner, position["id"], confirmation(proposal, selected), uuid4()
            )
        assert error.value.http_status == 422
    with repo.transaction() as c:
        c.execute("SELECT count(*) AS n FROM platform_hr_agent.standard_revisions")
        assert c.fetchone()["n"] == 0
    key = uuid4()
    request = confirmation(proposal)
    first = service.confirm(owner, position["id"], request, key)

    def revoked(*args):
        raise problem("scope_denied", http_status=403)

    repo.scope_validator = revoked
    for action in [
        lambda: service.confirm(owner, position["id"], request, key),
        lambda: service.read(owner, first["ref"]),
    ]:
        with pytest.raises(HrAgentProblem) as error:
            action()
        assert error.value.http_status == 403


def test_changed_proposal_returns_current_standard_revision_and_zero_writes(repo):
    from app.hr_agent.standards import StandardService
    from app.hr_agent.types import ModelContext, ModelReply, ToolCall, Usage

    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    service = StandardService(repo)
    _work, fence, outcome = save(repo, owner, proposal_args(position), keep=True)
    proposal = outcome["data"]
    args = proposal_args(position)
    args.update(
        result_id=proposal["ref"]["id"],
        expected_revision=proposal["ref"]["revision"],
        body="修订后的公开岗位建议",
    )
    context = ModelContext(
        "work", ({"role": "user", "content": "公开岗位"},), (), (), 100, 1
    )
    attempt = repo.prepare_model(fence, context)
    repo.mark_model_sending(fence, attempt.attempt_id)
    operations = repo.commit_model(
        fence,
        attempt.attempt_id,
        ModelReply(
            "",
            (ToolCall("update", "save_result", args),),
            "stop",
            Usage(None, 100, 20, "reported"),
        ),
    )
    updated = repo.execute_local_tool(fence, operations[0])
    assert updated["status"] == "ok"
    with pytest.raises(HrAgentProblem) as error:
        service.confirm(owner, position["id"], confirmation(proposal), uuid4())
    assert error.value.problem["details"] == {
        "conflict_kind": "proposal_revision",
        "current_revision": None,
    }
    with repo.transaction() as c:
        c.execute("SELECT count(*) AS n FROM platform_hr_agent.standard_revisions")
        assert c.fetchone()["n"] == 0


def test_owner_filter_precedes_standard_decryption(repo):
    from app.hr_agent.repository import HrAgentRepository
    from app.hr_agent.standards import StandardService

    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    proposal = saved(repo, owner, position)
    standard = StandardService(repo).confirm(
        owner, position["id"], confirmation(proposal), uuid4()
    )

    class Bomb:
        def unseal_json(self, *a):
            pytest.fail("foreign owner reached decryption")

    secured = StandardService(
        HrAgentRepository(
            repo.connection_factory, Bomb(), scope_validator=lambda *a: None
        )
    )
    for action in [
        lambda: secured.read(uuid4(), standard["ref"]),
        lambda: secured.confirm(
            uuid4(), position["id"], confirmation(proposal), uuid4()
        ),
    ]:
        with pytest.raises(HrAgentProblem) as error:
            action()
        assert error.value.http_status == 404


def test_confirmation_failure_rolls_back_pointer_revision_operation_and_edges(
    repo, monkeypatch
):
    from app.hr_agent.standards import StandardService

    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    service = StandardService(repo)
    proposal = saved(repo, owner, position)
    original = repo._edges

    def crash(c, owner, kind, *args):
        if kind == "standard":
            raise RuntimeError("injected transaction failure")
        return original(c, owner, kind, *args)

    key = uuid4()
    request = confirmation(proposal)
    monkeypatch.setattr(repo, "_edges", crash)
    with pytest.raises(RuntimeError):
        service.confirm(owner, position["id"], request, key)
    with repo.transaction() as c:
        for table in ["standards", "standard_revisions"]:
            c.execute("SELECT count(*) AS n FROM platform_hr_agent." + table)
            assert c.fetchone()["n"] == 0
        c.execute(
            "SELECT count(*) AS n FROM platform_hr_agent.operations WHERE request_key=%s",
            (str(key),),
        )
        assert c.fetchone()["n"] == 0
    monkeypatch.setattr(repo, "_edges", original)
    assert service.confirm(owner, position["id"], request, key)["items"]


def test_revoked_current_standard_sources_block_conflict_and_replay(repo):
    from app.hr_agent.standards import StandardService
    from app.hr_agent.types import problem

    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    source_position = {"kind": "position", "id": str(uuid4())}
    service = StandardService(repo)
    first_proposal = saved(repo, owner, position)
    first = service.confirm(
        owner, position["id"], confirmation(first_proposal), uuid4()
    )
    base = {
        "base_standard_ref": first["ref"],
        "basis": [
            {"kind": "confirmed_standard", "ref": first["ref"], "input_revision": None}
        ],
    }
    stale = saved(repo, owner, position, **base)
    source_args = proposal_args(source_position)
    source_args.update(kind="research", basis=[], changes=[])
    source = save(repo, owner, source_args)["data"]
    second_proposal = saved(
        repo, owner, position, preceding_refs=[source["ref"]], **base
    )
    key = uuid4()
    request = confirmation(second_proposal, expected=first["ref"]["revision"])
    second = service.confirm(owner, position["id"], request, key)

    def scope(owner, objects, refs, work_id):
        if source_position in objects:
            raise problem("scope_denied", http_status=403)

    repo.scope_validator = scope
    for action in [
        lambda: service.read(owner, second["ref"]),
        lambda: service.confirm(owner, position["id"], request, key),
        lambda: service.confirm(
            owner,
            position["id"],
            confirmation(stale, expected=first["ref"]["revision"]),
            uuid4(),
        ),
    ]:
        with pytest.raises(HrAgentProblem) as error:
            action()
        assert error.value.http_status == 403


def test_identical_concurrent_confirmation_returns_one_receipt(repo):
    from app.hr_agent.standards import StandardService

    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    service = StandardService(repo)
    proposal = saved(repo, owner, position)
    key = uuid4()
    request = confirmation(proposal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda _: service.confirm(owner, position["id"], request, key), range(2)
            )
        )
    assert receipts[0] == receipts[1]
    with repo.transaction() as c:
        c.execute("SELECT count(*) AS n FROM platform_hr_agent.standard_revisions")
        assert c.fetchone()["n"] == 1
