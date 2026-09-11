"""C1: real local uploads/DB/Loop, scripted model boundary; fictional data only."""

import importlib.util
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from app.hr_agent.types import HrAgentProblem
from tests import test_hr_agent_materials as fixtures
from tests.test_hr_agent_material_parsing import pdf, upload_document

uploaded, secured, database = fixtures.uploaded, fixtures.secured, fixtures.database
_FIXTURES = (uploaded, secured, database)


def test_candidate_intake_service_exists():
    assert importlib.util.find_spec("app.hr_agent.candidates") is not None


@pytest.fixture
def intake(uploaded, tmp_path):
    from app.hr_agent.candidates import CandidateIntakeService
    from app.hr_agent.material_parsing import MaterialParsingService
    from app.hr_agent.resources import PublishedKnowledge, ResourceReader
    from tests.test_hr_agent_context import publication

    _, _, repo, owner, materials, _, _, _ = uploaded
    repo.settings = replace(
        repo.settings,
        budget_profile={
            **repo.settings.budget_profile,
            "input_target_tokens": 20000,
            "input_trigger_tokens": 24000,
        },
    )
    publication(tmp_path)
    knowledge = PublishedKnowledge(tmp_path)
    resources = ResourceReader(repo, knowledge, material_service=materials)
    repo.scope_validator = resources.validate_scope
    repo.release_provider = knowledge.metadata
    MaterialParsingService(repo, materials)
    service = CandidateIntakeService(
        repo, materials, processing_authorizer=lambda o, a: o == owner
    )
    # Synthetic local fixture explicitly authorizes both intake and model outbox.
    repo.personal_processing_authorizer = service.processing_authorizer
    return service, resources


def batch_request(aids):
    return {
        "attachment_ids": list(aids),
        "position_id": None,
        "text": "为虚构简历整理人工核对草稿并保存研究成果。",
        "budget_profile": "test",
    }


def finish_profile(
    uploaded, intake, item, *, read_limit=None, save=True, answer_failure=False
):
    from app.hr_agent.runtime import run_work
    from tests.test_hr_agent_runtime import ScriptModel, answer, tool

    service, resources = intake
    _, _, repo, owner, _, _, _, _ = uploaded
    ref = item["text_ref"]
    read = {"ref": ref, **({"limit": read_limit} if read_limit else {})}
    steps = [tool("read_resource", read)]
    if save:
        steps.append(
            tool(
                "save_result",
                {
                    "kind": "research",
                    "title": "待核对虚构简历",
                    "body": "虚构姓名：白榆；经历：银翼机器人调试。",
                    "result_id": None,
                    "expected_revision": None,
                    "objects": [],
                    "source_refs": [ref],
                    "preceding_refs": [],
                    "base_standard_ref": None,
                    "changes": [],
                    "basis": [],
                },
            )
        )
    if answer_failure:
        from app.hr_agent.model import ModelTransportError

        steps.append(ModelTransportError("provider_refused"))
    else:
        steps.append(answer("已完成。"))
    model = ScriptModel(steps)
    fence = repo.claim("c1-test", 60)
    assert str(fence.work_id) == item["work_id"]
    assert run_work(repo, model, resources, fence)["state"] == (
        "failed" if answer_failure else "completed"
    )
    # A sibling may precede this item in the fair coordinator queue.
    for _ in range(4):
        service.advance_one("coordinator")
        current = service.get_item(owner, item["item_id"])
        if current["state"] != "profiling":
            return current, model
    return current, model


def confirmation(item, **changes):
    return {
        "expected_row_version": item["row_version"],
        "result_ref": item["result_ref"],
        "display_name": "白榆虚构人名",
        "summary": "人工核对：银翼机器人项目。",
        "decision": {"kind": "create"},
        "reviewed_limitations": True,
        **changes,
    }


def test_batch_is_atomic_private_and_one_work_per_file(uploaded, intake, database):
    service, _ = intake
    _, _, repo, owner, _, aid, _, _ = uploaded
    second = upload_document(
        uploaded,
        database,
        b"Fictional B robot experience.",
        "text/plain",
        "fictional-b.txt",
    )
    key = uuid4()
    request = batch_request([aid, second])
    batch = service.create_batch(owner, request, key)
    assert service.create_batch(owner, request, key) == batch
    with pytest.raises(HrAgentProblem) as e:
        service.create_batch(owner, batch_request([second]), key)
    assert e.value.http_status == 409
    with pytest.raises(HrAgentProblem):
        service.create_batch(uuid4(), request, uuid4())
    assert len(service.list_batches(owner)["items"]) == 1
    assert service.advance_one("a") and service.advance_one("b")
    items = service.get_batch(owner, batch["batch_id"])["items"]
    assert len({i["work_id"] for i in items}) == 2
    for item in items:
        with repo.transaction() as c:
            current, _ = repo._input(c, repo._work(c, owner, item["work_id"]))
            assert current["references"] == [item["text_ref"]]
            c.execute(
                "SELECT 1 FROM platform_hr_agent.personal_materials WHERE owner_id=%s AND attachment_id=%s",
                (owner, UUID(item["attachment_id"])),
            )
            assert c.fetchone()
    with pytest.raises(HrAgentProblem):
        service.get_batch(uuid4(), batch["batch_id"])


def test_loop_narrative_human_confirm_and_encrypted_replay(uploaded, intake, database):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("a")
    item, _ = finish_profile(
        uploaded, intake, service.get_batch(owner, batch["batch_id"])["items"][0]
    )
    assert item["state"] == "awaiting_review" and not item["unread_ranges"]
    assert item["profile_body"].startswith("虚构姓名")
    key = uuid4()
    request = confirmation(item)
    receipt = service.confirm_item(owner, item["item_id"], request, key)
    assert service.confirm_item(owner, item["item_id"], request, key) == receipt
    with pytest.raises(HrAgentProblem) as e:
        service.confirm_item(
            owner, item["item_id"], {**request, "summary": "changed"}, key
        )
    assert e.value.http_status == 409
    candidate = service.read_candidate(owner, receipt["candidate_id"])
    assert candidate["display_name"] == request["display_name"]
    assert candidate["documents"][0]["summary"] == request["summary"]
    assert (
        service.list_candidates(owner)["items"][0]["candidate_id"]
        == receipt["candidate_id"]
    )
    with pytest.raises(HrAgentProblem):
        service.read_candidate(uuid4(), receipt["candidate_id"])
    with database.admin_connection() as c:
        for table in (
            "candidate_batches",
            "candidate_intake_items",
            "personal_materials",
            "candidates",
            "candidate_documents",
            "operations",
        ):
            raw = str(
                c.execute(
                    f"SELECT row_to_json(t)::text FROM platform_hr_agent.{table} t"
                ).fetchall()
            )
            assert request["display_name"] not in raw and request["summary"] not in raw
            assert batch_request([aid])["text"] not in raw


def test_unread_and_revoked_source_prevent_unreviewed_confirmation(
    uploaded, intake, database
):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("a")
    item, _ = finish_profile(
        uploaded,
        intake,
        service.get_batch(owner, batch["batch_id"])["items"][0],
        read_limit=4,
    )
    assert item["unread_ranges"]
    with pytest.raises(HrAgentProblem):
        service.confirm_item(
            owner,
            item["item_id"],
            confirmation(item, reviewed_limitations=False),
            uuid4(),
        )
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (aid,),
        )
    with pytest.raises(HrAgentProblem) as e:
        service.confirm_item(owner, item["item_id"], confirmation(item), uuid4())
    assert e.value.http_status == 410
    assert service.get_item(owner, item["item_id"])["profile_body"] is None


def test_failed_sibling_and_profile_retry_are_isolated(uploaded, intake, database):
    service, _ = intake
    _, _, _, owner, materials, aid, _, _ = uploaded
    blank = upload_document(
        uploaded, database, pdf(blank=True), "application/pdf", "blank.pdf"
    )
    batch = service.create_batch(owner, batch_request([aid, blank]), uuid4())
    service.advance_one("a")
    service.advance_one("b")
    assert materials.parsing.process_one("parse")
    for _ in range(3):
        service.advance_one("c")
    items = service.get_batch(owner, batch["batch_id"])["items"]
    good = next(i for i in items if i["attachment_id"] == aid)
    bad = next(i for i in items if i["attachment_id"] == blank)
    assert bad["state"] == "failed" and bad["work_id"] is None
    missing, _ = finish_profile(uploaded, intake, good, save=False)
    assert missing["error_code"] == "profile_missing"
    key = uuid4()
    request = {"expected_row_version": missing["row_version"], "stage": "profile"}
    retried = service.retry_item(owner, missing["item_id"], request, key)
    assert service.retry_item(owner, missing["item_id"], request, key) == retried
    with pytest.raises(HrAgentProblem) as e:
        service.retry_item(owner, missing["item_id"], request, uuid4())
    assert e.value.http_status == 409
    service.advance_one("retry")
    assert service.get_item(owner, good["item_id"])["work_id"] != good["work_id"]
    assert service.get_item(owner, bad["item_id"])["state"] == "failed"


def test_link_existing_never_overwrites_profile(uploaded, intake, database):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    first = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("a")
    item, _ = finish_profile(
        uploaded, intake, service.get_batch(owner, first["batch_id"])["items"][0]
    )
    created = service.confirm_item(owner, item["item_id"], confirmation(item), uuid4())
    second_id = upload_document(
        uploaded, database, b"Fictional later CV.", "text/plain", "later.txt"
    )
    second = service.create_batch(owner, batch_request([second_id]), uuid4())
    service.advance_one("a")
    item, _ = finish_profile(
        uploaded, intake, service.get_batch(owner, second["batch_id"])["items"][0]
    )
    with pytest.raises(HrAgentProblem):
        service.confirm_item(
            owner,
            item["item_id"],
            confirmation(
                item, decision={"kind": "link_existing", "candidate_id": str(uuid4())}
            ),
            uuid4(),
        )
    linked = service.confirm_item(
        owner,
        item["item_id"],
        confirmation(
            item,
            display_name="新文件称谓",
            decision={"kind": "link_existing", "candidate_id": created["candidate_id"]},
        ),
        uuid4(),
    )
    candidate = service.read_candidate(owner, linked["candidate_id"])
    assert (
        linked["candidate_id"] == created["candidate_id"]
        and len(candidate["documents"]) == 2
    )
    assert candidate["display_name"] == "白榆虚构人名"


def test_processing_gate_defaults_closed(uploaded):
    from app.hr_agent.candidates import CandidateIntakeService

    _, _, repo, owner, materials, aid, _, _ = uploaded
    service = CandidateIntakeService(repo, materials)
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("a")
    item = service.get_batch(owner, batch["batch_id"])["items"][0]
    assert item["work_id"] is None and item["error_code"] == "processing_not_authorized"


def test_explicit_parse_retry_preserves_fence_and_sibling(
    uploaded, intake, database, monkeypatch
):
    from app.hr_agent import material_parsing
    from tests.test_hr_agent_material_parsing import DOCX, docx

    service, _ = intake
    _, _, _, owner, materials, aid, _, _ = uploaded
    binary = upload_document(uploaded, database, docx(), DOCX, "fiction.docx")
    batch = service.create_batch(owner, batch_request([binary, aid]), uuid4())
    service.advance_one("a")
    service.advance_one("a")
    real_parse = material_parsing.parse_bytes
    monkeypatch.setattr(
        material_parsing,
        "parse_bytes",
        lambda *_a, **_kw: material_parsing._failure("parse_timeout"),
    )
    assert materials.parsing.process_one("first")
    for _ in range(3):
        service.advance_one("a")
    items = service.get_batch(owner, batch["batch_id"])["items"]
    failed = next(i for i in items if i["attachment_id"] == binary)
    good = next(i for i in items if i["attachment_id"] == aid)
    assert failed["state"] == "failed" and good["work_id"]
    key = uuid4()
    request = {"expected_row_version": failed["row_version"], "stage": "parse"}
    receipt = service.retry_item(owner, failed["item_id"], request, key)
    assert service.retry_item(owner, failed["item_id"], request, key) == receipt
    monkeypatch.setattr(material_parsing, "parse_bytes", real_parse)
    assert materials.parsing.process_one("second")
    for _ in range(3):
        service.advance_one("a")
    now = service.get_item(owner, failed["item_id"])
    assert now["state"] == "profiling" and now["work_id"] != good["work_id"]
    assert service.get_item(owner, good["item_id"])["work_id"] == good["work_id"]
    with database.admin_connection() as c:
        assert c.execute(
            "SELECT attempts,retry_generation,generation_attempts FROM platform_hr_agent.material_parses WHERE attachment_id=%s",
            (binary,),
        ).fetchone() == (2, 1, 1)


def test_submit_committed_before_item_binding_recovers_same_work(
    uploaded, intake, monkeypatch
):
    service, _ = intake
    _, _, repo, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    original = service._set

    def crash(*args, **kwargs):
        raise OSError("local process loss after durable work submission")

    monkeypatch.setattr(service, "_set", crash)
    with pytest.raises(OSError):
        service.advance_one("lost")
    monkeypatch.setattr(service, "_set", original)
    assert service.advance_one("recovered")
    item = service.get_batch(owner, batch["batch_id"])["items"][0]
    assert item["work_id"]
    with repo.transaction() as c:
        c.execute(
            "SELECT count(*) AS n FROM platform_hr_agent.works WHERE owner_id=%s",
            (owner,),
        )
        assert c.fetchone()["n"] == 1


def test_revised_work_cannot_be_confirmed_as_original_file(uploaded, intake):
    service, _ = intake
    _, _, repo, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("a")
    item = service.get_batch(owner, batch["batch_id"])["items"][0]
    repo.append_input(
        owner,
        item["work_id"],
        {
            "expected_input_revision": 1,
            "text": "更换了原本任务的要求",
            "objects": [],
            "references": [item["text_ref"]],
            "question_id": None,
        },
        uuid4(),
    )
    service.advance_one("a")
    changed = service.get_item(owner, item["item_id"])
    assert (
        changed["state"] == "failed"
        and changed["error_code"] == "profile_scope_changed"
    )


def test_batch_replay_rejects_source_identity_change(uploaded, intake, database):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    key = uuid4()
    request = batch_request([aid])
    service.create_batch(owner, request, key)
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET immutable_locator='etag:changed' WHERE attachment_id=%s",
            (aid,),
        )
    with pytest.raises(HrAgentProblem) as e:
        service.create_batch(owner, request, key)
    assert e.value.http_status == 410


def test_concurrent_confirmation_cas_and_revoked_candidate_read(
    uploaded, intake, database
):
    from concurrent.futures import ThreadPoolExecutor

    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("a")
    item, _ = finish_profile(
        uploaded, intake, service.get_batch(owner, batch["batch_id"])["items"][0]
    )

    def confirm(_):
        try:
            return service.confirm_item(
                owner, item["item_id"], confirmation(item), uuid4()
            )
        except HrAgentProblem as e:
            return e.http_status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, range(2)))
    assert results.count(409) == 1
    result = next(r for r in results if isinstance(r, dict))
    with database.admin_connection() as c:
        c.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (aid,),
        )
    with pytest.raises(HrAgentProblem):
        service.read_candidate(owner, result["candidate_id"])
    assert service.list_candidates(owner)["items"] == [
        {"candidate_id": result["candidate_id"], "available": False}
    ]


def test_processing_gate_retry_requires_explicit_action(uploaded, intake):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    service.processing_authorizer = None
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("a")
    item = service.get_batch(owner, batch["batch_id"])["items"][0]
    assert item["error_code"] == "processing_not_authorized"
    service.processing_authorizer = lambda *_: True
    assert not service.advance_one("a"), (
        "configuration change alone must not run failed personal work"
    )
    assert item["failed_stage"] == "profile"
    service.retry_item(
        owner,
        item["item_id"],
        {"expected_row_version": item["row_version"], "stage": "profile"},
        uuid4(),
    )
    service.advance_one("a")
    assert service.get_item(owner, item["item_id"])["work_id"]


def test_optional_owned_position_relation_is_persisted(uploaded, intake, database):
    from app.hr.models import CreateManualPosition
    from app.hr.repository import HrPositionRepository

    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    positions = HrPositionRepository(database.dsn)
    position = positions.create_manual(
        CreateManualPosition(owner, uuid4(), uuid4(), "虚构结构岗位")
    )
    request = {**batch_request([aid]), "position_id": str(position.position_id)}
    with pytest.raises(HrAgentProblem):
        service.create_batch(owner, {**request, "position_id": str(uuid4())}, uuid4())
    batch = service.create_batch(owner, request, uuid4())
    service.advance_one("a")
    item, _ = finish_profile(
        uploaded, intake, service.get_batch(owner, batch["batch_id"])["items"][0]
    )
    created = service.confirm_item(owner, item["item_id"], confirmation(item), uuid4())
    with database.connection() as c:
        assert (
            c.execute(
                "SELECT position_id FROM platform_hr_agent.candidate_positions WHERE owner_id=%s AND candidate_id=%s",
                (owner, created["candidate_id"]),
            ).fetchone()[0]
            == position.position_id
        )


def test_saved_narrative_survives_final_answer_failure(uploaded, intake):
    service, _ = intake
    _, _, _, owner, _, aid, _, _ = uploaded
    batch = service.create_batch(owner, batch_request([aid]), uuid4())
    service.advance_one("a")
    item, _ = finish_profile(
        uploaded,
        intake,
        service.get_batch(owner, batch["batch_id"])["items"][0],
        answer_failure=True,
    )
    assert item["state"] == "awaiting_review"
    assert item["work_state"] == "failed"
    assert item["profile_body"]
    assert (
        service.confirm_item(owner, item["item_id"], confirmation(item), uuid4())[
            "state"
        ]
        == "confirmed"
    )
