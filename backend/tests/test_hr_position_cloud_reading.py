"""Cloud-phase position standards/results through authenticated HTTP and real PG."""

import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.agent_brain.authorization import AgentUseAuthorization
from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
from app.control_plane.authorization import AuthorizationRepository, AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.hr.models import CreateManualPosition
from app.hr.repository import HrNotFound, HrPositionRepository
from app.hr.position_intelligence_models import ConfirmContextModules, CreateContextDraft
from app.hr.position_intelligence_repository import PositionIntelligenceRepository
from app.hr.position_intelligence_routes import build_position_intelligence_router
from app.hr.position_intelligence_service import PositionIntelligenceService
from app.hr.tool_routes import build_hr_result_router
from app.hr.tool_service import HrToolService
from app.hr_agent.access import HrAccess
from app.hr_agent.repository import HrAgentRepository
from app.hr_agent.routes import build_hr_agent_router
from app.hr_agent.service import HrAgentService
from app.hr_agent.standards import StandardService
from app.hr_agent.types import ModelContext, ModelReply, ToolCall, Usage
from app.execution_relay.repository import ExecutionRelayRepository
from tests.hr_agent_support import hr_agent_database, make_hr_settings
from tests.test_hr_agent_proposals import proposal_args
from tests.test_hr_agent_standards import confirmation
from tests.test_hr_agent_worker_process import grant_test_owner


@pytest.fixture(scope="module")
def database():
    with hr_agent_database(cutover_phase="cloud") as value:
        yield value


def _object_authorizer(positions):
    def authorize(owner, obj):
        if obj["kind"] != "position":
            return False
        try:
            positions.position_for_owner(owner, UUID(obj["id"]))
        except HrNotFound:
            return False
        return True

    return authorize


@pytest.fixture
def cloud_api(tmp_path, database):
    with database.admin_connection() as connection:
        connection.execute(
            "TRUNCATE platform_hr_agent.threads, platform_hr_agent.operations, "
            "platform_hr_agent.standards, platform_hr.positions CASCADE"
        )
    owner = grant_test_owner(database)
    foreign_owner = uuid4()
    with database.admin_connection() as connection:
        generation = connection.execute(
            "SELECT active_generation_id FROM platform_control.directory_state WHERE singleton"
        ).fetchone()[0]
        connection.execute(
            "UPDATE platform_control.internal_users SET last_confirmed_generation_id=%s "
            "WHERE internal_user_id=%s",
            (generation, owner),
        )
        connection.execute(
            "INSERT INTO platform_control.internal_users "
            "(internal_user_id,display_name,status,last_confirmed_generation_id) "
            "VALUES (%s,'Other owner','active',%s)",
            (foreign_owner, generation),
        )
    settings = make_hr_settings(tmp_path)
    positions = HrPositionRepository(database.dsn)
    owned = positions.create_manual(
        CreateManualPosition(owner, uuid4(), uuid4(), "虚构云端岗位")
    )
    other_owned = positions.create_manual(
        CreateManualPosition(owner, uuid4(), uuid4(), "同用户其他岗位")
    )
    foreign = positions.create_manual(
        CreateManualPosition(foreign_owner, uuid4(), uuid4(), "其他用户岗位")
    )
    repository_holder = {}

    def authorize_reference(owner_id, ref, objects, _work_id):
        if ref["kind"] == "result":
            result = repository_holder["repository"].read_result(
                owner_id, ref["id"], ref["revision"]
            )
            return all(obj in result["objects"] for obj in objects)
        if ref["kind"] == "standard":
            standard = StandardService(repository_holder["repository"]).read(
                owner_id, ref
            )
            return any(
                obj["kind"] == "position" and obj["id"] == standard["ref"]["id"]
                for obj in objects
            )
        return False

    access = HrAccess(
        AgentUseAuthorization(database.dsn),
        object_authorizer=_object_authorizer(positions),
        reference_authorizer=authorize_reference,
    )
    repository = HrAgentRepository(
        database.connection,
        settings.create_codec(),
        settings=replace(
            settings,
            budget_profile={**settings.budget_profile, "id": "test"},
        ),
        scope_validator=lambda owner_id, objects, refs, work_id: access.authorize_scope(
            owner_id, objects, refs, work_id=work_id
        ),
    )
    repository_holder["repository"] = repository
    service = HrAgentService(repository, access)
    service.standards = StandardService(repository)
    contexts = PositionIntelligenceRepository(database.dsn)
    context_service = PositionIntelligenceService(contexts)
    draft = contexts.create_draft(
        CreateContextDraft(
            owner, uuid4(), owned.position_id, None, None,
            {"mission": {"text": "历史岗位使命"}}, "历史岗位上下文", uuid4(),
        )
    )
    confirmed_context = contexts.confirm_modules(
        ConfirmContextModules(
            owner, owned.position_id, draft.context_version_id, uuid4(), None,
            draft.row_version, ("mission",), owner,
        )
    )
    secrets = AuthSecrets(b"p" * 32, key_version=1)

    async def local_login(code, _verifier):
        assert code == "owner"
        return owner

    auth = DingTalkWebAuth(
        repository=WebSessionRepository(database.dsn, secrets=secrets),
        secrets=secrets,
        qr_login=local_login,
        in_client_login=None,
        environment="production",
        route_prefix="/",
        public_base_url="https://localhost",
        app_key="position-cloud-reading-fixture",
    )
    started = auth.start_qr("/")
    session = asyncio.run(auth.complete_qr(started.state, "owner")).session
    app = FastAPI()
    app.state.hr_agent_service = service
    app.include_router(build_hr_agent_router(service))

    async def require_hr_access(request: Request, *, writable=False):
        return access.authorize_user(request.state.auth_context, writable=writable)

    app.include_router(build_position_intelligence_router(context_service, require_hr_access))
    legacy_results = HrToolService(
        ExecutionRelayRepository(database.dsn, content_codec=settings.create_codec())
    )
    app.include_router(build_hr_result_router(legacy_results, require_hr_access))
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(AuthorizationRepository(database.dsn)),
        routes=tuple(app.router.routes),
    )
    client = TestClient(app, base_url="https://localhost")
    client.cookies.set(auth.cookie_name, session.cookie_token)
    client.cookies.set(auth.csrf_cookie_name, session.csrf_token)
    headers = {"Origin": "https://localhost", "X-CSRF-Token": session.csrf_token}
    return (
        client, headers, repository, owner, owned.position_id, other_owned.position_id,
        foreign.position_id,
        confirmed_context.context_version_id,
    )


def _save_result(repository, owner, position_id, *, prior_ref=None, active=None):
    position = {"kind": "position", "id": str(position_id)}
    args = proposal_args(position)
    args.update(
        kind="role_calibration",
        title="岗位成果" if prior_ref is None else "岗位成果修订",
        body="第一版岗位成果" if prior_ref is None else "第二版岗位成果",
        changes=[],
        source_refs=[],
        result_id=prior_ref["id"] if prior_ref else None,
        expected_revision=prior_ref["revision"] if prior_ref else None,
    )
    references = []
    if active is None:
        work = repository.submit(
            owner,
            {
                "thread_id": None,
                "text": "保存岗位成果",
                "objects": [position],
                "references": references,
                "budget_profile": "test",
            },
            uuid4(),
        )
        fence = repository.claim("cloud-reading-test", 60)
    else:
        work, fence = active
    context = ModelContext(
        "work", ({"role": "user", "content": "保存岗位成果"},), tuple(references), (), 100, 1
    )
    attempt = repository.prepare_model(fence, context)
    repository.mark_model_sending(fence, attempt.attempt_id)
    operations = repository.commit_model(
        fence,
        attempt.attempt_id,
        ModelReply(
            "",
            (ToolCall("save", "save_result", args),),
            "stop",
            Usage(None, 100, 20, "reported"),
        ),
    )
    outcome = repository.execute_local_tool(fence, operations[0])
    assert outcome["status"] == "ok", outcome
    return outcome["data"], (work, fence)


def test_cloud_standard_partial_confirmation_is_current_and_stale_safe(cloud_api):
    client, headers, repository, owner, position_id, _other_position_id, foreign_position_id, _ = cloud_api
    position = {"kind": "position", "id": str(position_id)}
    proposal = proposal_args(position)
    work = repository.submit(
        owner,
        {"thread_id": None, "text": "校准岗位", "objects": [position], "references": [], "budget_profile": "test"},
        uuid4(),
    )
    fence = repository.claim("standard-test", 60)
    context = ModelContext("work", ({"role": "user", "content": "校准岗位"},), (), (), 100, 1)
    attempt = repository.prepare_model(fence, context)
    repository.mark_model_sending(fence, attempt.attempt_id)
    operations = repository.commit_model(
        fence, attempt.attempt_id,
        ModelReply("", (ToolCall("proposal", "save_result", proposal),), "stop", Usage(None, 100, 20, "reported")),
    )
    saved = repository.execute_local_tool(fence, operations[0])["data"]
    path = f"/api/hr/agent/positions/{position_id}/standards"
    baseline_payload = confirmation(saved)

    assert client.post(path + "/confirm", json=baseline_payload).status_code == 403
    baseline = client.post(
        path + "/confirm", json=baseline_payload,
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert baseline.status_code == 200, baseline.text
    first_item, preserved_item = baseline.json()["items"]
    revised_args = proposal_args(position)
    revised_args.update(
        base_standard_ref=baseline.json()["ref"],
        basis=[{"kind": "confirmed_standard", "ref": baseline.json()["ref"], "input_revision": None}],
        changes=[
            {"action": "replace", "target_item_id": first_item["item_id"], "text": "更新后的职责"},
            {"action": "remove", "target_item_id": preserved_item["item_id"], "text": None},
        ],
    )
    next_attempt = repository.prepare_model(fence, context)
    repository.mark_model_sending(fence, next_attempt.attempt_id)
    next_operations = repository.commit_model(
        fence, next_attempt.attempt_id,
        ModelReply("", (ToolCall("proposal-2", "save_result", revised_args),), "stop", Usage(None, 100, 20, "reported")),
    )
    revised_outcome = repository.execute_local_tool(fence, next_operations[0])
    assert revised_outcome["status"] == "ok", revised_outcome
    revised = revised_outcome["data"]
    payload = confirmation(
        revised, [revised["changes"][0]["change_id"]], baseline.json()["ref"]["revision"]
    )
    confirmed = client.post(path + "/confirm", json=payload, headers={**headers, "Idempotency-Key": str(uuid4())})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["items"] == [
        {"item_id": first_item["item_id"], "text": "更新后的职责"}, preserved_item,
    ]
    assert client.get(path + "/current").json() == confirmed.json()
    stale = client.post(
        path + "/confirm", json=payload,
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert stale.status_code == 409
    assert stale.json()["details"]["current_revision"] == confirmed.json()["ref"]["revision"]
    assert client.get(f"/api/hr/agent/positions/{foreign_position_id}/standards/current").status_code == 404
    repository.cancel(owner, work["work_id"], "fixture complete", uuid4())


def test_cloud_position_result_list_and_exact_old_revision_remain_readable(cloud_api):
    client, _headers, repository, owner, position_id, other_position_id, foreign_position_id, context_id = cloud_api
    context = client.get(f"/api/hr/positions/{position_id}/context")
    assert context.status_code == 200, context.text
    assert context.json()["current"]["context_version_id"] == str(context_id)
    assert context.json()["current"]["modules"]["mission"]["text"] == "历史岗位使命"
    first, active = _save_result(repository, owner, position_id)
    second, _ = _save_result(
        repository, owner, position_id, prior_ref=first["ref"], active=active,
    )
    distractor, _ = _save_result(repository, owner, other_position_id)
    listed = client.get(
        "/api/hr/agent/results",
        params={"object_kind": "position", "object_id": str(position_id)},
    )
    assert listed.status_code == 200, listed.text
    assert [item["ref"] for item in listed.json()["items"]] == [second["ref"]]
    assert distractor["ref"] not in [item["ref"] for item in listed.json()["items"]]
    legacy = client.get(f"/api/v1/hr/positions/{position_id}/results")
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["items"] == []
    assert legacy.json()["positionId"] == str(position_id)
    exact = client.get(
        f"/api/hr/agent/results/{first['ref']['id']}/revisions/{first['ref']['revision']}"
    )
    assert exact.status_code == 200
    assert exact.json()["body"] == "第一版岗位成果"
    assert exact.json()["ref"]["id"] == second["ref"]["id"]
    assert exact.json()["ref"]["revision"] != second["ref"]["revision"]
    assert client.get(
        "/api/hr/agent/results",
        params={"object_kind": "position", "object_id": str(foreign_position_id)},
    ).status_code == 404
