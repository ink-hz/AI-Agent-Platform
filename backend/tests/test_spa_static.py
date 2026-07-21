import json

from fastapi.testclient import TestClient

from app.main import create_app


def test_frontend_deep_links_fall_back_to_spa_index(tmp_path, monkeypatch) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>Platform SPA</main>", encoding="utf-8")
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"bots": []}), encoding="utf-8")
    monkeypatch.setenv("PLATFORM_STATIC_DIR", str(static_dir))

    client = TestClient(create_app(
        registry_path=str(registry), cluster_contract_path=str(contract), start_poller=False,
    ))

    for path in ("/agents", "/sessions/fae%3Aone", "/flywheel"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Platform SPA" in response.text


def test_missing_static_asset_still_returns_404(tmp_path, monkeypatch) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>Platform SPA</main>", encoding="utf-8")
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"bots": []}), encoding="utf-8")
    monkeypatch.setenv("PLATFORM_STATIC_DIR", str(static_dir))
    client = TestClient(create_app(
        registry_path=str(registry), cluster_contract_path=str(contract), start_poller=False,
    ))

    assert client.get("/missing.js").status_code == 404
