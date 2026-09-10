from uuid import UUID, uuid4

import pytest
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from app.hr_agent.repository import HrAgentRepository
from app.hr_agent.types import HrAgentProblem, ModelContext, ModelReply, ToolCall, Usage
from hr_agent_support import hr_agent_database


@pytest.fixture(scope="module")
def database():
    with hr_agent_database() as db:
        yield db


@pytest.fixture
def repo(database):
    with database.admin_connection() as c:
        c.execute(
            "TRUNCATE platform_hr_agent.threads, platform_hr_agent.operations, platform_hr_agent.standards CASCADE"
        )
    return HrAgentRepository(
        database.connection,
        ContentCodec(IdentityKeyring(1, "platform-content-encryption", {1: b"k" * 32})),
        scope_validator=lambda *a: None,
    )


def proposal_args(position, **updates):
    return dict(
        result_id=None,
        expected_revision=None,
        kind="standard_proposal",
        title="公开岗位建议",
        body="明确职责与边界",
        objects=[position],
        source_refs=[],
        preceding_refs=[],
        base_standard_ref=None,
        changes=[
            {"action": "add", "target_item_id": None, "text": "独立处理公开案例"},
            {"action": "add", "target_item_id": None, "text": "说明判断边界"},
        ],
        basis=[{"kind": "user_temporary", "input_revision": 1, "ref": None}],
        **updates,
    )


def save(repo, owner, args, *, objects=None, references=None, keep=False):
    work = repo.submit(
        owner,
        {
            "thread_id": None,
            "text": "校准公开岗位",
            "objects": objects or args["objects"],
            "references": references or [],
            "budget_profile": "calibration-test",
        },
        uuid4(),
    )
    fence = repo.claim("b3-test", 60)
    context = ModelContext(
        "work",
        ({"role": "user", "content": "公开岗位"},),
        tuple(references or []),
        (),
        100,
        1,
    )
    attempt = repo.prepare_model(fence, context)
    repo.mark_model_sending(fence, attempt.attempt_id)
    operations = repo.commit_model(
        fence,
        attempt.attempt_id,
        ModelReply(
            "",
            (ToolCall("proposal", "save_result", args),),
            "stop",
            Usage(None, 100, 20, "reported"),
        ),
    )
    outcome = repo.execute_local_tool(fence, operations[0])
    if keep:
        return work, fence, outcome
    repo.cancel(owner, work["work_id"], "fixture done", uuid4())
    return outcome


def test_prepare_proposal_has_server_ids_and_single_explicit_target(repo):
    from app.hr_agent.proposals import prepare_proposal

    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    args = proposal_args(position)
    with repo.transaction() as c:
        document = prepare_proposal(
            repo,
            c,
            {"owner_id": owner, "work_id": None, "input_revision": 1},
            {},
            args,
            {
                "objects": [position, {"kind": "position", "id": str(uuid4())}],
                "references": [],
            },
            (),
        )
    assert document["objects"] == [position]
    assert len({UUID(change["change_id"]) for change in document["changes"]}) == 2
    assert "change_id" not in args["changes"][0]


def test_reject_direct_candidate_and_duplicate_target(repo):
    from app.hr_agent.proposals import prepare_proposal

    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    with repo.transaction() as c, pytest.raises(HrAgentProblem) as error:
        prepare_proposal(
            repo,
            c,
            {"owner_id": owner, "work_id": None, "input_revision": 1},
            {},
            proposal_args(position),
            {
                "objects": [position, {"kind": "candidate", "id": str(uuid4())}],
                "references": [],
            },
            (),
        )
    assert error.value.problem["code"] == "personal_source_not_allowed"


def test_transitive_candidate_ancestry_cannot_be_removed_by_relabeling(repo):
    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    candidate = {"kind": "candidate", "id": str(uuid4())}
    args = proposal_args(position)
    args.update(kind="retrospective", changes=[], basis=[], objects=[candidate])
    private = save(repo, owner, args)["data"]
    assert private
    args.update(objects=[position], source_refs=[private["ref"]])
    intermediate = save(repo, owner, args, references=[private["ref"]])["data"]
    assert intermediate
    outcome = save(
        repo, owner, proposal_args(position), references=[intermediate["ref"]]
    )
    assert outcome["status"] == "invalid"
    assert outcome["error"]["code"] == "personal_source_not_allowed"


def test_proposal_rejects_duplicate_base_targets_and_missing_base(repo):
    from app.hr_agent.standards import StandardService
    from test_hr_agent_standards import confirmation

    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    proposal = save(repo, owner, proposal_args(position))["data"]
    first = StandardService(repo).confirm(
        owner, position["id"], confirmation(proposal), uuid4()
    )
    args = proposal_args(position)
    assert save(repo, owner, args)["error"]["code"] == "revision_conflict"
    args.update(
        base_standard_ref=first["ref"],
        basis=[
            {"kind": "confirmed_standard", "ref": first["ref"], "input_revision": None}
        ],
        changes=[
            {
                "action": "remove",
                "target_item_id": first["items"][0]["item_id"],
                "text": None,
            }
        ]
        * 2,
    )
    assert save(repo, owner, args)["error"]["code"] == "invalid_input"


def test_public_method_used_in_candidate_work_is_not_personal_material(repo):
    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    candidate = {"kind": "candidate", "id": str(uuid4())}
    method = {
        "kind": "method",
        "id": "public-method",
        "revision": "v1",
        "sha256": "a" * 64,
    }
    args = proposal_args(position)
    args.update(
        kind="retrospective",
        changes=[],
        basis=[],
        objects=[candidate],
        source_refs=[method],
    )
    assert save(repo, owner, args, references=[method])["status"] == "ok"
    args = proposal_args(position)
    args["source_refs"] = [method]
    assert save(repo, owner, args, references=[method])["status"] == "ok"


def test_explicit_only_private_source_is_persisted_in_transitive_graph(repo):
    owner = uuid4()
    position = {"kind": "position", "id": str(uuid4())}
    candidate = {"kind": "candidate", "id": str(uuid4())}
    args = proposal_args(position)
    args.update(kind="retrospective", changes=[], basis=[], objects=[candidate])
    private = save(repo, owner, args)["data"]
    args.update(objects=[position], preceding_refs=[private["ref"]])
    intermediate = save(repo, owner, args)["data"]
    assert intermediate
    outcome = save(
        repo, owner, proposal_args(position), references=[intermediate["ref"]]
    )
    assert outcome["status"] == "invalid"
    assert outcome["error"]["code"] == "personal_source_not_allowed"
