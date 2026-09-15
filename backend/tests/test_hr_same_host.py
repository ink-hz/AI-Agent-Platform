"""Real loopback account/session + standalone HR HTTP, disposable PostgreSQL.

Only login-provider exchange and attachment object storage are local test doubles.
HR executes its installed entrypoint with its own interpreter; no platform source
is placed on that process's PYTHONPATH. No browser, production or real model call.
Run with HR_SAME_HOST_ROOT pointing at a separately installed HR checkout.
"""

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from ipaddress import ip_network
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from app.attachments.conversation_routes import build_conversation_attachment_router
from app.attachments.download_service import (
    ConversationAttachmentAccessRepository,
    ConversationAttachmentDownloadService,
)
from app.cloud_replica.loopback_proxy import LoopbackProxy
from app.control_plane.auth import HardStaleAccessAuditWriter
from app.control_plane.authorization import (
    AuthorizationRepository,
    AuthorizationService,
)
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.routes_auth import build_auth_router
from fastapi import FastAPI
from tests.helpers.hr_history_replay import environment


@contextmanager
def loopback_server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, proxy_headers=False)
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "owned platform HTTP process did not start"
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()


@pytest.fixture
def same_host(tmp_path):
    selected = os.environ.get("HR_SAME_HOST_ROOT")
    if not selected:
        pytest.skip("set HR_SAME_HOST_ROOT to a separately installed HR checkout")
    root = Path(selected).resolve()
    interpreter = root / "backend/.venv/bin/python"
    assert interpreter.is_file(), "install HR's own backend dependencies first"
    with environment(tmp_path / "runtime") as fixture:
        db = fixture["database"]
        old_app = fixture["client"].app
        auth = old_app.user_middleware[0].kwargs["auth"]
        auth.trusted_proxy_networks = (ip_network("127.0.0.1/32"),)
        auth.hard_stale_audit = HardStaleAccessAuditWriter(
            db.dsn.replace("user=platform_control_app", "user=platform_audit_append")
        )
        platform = FastAPI()
        platform_requests = []

        @platform.middleware("http")
        async def record_request(request, call_next):
            response = await call_next(request)
            platform_requests.append(
                (request.method, request.url.path, response.status_code)
            )
            return response

        platform.include_router(
            build_auth_router(
                auth,
                static_dir=str(tmp_path),
                public_assets=frozenset(),
                detailed_health=dict,
            )
        )
        platform.include_router(build_conversation_attachment_router())
        platform.state.conversation_attachment_upload_service = (
            old_app.state.conversation_attachment_upload_service
        )

        class LocalDownloadStore:
            def stage_verified(self, asset, directory):
                raw = fixture["store"].read_verified(asset)
                assert len(raw) == asset.size_bytes
                assert hashlib.sha256(raw).digest() == asset.sha256
                assert asset.immutable_locator == "etag:local-immutable"
                path = directory / "payload"
                path.write_bytes(raw)
                return path

        platform.state.conversation_attachment_download_service = (
            ConversationAttachmentDownloadService(
                ConversationAttachmentAccessRepository(
                    db.dsn, content_codec=fixture["repo"].codec
                ),
                LocalDownloadStore(),
                ticket_secret=b"t" * 32,
            )
        )
        platform.add_middleware(
            IdentitySecurityMiddleware,
            auth=auth,
            public_assets=frozenset(),
            authorization=AuthorizationService(AuthorizationRepository(db.dsn)),
            routes=tuple(platform.router.routes),
        )
        with (
            loopback_server(platform) as platform_backend,
            loopback_server(
                LoopbackProxy(
                    target_base_url=platform_backend, trusted_peer_cidrs="127.0.0.1/32"
                )
            ) as platform_origin,
        ):
            session_headers = {
                **fixture["headers"],
                "Host": "localhost",
                "Cookie": "; ".join(
                    f"{key}={value}" for key, value in fixture["client"].cookies.items()
                ),
            }
            account = httpx.get(
                platform_origin + "/api/v1/account",
                headers=session_headers,
                trust_env=False,
            )
            assert account.status_code == 200, account.text
            assert account.json()["internal_user_id"] == str(fixture["owner"])
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()
            config = tmp_path / "runtime/config"
            dsn_file = tmp_path / "database-url"
            dsn_file.write_text(db.dsn)
            dsn_file.chmod(0o600)
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("HR_", "PLATFORM_", "PYTHONPATH"))
            }
            env.update(
                {
                    "PYTHONPATH": str(root / "backend"),
                    "HR_API_PORT": str(port),
                    "HR_PUBLIC_ORIGIN": "https://localhost",
                    "HR_PLATFORM_ORIGIN": platform_origin,
                    "PLATFORM_CONTROL_DATABASE_URL_FILE": str(dsn_file),
                    "PLATFORM_HR_AGENT_ENABLED": "1",
                    "PLATFORM_RELEASE_SHA": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=root, text=True
                    ).strip(),
                    "PLATFORM_EXECUTION_RELAY_ENABLED": "0",
                    "PLATFORM_CONVERSATION_ATTACHMENT_ENABLED": "0",
                    "PLATFORM_HR_AGENT_WORK_DIR": str(config / "work"),
                    "PLATFORM_HR_AGENT_KNOWLEDGE_DIR": str(
                        tmp_path / "runtime/knowledge"
                    ),
                }
            )
            for key in (
                "CONTENT_KEYRING_FILE",
                "PROVIDER_PROFILE_FILE",
                "BUDGET_PROFILE_FILE",
                "DIAGNOSTIC_PROFILE_FILE",
            ):
                env["PLATFORM_HR_AGENT_" + key] = str(config / (key.lower() + ".json"))
            process = subprocess.Popen(
                [str(interpreter), "-m", "app"],
                cwd=root / "backend",
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                origin = f"http://127.0.0.1:{port}"
                with httpx.Client(
                    base_url=origin, timeout=5, trust_env=False
                ) as client:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            pytest.fail(
                                "standalone HR exited: " + process.communicate()[1]
                            )
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.05)
                    else:
                        pytest.fail("standalone HR did not become live")
                    cookies = fixture["client"].cookies
                    headers = {
                        **fixture["headers"],
                        "Host": "localhost",
                        "Cookie": "; ".join(
                            f"{key}={value}" for key, value in cookies.items()
                        ),
                    }
                    yield {
                        **fixture,
                        "http": client,
                        "headers": headers,
                        "auth": auth,
                        "platform_requests": platform_requests,
                        "hr_environment": env,
                        "hr_root": root,
                        "hr_interpreter": interpreter,
                        "platform_origin": platform_origin,
                        "process": process,
                    }
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()


def body():
    return {
        "thread_id": None,
        "text": "Synthetic same-host public HR question",
        "objects": [],
        "references": [],
        "budget_profile": "test",
    }


def test_real_account_session_csrf_submit_replay_owner_and_revocation(same_host):
    e = same_host
    client, headers, db = e["http"], e["headers"], e["database"]
    url = "/api/hr/agent/works"
    assert client.get("/api/hr/agent/configuration").status_code == 401
    assert client.get("/api/hr/agent/configuration", headers=headers).status_code == 200
    key = str(uuid4())
    mutation = {**headers, "Idempotency-Key": key}
    assert (
        client.post(
            url,
            json=body(),
            headers={k: v for k, v in mutation.items() if k != "X-CSRF-Token"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            url, json=body(), headers={**mutation, "Origin": "https://wrong.invalid"}
        ).status_code
        == 403
    )
    injected = client.post(
        url, json={**body(), "owner_id": str(uuid4())}, headers=mutation
    )
    assert injected.status_code == 422, injected.text
    created = client.post(url, json=body(), headers=mutation)
    assert created.status_code == 201, created.text
    replay = client.post(url, json=body(), headers=mutation)
    assert replay.status_code in (200, 201), replay.text
    assert replay.json()["work_id"] == created.json()["work_id"]
    work_url = url + "/" + created.json()["work_id"]
    assert client.get(work_url, headers=headers).status_code == 200
    other_cookie = "; ".join(f"{k}={v}" for k, v in e["other"].cookies.items())
    assert client.get(work_url, headers={"Cookie": other_cookie}).status_code == 404
    with db.admin_connection() as conn:
        conn.execute(
            "UPDATE platform_control.internal_users SET role='member' WHERE internal_user_id=%s",
            (e["owner"],),
        )
        conn.execute(
            "UPDATE platform_control.agent_use_grants SET revoked_at=now(),revoked_by=target_internal_user_id WHERE target_internal_user_id=%s",
            (e["owner"],),
        )
    assert client.get("/api/hr/agent/configuration", headers=headers).status_code == 403
    session = e["auth"].authenticate(e["client"].cookies[e["auth"].cookie_name])
    e["auth"].logout(session[0])
    assert client.get("/api/hr/agent/configuration", headers=headers).status_code == 401


def test_hard_stale_session_blocks_registered_mutations_and_retains_reads(same_host):
    e = same_host
    with e["database"].admin_connection() as conn:
        conn.execute(
            "UPDATE platform_control.directory_state SET last_complete_at=now()-interval '8 days' WHERE singleton"
        )
    response = e["http"].get("/api/hr/agent/configuration", headers=e["headers"])
    assert response.status_code == 200, response.text
    response = e["http"].post(
        "/api/hr/agent/works",
        json=body(),
        headers={**e["headers"], "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 503, response.text
    assert (
        e["http"].get("/api/hr/not-implemented", headers=e["headers"]).status_code
        == 404
    )


def test_hr_resource_ticket_is_issued_and_consumed_by_existing_platform(same_host):
    from app.attachments.derivatives import DerivativeBuilder
    from app.attachments.scanner import TrustedInternalScanner
    from app.attachments.validation import AttachmentValidator
    from app.attachments.worker import AttachmentProcessor
    from app.attachments.worker_runtime import AttachmentProcessingRepository

    e = same_host
    db, headers = e["database"], e["headers"]
    data = b"Synthetic public position material, no personal information."
    with httpx.Client(
        base_url=e["platform_origin"],
        headers={
            **headers,
            "X-Real-IP": "127.0.0.1",
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Proto": "https",
        },
        trust_env=False,
    ) as platform:
        response = platform.post(
            "/api/v1/attachments/uploads",
            json={
                "conversation_id": None,
                "original_name": "public-position.txt",
                "declared_mime": "text/plain",
                "declared_size": len(data),
            },
        )
        assert response.status_code == 201, response.text
        upload = response.json()
        uid, aid = upload["upload_id"], upload["attachment_id"]
        response = platform.put(
            f"/api/v1/attachments/uploads/{uid}/content",
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 200, response.text
        assert (
            platform.post(f"/api/v1/attachments/uploads/{uid}/complete").status_code
            == 200
        )
        processor = AttachmentProcessor(
            repository=AttachmentProcessingRepository(
                db.dsn.replace(
                    "user=platform_control_app", "user=platform_brain_worker"
                ),
                content_codec=e["repo"].codec,
            ),
            object_store=e["store"],
            validator=AttachmentValidator(),
            scanner=TrustedInternalScanner(),
            derivatives=DerivativeBuilder(),
            worker_id="same-host-synthetic-attachment",
        )
        for _ in range(8):
            if not asyncio.run(processor.process_next()):
                break
        position = uuid4()
        # Test input setup only: existing imported position and material binding.
        # Attachment ready state is produced by real upload/processor methods above.
        with db.admin_connection() as conn:
            conn.execute(
                "INSERT INTO platform_hr.positions(position_id,owner_internal_user_id,client_request_id,source_kind,title) VALUES(%s,%s,%s,'manual','Synthetic position')",
                (position, e["owner"], uuid4()),
            )
            conn.execute(
                "INSERT INTO platform_hr.position_materials(position_id,attachment_id,owner_internal_user_id,client_request_id) VALUES(%s,%s,%s,%s)",
                (position, aid, e["owner"], uuid4()),
            )
        projected = e["http"].get(
            f"/api/hr/positions/{position}/resources", headers=headers
        )
        assert projected.status_code == 200, projected.text
        assert projected.json()["materials"][0]["attachment_id"] == aid
        url = f"/api/hr/positions/{position}/resources/{aid}/ticket"
        denied = e["http"].post(
            url,
            json={"purpose": "download"},
            headers={k: v for k, v in headers.items() if k != "X-CSRF-Token"},
        )
        assert denied.status_code == 403, denied.text
        response = e["http"].post(url, json={"purpose": "download"}, headers=headers)
        assert response.status_code == 200, (
            response.text,
            [r for r in e["platform_requests"] if r[1].endswith("/ticket")],
        )
        content = platform.get(response.json()["content_path"])
        assert content.status_code == 200, content.text
        assert content.content == data
        other = {"Cookie": "; ".join(f"{k}={v}" for k, v in e["other"].cookies.items())}
        assert (
            e["http"]
            .get(f"/api/hr/positions/{position}/resources", headers=other)
            .status_code
            == 404
        )
        with db.admin_connection() as conn:
            conn.execute(
                "UPDATE platform_attachments.attachments SET retained_until=now()-interval '1 second' WHERE attachment_id=%s",
                (aid,),
            )
        assert (
            e["http"]
            .post(url, json={"purpose": "download"}, headers=headers)
            .status_code
            == 404
        )


def test_standalone_worker_cli_health_and_graceful_stop(same_host, tmp_path):
    """Actual installed Worker CLI polls the DB; empty queue makes no model call."""
    e = same_host
    status = tmp_path / "worker-status.json"
    env = {**e["hr_environment"], "HR_WORKER_STATUS_FILE": str(status)}
    command = [str(e["hr_interpreter"]), "-m", "app.hr_agent.worker"]
    kwargs = {"cwd": e["hr_root"] / "backend", "env": env}

    def check():
        return subprocess.run(
            command + ["healthcheck"],
            **kwargs,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    assert check().returncode == 1
    process = subprocess.Popen(
        command, **kwargs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("owned Worker exited: " + process.communicate()[1])
            probe = check()
            if probe.returncode == 0:
                report = json.loads(probe.stdout)
                assert report["ready"] is True
                assert report["role"] == "worker"
                assert report["worker"]["pid"] == process.pid
                assert report["phase"] == "cloud"
                break
            time.sleep(0.05)
        else:
            pytest.fail("owned Worker did not become ready: " + probe.stdout)
        with e["database"].admin_connection() as connection:
            assert connection.execute(
                "SELECT count(*) FROM platform_hr_agent.model_attempts"
            ).fetchone() == (0,)
        process.terminate()
        process.communicate(timeout=8)
        assert process.returncode == 0
        assert not status.exists()
        assert check().returncode == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
