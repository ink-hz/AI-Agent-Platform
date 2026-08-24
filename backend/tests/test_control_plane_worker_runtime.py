from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest


def _private_file(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def _install_worker_secret_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    values = {
        "PLATFORM_CONTROL_DIRECTORY_DATABASE_URL_FILE": (
            "postgresql://platform_directory_worker:secret@db/agent_platform_control"
        ),
        "PLATFORM_CONTROL_STREAM_DATABASE_URL_FILE": (
            "postgresql://platform_stream_ingest:secret@db/agent_platform_control"
        ),
        "PLATFORM_DINGTALK_APP_KEY_FILE": "app-key",
        "PLATFORM_DINGTALK_AGENT_ID_FILE": "12345",
        "PLATFORM_DINGTALK_CORP_ID_FILE": "corp-id",
        "PLATFORM_DINGTALK_APP_SECRET_FILE": "app-secret",
        "PLATFORM_DINGTALK_HRM_REAL_NAME_FIELD_CODE_FILE": "private-real-name-code",
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
    return values


def test_worker_runtime_reads_credentials_only_from_private_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.control_plane.worker_runtime import load_worker_settings

    _install_worker_secret_files(tmp_path, monkeypatch)

    settings = load_worker_settings()

    assert settings.app_key == "app-key"
    assert settings.corp_id == "corp-id"
    assert settings.app_secret == "app-secret"
    assert settings.agent_id == 12345
    assert settings.hrm_real_name_field_code == "private-real-name-code"
    assert settings.directory_database_url.endswith("/agent_platform_control")
    assert settings.stream_database_url.endswith("/agent_platform_control")
    rendered = repr(settings)
    for secret in (
        "app-secret",
        "platform_directory_worker",
        "platform_stream_ingest",
        "private-real-name-code",
    ):
        assert secret not in rendered


@pytest.mark.parametrize("failure", ["missing", "empty", "unavailable"])
def test_directory_worker_fails_closed_without_valid_hrm_field_code_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from app.control_plane.worker_runtime import load_worker_settings
    from app.local_secrets import SecretFileUnavailable

    _install_worker_secret_files(tmp_path, monkeypatch)
    environment_name = "PLATFORM_DINGTALK_HRM_REAL_NAME_FIELD_CODE_FILE"
    sensitive_value = "private-real-name-code"
    if failure == "missing":
        monkeypatch.delenv(environment_name)
        expected_error = ValueError
    elif failure == "empty":
        monkeypatch.setenv(
            environment_name,
            str(_private_file(tmp_path / "empty-hrm-code", " \n")),
        )
        expected_error = SecretFileUnavailable
    else:
        monkeypatch.setenv(environment_name, str(tmp_path / sensitive_value))
        expected_error = SecretFileUnavailable

    with pytest.raises(expected_error) as caught:
        load_worker_settings("directory")

    assert sensitive_value not in str(caught.value)


def test_stream_worker_does_not_receive_hrm_profile_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.control_plane.worker_runtime import load_worker_settings

    _install_worker_secret_files(tmp_path, monkeypatch)
    monkeypatch.delenv("PLATFORM_DINGTALK_AGENT_ID_FILE")
    monkeypatch.delenv("PLATFORM_DINGTALK_HRM_REAL_NAME_FIELD_CODE_FILE")

    settings = load_worker_settings("stream")

    assert settings.agent_id is None
    assert settings.hrm_real_name_field_code is None


def test_directory_worker_injects_hrm_configuration_only_into_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.control_plane import worker_runtime

    settings = worker_runtime.WorkerSettings(
        app_key="app-key",
        corp_id="corp-id",
        app_secret="app-secret",
        encryption_keyring_file=tmp_path / "encryption.json",
        hmac_keyring_file=tmp_path / "lookup.json",
        agent_id=12345,
        hrm_real_name_field_code="private-real-name-code",
        directory_database_url="postgresql://directory",
    )
    captured: dict[str, object] = {}
    provider = object()

    class FakeKeyring:
        @staticmethod
        def from_file(*args, **kwargs):
            return object()

    def fake_provider(**kwargs):
        captured.update(kwargs)
        return provider

    monkeypatch.setattr(worker_runtime, "load_worker_settings", lambda service: settings)
    monkeypatch.setattr(worker_runtime, "_encryption_keyring", lambda value: object())
    monkeypatch.setattr(worker_runtime, "IdentityKeyring", FakeKeyring)
    monkeypatch.setattr(worker_runtime, "DingTalkClient", fake_provider)
    for name in (
        "ProviderIdentityCodec",
        "DirectoryWorkerRepository",
        "DirectoryReconciler",
        "DirectoryWorker",
        "DirectoryEventRepository",
        "StreamPayloadCipher",
        "TargetedMemberRefresher",
        "DirectoryEventWorker",
    ):
        monkeypatch.setattr(worker_runtime, name, lambda *args, **kwargs: object())

    built_provider, _, _ = worker_runtime.build_directory_services()

    assert built_provider is provider
    assert captured["agent_id"] == 12345
    assert captured["hrm_real_name_field_code"] == "private-real-name-code"


@pytest.mark.parametrize(
    "environment_name",
    [
        "PLATFORM_DINGTALK_APP_SECRET",
        "PLATFORM_DINGTALK_AGENT_ID",
        "PLATFORM_DINGTALK_HRM_REAL_NAME_FIELD_CODE",
    ],
)
def test_worker_runtime_rejects_inline_secret_environment(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
):
    from app.control_plane.worker_runtime import load_worker_settings

    monkeypatch.setenv(environment_name, "inline-secret")
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


def test_directory_repository_stages_schema_v3_member_profile() -> None:
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
    assert query.startswith("select platform_control.stage_directory_member_v39(")
    assert len(parameters[0]) == 22
    assert parameters[0][12] == "female"
    assert parameters[0][13:] == (None,) * 9


def test_directory_repository_creates_schema_v3_generation() -> None:
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
        3,
        "a" * 64,
        1,
        1,
        1,
    )

    query, parameters = captured[0]
    assert query.startswith(
        "select platform_control.create_directory_staging_generation_v39("
    )
    assert parameters[7] == 3
    assert parameters[9:] == (1, 1, 1)


def test_directory_repository_reads_only_aggregate_employee_profile_readiness() -> None:
    from app.control_plane.directory_worker import DirectoryWorkerRepository

    repository = DirectoryWorkerRepository(
        "postgresql://platform_directory_worker@127.0.0.1/agent_platform_control"
    )
    captured = []
    repository._call = lambda query, parameters, **kwargs: (
        captured.append((query, parameters))
        or {
            "generation_id": UUID("50000000-0000-4000-8000-000000000001"),
            "active_employee_count": 10,
            "real_name_present_count": 8,
            "mobile_present_count": 7,
            "primary_department_present_count": 9,
        }
    )

    readiness = repository.read_employee_profile_readiness()

    assert readiness.ready is True
    assert readiness.as_public_dict() == {
        "generation_id": "50000000-0000-4000-8000-000000000001",
        "active_employee_count": 10,
        "real_name_present_count": 8,
        "mobile_present_count": 7,
        "primary_department_present_count": 9,
    }
    query, parameters = captured[0]
    assert "read_employee_profile_readiness_v39" in query
    assert parameters == ()
    for forbidden in ("display_name", "ciphertext", "nonce", "lookup_hmac"):
        assert forbidden not in query


def test_employee_profile_probe_outputs_only_privacy_safe_aggregates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.control_plane import employee_profile_probe
    from app.control_plane.directory_worker import EmployeeProfileReadiness

    class Settings:
        directory_database_url = "postgresql://private-database-url"

    class Repository:
        def __init__(self, database_url):
            assert database_url == Settings.directory_database_url

        def read_employee_profile_readiness(self):
            return EmployeeProfileReadiness(
                UUID("50000000-0000-4000-8000-000000000001"),
                10,
                8,
                7,
                9,
            )

    monkeypatch.setattr(
        employee_profile_probe,
        "load_worker_settings",
        lambda service: Settings(),
    )
    monkeypatch.setattr(
        employee_profile_probe,
        "DirectoryWorkerRepository",
        Repository,
    )

    assert employee_profile_probe.main() == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "private-database-url" not in captured.out
    assert set(json.loads(captured.out)) == {
        "generation_id",
        "active_employee_count",
        "real_name_present_count",
        "mobile_present_count",
        "primary_department_present_count",
    }
