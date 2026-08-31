from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_ready(url: str, *, headers=None) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, headers=headers, timeout=0.2).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.05)
    raise AssertionError(f"subprocess did not become ready: {url}")


def test_real_uvicorn_proxy_boundary_preserves_peer_and_overwrites_headers(tmp_path):
    probe = tmp_path / "proxy_probe.py"
    probe.write_text(
        "from ipaddress import ip_network\n"
        "import os\n"
        "from starlette.requests import Request\n"
        "from starlette.responses import JSONResponse\n"
        "from app.control_plane.client_address import resolve_edge_source\n"
        "async def app(scope, receive, send):\n"
        "    request = Request(scope, receive)\n"
        "    edge = resolve_edge_source(request, (ip_network(os.environ['PROBE_TRUSTED']),))\n"
        "    await JSONResponse({'peer': request.client.host, 'edge': str(edge.ip), 'scheme': edge.scheme, 'xff': request.headers.get('x-forwarded-for'), 'authorization': request.headers.get('authorization')})(scope, receive, send)\n"
        ,
        encoding="utf-8",
    )
    raw_upstream_port = _free_port()
    proxy_upstream_port = _free_port()
    proxy_port = _free_port()
    while len({raw_upstream_port, proxy_upstream_port, proxy_port}) != 3:
        raw_upstream_port = _free_port()
        proxy_upstream_port = _free_port()
        proxy_port = _free_port()
    python_path = os.pathsep.join(
        [str(tmp_path), str(Path(__file__).parents[1]), os.environ.get("PYTHONPATH", "")]
    )
    common = {**os.environ, "PYTHONPATH": python_path}
    raw_upstream = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "proxy_probe:app", "--host",
            "127.0.0.1", "--port", str(raw_upstream_port), "--no-proxy-headers",
            "--no-access-log",
        ],
        env={**common, "PROBE_TRUSTED": "192.0.2.1/32"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    proxy_upstream = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "proxy_probe:app", "--host",
            "127.0.0.1", "--port", str(proxy_upstream_port), "--no-proxy-headers",
            "--no-access-log",
        ],
        env={**common, "PROBE_TRUSTED": "127.0.0.1/32"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    proxy_env = {
        **common,
        "PLATFORM_LOOPBACK_TARGET_BASE_URL": f"http://127.0.0.1:{proxy_upstream_port}",
        "PLATFORM_LOOPBACK_TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
    }
    office_bearer = tmp_path / "office-recipient-bearer"
    office_bearer.write_text("s" * 32, encoding="utf-8")
    office_bearer.chmod(0o600)
    proxy_env.update(
        {
            "PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED": "1",
            "PLATFORM_OFFICE_RECIPIENT_BEARER_FILE": str(office_bearer),
            "PLATFORM_OFFICE_RECIPIENT_LOCAL_PEER_CIDRS": "127.0.0.1/32",
        }
    )
    proxy = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "app.cloud_replica.loopback_proxy:create_app", "--factory", "--host",
            "127.0.0.1", "--port", str(proxy_port), "--no-proxy-headers",
            "--no-access-log",
        ],
        env=proxy_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_ready(f"http://127.0.0.1:{raw_upstream_port}/")
        _wait_ready(
            f"http://127.0.0.1:{proxy_upstream_port}/",
            headers={"X-Real-IP": "127.0.0.1", "X-Forwarded-Proto": "http"},
        )
        _wait_ready(f"http://127.0.0.1:{proxy_port}/")
        direct_via_proxy = httpx.get(
            f"http://127.0.0.1:{proxy_port}/",
            headers={"Authorization": "Bearer must-never-reach-upstream"},
        ).json()
        assert direct_via_proxy == {
            "peer": "127.0.0.1",
            "edge": "127.0.0.1",
            "scheme": "http",
            "xff": "127.0.0.1",
            "authorization": None,
        }
        spoofed = httpx.get(
            f"http://127.0.0.1:{raw_upstream_port}/",
            headers={
                "X-Real-IP": "203.0.113.90",
                "X-Forwarded-For": "203.0.113.90",
                "X-Forwarded-Proto": "https",
            },
        ).json()
        assert spoofed == {
            "peer": "127.0.0.1",
            "edge": "127.0.0.1",
            "scheme": "http",
            "xff": "203.0.113.90",
            "authorization": None,
        }

        proxied = httpx.get(
            f"http://127.0.0.1:{proxy_port}/",
            headers={
                "X-Real-IP": "203.0.113.91",
                "X-Forwarded-For": "198.51.100.1, 127.0.0.1",
                "X-Forwarded-Proto": "https",
                "Forwarded": "",
            },
        ).json()
        assert proxied == {
            "peer": "127.0.0.1",
            "edge": "203.0.113.91",
            "scheme": "https",
            "xff": "203.0.113.91",
            "authorization": None,
        }

        office_url = (
            f"http://127.0.0.1:{proxy_port}"
            "/api/v1/internal/office/recipient-directory/search"
        )
        correct = httpx.post(
            office_url,
            headers={"Authorization": f"Bearer {'s' * 32}"},
            json={"query": "苍渊"},
        )
        missing = httpx.post(office_url, json={"query": "苍渊"})
        wrong = httpx.post(
            office_url,
            headers={"Authorization": f"Bearer {'x' * 32}"},
            json={"query": "苍渊"},
        )

        assert correct.status_code == 200
        assert correct.json()["authorization"] == f"Bearer {'s' * 32}"
        assert missing.status_code == 404
        assert wrong.status_code == 404
    finally:
        proxy.terminate()
        raw_upstream.terminate()
        proxy_upstream.terminate()
        for process in (proxy, raw_upstream, proxy_upstream):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def office_topology_proxy(tmp_path, monkeypatch):
    probe = tmp_path / "office_proxy_probe.py"
    probe.write_text(
        "from starlette.requests import Request\n"
        "from starlette.responses import JSONResponse\n"
        "async def app(scope, receive, send):\n"
        "    request = Request(scope, receive)\n"
        "    await JSONResponse({\n"
        "        'authorization': request.headers.get('authorization'),\n"
        "        'x_real_ip': request.headers.get('x-real-ip'),\n"
        "        'x_forwarded_for': request.headers.get('x-forwarded-for'),\n"
        "        'x_forwarded_proto': request.headers.get('x-forwarded-proto'),\n"
        "    })(scope, receive, send)\n",
        encoding="utf-8",
    )
    upstream_port = _free_port()
    python_path = os.pathsep.join(
        [str(tmp_path), str(Path(__file__).parents[1]), os.environ.get("PYTHONPATH", "")]
    )
    upstream = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "office_proxy_probe:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(upstream_port),
            "--no-proxy-headers",
            "--no-access-log",
        ],
        env={**os.environ, "PYTHONPATH": python_path},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    office_bearer = tmp_path / "office-recipient-bearer"
    office_bearer.write_text("s" * 32, encoding="utf-8")
    office_bearer.chmod(0o600)
    monkeypatch.setenv(
        "PLATFORM_LOOPBACK_TARGET_BASE_URL",
        f"http://127.0.0.1:{upstream_port}",
    )
    monkeypatch.setenv("PLATFORM_LOOPBACK_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_OFFICE_RECIPIENT_BEARER_FILE", str(office_bearer))
    monkeypatch.setenv(
        "PLATFORM_OFFICE_RECIPIENT_LOCAL_PEER_CIDRS",
        "172.31.0.1/32",
    )
    try:
        _wait_ready(f"http://127.0.0.1:{upstream_port}/")
        from app.cloud_replica.loopback_proxy import create_app

        yield create_app()
    finally:
        upstream.terminate()
        try:
            upstream.wait(timeout=5)
        except subprocess.TimeoutExpired:
            upstream.kill()
            upstream.wait(timeout=5)


def test_office_proxy_canonicalizes_only_configured_bridge_peer(
    office_topology_proxy,
):
    path = "/api/v1/internal/office/recipient-directory/search"
    authorization = {"Authorization": f"Bearer {'s' * 32}"}
    with TestClient(
        office_topology_proxy,
        client=("172.31.0.1", 51000),
    ) as allowed_client:
        allowed = allowed_client.post(path, headers=authorization, json={"query": ""})
        spoofed = allowed_client.post(
            path,
            headers={
                **authorization,
                "X-Real-IP": "127.0.0.1",
                "X-Forwarded-For": "127.0.0.1",
                "X-Forwarded-Proto": "http",
            },
            json={"query": ""},
        )
    with TestClient(
        office_topology_proxy,
        client=("172.31.0.9", 51000),
    ) as untrusted_client:
        untrusted = untrusted_client.post(
            path,
            headers=authorization,
            json={"query": ""},
        )

    assert allowed.status_code == 200
    assert allowed.json() == {
        "authorization": f"Bearer {'s' * 32}",
        "x_real_ip": "127.0.0.1",
        "x_forwarded_for": "127.0.0.1",
        "x_forwarded_proto": "http",
    }
    assert spoofed.status_code == 404
    assert untrusted.status_code == 404
