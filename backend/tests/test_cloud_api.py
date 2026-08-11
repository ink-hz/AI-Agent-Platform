from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _private(path: Path) -> Path:
    path.write_text("test-secret", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_cloud_deployment_endpoint_and_mutating_routes_are_read_only(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PLATFORM_DEPLOYMENT_MODE", "cloud-replica")
    monkeypatch.setenv("PLATFORM_HOST", "127.0.0.1")
    monkeypatch.setenv("PLATFORM_PORT", "8080")
    monkeypatch.setenv("PLATFORM_FLYWHEEL_ENABLED", "0")
    monkeypatch.setenv("PLATFORM_REVIEW_ENABLED", "0")
    monkeypatch.setenv("PLATFORM_ATTACHMENT_ENABLED", "0")
    for name in (
        "PLATFORM_REPLICA_DATABASE_URL_FILE",
        "PLATFORM_REPLICA_ENCRYPTION_KEY_FILE",
        "PLATFORM_REPLICA_SIGNING_PUBLIC_KEY_FILE",
    ):
        monkeypatch.setenv(name, str(_private(tmp_path / name.lower())))
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text('{"bots": []}', encoding="utf-8")

    class EmptyObservability:
        async def list_agents(self): return []
        async def list_sessions(self, *_args):
            return {"items": [], "total": 0, "limit": 50, "offset": 0}

    monkeypatch.setattr(
        "app.main.build_cloud_replica_services",
        lambda *_args: (
            object(),
            EmptyObservability(),
            type("Repository", (), {"deployment_status": lambda self: {
                "mode": "cloud-replica", "read_only": True, "auth": "ssh-tunnel",
                "freshness": "unavailable", "last_success_at": None,
            }})(),
        ),
    )

    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=True,
    )
    client = TestClient(app)

    assert client.get("/api/deployment").json() == {
        "mode": "cloud-replica",
        "read_only": True,
        "auth": "ssh-tunnel",
        "freshness": "unavailable",
        "last_success_at": None,
    }
    assert client.get("/api/sessions").status_code == 200
    assert client.post("/api/attachments/x/ticket").status_code == 404
    assert client.post("/api/review/issues").status_code in {404, 422, 503}
