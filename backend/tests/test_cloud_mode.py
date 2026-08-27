from pathlib import Path

from fastapi.testclient import TestClient

from app.config import is_cloud_mode, load_config
from app.fleet.catalog import AgentCatalog
from app.main import build_cloud_replica_services, create_app


def _private_file(path: Path) -> Path:
    path.write_text("test-secret", encoding="utf-8")
    path.chmod(0o600)
    return path


def _configure_cloud(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PLATFORM_DEPLOYMENT_MODE", "cloud-replica")
    monkeypatch.setenv("PLATFORM_HOST", "127.0.0.1")
    monkeypatch.setenv("PLATFORM_PORT", "8080")
    monkeypatch.setenv("PLATFORM_FLYWHEEL_ENABLED", "0")
    monkeypatch.setenv("PLATFORM_REVIEW_ENABLED", "0")
    monkeypatch.setenv("PLATFORM_ATTACHMENT_ENABLED", "0")
    monkeypatch.setenv(
        "PLATFORM_REPLICA_DATABASE_URL_FILE",
        str(_private_file(tmp_path / "database-url")),
    )
    monkeypatch.setenv(
        "PLATFORM_REPLICA_ENCRYPTION_KEY_FILE",
        str(_private_file(tmp_path / "encryption-key")),
    )
    monkeypatch.setenv(
        "PLATFORM_REPLICA_SIGNING_PUBLIC_KEY_FILE",
        str(_private_file(tmp_path / "signing-public-key")),
    )
    monkeypatch.setenv("PLATFORM_REPLICA_STALE_SECONDS", "900")


def test_is_cloud_mode(monkeypatch, tmp_path):
    _configure_cloud(monkeypatch, tmp_path)

    assert is_cloud_mode(load_config()) is True


def test_cloud_mode_starts_without_local_pollers_or_mutating_services(
    monkeypatch, tmp_path
):
    _configure_cloud(monkeypatch, tmp_path)
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cloud mode touched a local or remote source")

    monkeypatch.setattr("app.main.resolve_flywheel_database_url", forbidden)
    monkeypatch.setattr("app.main.resolve_review_database_url", forbidden)
    monkeypatch.setattr("app.main.build_operations", forbidden)
    monkeypatch.setattr("app.main.build_attachment_service", forbidden)
    monkeypatch.setattr("app.main.poll_loop", forbidden)
    monkeypatch.setattr("app.main.cluster_poll_loop", forbidden)
    monkeypatch.setattr("app.main.remote_poll_loop", forbidden)
    monkeypatch.setattr("app.main.operations_poll_loop", forbidden)
    monkeypatch.setattr(
        "app.main.build_cloud_replica_services",
        lambda *_args: (object(), object(), object()),
    )

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=True,
    )

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/review/overview").status_code == 503
        assert client.post("/api/attachments/missing/ticket").status_code == 404
        assert client.get("/api/attachments/missing/content").status_code == 404

    assert app.state.operations_service is None
    assert app.state.operations_scheduler is None
    assert app.state.attachment_service is None


def test_cloud_fleet_enables_catalog_roster_completion(monkeypatch, tmp_path):
    _configure_cloud(monkeypatch, tmp_path)
    captured = {}
    fleet_service = object()

    class ReplicaRepository:
        def check_schema(self):
            return None

        def usage_leaders(self, _date_from, _date_to, _visibility):
            return ()

    repository = ReplicaRepository()
    monkeypatch.setattr("app.main.read_secret_file", lambda _path: "database-url")
    monkeypatch.setattr("app.main.read_key_file", lambda *_args, **_kwargs: b"0" * 32)
    monkeypatch.setattr(
        "app.main.ReplicaObservabilityRepository",
        lambda *_args, **_kwargs: repository,
    )
    monkeypatch.setattr(
        "app.main.ReplicaReviewRepository",
        lambda *_args, **_kwargs: object(),
    )
    operations_kwargs = {}

    def build_operations_repository(*_args, **kwargs):
        operations_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(
        "app.main.ReplicaOperationsRepository", build_operations_repository,
    )
    monkeypatch.setattr(
        "app.main.ReplicaFlywheelRepository",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.main.UsageCache",
        lambda *_args, **_kwargs: object(),
    )

    def capture_fleet_service(*_args, **kwargs):
        captured.update(kwargs)
        return fleet_service

    monkeypatch.setattr("app.main.FleetReadService", capture_fleet_service)

    built_fleet, _observability, built_repository = build_cloud_replica_services(
        load_config(),
        object(),
        object(),
        AgentCatalog.default(),
    )

    assert built_fleet is fleet_service
    assert built_repository is repository
    assert captured["include_catalog_agents"] is True
    assert operations_kwargs["usage_reader"] == repository.usage_leaders
