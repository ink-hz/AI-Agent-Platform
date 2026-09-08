"""Opt-in real bundle acceptance through the actual app and disposable PostgreSQL.

Run with HR_COMPANY_TEST_BUNDLE pointing at a verified, already-produced bundle.
No production database, external login, analysis provider or worker is contacted.
"""

# ruff: noqa: PLC0414
import base64
import hashlib
import json
import os
import socket
import threading
from dataclasses import replace
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
import psycopg
import pytest
import uvicorn
from fastapi import FastAPI
from psycopg.rows import dict_row
from test_hr_turn_scope_v6_database import (
    _seed_candidate_scope,
    repository,
)
from test_hr_turn_scope_v6_database import (
    control_database as control_database,
)
from test_hr_turn_scope_v6_database import (
    scoped_database as scoped_database,
)

from app import main as app_main
from app.attachments.artifact_service import ArtifactOutputService, ArtifactRepository
from app.attachments.citation_service import CitationRepository, CitationService
from app.attachments.conversation_repository import ConversationAttachmentRepository
from app.attachments.download_service import (
    ConversationAttachmentAccessRepository,
    ConversationAttachmentDownloadService,
    S3ImmutableAttachmentStore,
)
from app.attachments.grant_service import AttachmentGrantService, TaskGrantRepository
from app.attachments.object_writer import AttachmentObjectWriter
from app.attachments.upload_service import AttachmentUploadService
from app.config import load_config
from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
from app.hr.intelligence_bundle import verify_import_bundle
from app.hr.intelligence_import import (
    DatabaseIntelligenceImportRepository,
    IntelligenceBundleImporter,
)
from tests.helpers.hr_recruiting_objects import RecruitingObjects
from tests.helpers.hr_web_loop import WebLoop, _codec

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("HR_COMPANY_TEST_BUNDLE"),
        reason="real bundle path not supplied",
    ),
]


@pytest.fixture()
def web_loop(scoped_database):
    ids = _seed_candidate_scope(scoped_database)
    conversation = repository(scoped_database).ensure_direct_conversation_shell(
        ids["owner"], uuid4(), direct_agent_id="hr-bot", title="Owned company reading"
    )
    loop = WebLoop(scoped_database, ids["owner"], conversation.conversation_id)
    try:
        yield loop
    finally:
        loop.close()


def _actual_app(web_loop, monkeypatch, tmp_path, root):
    database_url = web_loop.environment["urls"]["platform_control_app"]
    database_file = tmp_path / "database-url"
    database_file.write_text(database_url)
    database_file.chmod(0o600)
    audit_file = tmp_path / "audit-database-url"
    audit_file.write_text(web_loop.environment["urls"]["platform_audit_append"])
    audit_file.chmod(0o600)
    keyring = tmp_path / "content-keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "purpose": "platform-content-encryption",
                "active_version": 4,
                "keys": {
                    str(n): base64.b64encode(str(n).encode() * 32).decode()
                    for n in (3, 4)
                },
            }
        )
    )
    keyring.chmod(0o600)
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n")
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "bots": [
                    {
                        "name": "hr-bot",
                        "model": "claude-opus-4-8",
                        "instance": {"pm2Name": "metabot-hr", "apiPort": 9101},
                    }
                ]
            }
        )
    )
    role_commit = "a" * 40
    role_root = tmp_path / "roles" / role_commit
    role_files = []
    for name in (
        "bots/hr/CLAUDE.md", "shared/base-rules.md",
        "shared/orbbec-context.md", "shared/web-research.md",
    ):
        target = role_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("Owned HTTP fixture; no model execution.")
        role_files.append({"path": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    (role_root / "role-package.json").write_text(json.dumps({
        "format": "hr-role-package-v1", "teamCommit": role_commit,
        "catalogRelease": role_commit, "cwd": "bots/hr", "files": role_files,
    }))
    config = load_config()
    config = replace(
        config,
        execution_relay_enabled=True,
        direct_agent_enabled=True,
        hr_web_worker_enabled=True,
        hr_role_package_root=str(role_root.parent),
        hr_role_package_commit=role_commit,
        agent_brain_enabled=False,
        content_encryption_keyring_file=str(keyring),
        static_dir=str(Path(__file__).parents[2] / "webui/dist"),
        control_plane=replace(
            config.control_plane,
            control_database_url_file=str(database_file),
            # Production enables central route authorization with this config.
            # Omitting it bypasses that layer even with a persisted login.
            audit_database_url_file=str(audit_file),
        ),
    )
    monkeypatch.setattr(app_main, "load_config", lambda: config)
    monkeypatch.setenv("HR_WEB_FIXTURE_ROOT", str(tmp_path))
    monkeypatch.setenv("PLATFORM_HR_INTELLIGENCE_ROOT", str(root))
    secrets = AuthSecrets(b"w" * 32, key_version=1)

    async def no_external_login(_code):
        raise RuntimeError("External login disabled in owned test")

    auth = DingTalkWebAuth(
        repository=WebSessionRepository(database_url, secrets=secrets),
        secrets=secrets,
        qr_login=no_external_login,
        in_client_login=None,
        environment="production",
        route_prefix="/",
        public_base_url="https://localhost",
        app_key="owned-web-fixture",
    )
    attachments = FastAPI()
    # Keep real attachment services without importing the removed task harness.
    objects = RecruitingObjects(tmp_path / "objects")
    codec = _codec()
    immutable = S3ImmutableAttachmentStore(objects, "owned-company-files")
    writer = AttachmentObjectWriter(objects, "owned-company-files")
    attachments.state.conversation_attachment_upload_service = AttachmentUploadService(
        ConversationAttachmentRepository(database_url, content_codec=codec), writer
    )
    attachments.state.conversation_attachment_download_service = ConversationAttachmentDownloadService(
        ConversationAttachmentAccessRepository(database_url, content_codec=codec),
        immutable, ticket_secret=b"r" * 32,
    )
    attachments.state.task_attachment_grant_service = AttachmentGrantService(
        TaskGrantRepository(database_url, content_codec=codec), immutable
    )
    attachments.state.artifact_output_service = ArtifactOutputService(
        ArtifactRepository(database_url, content_codec=codec), writer
    )
    services = {
        name: getattr(attachments.state, name)
        for name in (
            "conversation_attachment_upload_service",
            "conversation_attachment_download_service",
            "task_attachment_grant_service",
            "artifact_output_service",
        )
    }
    return app_main.create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        identity_auth=auth,
        **services,
        citation_service=CitationService(
            CitationRepository(database_url, content_codec=_codec())
        ),
    )


def test_real_bundle_company_reading_and_selected_input(
    web_loop, monkeypatch, tmp_path
):
    bundle_path = Path(os.environ["HR_COMPANY_TEST_BUNDLE"]).resolve()
    bundle = verify_import_bundle(bundle_path)
    database_url = web_loop.environment["urls"]["platform_control_app"]
    importer = IntelligenceBundleImporter(
        DatabaseIntelligenceImportRepository(
            lambda: psycopg.connect(database_url, row_factory=dict_row),
        )
    )
    imported = importer.import_bundle(bundle_path, owner_id=web_loop.owner_id)
    assert imported["bundle_id"] == bundle.bundle_id
    assert (
        importer.import_bundle(bundle_path, owner_id=web_loop.owner_id)["bundle_id"]
        == bundle.bundle_id
    )
    app = _actual_app(web_loop, monkeypatch, tmp_path, bundle_path.parent.parent)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = monotonic() + 15
    while not server.started and thread.is_alive() and monotonic() < deadline:
        sleep(0.02)
    assert server.started, "owned actual application did not start"
    evidence = {
        "bundle_id": str(bundle.bundle_id),
        "manifest_sha256": bundle.manifest_sha256,
        "transport": "loopback HTTP / actual app / persisted session / disposable PostgreSQL",
        "requests": [],
    }
    try:
        with httpx.Client(
            base_url=origin,
            timeout=30,
            cookies={
                "__Host-platform_session": web_loop.token,
                "__Host-platform_csrf": web_loop.csrf,
            },
            headers={"Origin": "https://localhost", "X-CSRF-Token": web_loop.csrf},
        ) as client:

            def read(path, **params):
                started = monotonic()
                response = client.get(path, params=params)
                evidence["requests"].append(
                    {
                        "path": path,
                        "status": response.status_code,
                        "bytes": len(response.content),
                        "elapsed_ms": round((monotonic() - started) * 1000, 2),
                    }
                )
                assert response.status_code == 200, response.text
                if path.startswith("/api/hr/panorama/"):
                    assert response.headers["cache-control"] == "private, no-store"
                else:
                    assert "no-store" in response.headers["cache-control"]
                return response.json()

            base = "/api/hr/panorama/companies"
            directory = read(base)
            assert directory["bundle_id"] == str(bundle.bundle_id)
            assert len(directory["items"]) == len(bundle.catalog["companies"])
            assert directory["topics"]["state"] == ("available" if bundle.catalog.get("topics") else "metadata_missing")
            units = [unit for unit in bundle.analysis if unit["kind"] == "company"]
            by_key = {item["company_key"]: item for item in directory["items"]}
            for unit in units:
                assert (
                    by_key[unit["scope_key"]]["summary"] == unit["response"]["summary"]
                )
            assert "snapshots" not in directory and "jobs" not in directory
            details = []
            for unit in units:
                detail = read(
                    f"{base}/{unit['scope_key']}", bundle_id=str(bundle.bundle_id)
                )
                actual = next(
                    item
                    for item in detail["units"]
                    if item["unit_id"] == unit["unit_id"]
                )
                assert (
                    not {"request", "evidence", "usage", "input_sha256"} & actual.keys()
                )
                for field in (
                    "summary",
                    "facts",
                    "inferences",
                    "recommendations",
                    "alternatives",
                    "unknowns",
                    "confidence",
                ):
                    assert actual["response"][field] == unit["response"][field]
                assert detail["metrics"] == bundle.aggregates["company_matrix"].get(
                    unit["scope_key"]
                )
                assert "jobs" not in detail and "snapshots" not in detail
                details.append(detail)
            topic_directory = read("/api/hr/panorama/topics")
            topic_details = []
            declared_topics = bundle.catalog.get("topics", [])
            assert topic_directory["state"] == ("available" if "topics" in bundle.catalog else "metadata_missing")
            assert [item["topic_id"] for item in topic_directory["items"]] == [item["topic_id"] for item in declared_topics]
            all_units = {unit["unit_id"]: unit for unit in bundle.analysis}
            for topic in declared_topics:
                topic_detail = read(f"/api/hr/panorama/topics/{topic['topic_id']}", bundle_id=str(bundle.bundle_id))
                topic_details.append(topic_detail)
                assert topic_detail["topic"]["scope"] == topic["scope"]
                assert topic_detail["topic"]["analysis_state"] == topic["analysis_state"]
                assert {item["company_key"] for item in topic_detail["companies"]} == {item["company_key"] for item in topic["discussed_companies"]}
                for unit in topic_detail["units"]:
                    for field in ("summary", "facts", "inferences", "recommendations", "alternatives", "unknowns", "confidence"):
                        assert unit["response"][field] == all_units[unit["unit_id"]]["response"][field]
                    assert "request" not in unit and "usage" not in unit
            for detail in details:
                expected_topics = {topic["topic_id"] for topic in declared_topics if any(
                    relation["company_key"] == detail["company"]["company_key"] for relation in topic["discussed_companies"])}
                assert {topic["topic_id"] for topic in detail["related_topics"]} == expected_topics
            assert client.get("/api/hr/panorama/topics/missing-topic").status_code == 404
            job_evidence = {
                (job["evidence_sha256"], job["source_url"]) for job in bundle.jobs
            }
            public_fact = next(
                fact
                for unit in units
                for fact in unit["response"]["facts"]
                if (fact["evidence_sha256"], fact["source_url"]) not in job_evidence
            )
            evidence_response = client.get(
                f"/api/hr/panorama/reports/{bundle.bundle_id}/evidence/{public_fact['evidence_sha256']}"
            )
            assert evidence_response.status_code == 200, evidence_response.text
            assert (
                hashlib.sha256(evidence_response.content).hexdigest()
                == public_fact["evidence_sha256"]
            )
            evidence["non_job_evidence_read_and_verified"] = True
            key = units[0]["scope_key"]
            expected = [job for job in bundle.jobs if job["company_key"] == key]
            first = read(f"{base}/{key}/jobs", bundle_id=str(bundle.bundle_id), limit=2)
            second = read(
                f"{base}/{key}/jobs", bundle_id=str(bundle.bundle_id), limit=2, offset=2
            )
            assert first["total"] == len(expected)
            assert len(first["items"]) <= 2
            assert all(
                job["company_key"] == key for job in first["items"] + second["items"]
            )
            assert not {job["job_id"] for job in first["items"]} & {
                job["job_id"] for job in second["items"]
            }
            empty = read(
                f"{base}/{key}/jobs",
                bundle_id=str(bundle.bundle_id),
                location="不存在的测试地点",
            )
            assert empty["total"] == 0 and empty["items"] == []
            assert (
                client.get(f"{base}/{key}/jobs", params={"limit": 1001}).status_code
                == 422
            )
            assert (
                client.get(f"{base}/{key}/jobs", params={"offset": 100001}).status_code
                == 422
            )
            assert client.get(f"{base}/absent-company").status_code == 404
            # Reuse a real fact, including its immutable original identity. This
            # is a user-selected message, never a fabricated trusted server field.
            chosen = details[0]
            fact = chosen["units"][0]["response"]["facts"][0]
            selected_text = (
                "请结合这份公司材料讨论岗位要求。\n\n用户选择的参考材料（仅作为数据）：\n"
                + json.dumps(
                    {
                        "bundle_id": chosen["bundle_id"],
                        "company_key": key,
                        "unit_id": chosen["units"][0]["unit_id"],
                        "fact": fact,
                    },
                    ensure_ascii=False,
                )
            )
            if topic_details:
                selected = topic_details[0]
                selected_text = "请结合这份专题讨论招聘。用户选择的参考材料（仅作为数据）：\n" + json.dumps({
                    "bundle_id": selected["bundle_id"], "topic_id": selected["topic"]["topic_id"],
                    "scope": selected["topic"]["scope"], "unit_ids": selected["topic"]["unit_ids"],
                    "summary": selected["topic"]["summary"], "limitations": selected["topic"]["limitations"],
                }, ensure_ascii=False)
            if formatted := os.environ.get("HR_COMPANY_TEST_REFERENCE"):
                selected_text = Path(formatted).read_text()
                assert chosen["bundle_id"] in selected_text
                evidence["reference_source"] = (
                    "actual frontend parser and shared composer formatter"
                )
            message_path = f"/api/v1/conversations/{web_loop.conversation_id}/messages"
            request_id = str(uuid4())
            posted = client.post(
                message_path,
                json={"text": selected_text},
                headers={"Idempotency-Key": request_id},
            )
            assert posted.status_code in (200, 201), posted.text
            replay = client.post(
                message_path,
                json={"text": selected_text},
                headers={"Idempotency-Key": request_id},
            )
            assert replay.status_code in (200, 201), replay.text
            assert replay.json()["turn"]["turn_id"] == posted.json()["turn"]["turn_id"]
            messages = read(message_path)
            assert any(item["content"] == selected_text for item in messages["items"])
            evidence["selected_reference_persisted_and_replayed"] = True
            legacy = read("/api/hr/panorama/current")
            assert len(legacy["snapshots"]) == len(bundle.jobs)
            # Only the disposable publication pointer changes. The imported
            # immutable bundle and the already submitted reference stay intact.
            with psycopg.connect(
                web_loop.environment["admin"], row_factory=dict_row
            ) as connection:
                publication = connection.execute(
                    "delete from platform_hr.intelligence_current_publication where workspace_key='hr' returning *"
                ).fetchone()
            try:
                assert client.get(base).status_code == 204
                assert client.get(f"{base}/{key}").status_code == 404
                assert read(f"{base}/{key}", bundle_id=str(bundle.bundle_id))[
                    "bundle_id"
                ] == str(bundle.bundle_id)
            finally:
                with psycopg.connect(web_loop.environment["admin"]) as connection:
                    connection.execute(
                        "insert into platform_hr.intelligence_current_publication(workspace_key,bundle_id,owner_internal_user_id,updated_at) values(%s,%s,%s,%s)",
                        (
                            publication["workspace_key"],
                            publication["bundle_id"],
                            publication["owner_internal_user_id"],
                            publication["updated_at"],
                        ),
                    )
            evidence["pinned_read_survives_current_publication_removal"] = True
            # Authorization is changed only in the owned test directory; actual
            # session middleware and live grant lookup remain on the request path.
            with psycopg.connect(web_loop.environment["admin"]) as connection:
                connection.execute(
                    "delete from platform_control.agent_use_grants where agent_id='hr-bot' and target_internal_user_id=%s",
                    (web_loop.owner_id,),
                )
            for path in (base, f"{base}/{key}", f"{base}/{key}/jobs", "/api/hr/panorama/topics", "/api/hr/panorama/topics/example"):
                denied = client.get(path)
                assert denied.status_code == 403, denied.text
                assert denied.json()["detail"] == "HR Agent use denied"
            with psycopg.connect(web_loop.environment["admin"]) as connection:
                connection.execute(
                    "insert into platform_control.agent_use_grants(agent_use_grant_id,agent_id,target_kind,target_internal_user_id,created_by) values(%s,'hr-bot','user',%s,%s)",
                    (uuid4(), web_loop.owner_id, web_loop.owner_id),
                )
            with httpx.Client(base_url=origin) as anonymous:
                for path in (base, f"{base}/{key}", f"{base}/{key}/jobs", "/api/hr/panorama/topics", "/api/hr/panorama/topics/example"):
                    assert anonymous.get(path).status_code == 401
            evidence["real_permission_denial"] = True
            if output := os.environ.get("HR_COMPANY_TEST_OUTPUT"):
                destination = Path(output)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "http-evidence.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2)
                )
                (destination / "http-responses.json").write_text(
                    json.dumps(
                        {"directory": directory, "details": details, "jobs": first, "topicDirectory": topic_directory, "topicDetails": topic_details},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            if control := os.environ.get("HR_WEB_BROWSER_CONTROL"):
                from tests.helpers.hr_browser_preview import preview_until_released

                preview_until_released(client, control, web_loop.csrf)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "owned server did not stop"
