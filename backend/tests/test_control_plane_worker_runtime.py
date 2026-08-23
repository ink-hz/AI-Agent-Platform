from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID

import pytest


def _private_file(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_worker_runtime_reads_credentials_only_from_private_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.control_plane.worker_runtime import load_worker_settings

    values = {
        "PLATFORM_CONTROL_DIRECTORY_DATABASE_URL_FILE": (
            "postgresql://platform_directory_worker:secret@db/agent_platform_control"
        ),
        "PLATFORM_CONTROL_STREAM_DATABASE_URL_FILE": (
            "postgresql://platform_stream_ingest:secret@db/agent_platform_control"
        ),
        "PLATFORM_DINGTALK_APP_KEY_FILE": "app-key",
        "PLATFORM_DINGTALK_AGENT_ID_FILE": "123456",
        "PLATFORM_DINGTALK_CORP_ID_FILE": "corp-id",
        "PLATFORM_DINGTALK_APP_SECRET_FILE": "app-secret",
    }
    for name, value in values.items():
        file_path = _private_file(tmp_path / name.lower(), value)
        monkeypatch.setenv(name, str(file_path))
    monkeypatch.setenv(
        "PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE",
        str(_private_file(tmp_path / "encryption.json", "{}")),
    )
    monkeypatch.setenv(
        "PLATFORM_IDENTITY_HMAC_KEYRING_FILE",
        str(_private_file(tmp_path / "lookup.json", "{}")),
    )

    settings = load_worker_settings()

    assert settings.app_key == "app-key"
    assert settings.agent_id == 123456
    assert settings.corp_id == "corp-id"
    assert settings.app_secret == "app-secret"
    assert settings.directory_database_url.endswith("/agent_platform_control")
    assert settings.stream_database_url.endswith("/agent_platform_control")
    rendered = repr(settings)
    for secret in ("app-secret", "platform_directory_worker", "platform_stream_ingest"):
        assert secret not in rendered


def test_worker_runtime_rejects_inline_secret_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.control_plane.worker_runtime import load_worker_settings

    monkeypatch.setenv("PLATFORM_DINGTALK_APP_SECRET", "inline-secret")
    with pytest.raises(ValueError, match="secret files"):
        load_worker_settings()


def test_worker_runtime_dispatches_only_known_services(monkeypatch: pytest.MonkeyPatch):
    from app.control_plane import worker_runtime

    called: list[str] = []

    async def fake_directory() -> None:
        called.append("directory")

    async def fake_stream() -> None:
        called.append("stream")

    monkeypatch.setattr(worker_runtime, "serve_directory", fake_directory)
    monkeypatch.setattr(worker_runtime, "serve_stream", fake_stream)

    assert worker_runtime.main(["directory"]) == 0
    assert worker_runtime.main(["stream"]) == 0
    assert called == ["directory", "stream"]
    with pytest.raises(SystemExit):
        worker_runtime.main(["unknown"])


def test_directory_service_cancels_peer_and_closes_provider(monkeypatch: pytest.MonkeyPatch):
    from app.control_plane import worker_runtime

    events: list[str] = []

    class Provider:
        async def aclose(self):
            events.append("closed")

    class Service:
        def __init__(self, name: str, fail: bool = False):
            self.name = name
            self.fail = fail

        async def serve(self):
            events.append(self.name)
            if self.fail:
                raise RuntimeError("failed")
            try:
                await asyncio.Event().wait()
            finally:
                events.append(f"{self.name}-cancelled")

    monkeypatch.setattr(
        worker_runtime,
        "build_directory_services",
        lambda: (Provider(), Service("schedule", fail=True), Service("events")),
    )

    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(worker_runtime.serve_directory())
    assert "events-cancelled" in events
    assert events[-1] == "closed"


def test_directory_repository_stages_schema_v2_member_gender() -> None:
    from app.control_plane.crypto import ProtectedProviderId
    from app.control_plane.directory import StagedMember
    from app.control_plane.directory_worker import DirectoryWorkerRepository

    corporate = ProtectedProviderId("employee", b"c" * 32, 1, b"c" * 29, 1)
    union = ProtectedProviderId("employee_union", b"u" * 32, 1, b"u" * 29, 1)
    row = StagedMember(
        UUID("10000000-0000-4000-8000-000000000001"),
        corporate,
        union,
        "Alice",
        "active",
        "female",
    )
    repository = DirectoryWorkerRepository(
        "postgresql://platform_directory_worker@127.0.0.1/agent_platform_control"
    )
    captured = []
    repository._batch = lambda query, parameters, **kwargs: captured.append(
        (query, tuple(parameters))
    )

    repository.stage_members(
        UUID("20000000-0000-4000-8000-000000000001"), (row,)
    )

    query, parameters = captured[0]
    assert query == (
        "select platform_control.stage_directory_member_v34("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    )
    assert parameters[0][-1] == "female"


def test_directory_repository_creates_schema_v2_generation() -> None:
    from app.control_plane.directory_worker import DirectoryWorkerRepository

    repository = DirectoryWorkerRepository(
        "postgresql://platform_directory_worker@127.0.0.1/agent_platform_control"
    )
    captured = []
    repository._call = lambda query, parameters, **kwargs: captured.append(
        (query, parameters)
    )

    repository.create_staging_generation(
        UUID("30000000-0000-4000-8000-000000000001"),
        UUID("40000000-0000-4000-8000-000000000001"),
        "scheduled",
        1,
        1,
        1,
        1,
        2,
        "a" * 64,
    )

    query, parameters = captured[0]
    assert query.startswith(
        "select platform_control.create_directory_staging_generation_v34("
    )
    assert parameters[7] == 2
