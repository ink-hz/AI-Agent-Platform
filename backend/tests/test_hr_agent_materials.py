"""Real HTTP upload, processing and private material references; local object store."""

import asyncio
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.attachments.conversation_models import ObjectReceipt
from app.attachments.conversation_repository import ConversationAttachmentRepository
from app.attachments.conversation_routes import build_conversation_attachment_router
from app.attachments.derivatives import DerivativeBuilder
from app.attachments.scanner import TrustedInternalScanner
from app.attachments.upload_service import AttachmentUploadService
from app.attachments.validation import AttachmentValidator, OpenedObject
from app.attachments.worker import AttachmentProcessor
from app.attachments.worker_runtime import AttachmentProcessingRepository
from app.hr_agent.materials import MaterialService
from app.hr_agent.types import HrAgentProblem, content_sha256
from tests.test_hr_agent_routes import body
from tests.test_hr_agent_routes import database as database  # noqa: PLC0414
from tests.test_hr_agent_routes import secured as secured  # noqa: PLC0414


class MemoryStore:
    def __init__(self):
        self.objects = {}

    def put_stream(self, ref, stream, expected_size):
        data = stream.read()
        assert len(data) == expected_size
        self.objects[ref] = data
        return ObjectReceipt(len(data), hashlib.sha256(data).digest())

    def delete(self, ref):
        self.objects.pop(ref, None)

    def open(self, ref, immutable_locator=None):
        data = self.objects[ref]
        return OpenedObject(io.BytesIO(data), len(data), "etag:local-immutable")

    def read_verified(self, asset):
        return self.objects[asset.object_ref]


@pytest.fixture
def uploaded(secured, database):
    client, headers, repo, owner = secured
    with database.admin_connection() as conn:
        conn.execute("TRUNCATE platform_attachments.attachments CASCADE")
        conn.execute(
            "INSERT INTO platform_control.internal_users (internal_user_id,display_name,status) VALUES (%s,'Local Test','active')",
            (owner,),
        )
    store = MemoryStore()
    uploads = AttachmentUploadService(
        ConversationAttachmentRepository(database.dsn, content_codec=repo.codec), store
    )
    # Build a fresh app so the middleware route snapshot includes upload endpoints.
    from app.control_plane.authorization import AuthorizationService
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from app.hr_agent.access import HrAccess
    from app.hr_agent.routes import build_hr_agent_router
    from app.hr_agent.service import HrAgentService
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.test_hr_position_api import _SecurityAuth

    materials = MaterialService(database.connection, repo.codec, store)
    access = HrAccess(
        SimpleNamespace(decide_for_user_id=lambda *_: SimpleNamespace(allowed=True)),
        reference_authorizer=lambda owner, ref, *_: (
            materials.read_text(owner, ref) is not None
        ),
    )
    repo.scope_validator = lambda owner, objects, refs, work: access.authorize_scope(
        owner, objects, refs, work_id=work
    )
    app = FastAPI()
    app.state.conversation_attachment_upload_service = uploads
    app.include_router(build_conversation_attachment_router())
    app.include_router(
        build_hr_agent_router(HrAgentService(repo, access, materials=materials))
    )
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=_SecurityAuth(owner),
        public_assets=frozenset(),
        authorization=AuthorizationService(SimpleNamespace(permits=lambda *_: False)),
        routes=tuple(app.router.routes),
    )
    client = TestClient(app)
    client.cookies.set("session", "valid")
    client.cookies.set("csrf", "csrf-token")
    data = "公开岗位：负责机器人算法研发。\n需要真实项目经验。".encode()
    response = client.post(
        "/api/v1/attachments/uploads",
        json={
            "conversation_id": None,
            "original_name": "public-jd.txt",
            "declared_mime": "text/plain",
            "declared_size": len(data),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    upload = response.json()
    uid = upload["upload_id"]
    aid = upload["attachment_id"]
    response = client.put(
        f"/api/v1/attachments/uploads/{uid}/content",
        content=data,
        headers={
            **headers,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
        },
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            f"/api/v1/attachments/uploads/{uid}/complete", headers=headers
        ).status_code
        == 200
    )
    processing = AttachmentProcessingRepository(
        database.dsn.replace("user=platform_control_app", "user=platform_brain_worker"),
        content_codec=repo.codec,
    )
    processor = AttachmentProcessor(
        repository=processing,
        object_store=store,
        validator=AttachmentValidator(),
        scanner=TrustedInternalScanner(),
        derivatives=DerivativeBuilder(),
        worker_id="local-material-test",
    )
    for _ in range(4):
        if not asyncio.run(processor.process_next()):
            break
    return client, headers, repo, owner, materials, aid, data, store


def test_real_upload_material_ref_submit_and_integrity(uploaded):
    client, headers, _repo, owner, materials, aid, data, store = uploaded
    response = client.get("/api/hr/agent/materials/" + aid)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["parse_state"] == "ready", view
    digest = hashlib.sha256(data).hexdigest()
    assert view["original_ref"]["sha256"] == content_sha256(
        {"byte_sha256": digest, "detected_mime": "text/plain", "size_bytes": len(data)}
    )
    ref = view["text_ref"]
    assert materials.read_text(owner, ref).text == data.decode()
    response = client.post(
        "/api/hr/agent/works", headers=headers, json={**body(), "references": [ref]}
    )
    assert response.status_code == 201, response.text
    with pytest.raises(HrAgentProblem) as error:
        materials.resolve(uuid4(), aid)
    assert error.value.http_status == 404
    store.objects[next(iter(store.objects))] = b"tampered"
    assert client.get("/api/hr/agent/materials/" + aid).status_code == 503


def test_deleted_material_never_returns_available_ref(uploaded, database):
    client, _headers, _repo, _owner, _materials, aid, _data, _store = uploaded
    with database.admin_connection() as conn:
        conn.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (aid,),
        )
    assert client.get("/api/hr/agent/materials/" + aid).status_code == 410


def test_quarantine_during_object_read_does_not_release_text(uploaded, database):
    client, _headers, _repo, _owner, _materials, aid, _data, store = uploaded
    stage = store.read_verified

    def quarantine(asset):
        data = stage(asset)
        with database.admin_connection() as conn:
            conn.execute(
                "UPDATE platform_attachments.attachments SET state='quarantined' WHERE attachment_id=%s",
                (aid,),
            )
        return data

    store.read_verified = quarantine
    response = client.get("/api/hr/agent/materials/" + aid)
    assert response.status_code == 410, response.text


def test_uploaded_jd_runs_real_loop_and_saved_result_is_readable_from_http(
    uploaded, tmp_path, database
):
    from app.hr_agent.resources import PublishedKnowledge, ResourceReader
    from app.hr_agent.runtime import run_work
    from tests.test_hr_agent_context import publication
    from tests.test_hr_agent_runtime import ScriptModel, answer, tool

    client, headers, repo, owner, materials, aid, data, _store = uploaded
    from dataclasses import replace

    # This provider uses a conservative one-unit-per-byte estimate. Keep an
    # explicit engineering profile for tool schemas and returned text.
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
    ref = client.get("/api/hr/agent/materials/" + aid).json()["text_ref"]
    response = client.post(
        "/api/hr/agent/works", headers=headers, json={**body(), "references": [ref]}
    )
    assert response.status_code == 201, response.text
    work = response.json()
    args = {
        "kind": "role_calibration",
        "title": "公开岗位校准",
        "body": "岗位要求需结合真实项目证据核验。",
        "result_id": None,
        "expected_revision": None,
        "objects": [],
        "source_refs": [ref],
        "preceding_refs": [],
        "base_standard_ref": None,
        "changes": [],
        "basis": [{"kind": "user_temporary", "input_revision": 1, "ref": ref}],
    }
    model = ScriptModel(
        [
            tool("read_resource", {"ref": ref}),
            tool("save_result", args),
            answer("已保存岗位校准。"),
        ]
    )
    # Only the provider boundary is scripted. Context and all five-tool dispatch are real.
    fence = repo.claim("uploaded-jd-worker", 60)
    assert str(fence.work_id) == work["work_id"]
    done = run_work(repo, model, resources, fence)
    assert done["state"] == "completed", done
    import json

    assert any(
        message["role"] == "tool"
        and (json.loads(message["content"]).get("data") or {}).get("text")
        == data.decode()
        for request in model.requests[1:]
        for message in request.messages
    )
    response = client.get("/api/hr/agent/results?thread_id=" + work["thread_id"])
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1, items
    result_ref = items[0]["ref"]
    response = client.get(
        f"/api/hr/agent/results/{result_ref['id']}/revisions/{result_ref['revision']}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["body"] == args["body"]
    assert response.json()["basis"] == args["basis"]
    from app.hr.models import CreateManualPosition
    from app.hr.repository import HrPositionRepository

    position = HrPositionRepository(database.dsn).create_manual(
        CreateManualPosition(owner, uuid4(), uuid4(), "虚构岗位", "研发", ("深圳",))
    )
    obj = {"kind": "position", "id": str(position.position_id)}
    response = client.post(
        f"/api/hr/agent/works/{work['work_id']}/inputs",
        headers=headers,
        json={
            "expected_input_revision": 1,
            "text": "将已有校准用于这个岗位",
            "objects": [obj],
            "references": [ref, result_ref],
            "question_id": None,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["input_revision"] == 2
    link_body = {"expected_result_revision": result_ref["revision"], "objects": [obj]}
    response = client.post(
        f"/api/hr/agent/results/{result_ref['id']}/links",
        headers=headers,
        json=link_body,
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            f"/api/hr/agent/results/{result_ref['id']}/links",
            headers=headers,
            json=link_body,
        ).json()
        == response.json()
    )
    response = client.get(
        f"/api/hr/agent/results?object_kind=position&object_id={position.position_id}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["ref"] == result_ref
    # Revocation is checked again on both list and exact result reads.
    with database.admin_connection() as conn:
        conn.execute(
            "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
            (aid,),
        )
    response = client.get(
        f"/api/hr/agent/results/{result_ref['id']}/revisions/{result_ref['revision']}"
    )
    assert response.status_code == 403, response.text
    assert args["body"] not in response.text


def test_worker_material_factory_loads_only_attachment_settings(
    uploaded, database, tmp_path, monkeypatch
):
    from app.attachments.download_service import S3ImmutableAttachmentStore
    from app.hr_agent.materials import build_material_service_from_environment

    _client, _headers, repo, owner, _materials, aid, data, store = uploaded
    secret = tmp_path / "attachment-dsn"
    secret.write_text(database.dsn)
    secret.chmod(0o600)
    captured = []

    def configured_store(config):
        captured.append(config)
        return store

    monkeypatch.setattr(S3ImmutableAttachmentStore, "from_config", configured_store)
    env = {
        "PLATFORM_CONVERSATION_ATTACHMENT_ENABLED": "1",
        "PLATFORM_ATTACHMENT_CONTROL_DATABASE_URL_FILE": str(secret),
        "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE": str(
            repo.settings.content_keyring_file
        ),
        "PLATFORM_ATTACHMENT_S3_ENDPOINT": "http://local.invalid",
        "PLATFORM_ATTACHMENT_S3_BUCKET": "local-test",
        "PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE": "/unused-local-access",
        "PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE": "/unused-local-secret",
    }
    service = build_material_service_from_environment(
        env, temporary_root=repo.settings.work_dir
    )
    ref = service.resolve(owner, aid)["text_ref"]
    assert service.read_text(owner, ref).text == data.decode()
    assert len(captured) == 1
    assert build_material_service_from_environment({}) is None
    with pytest.raises(ValueError, match="configuration unavailable"):
        build_material_service_from_environment(
            {"PLATFORM_CONVERSATION_ATTACHMENT_ENABLED": "1"}
        )


def test_material_resolve_never_stages_plaintext_on_disk(uploaded, monkeypatch):
    import tempfile

    _client, _headers, _repo, owner, materials, aid, data, _store = uploaded

    def no_staging(*args, **kwargs):
        pytest.fail("Material plaintext must stay in memory")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", no_staging)
    view = materials.resolve(owner, aid)
    assert materials.read_text(owner, view["text_ref"]).text == data.decode()


def test_real_s3_reader_preserves_immutable_precondition_and_bounds(uploaded):
    from app.attachments.download_service import S3ImmutableAttachmentStore

    _client, _headers, _repo, owner, materials, aid, data, store = uploaded
    requests = []
    streams = []

    class LocalS3:
        def get_object(self, **request):
            requests.append(request)
            stream = io.BytesIO(store.objects[request["Key"]])
            streams.append(stream)
            return {"Body": stream, "ContentLength": len(data)}

    materials.store = S3ImmutableAttachmentStore(LocalS3(), "local-bucket")
    assert (
        materials.read_text(owner, materials.resolve(owner, aid)["text_ref"]).text
        == data.decode()
    )
    assert all(request["IfMatch"] == "local-immutable" for request in requests)
    assert all(stream.closed for stream in streams)
    store.objects[next(iter(store.objects))] = data + b"extra"
    with pytest.raises(HrAgentProblem):
        materials.resolve(owner, aid)
    assert streams[-1].closed


def test_killed_material_reader_leaves_no_plaintext_files(uploaded, database, tmp_path):
    import json
    import subprocess
    import sys
    import time

    _client, _headers, repo, _owner, _materials, aid, data, _store = uploaded
    config = tmp_path / "material-process.json"
    marker = tmp_path / "read-started"
    config.write_text(
        json.dumps(
            {
                "dsn": database.dsn,
                "keyring": str(repo.settings.content_keyring_file),
                "root": str(repo.settings.work_dir),
                "marker": str(marker),
                "aid": aid,
                "owner": str(_owner),
                "size": len(data),
            }
        )
    )
    config.chmod(0o600)
    script = """
import json,sys,time
from pathlib import Path
from uuid import UUID
from app.attachments.download_service import S3ImmutableAttachmentStore
from app.hr_agent.materials import build_material_service
cfg=json.loads(Path(sys.argv[1]).read_text())
class Body:
    def read(self, size):
        Path(cfg['marker']).touch()
        time.sleep(60)
        return b''
    def close(self):pass
class LocalS3:
    def get_object(self,**request):return {'Body':Body(),'ContentLength':cfg['size']}
service=build_material_service(cfg['dsn'],cfg['keyring'],S3ImmutableAttachmentStore(LocalS3(),'local'),temporary_root=cfg['root'])
service.resolve(UUID(cfg['owner']),cfg['aid'])
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(config)],
        cwd=Path(__file__).parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while (
            not marker.exists()
            and time.monotonic() < deadline
            and process.poll() is None
        ):
            time.sleep(0.02)
        assert marker.exists()
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode < 0
        assert list(repo.settings.work_dir.rglob("*")) == []
        assert data not in stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
