from __future__ import annotations

import hashlib
import json
import socket
from base64 import b64encode
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_agent_brain_conversation_repository import _codec
from test_control_plane_migration import control_database  # noqa: F401

import app.main as main_module
from app.agent_brain.conversation_context import ConversationContextBuilder
from app.agent_brain.conversation_projection import ConversationProjection
from app.agent_brain.models import load_capability_cards
from app.agent_brain.orchestrator import MissionOrchestrator
from app.attachments.result_projection import ConversationResultProjection
from app.control_plane.auth import AuthSecrets
from app.control_plane.models import AuthContext, IdentityMode, Role
from app.execution_relay.models import RelayEvent
from app.execution_relay.repository import ExecutionRelayRepository
from app.hr.models import CreateManualPosition
from app.hr.panorama_repository import PanoramaRepository
from app.hr.panorama_runtime import PanoramaResultProjector
from app.hr.repository import HrPositionRepository
from app.hr.structured_output import encode_hr_envelope
from app.main import create_app


class _AllowHrAgent:
    def decide_for_user_id(self, owner_id: UUID, agent_id: str):
        assert isinstance(owner_id, UUID)
        assert agent_id == "hr-bot"
        return SimpleNamespace(allowed=True)

    def permitted_agents_for_user_id(self, _owner_id: UUID):
        return tuple(card for card in load_capability_cards() if card.agent_id == "hr-bot")

    def permitted_catalog_for_user_id(self, _owner_id: UUID):
        return ()


class _IdentityAuth:
    mode = IdentityMode.PRODUCTION
    route_prefix = "/"
    cookie_name = "panorama-session"
    csrf_cookie_name = "panorama-csrf"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None
    secrets = AuthSecrets(b"s" * 32, key_version=1)

    def __init__(self, owner_id: UUID) -> None:
        self._context = AuthContext(owner_id, Role.PLATFORM_OWNER, uuid4(), False)

    def authenticate(self, token: str):
        return (self._context, "csrf") if token == "valid" else None

    @staticmethod
    def verify_csrf(submitted: str, expected: str) -> bool:
        return submitted == expected == "csrf"


def _main_app(monkeypatch, tmp_path: Path, app_url: str, owner_id: UUID):
    database_secret = tmp_path / "control-database-url"
    database_secret.write_text(app_url, encoding="utf-8")
    database_secret.chmod(0o600)
    keyring = tmp_path / "content-keyring.json"
    keyring.write_text(json.dumps({
        "purpose": "platform-content-encryption", "active_version": 4,
        "keys": {"3": b64encode(b"3" * 32).decode(), "4": b64encode(b"4" * 32).decode()},
    }), encoding="utf-8")
    keyring.chmod(0o600)
    base = main_module.load_config()
    config = replace(
        base,
        execution_relay_enabled=True,
        direct_agent_enabled=True,
        agent_brain_enabled=False,
        agent_brain_v2_enabled=False,
        content_encryption_keyring_file=str(keyring),
        metabot_contract_path=str(Path(__file__).parents[2] / "deploy/cloud/metabot.runtime-contract.json"),
        control_plane=replace(
            base.control_plane,
            mode=IdentityMode.PRODUCTION,
            route_prefix="/",
            control_database_url_file=str(database_secret),
            audit_database_url_file="",
        ),
    )
    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "AgentUseAuthorization", lambda _url: _AllowHrAgent())
    original_getaddrinfo = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs:
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]
        if str(host).endswith(".example.com") else original_getaddrinfo(host, port, *args, **kwargs))
    app = create_app(
        registry_path=str(Path(__file__).parents[2] / "registry.yaml"),
        cluster_contract_path=config.metabot_contract_path,
        start_poller=False,
        identity_auth=_IdentityAuth(owner_id),
    )
    return app


def _public_resolver(_hostname: str, port: int) -> tuple[str, ...]:
    assert port == 443
    return ("93.184.216.34",)


def _answer(run: dict, sources: list[dict], failed_ids: set[str], revision: str) -> tuple[str, list[str]]:
    observed_at = run["created_at"]
    successful = [source for source in sources if source["source_id"] not in failed_ids]
    jobs = []
    facts = []
    citations = []
    for index, source in enumerate(successful, start=1):
        url = f"{source['approved_urls'][0]}/{revision}-job-{index}"
        public_key = f"{revision}-job-{index}"
        jobs.append({
            "company": source["canonical_name"], "public_job_key": public_key,
            "title": "高级结构工程师", "location": "深圳",
            "duty_excerpt": f"负责{source['canonical_name']}精密结构研发",
            "requirement_excerpt": "需要量产与可靠性经验", "source_url": url,
            "observed_at": observed_at,
            "content_sha256": hashlib.sha256(url.encode()).hexdigest(),
        })
        facts.append({
            "fact_id": f"{revision}-fact-{index}",
            "text": f"{source['canonical_name']}公开招聘高级结构工程师",
            "company": source["canonical_name"], "public_job_key": public_key,
            "source_url": url, "observed_at": observed_at,
        })
        citations.append(url)
    payload = {
        "companies": [{
            "source_id": source["source_id"],
            "canonical_name": source["canonical_name"],
            "approved_urls": source["approved_urls"],
            "status": "failed" if source["source_id"] in failed_ids else "completed",
            "error_code": "SEARCH_UNAVAILABLE" if source["source_id"] in failed_ids else None,
        } for source in sources],
        "jobs": jobs,
        "facts": facts,
        "direction_clusters": {"精密结构": len(successful)},
        "inferences": [{
            "text": "关注公司持续投入精密结构方向",
            "basis_fact_ids": [fact["fact_id"] for fact in facts],
        }],
        "unknowns": [{"text": "团队编制仍待确认"}],
        "summary": f"{revision} 全景招聘分析",
    }
    return f"# {revision} 全景招聘分析\n\n" + encode_hr_envelope("panorama_report", payload), citations


def _complete_run(*, environment, run: dict, sources: list[dict], failed_ids: set[str], revision: str,
                  orchestrator: MissionOrchestrator, relay: ExecutionRelayRepository,
                  projector: PanoramaResultProjector, client: TestClient) -> dict:
    worker_id = f"panorama-acceptance-{revision}"
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) "
            "values (%s,array['hr-bot'],'active')", (worker_id,),
        )
    assert orchestrator.advance_pending(limit=50) == 1
    lease = relay.lease(worker_id, ("hr-bot",), 300, ("direct_agent",))
    assert lease is not None
    relay.mark_dispatched(worker_id, lease.payload.run_id)
    answer, urls = _answer(run, sources, failed_ids, revision)
    relay.append_events(worker_id, (RelayEvent(
        run_id=lease.payload.run_id, seq=1, event_type="agent.complete",
        created_at=datetime.now(UTC), payload={"result": {
            "contractVersion": "core_chat_collaboration_v4",
            "publicAnswerMarkdown": answer,
            "citations": [{
                "citationKey": f"source-{index}", "title": "公开招聘岗位",
                "url": url, "site": url.split("/", 3)[2], "retrievedAt": run["created_at"],
                "supports": ["公开岗位"],
            } for index, url in enumerate(urls, start=1)],
            "artifacts": [], "completion": "completed", "recovery": None,
        }},
    ),))
    relay.finish(worker_id, lease.payload.run_id, "completed")
    assert orchestrator.advance_pending(limit=50) >= 1
    orchestrator.advance_pending(limit=50)
    assert projector.reconcile_one() is True
    status = client.get(f"/api/hr/panorama/runs/{run['run_id']}")
    assert status.status_code == 200
    return status.json()


@pytest.mark.postgres
def test_panorama_public_flow_preserves_last_valid_retries_one_source_and_reuses_latest_in_position(
    control_database, monkeypatch, tmp_path,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    owner_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users(internal_user_id,display_name,status) "
            "values (%s,'Panorama acceptance','active')", (owner_id,),
        )

    app = _main_app(monkeypatch, tmp_path, app_url, owner_id)
    commands = app.state.conversation_command_service
    repository = PanoramaRepository(app_url)
    relay = app.state.execution_relay_repository
    orchestrator = app.state.agent_brain_orchestrator
    projector = app.state.hr_panorama_projector
    assert orchestrator is not None and projector is not None
    panorama_orchestrator = MissionOrchestrator(
        app.state.mission_repository,
        relay,
        capability_provider=lambda _owner: tuple(
            card for card in load_capability_cards() if card.agent_id == "hr-bot"
        ),
        conversation_context_builder=ConversationContextBuilder(
            app.state.conversation_repository
        ),
        conversation_projection=ConversationProjection(
            app.state.conversation_repository,
            result_projection=ConversationResultProjection(content_codec=_codec()),
        ),
        mission_modes=("direct_agent",),
    )

    with TestClient(
        app,
        cookies={"panorama-session": "valid", "panorama-csrf": "csrf"},
        headers={"Origin": "https://agent.example.test"},
    ) as client:
        sources = []
        for index, company in enumerate(("联合光电", "奥比中光", "舜宇光学"), start=1):
            response = client.post(
                "/api/hr/panorama/sources",
                headers={"Idempotency-Key": str(uuid4()), "X-CSRF-Token": "csrf"},
                json={"canonical_name": company, "aliases": [],
                      "approved_urls": [f"https://company-{index}.example.com/jobs"]},
            )
            assert response.status_code == 200
            sources.append(response.json())

        def start(selected: list[dict]) -> dict:
            response = client.post(
                "/api/hr/panorama/runs",
                headers={"Idempotency-Key": str(uuid4()), "X-CSRF-Token": "csrf"},
                json={"source_ids": [item["source_id"] for item in selected]},
            )
            assert response.status_code == 202, response.text
            return response.json()

        baseline_run = start([sources[2]])
        assert _complete_run(
            environment=environment, run=baseline_run, sources=[sources[2]], failed_ids=set(),
            revision="baseline", orchestrator=panorama_orchestrator, relay=relay,
            projector=projector, client=client,
        )["state"] == "completed"
        baseline_insight = next(
            item for item in client.get("/api/hr/panorama/reports").json()["items"]
            if item["run_id"] == baseline_run["run_id"]
        )
        baseline_report = client.get(
            f"/api/hr/panorama/reports/{baseline_insight['insight_version_id']}"
        ).json()

        partial_run = start(sources)
        partial = _complete_run(
            environment=environment, run=partial_run, sources=sources,
            failed_ids={sources[2]["source_id"]}, revision="partial",
            orchestrator=panorama_orchestrator, relay=relay, projector=projector, client=client,
        )
        assert partial["state"] == "partially_completed"
        assert partial["source_failures"] == {sources[2]["source_id"]: "search_unavailable"}
        assert client.get(
            f"/api/hr/panorama/reports/{baseline_insight['insight_version_id']}"
        ).json() == baseline_report
        current_before_retry = repository.list_snapshots(owner_id, UUID(sources[2]["source_id"]))[0]
        assert str(current_before_retry.snapshot_id) == baseline_report["snapshots"][0]["snapshot_id"]

        partial_insight = next(
            item for item in client.get("/api/hr/panorama/reports").json()["items"]
            if item["run_id"] == partial_run["run_id"]
        )
        partial_report = client.get(
            f"/api/hr/panorama/reports/{partial_insight['insight_version_id']}"
        ).json()
        assert partial_report["insight"]["direction_clusters"] == {"精密结构": 2}
        assert {fact["source_url"] for fact in partial_report["insight"]["facts"]} == {
            item["source_url"] for item in partial_report["snapshots"]
        }

        retry_run = start([sources[2]])
        assert _complete_run(
            environment=environment, run=retry_run, sources=[sources[2]], failed_ids=set(),
            revision="retry", orchestrator=panorama_orchestrator, relay=relay,
            projector=projector, client=client,
        )["state"] == "completed"
        retry_insight = next(
            item for item in client.get("/api/hr/panorama/reports").json()["items"]
            if item["run_id"] == retry_run["run_id"]
        )

        position_repository = HrPositionRepository(app_url)
        position = position_repository.create_manual(
            CreateManualPosition(owner_id, uuid4(), uuid4(), "高级结构工程师")
        )
        shell = commands.ensure_direct_conversation_shell(
            owner_id, uuid4(), direct_agent_id="hr-bot", title="高级结构工程师招聘"
        )
        response = client.post(
            f"/api/hr/positions/{position.position_id}/conversations/{shell.conversation_id}",
            headers={"Idempotency-Key": str(uuid4()), "X-CSRF-Token": "csrf"},
            json={},
        )
        assert response.status_code == 200
        response = client.post(
            f"/api/v1/conversations/{shell.conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4()), "X-CSRF-Token": "csrf"},
            json={"text": "参考舜宇光学最新全景分析，修订这个岗位的 JD/JR"},
        )
        assert response.status_code == 201
        position_turn_id = UUID(response.json()["turn"]["turn_id"])
        assert orchestrator.advance_pending(limit=50) == 1
        worker_id = "panorama-position-acceptance"
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "insert into platform_control.execution_workers(worker_id,allowed_agent_ids,status) "
                "values (%s,array['hr-bot'],'active')", (worker_id,),
            )
        position_lease = relay.lease(worker_id, ("hr-bot",), 300, ("direct_agent",))
        assert position_lease is not None
        envelope = json.loads(position_lease.payload.prompt.split("\n", 1)[1])
        panorama = envelope["hr_panorama_context"]
        assert panorama["insight_version_ids"] == [retry_insight["insight_version_id"]]
        assert panorama["facts"]
        assert all("舜宇光学" in fact["text"] for fact in panorama["facts"])
        assert panorama["source_urls"] == [
            "https://company-3.example.com/jobs/retry-job-1"
        ]
        assert panorama["freshness"]["as_of"] == retry_run["created_at"]
        retrieval = repository.retrieval_for_turn(
            owner_id, position.position_id, position_turn_id
        )
        assert retrieval is not None
        assert retrieval.turn_id == position_turn_id
        assert retrieval.insight_version_ids == (UUID(retry_insight["insight_version_id"]),)
