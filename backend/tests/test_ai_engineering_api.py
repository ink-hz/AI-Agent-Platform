from __future__ import annotations

import json
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.control_plane.models import AuthContext, Role
from test_dingtalk_auth_api import FakeAuth, _app

PREFIX = '/api/v1/ai-engineering'
CONTENT = [
    '', '/panorama', '/export.svg', '/export.png',
    '/documents/overview', '/assets/panorama.svg', '/assets/panorama.png',
]


def client_for(tmp_path, monkeypatch, role=Role.PLATFORM_ADMIN):
    auth = FakeAuth()
    auth.context = AuthContext(uuid4(), role, uuid4(), False)
    client = TestClient(_app(tmp_path, monkeypatch, auth), base_url='https://agent.example.test')
    return client, auth


def assert_private(response):
    assert 'no-store' in response.headers['cache-control']
    assert 'private' in response.headers['cache-control']


@pytest.mark.parametrize('role', list(Role))
def test_existing_platform_management_roles(tmp_path, monkeypatch, role):
    client, auth = client_for(tmp_path, monkeypatch, role)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    permitted = role in {Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER}
    assert client.get(PREFIX + '/access').json() == {'allowed': permitted}
    for suffix in CONTENT:
        response = client.get(PREFIX + suffix, headers={'X-Role': 'platform_admin'})
        assert response.status_code == (200 if permitted else 403)
        assert_private(response)


@pytest.mark.parametrize('role', [Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER])
def test_role_revocation_with_same_session(tmp_path, monkeypatch, role):
    client, auth = client_for(tmp_path, monkeypatch, role)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    assert client.get(PREFIX).status_code == 200
    auth.context = AuthContext(auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False)
    assert client.get(PREFIX + '/access').json() == {'allowed': False}
    for suffix in CONTENT:
        assert client.get(PREFIX + suffix).status_code == 403
    # Shared identity remains available to independent /office/, /hr/ apps.
    assert client.get('/api/v1/account').status_code == 200
    assert client.get('/hr/').status_code == 200
    from app.control_plane.auth import validate_return_path
    for path in ['/office/', '/hr/', '/voc/', '/fae/']:
        assert validate_return_path(path, route_prefix='/') == path


def test_session_required_even_for_admin(tmp_path, monkeypatch):
    client, auth = client_for(tmp_path, monkeypatch)
    for suffix in ['/access', *CONTENT]:
        response = client.get(PREFIX + suffix)
        assert response.status_code == 401
        assert_private(response)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    auth.revoked = True
    assert client.get(PREFIX + '/documents/overview').status_code == 401


def test_fixed_content_paths_and_sources(tmp_path, monkeypatch):
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    manifest = client.get(PREFIX).json()
    assert len(manifest['documents']) == 6
    for doc in manifest['documents']:
        payload = client.get(PREFIX + '/documents/' + doc['slug']).json()
        assert payload['markdown']
        assert payload['slug'] == doc['slug']
    for path in ['/documents/unknown', '/assets/source-manifest.json', '/assets/%2e%2e%2fsource-manifest.json', '/documents/%2fetc%2fpasswd']:
        response = client.get(PREFIX + path)
        assert response.status_code in {403, 404}
        assert 'LOGIN SHELL' not in response.text
        assert_private(response)
    assert client.get('/ai-engineering').status_code == 200
    assert client.get('/assets/panorama.svg').status_code != 200


def test_panorama_contract_uses_four_layers_and_fixed_ids(tmp_path, monkeypatch):
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, "valid-cookie")

    response = client.get(PREFIX + "/panorama")
    assert response.status_code == 200
    assert_private(response)
    payload = response.json()
    assert set(payload) == {
        "version",
        "updated_at",
        "title",
        "layers",
        "nodes",
        "edges",
        "sources",
    }
    assert [layer["kind"] for layer in payload["layers"]] == [
        "industry",
        "portfolio",
        "workflow",
        "support",
    ]
    assert {"robotics", "camera", "sdk-tech", "talent", "digital"} <= {
        node["id"] for node in payload["nodes"]
    }
    source_ids = {source["id"] for source in payload["sources"]}
    assert source_ids
    referenced = {
        source_id for node in payload["nodes"] for source_id in node["source_ids"]
    }
    assert referenced <= source_ids
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in (
        "internal_user_id",
        "session_id",
        "workspace",
        "employee",
        "手机号",
        "员工姓名",
    ):
        assert forbidden not in serialized


def test_panorama_exports_are_same_version_private_and_1920_by_1080(
    tmp_path, monkeypatch
):
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, "valid-cookie")
    data_response = client.get(PREFIX + "/panorama")
    svg_response = client.get(PREFIX + "/export.svg")
    png_response = client.get(PREFIX + "/export.png")

    for response in (svg_response, png_response):
        assert response.status_code == 200
        assert_private(response)
        assert (
            response.headers["x-panorama-content-sha256"]
            == data_response.headers["x-panorama-content-sha256"]
        )
        assert response.headers["content-disposition"].startswith(
            "attachment; filename="
        )
    assert svg_response.headers["content-type"].startswith("image/svg+xml")
    svg = svg_response.text
    assert "<svg" in svg and 'width="1920"' in svg and 'height="1080"' in svg
    assert data_response.json()["version"] in svg
    for layer in data_response.json()["layers"]:
        assert layer["title"] in svg
    for node in data_response.json()["nodes"]:
        assert node["title"] in svg
    with Image.open(BytesIO(png_response.content)) as image:
        assert image.size == (1920, 1080)
        assert image.format == "PNG"


@pytest.mark.parametrize('suffix', ['/export.svg', '/export.png'])
def test_export_rejects_requested_content_version_mismatch(
    tmp_path, monkeypatch, suffix
):
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, "valid-cookie")

    version = client.get(PREFIX + "/panorama").json()["version"]
    assert client.get(PREFIX + suffix + "?version=" + version).status_code == 200
    response = client.get(PREFIX + suffix + "?version=stale-version")
    assert response.status_code == 409
    assert response.json()["detail"] == "panorama version mismatch"
    assert_private(response)


def test_exports_contain_no_old_financial_main_content():
    from app.ai_engineering.panorama import render_svg

    svg = render_svg().decode()
    assert "营业收入" not in svg
    assert "归母净利润" not in svg


def test_cloud_runtime_installs_cjk_font_for_png_export():
    from pathlib import Path

    dockerfile = (Path(__file__).parents[2] / 'deploy/cloud/Dockerfile').read_text()
    assert 'fonts-noto-cjk' in dockerfile


@pytest.mark.parametrize('prefix', ['/', '/_preview/dingtalk-r1/'])
def test_login_return_paths(prefix):
    from app.control_plane.auth import validate_return_path
    base = prefix.rstrip('/')
    for path in ['/brain', '/ai-engineering', '/ai-engineering?document=domains']:
        assert validate_return_path(base + path, route_prefix=prefix) == base + path.split('?')[0]
    for query in ['document=unknown', 'document=domains&document=reading', 'document=domains&x=1', 'document=%64omains']:
        with pytest.raises(ValueError):
            validate_return_path(base + '/ai-engineering?' + query, route_prefix=prefix)


def test_prefixed_content_uses_same_gate(tmp_path, monkeypatch):
    from app.control_plane.models import IdentityMode
    prefix = '/_preview/dingtalk-r1/'
    auth = FakeAuth(mode=IdentityMode.PREVIEW, prefix=prefix)
    client = TestClient(_app(tmp_path, monkeypatch, auth), base_url='https://agent.example.test')
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    assert client.get(prefix.rstrip('/') + PREFIX + '/documents/overview').status_code == 200
    auth.context = AuthContext(auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False)
    response = client.get(prefix.rstrip('/') + PREFIX + '/assets/panorama.svg')
    assert response.status_code == 403
    assert_private(response)


def test_content_snapshot_hashes():
    import hashlib
    from pathlib import Path
    content = Path(__file__).parents[1] / 'app/ai_engineering/content'
    manifest = json.loads((content / 'source-manifest.json').read_text())
    assert manifest['commit'] == '69a9de2168de6fa42b14d586587169487797e6f3'
    for filename, metadata in manifest['files'].items():
        assert hashlib.sha256((content / filename).read_bytes()).hexdigest() == metadata['sha256']


def test_editor_api_is_durable_and_publish_controls_public_version(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "panorama" / "panorama.sqlite3"
    monkeypatch.setenv("PLATFORM_PANORAMA_STATE_PATH", str(state_path))
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, "valid-cookie")
    client.cookies.set(auth.csrf_cookie_name, auth.csrf)
    headers = {"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf}
    public = client.get(PREFIX + "/panorama").json()
    state = client.get(PREFIX + "/panorama/draft").json()
    edited = json.loads(json.dumps(public))
    edited["title"] = "共享草稿"
    saved = client.put(
        PREFIX + "/panorama/draft",
        json={
            "expected_revision": state["revision"],
            "data": edited,
        },
        headers=headers,
    )
    assert saved.status_code == 200
    assert client.get(PREFIX + "/panorama").json()["title"] == public["title"]
    conflict = client.put(
        PREFIX + "/panorama/draft",
        json={
            "expected_revision": state["revision"],
            "data": edited,
        },
        headers=headers,
    )
    assert conflict.status_code == 409
    published = client.post(
        PREFIX + "/panorama/publish",
        json={
            "expected_revision": saved.json()["revision"],
        },
        headers=headers,
    )
    assert published.status_code == 200
    assert client.get(PREFIX + "/panorama").json()["title"] == "共享草稿"
    assert state_path.is_file()



def test_editor_mutations_require_manager_csrf_and_fresh_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PLATFORM_PANORAMA_STATE_PATH", str(tmp_path / "state.sqlite3"))
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, "valid-cookie")
    state = client.get(PREFIX + "/panorama/draft").json()
    assert (
        client.request(
            "DELETE",
            PREFIX + "/panorama/draft",
            json={"expected_revision": state["revision"]},
        ).status_code
        == 403
    )
    client.cookies.set(auth.csrf_cookie_name, auth.csrf)
    headers = {"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf}
    auth.context = AuthContext(
        auth.context.internal_user_id,
        Role.PLATFORM_ADMIN,
        auth.context.session_id,
        True,
    )
    assert (
        client.request(
            "DELETE",
            PREFIX + "/panorama/draft",
            json={"expected_revision": state["revision"]},
            headers=headers,
        ).status_code
        == 503
    )
    auth.context = AuthContext(
        auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False
    )
    assert client.get(PREFIX + "/panorama/draft").status_code == 403


@pytest.mark.parametrize("mutation", [
    lambda p: p["edges"][0].__setitem__("from", []),
    lambda p: p["edges"][0].__setitem__("to", {}),
    lambda p: p["sources"][0].__setitem__("document", []),
    lambda p: p["layers"][0].__setitem__("kind", {}),
    lambda p: p["layers"][0]["groups"][0].__setitem__("role", []),
    lambda p: p["nodes"][0].__setitem__("actions", [{}]),
    lambda p: p["nodes"][0].__setitem__("source_ids", [[]]),
])
def test_editor_api_returns_422_for_malformed_nested_values(tmp_path, monkeypatch, mutation):
    monkeypatch.setenv('PLATFORM_PANORAMA_STATE_PATH', str(tmp_path / 'state.sqlite3'))
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    client.cookies.set(auth.csrf_cookie_name, auth.csrf)
    headers = {'Origin': auth.public_base_url, 'X-CSRF-Token': auth.csrf}
    state = client.get(PREFIX + '/panorama/draft').json()
    data = json.loads(json.dumps(state['published']))
    mutation(data)
    response = client.put(PREFIX + '/panorama/draft', json={
        'expected_revision': state['revision'], 'data': data,
    }, headers=headers)
    assert response.status_code == 422


@pytest.mark.parametrize('prefix', ['/', '/_preview/dingtalk-r1/'])
def test_deep_link_shared_shell_reaches_login_without_private_content(tmp_path, monkeypatch, prefix):
    from app.control_plane.models import IdentityMode
    auth = FakeAuth(mode=IdentityMode.PREVIEW if prefix != '/' else IdentityMode.PRODUCTION, prefix=prefix)
    client = TestClient(_app(tmp_path, monkeypatch, auth), base_url='https://agent.example.test')
    for path in ['/brain', '/ai-engineering?document=domains']:
        response = client.get(prefix.rstrip('/') + path)
        assert response.status_code == 200
        assert 'LOGIN SHELL' in response.text
        assert 'no-store' in response.headers['cache-control']
    for suffix in CONTENT:
        assert client.get(prefix.rstrip('/') + PREFIX + suffix).status_code == 401


@pytest.mark.parametrize('role', list(Role))
@pytest.mark.parametrize('prefix', ['/', '/_preview/dingtalk-r1/'])
@pytest.mark.parametrize('path', ['', 'ai-engineering', 'ai-engineering?document=products'])
def test_home_shell_is_admin_only_with_contact_notice(tmp_path, monkeypatch, role, prefix, path):
    from app.control_plane.models import IdentityMode
    auth = FakeAuth(mode=IdentityMode.PREVIEW if prefix != '/' else IdentityMode.PRODUCTION, prefix=prefix)
    auth.context = AuthContext(uuid4(), role, uuid4(), False)
    client = TestClient(_app(tmp_path, monkeypatch, auth), base_url='https://agent.example.test')
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    response = client.get(prefix + path, headers={'X-Role': 'platform_admin'})
    if role in {Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER}:
        assert response.status_code == 200
        assert 'LOGIN SHELL' in response.text
    else:
        assert response.status_code == 403
        assert '无权限' in response.text and '请联系苍渊' in response.text
        assert 'LOGIN SHELL' not in response.text
        assert '<script' not in response.text
        assert_private(response)


def test_home_shell_rechecks_revoked_role_on_the_same_session(tmp_path, monkeypatch):
    client, auth = client_for(tmp_path, monkeypatch)
    client.cookies.set(auth.cookie_name, 'valid-cookie')
    assert client.get('/').status_code == 200
    auth.context = AuthContext(auth.context.internal_user_id, Role.MEMBER, auth.context.session_id, False)
    assert client.get('/').status_code == 403
    assert client.get('/ai-engineering').status_code == 403
    assert client.get('/hr/').status_code == 200
