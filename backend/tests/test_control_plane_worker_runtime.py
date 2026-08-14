from __future__ import annotations

import asyncio
import os
from pathlib import Path

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
