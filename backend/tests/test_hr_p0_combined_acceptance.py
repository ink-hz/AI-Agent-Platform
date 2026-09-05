from __future__ import annotations

import json
import socket
from base64 import b64encode
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
import test_hr_p0_recruiting_loop as recruiting_flow
from fastapi.testclient import TestClient
from fixtures.conversation_attachments.build_fixtures import _pdf as build_test_pdf
from psycopg.rows import dict_row
from test_agent_brain_conversation_repository import _codec
from test_control_plane_migration import control_database  # noqa: F401
from test_hr_p0_panorama_flow import (
    _AllowHrAgent,
    _complete_run,
    _IdentityAuth,
)
from test_hr_position_package_projection import _seed_conversation, _seed_turn

import app.main as main_module
from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.conversation_repository import message_subject
from app.agent_brain.models import load_capability_cards
from app.agent_brain.orchestrator import MissionOrchestrator
from app.attachments.conversation_routes import build_conversation_attachment_router
from app.attachments.grant_service import OutputWriteGrant, TaskAttachmentGrant
from app.attachments.result_projection import ConversationResultProjection
from app.execution_relay.content_crypto import SealedContent
from app.execution_relay.models import RelayEvent
from app.hr.resource_service import (
    HrPositionResourceService,
    PsycopgPositionResourceRepository,
)
from app.hr.structured_output import extract_hr_envelope
from app.main import create_app

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "hr_p0"


class _FreshTicketStorage:
    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes
        self._ticket_number = 0
        self._attachments: set[UUID] = set()
        self._tickets: dict[str, bytes] = {}

    def register(self, attachment_id: UUID) -> None:
        self._attachments.add(attachment_id)

    def attachment(self, owner_id: UUID, attachment_id: UUID):
        assert attachment_id in self._attachments
        return SimpleNamespace(
            owner_internal_user_id=owner_id,
            attachment_id=attachment_id,
            original_name="高级结构工程师-匿名候选人甲-面试题.pdf",
            detected_mime="application/pdf",
            declared_mime="application/pdf",
            size_bytes=len(self._pdf_bytes),
            state="ready",
            created_at=datetime(2026, 9, 4, tzinfo=UTC),
        )

    def issue_ticket(self, owner_id: UUID, attachment_id: UUID, purpose: str):
        assert isinstance(owner_id, UUID)
        assert isinstance(attachment_id, UUID)
        assert attachment_id in self._attachments
        assert purpose == "download"
        self._ticket_number += 1
        token = (
            "a" * 32
            if self._ticket_number == 1
            else f"{'a' * 30}{self._ticket_number:02d}"
        )
        content_path = f"/api/v1/attachments/content/{token}"
        self._tickets[token] = self._pdf_bytes
        return {
            "content_path": content_path,
            "expires_at": f"2026-09-04T10:{self._ticket_number:02d}:00Z",
        }

    def open_content(self, owner_id: UUID, ticket: str, range_header: str | None):
        assert isinstance(owner_id, UUID)
        assert range_header is None
        return SimpleNamespace(
            stream=iter((self._tickets[ticket],)),
            status_code=200,
            media_type="application/pdf",
            headers={"Content-Length": str(len(self._tickets[ticket]))},
        )


class _AcceptanceAttachmentGrants:
    def __init__(self, storage: _FreshTicketStorage) -> None:
        self._storage = storage

    def issue_attachment(self, task_id: UUID, attachment_id: UUID, agent_id: str):
        assert isinstance(task_id, UUID)
        assert agent_id == "hr-bot"
        return TaskAttachmentGrant(
            attachment_id=attachment_id,
            display_name="匿名候选人简历.pdf",
            detected_mime="application/pdf",
            size_bytes=128,
            sha256_hex="1" * 64,
            download_url=(
                f"/api/v1/execution-worker/attachments/{attachment_id}/content"
            ),
            bearer_token="r" * 43,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

    def issue_output(self, task_id: UUID, agent_id: str):
        return OutputWriteGrant(
            task_id=task_id,
            agent_id=agent_id,
            upload_url=f"/api/v1/execution-worker/tasks/{task_id}/artifacts",
            bearer_token="w" * 43,
            max_files=5,
            max_total_bytes=50 * 1024 * 1024,
        )

    def classify_result_artifacts(self, task_id: UUID, agent_id: str, artifacts):
        assert isinstance(task_id, UUID)
        assert agent_id == "hr-bot"
        assert all(
            UUID(artifact["attachmentId"]) in self._storage._attachments
            for artifact in artifacts
        )
        return "ready"


def _result_payload(filename: str, name: str) -> tuple[str, dict[str, object]]:
    document = json.loads((FIXTURE_ROOT / filename).read_text("utf-8"))
    assert document["fixture_label"] == "SYNTHETIC TEST DATA"
    result = next(item for item in document["results"] if item["name"] == name)
    parsed = extract_hr_envelope(result["markdown"], result["kind"])
    assert parsed is not None
    return result["markdown"], parsed.payload


def _seed_ready_pdf_artifact(
    environment,
    owner_id: UUID,
    conversation_id: UUID,
    task_id: UUID,
    pdf_bytes: bytes,
    storage: _FreshTicketStorage,
) -> tuple[UUID, UUID]:
    attachment_id, artifact_id, version_id = uuid4(), uuid4(), uuid4()
    digest = sha256(pdf_bytes).digest()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_attachments.task_grants("
            "grant_id,token_sha256,task_id,agent_id,scope,expires_at,max_reads,"
            "max_bytes,max_files,max_file_bytes) values ("
            "%s,%s,%s,'hr-bot','write_output',now()+interval '1 hour',0,"
            "52428800,5,52428800)",
            (uuid4(), uuid4().bytes + uuid4().bytes, task_id),
        )
        connection.execute(
            "insert into platform_attachments.attachments("
            "attachment_id,owner_internal_user_id,conversation_id,source_kind,"
            "original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,declared_mime,"
            "detected_mime,immutable_locator,size_bytes,sha256,state,ready_at) "
            "values (%s,%s,%s,'agent_output',%s,1,%s,1,'application/pdf',"
            "'application/pdf',%s,%s,%s,'ready',now())",
            (
                attachment_id,
                owner_id,
                conversation_id,
                b"n" * 29,
                b"o" * 29,
                f"version:{version_id}",
                len(pdf_bytes),
                digest,
            ),
        )
        connection.execute(
            "insert into platform_attachments.artifacts("
            "artifact_id,artifact_key,owner_internal_user_id,conversation_id,"
            "task_id,agent_id) values (%s,'candidate-interview',%s,%s,%s,'hr-bot')",
            (artifact_id, owner_id, conversation_id, task_id),
        )
        connection.execute(
            "insert into platform_attachments.bindings("
            "binding_id,attachment_id,owner_internal_user_id,kind,conversation_id,"
            "task_id,agent_id) values (%s,%s,%s,'task_output',%s,%s,'hr-bot')",
            (uuid4(), attachment_id, owner_id, conversation_id, task_id),
        )
        connection.execute(
            "insert into platform_attachments.artifact_versions("
            "artifact_version_id,artifact_id,attachment_id,version_no,"
            "producer_version_id,original_name_ciphertext,original_name_key_version,"
            "object_ref_ciphertext,object_ref_key_version,detected_mime,"
            "immutable_locator,size_bytes,sha256,state,result_status) values ("
            "%s,%s,%s,1,'combined-p0-v1',%s,1,%s,1,'application/pdf',"
            "%s,%s,%s,'ready','succeeded')",
            (
                version_id,
                artifact_id,
                attachment_id,
                b"n" * 29,
                b"o" * 29,
                f"version:{version_id}",
                len(pdf_bytes),
                digest,
            ),
        )
    storage.register(attachment_id)
    return attachment_id, version_id


def _combined_app(
    monkeypatch,
    tmp_path: Path,
    app_url: str,
    owner_id: UUID,
    resources: HrPositionResourceService,
    storage: _FreshTicketStorage,
):
    database_secret = tmp_path / "control-database-url"
    database_secret.write_text(app_url, encoding="utf-8")
    database_secret.chmod(0o600)
    keyring = tmp_path / "content-keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "purpose": "platform-content-encryption",
                "active_version": 4,
                "keys": {
                    "3": b64encode(b"3" * 32).decode(),
                    "4": b64encode(b"4" * 32).decode(),
                },
            }
        ),
        encoding="utf-8",
    )
    keyring.chmod(0o600)
    base = main_module.load_config()
    config = replace(
        base,
        execution_relay_enabled=True,
        direct_agent_enabled=True,
        agent_brain_enabled=False,
        agent_brain_v2_enabled=False,
        content_encryption_keyring_file=str(keyring),
        metabot_contract_path=str(
            Path(__file__).parents[2] / "deploy/cloud/metabot.runtime-contract.json"
        ),
        control_plane=replace(
            base.control_plane,
            mode=main_module.IdentityMode.PRODUCTION,
            route_prefix="/",
            control_database_url_file=str(database_secret),
            audit_database_url_file="",
        ),
    )
    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(
        main_module, "AgentUseAuthorization", lambda _url: _AllowHrAgent()
    )
    original_getaddrinfo = socket.getaddrinfo

    def example_dns(host, port, *args, **kwargs):
        if str(host) == "example.com" or str(host).endswith(".example.com"):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", port),
                )
            ]
        return original_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", example_dns)
    app = create_app(
        registry_path=str(Path(__file__).parents[2] / "registry.yaml"),
        cluster_contract_path=config.metabot_contract_path,
        start_poller=False,
        identity_auth=_IdentityAuth(owner_id),
        hr_resource_service=resources,
    )
    app.state.conversation_attachment_download_service = storage
    if not any(
        getattr(route, "path", None) == "/api/v1/attachments/content/{ticket}"
        for route in app.routes
    ):
        app.include_router(build_conversation_attachment_router())
    return app


def _assistant_answers(environment, codec, owner_id: UUID) -> dict[UUID, str]:
    with psycopg.connect(environment["admin"]) as connection:
        rows = connection.execute(
            "select turn.turn_id,turn.conversation_id,message.message_id,"
            "message.content_ciphertext,"
            "message.encryption_key_version from "
            "platform_control.conversation_turns turn join "
            "platform_control.conversations conversation using (conversation_id) "
            "join platform_control.conversation_messages message on "
            "message.conversation_id=turn.conversation_id and "
            "message.turn_id=turn.turn_id and message.role='assistant' "
            "where conversation.owner_internal_user_id=%s and "
            "turn.status='completed'",
            (owner_id,),
        ).fetchall()
    answers: dict[UUID, str] = {}
    for turn_id, conversation_id, message_id, ciphertext, key_version in rows:
        opened = codec.unseal_json(
            message_subject(conversation_id, message_id),
            SealedContent(bytes(ciphertext), key_version),
        )
        answers[turn_id] = str(opened.get("text", ""))
    return answers


@pytest.mark.postgres
def test_combined_p0_business_flow_survives_failures_and_reloads_every_result(
    control_database,  # noqa: F811
    monkeypatch,
    tmp_path,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    codec = _codec()
    owner_id = uuid4()
    pdf_bytes = build_test_pdf()
    assert pdf_bytes.startswith(b"%PDF-")
    storage = _FreshTicketStorage(pdf_bytes)
    resources = HrPositionResourceService(
        PsycopgPositionResourceRepository(
            lambda: psycopg.connect(app_url, row_factory=dict_row), storage
        ),
        storage,
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users("
            "internal_user_id,display_name,status) values "
            "(%s,'Combined P0 acceptance','active')",
            (owner_id,),
        )

    app = _combined_app(monkeypatch, tmp_path, app_url, owner_id, resources, storage)
    positions = app.state.hr_position_service
    candidates = app.state.hr_candidate_service
    intelligence = app.state.hr_position_intelligence_service
    tasks = app.state.hr_position_task_service
    task_contexts = app.state.hr_task_context_provider
    relay = app.state.execution_relay_repository
    projector = app.state.hr_panorama_projector
    package_projector = app.state.hr_position_package_projector
    assert all(
        value is not None
        for value in (
            positions,
            candidates,
            intelligence,
            tasks,
            task_contexts,
            relay,
            projector,
            package_projector,
        )
    )
    orchestrator = MissionOrchestrator(
        app.state.mission_repository,
        relay,
        capability_provider=lambda _owner: tuple(
            card for card in load_capability_cards() if card.agent_id == "hr-bot"
        ),
        conversation_context_builder=ConversationContextBuilder(
            app.state.conversation_repository,
            hr_task_context_provider=task_contexts,
            panorama_context_provider=app.state.hr_panorama_context_provider,
            candidate_parser_input_provider=app.state.hr_candidate_parser_input_provider,
        ),
        conversation_projection=ConversationProjection(
            app.state.conversation_repository,
            result_projection=ConversationResultProjection(content_codec=codec),
        ),
        mission_modes=("direct_agent",),
    )
    task_orchestrator = MissionOrchestrator(
        app.state.mission_repository,
        relay,
        capability_provider=lambda _owner: tuple(
            card for card in load_capability_cards() if card.agent_id == "hr-bot"
        ),
        conversation_context_builder=ConversationContextBuilder(
            app.state.conversation_repository,
            hr_task_context_provider=task_contexts,
            panorama_context_provider=app.state.hr_panorama_context_provider,
            candidate_parser_input_provider=app.state.hr_candidate_parser_input_provider,
        ),
        conversation_projection=ConversationProjection(
            app.state.conversation_repository,
            result_projection=ConversationResultProjection(content_codec=codec),
        ),
        mission_modes=("direct_agent",),
        attachment_grants=_AcceptanceAttachmentGrants(storage),
    )

    initial_markdown, initial_package = _result_payload(
        "recruiting-results.json", "initial_position_package"
    )
    revised_markdown, revised_package = _result_payload(
        "recruiting-results.json", "revised_position_package"
    )
    _, strong_match = _result_payload(
        "recruiting-results.json", "strong_candidate_match"
    )
    _, adjacent_match = _result_payload(
        "recruiting-results.json", "adjacent_candidate_match"
    )
    _, interview_payload = _result_payload(
        "recruiting-results.json", "strong_candidate_interview_plan"
    )
    panorama_markdown, panorama_fixture = _result_payload(
        "panorama-result.json", "partial_panorama_report"
    )
    assert "SYNTHETIC TEST DATA" in panorama_markdown
    monkeypatch.setattr(
        recruiting_flow, "MATCH_PAYLOADS", (strong_match, adjacent_match)
    )
    monkeypatch.setattr(recruiting_flow, "INTERVIEW_PAYLOAD", interview_payload)

    started_at = datetime(2026, 9, 4, tzinfo=UTC)
    with psycopg.connect(environment["admin"]) as connection:
        conversation_id, _, _ = _seed_conversation(
            connection,
            owner_id,
            "请补充岗位地点和量产范围。",
            created_at=started_at,
        )
        _seed_turn(
            connection,
            conversation_id,
            initial_markdown,
            seq=3,
            created_at=started_at,
        )
    assert package_projector.reconcile_one() is True
    assert package_projector.reconcile_one() is True

    headers = {
        "Origin": "https://agent.example.test",
        "X-CSRF-Token": "csrf",
    }
    client = TestClient(
        app,
        cookies={"panorama-session": "valid", "panorama-csrf": "csrf"},
        headers=headers,
    )

    def finish_task_via_relay(
        selected_environment,
        task,
        selected_owner_id,
        selected_position_id,
        task_kind,
        value,
        *,
        model_version="hr-runtime-before-upgrade",
        candidate_scope=None,
        artifact_specs=(),
        grant_agent_id="hr-bot",
        existing_task_context=False,
    ):
        assert selected_environment is environment
        assert selected_owner_id == owner_id
        assert selected_position_id == position_id
        assert task_kind in {"candidate_match", "candidate_interview_plan"}
        assert model_version == "hr-runtime-before-upgrade"
        assert candidate_scope is not None
        assert grant_agent_id == "hr-bot"
        assert existing_task_context is True
        assert task_orchestrator.advance_pending(limit=50) == 1
        worker_id = f"combined-task-{task_kind}-{uuid4().hex[:8]}"
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "insert into platform_control.execution_workers("
                "worker_id,allowed_agent_ids,status) values "
                "(%s,array['hr-bot'],'active')",
                (worker_id,),
            )
        lease = relay.lease(worker_id, ("hr-bot",), 300, ("direct_agent",))
        assert lease is not None
        assert lease.payload.output_write_grant is not None
        mission_task_id = lease.payload.output_write_grant.task_id
        artifact_versions: tuple[UUID, ...] = ()
        artifacts: list[dict[str, object]] = []
        if artifact_specs:
            assert task_kind == "candidate_interview_plan"
            assert len(artifact_specs) == 1
            attachment_id, artifact_version_id = _seed_ready_pdf_artifact(
                environment,
                owner_id,
                task.conversation_id,
                mission_task_id,
                pdf_bytes,
                storage,
            )
            artifact_versions = (artifact_version_id,)
            artifacts.append(
                {
                    "attachmentId": str(attachment_id),
                    "artifactKey": "candidate-interview",
                    "producerVersionId": "combined-p0-v1",
                    "displayName": "高级结构工程师-匿名候选人甲-面试题.pdf",
                    "status": "ready",
                }
            )
        relay.mark_dispatched(worker_id, lease.payload.run_id)
        relay.append_events(
            worker_id,
            (
                RelayEvent(
                    run_id=lease.payload.run_id,
                    seq=1,
                    event_type="agent.complete",
                    created_at=datetime.now(UTC),
                    payload={
                        "result": {
                            "contractVersion": "core_chat_collaboration_v4",
                            "publicAnswerMarkdown": value["text"],
                            "citations": [],
                            "artifacts": artifacts,
                            "completion": "completed",
                            "recovery": None,
                        }
                    },
                ),
            ),
        )
        relay.finish(worker_id, lease.payload.run_id, "completed")
        assert task_orchestrator.advance_pending(limit=50) >= 1
        task_orchestrator.advance_pending(limit=50)
        return mission_task_id, artifact_versions

    monkeypatch.setattr(recruiting_flow, "_finish_task", finish_task_via_relay)
    try:
        package_response = client.get(
            f"/api/hr/conversations/{conversation_id}/position-package"
        )
        assert package_response.status_code == 200
        package = package_response.json()
        assert package["modules"] == initial_package["modules"]
        confirmed_response = client.post(
            f"/api/hr/position-drafts/{package['draft_id']}/versions/"
            f"{package['draft_version_id']}/confirm",
            headers={"Idempotency-Key": str(uuid4())},
            json={"expected_row_version": package["row_version"]},
        )
        assert confirmed_response.status_code == 200, confirmed_response.text
        position_id = UUID(confirmed_response.json()["position_id"])
        initial_context_id = UUID(confirmed_response.json()["context_version_id"])

        created_sources = []
        fixture_companies = panorama_fixture["companies"]
        for company in fixture_companies:
            response = client.post(
                "/api/hr/panorama/sources",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "canonical_name": company["canonical_name"],
                    "aliases": [],
                    "approved_urls": company["approved_urls"],
                },
            )
            assert response.status_code == 200, response.text
            created_sources.append(response.json())
        run_response = client.post(
            "/api/hr/panorama/runs",
            headers={"Idempotency-Key": str(uuid4())},
            json={"source_ids": [source["source_id"] for source in created_sources]},
        )
        assert run_response.status_code == 202, run_response.text
        run = run_response.json()
        run_result = _complete_run(
            environment=environment,
            run=run,
            sources=created_sources,
            failed_ids={created_sources[2]["source_id"]},
            revision="combined",
            orchestrator=orchestrator,
            relay=relay,
            projector=projector,
            client=client,
        )
        assert run_result["state"] == "partially_completed"
        reports = client.get("/api/hr/panorama/reports").json()["items"]
        report_summary = next(
            item for item in reports if item["run_id"] == run["run_id"]
        )
        report = client.get(
            f"/api/hr/panorama/reports/{report_summary['insight_version_id']}"
        ).json()
        expected_source = created_sources[0]
        expected_source_name = expected_source["canonical_name"]
        expected_source_url = f"{expected_source['approved_urls'][0]}/combined-job-1"

        bind = client.post(
            f"/api/hr/positions/{position_id}/conversations/{conversation_id}",
            headers={"Idempotency-Key": str(uuid4())},
            json={},
        )
        assert bind.status_code == 200
        position_message = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4())},
            json={"text": (f"只参考{expected_source_name}最新全景证据修订岗位 JD/JR")},
        )
        assert position_message.status_code == 201, position_message.text
        position_turn_id = UUID(position_message.json()["turn"]["turn_id"])
        assert orchestrator.advance_pending(limit=50) == 1
        worker_id = "combined-position-context"
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "insert into platform_control.execution_workers("
                "worker_id,allowed_agent_ids,status) values "
                "(%s,array['hr-bot'],'active')",
                (worker_id,),
            )
        position_lease = relay.lease(worker_id, ("hr-bot",), 300, ("direct_agent",))
        assert position_lease is not None
        prompt = json.loads(position_lease.payload.prompt.split("\n", 1)[1])
        panorama_context = prompt["hr_panorama_context"]
        assert panorama_context["insight_version_ids"] == [
            report_summary["insight_version_id"]
        ]
        assert panorama_context["facts"]
        assert all(
            expected_source_name in fact["text"] for fact in panorama_context["facts"]
        )
        assert panorama_context["source_urls"] == [expected_source_url]
        assert panorama_context["freshness"]["as_of"] == run["created_at"]
        assert all(
            other["canonical_name"]
            not in json.dumps(panorama_context, ensure_ascii=False)
            for other in created_sources[1:]
        )
        relay.mark_dispatched(worker_id, position_lease.payload.run_id)
        relay.append_events(
            worker_id,
            (
                RelayEvent(
                    run_id=position_lease.payload.run_id,
                    seq=1,
                    event_type="agent.complete",
                    created_at=datetime.now(UTC),
                    payload={
                        "result": {
                            "contractVersion": "core_chat_result_v2",
                            "success": True,
                            "outputText": revised_markdown,
                            "publicAnswerMarkdown": revised_markdown,
                        }
                    },
                ),
            ),
        )
        relay.finish(worker_id, position_lease.payload.run_id, "completed")
        assert orchestrator.advance_pending(limit=50) >= 1
        orchestrator.advance_pending(limit=50)

        context_draft_response = client.post(
            f"/api/hr/positions/{position_id}/context/drafts",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "base_context_version_id": str(initial_context_id),
                "official_version_id": None,
                "modules": revised_package["modules"],
                "summary": "已参考全景证据修订的高级结构工程师岗位",
                "source_conversation_id": str(conversation_id),
                "source_turn_id": str(position_turn_id),
                "source_artifact_version_id": None,
                "source_material_attachment_ids": [],
                "agent_id": "hr-bot",
                "model_version": "combined-p0-acceptance",
            },
        )
        assert context_draft_response.status_code == 200, context_draft_response.text
        context_draft = context_draft_response.json()
        revised_context_response = client.post(
            f"/api/hr/positions/{position_id}/context/drafts/"
            f"{context_draft['context_version_id']}/confirm",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "expected_current_context_version_id": str(initial_context_id),
                "expected_draft_row_version": context_draft["row_version"],
                "module_names": ["mission", "jd", "jr"],
            },
        )
        assert revised_context_response.status_code == 200, (
            revised_context_response.text
        )
        revised_context = revised_context_response.json()
        context_id = UUID(revised_context["context_version_id"])
        assert revised_context["modules"]["jd"] != initial_package["modules"]["jd"]
        assert revised_context["modules"]["jr"] != initial_package["modules"]["jr"]

        draft_ids = recruiting_flow._parse_resumes(
            environment, codec, owner_id, client, position_id
        )
        assert len(set(draft_ids)) == 3
        with psycopg.connect(environment["admin"]) as connection:
            mutation_rows = connection.execute(
                "select draft_id,array_agg(mutation_kind order by created_at) "
                "from platform_hr.candidate_draft_mutation_events "
                "where owner_internal_user_id=%s and draft_id=any(%s) "
                "group by draft_id",
                (owner_id, list(draft_ids)),
            ).fetchall()
            attempt_rows = connection.execute(
                "select draft_id,array_agg(state order by claimed_at) "
                "from platform_hr.candidate_draft_processing_attempts "
                "where owner_internal_user_id=%s and draft_id=any(%s) "
                "group by draft_id",
                (owner_id, list(draft_ids)),
            ).fetchall()
        mutation_kinds = {draft_id: kinds for draft_id, kinds in mutation_rows}
        attempt_states = {draft_id: states for draft_id, states in attempt_rows}
        assert mutation_kinds[draft_ids[0]] == ["complete"]
        assert mutation_kinds[draft_ids[1]] == ["complete"]
        assert mutation_kinds[draft_ids[2]] == [
            "fail",
            "retry",
            "complete",
        ]
        assert attempt_states[draft_ids[0]] == ["completed"]
        assert attempt_states[draft_ids[1]] == ["completed"]
        assert attempt_states[draft_ids[2]] == ["failed", "completed"]
        successful_drafts = [
            client.get(f"/api/hr/candidate-drafts/{draft_id}").json()
            for draft_id in draft_ids[:2]
        ]
        assert [
            draft["extracted_facts"]["stable_name"] for draft in successful_drafts
        ] == [
            "候选人甲",
            "候选人乙",
        ]

        confirmed_candidates, reconciler = (
            recruiting_flow._confirm_candidates_and_project_matches(
                environment,
                codec,
                owner_id,
                client,
                positions,
                candidates,
                intelligence,
                tasks,
                task_contexts,
                conversation_id,
                position_id,
                context_id,
                draft_ids,
            )
        )
        recruiting_flow._project_interview_pdf(
            environment,
            owner_id,
            client,
            tasks,
            task_contexts,
            reconciler,
            conversation_id,
            position_id,
            context_id,
            confirmed_candidates,
        )

        current = client.get(f"/api/hr/positions/{position_id}/context").json()[
            "current"
        ]
        history = client.get(
            f"/api/hr/positions/{position_id}/context/versions"
        ).json()["items"]
        reloaded_report = client.get(
            f"/api/hr/panorama/reports/{report_summary['insight_version_id']}"
        ).json()
        reloaded_candidates = client.get(
            f"/api/hr/positions/{position_id}/candidates"
        ).json()["items"]
        reloaded_analyses = [
            client.get(
                f"/api/hr/position-candidates/{item['position_candidate_id']}/analyses"
            ).json()["items"]
            for item in reloaded_candidates
        ]
        reloaded_resources = client.get(
            f"/api/hr/positions/{position_id}/resources"
        ).json()

        assert current["context_version_id"] == str(context_id)
        assert current["modules"]["jd"] == revised_package["modules"]["jd"]
        assert current["modules"]["jr"] == revised_package["modules"]["jr"]
        assert {item["context_version_id"] for item in history} >= {
            str(initial_context_id),
            str(context_id),
        }
        assert reloaded_report == report
        assert len(reloaded_candidates) == 2
        for relation, versions in zip(
            reloaded_candidates, reloaded_analyses, strict=True
        ):
            match = next(item for item in versions if item["analysis_kind"] == "match")
            assert set(match["result"]) >= {"summary", "dimensions", "gaps", "risks"}
            assert match["position_id"] == str(position_id)
            assert match["candidate_id"] == relation["candidate_id"]
            assert match["context_version_id"] == str(context_id)
            assert match["document_ids"]

        primary_relation = reloaded_candidates[0]
        primary_versions = reloaded_analyses[0]
        interview = next(
            item
            for item in primary_versions
            if item["analysis_kind"] == "candidate_interview_plan"
        )
        assert interview["candidate_id"] == primary_relation["candidate_id"]
        assert interview["source_artifact_version_id"] is not None
        pdf = next(
            item
            for item in reloaded_resources["artifacts"]
            if item["artifact_version_id"] == interview["source_artifact_version_id"]
        )
        assert (pdf["media_type"], pdf["state"], pdf["download_available"]) == (
            "application/pdf",
            "ready",
            True,
        )
        first_ticket = client.post(
            f"/api/hr/positions/{position_id}/resources/{pdf['attachment_id']}/ticket",
            json={"purpose": "download"},
        ).json()
        second_ticket = client.post(
            f"/api/hr/positions/{position_id}/resources/{pdf['attachment_id']}/ticket",
            json={"purpose": "download"},
        ).json()
        assert first_ticket["content_path"] != second_ticket["content_path"]
        assert first_ticket["expires_at"] != second_ticket["expires_at"]
        downloaded_pdf = client.get(first_ticket["content_path"])
        assert downloaded_pdf.status_code == 200, downloaded_pdf.text
        assert downloaded_pdf.headers["content-type"] == "application/pdf"
        assert downloaded_pdf.content.startswith(b"%PDF-")
    finally:
        client.close()

    answers = _assistant_answers(environment, codec, owner_id)
    with psycopg.connect(environment["admin"]) as connection:
        completed_turn_ids = {
            row[0]
            for row in connection.execute(
                "select turn.turn_id from platform_control.conversation_turns turn "
                "join platform_control.conversations conversation "
                "using (conversation_id) where "
                "conversation.owner_internal_user_id=%s and "
                "turn.status='completed'",
                (owner_id,),
            ).fetchall()
        }
    assert completed_turn_ids
    assert completed_turn_ids == set(answers)
    assert all(answer.strip() for answer in answers.values())
