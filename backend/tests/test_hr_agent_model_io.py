from __future__ import annotations

import json
import logging
import stat
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import pytest


@contextmanager
def provider_server(chunks: list[bytes], *, status: int = 200, delay: float = 0):
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                if delay:
                    time.sleep(delay)
                self.wfile.write(chunk)
                self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions", requests
    finally:
        server.shutdown()
        thread.join()


def request(*, deadline_seconds: float = 2.0):
    from app.hr_agent.types import ModelRequest

    return ModelRequest(
        attempt_id=uuid4(),
        purpose="work",
        profile_id="test-profile",
        messages=({"role": "user", "content": "PROMPT_SENTINEL"},),
        tools=({"type": "function", "function": {"name": "save_note"}},),
        max_output_tokens=128,
        deadline_seconds=deadline_seconds,
    )


def openai_profile(endpoint: str, credential_file: Path):
    from app.hr_agent.model import ProviderProfile

    return ProviderProfile(
        profile_id="test-profile",
        revision="profile-r1",
        protocol="openai_chat_sse",
        endpoint=endpoint,
        model="synthetic-model",
        credential_file=credential_file,
        timeout_seconds=3.0,
    )


def test_model_port_builds_from_validated_settings_profile(tmp_path: Path) -> None:
    from app.hr_agent.model import ConfiguredHttpModelPort

    credential = tmp_path / "credential"
    write_credential(credential)
    port = ConfiguredHttpModelPort.from_mapping(
        {
            "id": "test-profile",
            "revision": "profile-r1",
            "protocol": "openai_chat_sse",
            "endpoint": "http://127.0.0.1:12345/v1/chat/completions",
            "model": "synthetic-model",
            "credential_file": str(credential),
            "tokenizer": "conservative_utf8",
            "context_window_tokens": 4096,
            "timeout_seconds": 5,
        }
    )
    assert port.profile_revision == "profile-r1"


def write_credential(path: Path) -> None:
    path.write_text("CREDENTIAL_SENTINEL", encoding="utf-8")
    path.chmod(0o600)


def test_openai_stream_normalizes_complete_tool_call_and_usage(tmp_path: Path) -> None:
    chunks = [
        b'data: {"id":"req-1","choices":[{"delta":{"content":"done ","tool_calls":[{"index":0,"id":"call-1","function":{"name":"save_note","arguments":"{\\"body\\":\\"ok"}}]},"finish_reason":null}],"usage":null}\n\n',
        b'data: {"id":"req-1","choices":[{"delta":{"content":"now","tool_calls":[{"index":0,"function":{"arguments":"\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":11,"completion_tokens":7}}\n\n',
        b"data: [DONE]\n\n",
    ]
    credential = tmp_path / "credential"
    write_credential(credential)
    with provider_server(chunks) as (endpoint, seen):
        from app.hr_agent.model import ConfiguredHttpModelPort, collect_reply

        reply = collect_reply(
            ConfiguredHttpModelPort(openai_profile(endpoint, credential)).stream(
                request()
            )
        )

    assert reply.text == "done now"
    assert reply.tool_calls[0].arguments == {"body": "ok"}
    assert reply.stop_reason == "tool_calls"
    assert reply.usage.input_total == 11
    assert reply.usage.output_total == 7
    assert seen == [
        {
            "path": "/v1/chat/completions",
            "authorization": "Bearer CREDENTIAL_SENTINEL",
            "body": {
                "model": "synthetic-model",
                "messages": [{"role": "user", "content": "PROMPT_SENTINEL"}],
                "tools": [{"type": "function", "function": {"name": "save_note"}}],
                "max_tokens": 128,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        }
    ]


def test_incomplete_tool_json_is_rejected_without_retry(tmp_path: Path) -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-x","function":{"name":"save_note","arguments":"{\\"secret\\":"}}]},"finish_reason":"tool_calls"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    credential = tmp_path / "credential"
    write_credential(credential)
    with provider_server(chunks) as (endpoint, seen):
        from app.hr_agent.model import (
            ConfiguredHttpModelPort,
            ModelProtocolError,
            collect_reply,
        )

        with pytest.raises(ModelProtocolError, match="incomplete_response"):
            collect_reply(
                ConfiguredHttpModelPort(openai_profile(endpoint, credential)).stream(
                    request()
                )
            )
    assert len(seen) == 1


def test_anthropic_stream_profile_normalizes_text_and_usage(tmp_path: Path) -> None:
    chunks = [
        b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"answer"}}\n\n',
        b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}\n\n',
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]
    credential = tmp_path / "credential"
    write_credential(credential)
    with provider_server(chunks) as (endpoint, seen):
        from app.hr_agent.model import (
            ConfiguredHttpModelPort,
            ProviderProfile,
            collect_reply,
        )

        profile = ProviderProfile(
            profile_id="test-profile",
            revision="anthropic-r1",
            protocol="anthropic_messages_sse",
            endpoint=endpoint,
            model="claude-synthetic",
            credential_file=credential,
        )
        reply = collect_reply(ConfiguredHttpModelPort(profile).stream(request()))
    assert reply.text == "answer"
    assert reply.stop_reason == "end_turn"
    assert (reply.usage.input_total, reply.usage.output_total) == (5, 3)
    assert seen[0]["body"]["tools"] == [
        {"name": "save_note", "input_schema": {"type": "object"}}
    ]


def test_missing_done_marker_is_incomplete(tmp_path: Path) -> None:
    credential = tmp_path / "credential"
    write_credential(credential)
    chunks = [
        b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"stop"}]}\n\n'
    ]
    with provider_server(chunks) as (endpoint, seen):
        from app.hr_agent.model import (
            ConfiguredHttpModelPort,
            ModelProtocolError,
            collect_reply,
        )

        with pytest.raises(ModelProtocolError, match="incomplete_response"):
            collect_reply(
                ConfiguredHttpModelPort(openai_profile(endpoint, credential)).stream(
                    request()
                )
            )
    assert len(seen) == 1


def test_provider_refusal_has_stable_classification(tmp_path: Path) -> None:
    credential = tmp_path / "credential"
    write_credential(credential)
    chunks = [
        b'data: {"choices":[{"delta":{},"finish_reason":"content_filter"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    with provider_server(chunks) as (endpoint, _seen):
        from app.hr_agent.model import (
            ConfiguredHttpModelPort,
            ModelTransportError,
            collect_reply,
        )

        with pytest.raises(ModelTransportError) as caught:
            collect_reply(
                ConfiguredHttpModelPort(openai_profile(endpoint, credential)).stream(
                    request()
                )
            )
    assert caught.value.code == "provider_refused"


def test_first_request_logs_exclude_sensitive_payloads(tmp_path: Path, caplog) -> None:
    credential = tmp_path / "credential"
    write_credential(credential)
    chunks = [b"PROVIDER_ERROR_SENTINEL"]
    caplog.set_level(logging.DEBUG)
    with provider_server(chunks, status=500) as (endpoint, _seen):
        from app.hr_agent.model import ConfiguredHttpModelPort, ModelTransportError

        with pytest.raises(ModelTransportError):
            list(
                ConfiguredHttpModelPort(openai_profile(endpoint, credential)).stream(
                    request()
                )
            )
    assert "PROMPT_SENTINEL" not in caplog.text
    assert "CREDENTIAL_SENTINEL" not in caplog.text
    assert "PROVIDER_ERROR_SENTINEL" not in caplog.text
    assert endpoint not in caplog.text


def test_deadline_caps_transport_timeout_and_error_is_sanitized(tmp_path: Path) -> None:
    credential = tmp_path / "credential"
    write_credential(credential)
    with provider_server([b"data: [DONE]\n\n"], delay=0.2) as (endpoint, seen):
        from app.hr_agent.model import ConfiguredHttpModelPort, ModelTransportError

        with pytest.raises(ModelTransportError) as caught:
            list(
                ConfiguredHttpModelPort(openai_profile(endpoint, credential)).stream(
                    request(deadline_seconds=0.03)
                )
            )
    assert caught.value.code == "transport_error"
    assert "CREDENTIAL_SENTINEL" not in str(caught.value)
    assert endpoint not in str(caught.value)
    assert len(seen) == 1


def test_ordinary_log_is_whitelisted_and_never_formats_exception(caplog) -> None:
    from app.hr_agent.observability import emit_log
    from app.hr_agent.types import HrAgentProblem

    caplog.set_level(logging.INFO, logger="app.hr_agent")
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": "model_request",
        "work_id": None,
        "attempt_id": None,
        "operation_id": None,
        "state": "failed",
        "duration_ms": 1,
        "input_tokens": None,
        "output_tokens": None,
        "error_code": "transport_error",
        "profile_revision": "profile-r1",
    }
    emit_log(record)
    assert "model_request" in caplog.text
    assert "transport_error" in caplog.text
    assert "PROMPT_SENTINEL" not in caplog.text
    with pytest.raises(HrAgentProblem):
        emit_log({**record, "event": "arbitrary PROMPT_SENTINEL"})
    with pytest.raises(HrAgentProblem):
        emit_log({**record, "message": "PROMPT_SENTINEL"})


def fence(owner_id: UUID | None = None, work_id: UUID | None = None):
    from app.hr_agent.types import LeaseFence

    # owner_id is intentionally server-side context supplied to WorkFiles.
    return owner_id or uuid4(), LeaseFence(work_id or uuid4(), 1, 1, "worker-test")


def test_work_files_are_private_and_cannot_cross_scope_or_follow_symlink(
    tmp_path: Path,
) -> None:
    from app.hr_agent.work_files import WorkFileError, WorkFiles

    files = WorkFiles(tmp_path / "work", ttl_seconds=1)
    owner_a, fence_a = fence()
    owner_b, fence_b = fence()
    files_a = files.for_owner(owner_a)
    file_id = uuid4()
    with files_a.open(fence_a, file_id, "xb") as stream:
        stream.write(b"private")
    path = files_a.path(fence_a, file_id)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with files.for_owner(owner_b).open(fence_b, file_id, "xb") as stream:
        stream.write(b"other")
    with pytest.raises(WorkFileError):
        files.for_owner(owner_b).open(fence_a, file_id, "rb")
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    with pytest.raises(WorkFileError):
        files_a.open(fence_a, file_id, "rb")


def test_work_files_exact_open_api_uses_server_bound_owner(tmp_path: Path) -> None:
    from app.hr_agent.work_files import WorkFiles

    owner, lease = fence()
    files = WorkFiles(tmp_path / "work", owner_id=owner, ttl_seconds=10)
    with files.open(lease, uuid4(), "xb") as stream:
        stream.write(b"bounded")


def test_work_file_cleanup_only_removes_expired_inactive_attempts(
    tmp_path: Path,
) -> None:
    from app.hr_agent.work_files import WorkFiles

    files = WorkFiles(tmp_path / "work", ttl_seconds=1)
    owner, old_fence = fence()
    scoped = files.for_owner(owner)
    with scoped.open(old_fence, uuid4(), "xb") as stream:
        stream.write(b"old")
    attempt_dir = scoped.attempt_dir(old_fence)
    old = time.time() - 10
    for path in [attempt_dir, attempt_dir.parent, attempt_dir.parent.parent]:
        path.touch()
    attempt_dir.touch()
    import os

    os.utime(attempt_dir, (old, old))
    assert files.cleanup(active={(owner, old_fence.work_id, old_fence.epoch)}) == 0
    assert files.cleanup(active=set(), now=time.time()) == 1
    assert not attempt_dir.exists()


class JsonCodec:
    def seal_json(self, subject: str, value: dict[str, object]):
        return subject, json.dumps(value).encode()

    def unseal_json(self, subject: str, sealed):
        assert sealed[0] == subject
        return json.loads(sealed[1])


def test_diagnostics_disabled_untrusted_expired_and_deleted_are_unreadable(
    tmp_path: Path,
) -> None:
    from app.hr_agent.diagnostics import DiagnosticAccessError, DiagnosticStore
    from app.hr_agent.types import DiagnosticIdentity

    now = datetime.now(timezone.utc)
    trusted = DiagnosticIdentity(uuid4(), ("hr_diagnostics",))
    untrusted = DiagnosticIdentity(uuid4(), ("hr_user",))
    disabled = DiagnosticStore(
        tmp_path / "disabled",
        JsonCodec(),
        enabled=False,
        trusted_roles=("hr_diagnostics",),
    )
    with pytest.raises(DiagnosticAccessError):
        disabled.create(
            trusted, uuid4(), {"body": "DIAGNOSTIC_SENTINEL"}, ttl_seconds=10, now=now
        )

    store = DiagnosticStore(
        tmp_path / "enabled",
        JsonCodec(),
        enabled=True,
        trusted_roles=("hr_diagnostics",),
        max_ttl_seconds=60,
    )
    record = store.create(
        trusted, uuid4(), {"body": "DIAGNOSTIC_SENTINEL"}, ttl_seconds=1, now=now
    )
    with pytest.raises(DiagnosticAccessError):
        store.read(untrusted, record.diagnostic_id, now=now)
    restarted = DiagnosticStore(
        tmp_path / "enabled",
        JsonCodec(),
        enabled=True,
        trusted_roles=("hr_diagnostics",),
        max_ttl_seconds=60,
    )
    assert (
        restarted.read(trusted, record.diagnostic_id, now=now).sealed_payload["body"]
        == "DIAGNOSTIC_SENTINEL"
    )
    with pytest.raises(DiagnosticAccessError):
        store.read(trusted, record.diagnostic_id, now=now + timedelta(seconds=2))
    store.delete(trusted, record.diagnostic_id)
    with pytest.raises(DiagnosticAccessError):
        store.read(trusted, record.diagnostic_id, now=now)
