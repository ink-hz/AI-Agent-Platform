import json
import os

from fastapi.testclient import TestClient
import pytest

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


def test_build_hashed_asset_name_is_exact_and_rejects_maps_and_traversal() -> None:
    from app.spa import is_public_build_asset

    assert is_public_build_asset("app-a1b2c3d4.js")
    assert is_public_build_asset("style-ABCDEF12.css")
    for name in (
        "app.js",
        "app-a1b2c3d4.js.map",
        "../index.html",
        "%2e%2e/index.html",
        "nested/app-a1b2c3d4.js",
        "app-a1b2c3d4.svg",
    ):
        assert not is_public_build_asset(name)


def test_public_manifest_must_be_a_regular_non_symlink_file(tmp_path) -> None:
    from app.spa import load_public_asset_manifest

    static = tmp_path / "static"
    manifest_dir = static / ".vite"
    manifest_dir.mkdir(parents=True)
    outside = tmp_path / "outside-manifest.json"
    outside.write_text(
        json.dumps({"index.html": {"file": "assets/app-a1b2c3d4.js"}}),
        encoding="utf-8",
    )
    (manifest_dir / "manifest.json").symlink_to(outside)

    assert load_public_asset_manifest(str(static)) == frozenset()


def test_public_asset_open_rejects_asset_and_intermediate_symlinks(tmp_path) -> None:
    from app.spa import PublicAssetUnavailable, open_public_build_asset

    static = tmp_path / "static"
    assets = static / "assets"
    assets.mkdir(parents=True)
    outside = tmp_path / "outside.js"
    outside.write_text("TOP SECRET", encoding="utf-8")
    (assets / "app-a1b2c3d4.js").symlink_to(outside)

    with pytest.raises(PublicAssetUnavailable):
        open_public_build_asset(str(static), "app-a1b2c3d4.js")

    (assets / "app-a1b2c3d4.js").unlink()
    assets.rmdir()
    (tmp_path / "outside-deadbeef.js").write_text(
        "TOP SECRET", encoding="utf-8"
    )
    assets.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PublicAssetUnavailable):
        open_public_build_asset(str(static), "outside-deadbeef.js")


def test_public_asset_response_is_bound_to_opened_regular_inode(tmp_path) -> None:
    from app.spa import open_public_build_asset

    static = tmp_path / "static"
    assets = static / "assets"
    assets.mkdir(parents=True)
    target = assets / "app-a1b2c3d4.js"
    target.write_bytes(b"trusted build")
    outside = tmp_path / "outside.js"
    outside.write_bytes(b"TOP SECRET")

    opened = open_public_build_asset(str(static), target.name)
    os.replace(target, assets / "original-a1b2c3d4.js")
    target.symlink_to(outside)
    try:
        assert opened.file.read() == b"trusted build"
        assert opened.size == len(b"trusted build")
    finally:
        opened.file.close()
