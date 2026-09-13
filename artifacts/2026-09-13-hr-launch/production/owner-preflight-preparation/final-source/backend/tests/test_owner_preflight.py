"""Existing authenticated HTTP/PG routes; no business-object mutation."""

import importlib.util
import json
from pathlib import Path

from tests.test_attachment_erasure_production_canary import api

_FIXTURES = (api,)
PRODUCTION = Path(__file__).parents[2] / "artifacts/2026-09-13-hr-launch/production"


def module():
    path = PRODUCTION / "owner_preflight.py"
    assert path.is_file(), "owner preflight entry missing"
    spec = importlib.util.spec_from_file_location("owner_preflight_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def config_file(tmp_path, config):
    path = tmp_path / "owner.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    return path


def counts(api):
    with api["db"].admin_connection() as c:
        return tuple(
            c.execute("select count(*) from platform_attachments." + table).fetchone()[
                0
            ]
            for table in (
                "attachments",
                "uploads",
                "upload_write_attempts",
                "processing_jobs",
                "erasure_jobs",
            )
        )


def test_real_owner_csrf_rejected_before_validated_empty_body(
    api, tmp_path, monkeypatch
):
    mod = module()
    from app.attachments import conversation_routes

    reached = []
    monkeypatch.setattr(
        conversation_routes, "_upload_service", lambda request: reached.append(True)
    )
    before = counts(api)
    result = mod.preflight(
        config_file(tmp_path, api["config"]),
        tmp_path / "receipt.json",
        run_id="6dc1b038-5c2b-4109-973c-2a45f230e4bc",
        client=api["client"],
    )
    assert result["status"] == "verified"
    assert result["owner_verified"] and result["csrf_verified"]
    assert [r["status"] for r in result["requests"]] == [200, 403, 422]
    assert counts(api) == before
    assert reached == []
    raw = (tmp_path / "receipt.json").read_text()
    assert api["config"]["session_cookie"] not in raw
    assert api["config"]["csrf"] not in raw


def test_wrong_csrf_never_passes(api, tmp_path):
    mod = module()
    config = dict(api["config"], csrf="wrong-token")
    before = counts(api)
    result = mod.preflight(
        config_file(tmp_path, config),
        tmp_path / "receipt.json",
        run_id="6dc1b038-5c2b-4109-973c-2a45f230e4bc",
        client=api["client"],
    )
    assert result["status"] == "failed"
    assert result["csrf_verified"] is False
    assert counts(api) == before


def test_wrong_owner_stops_before_post(api, tmp_path):
    mod = module()
    config = dict(api["config"], owner_id="d2bb175d-c5b7-4d0b-a173-e62b07c9a166")
    result = mod.preflight(
        config_file(tmp_path, config),
        tmp_path / "receipt.json",
        run_id="6dc1b038-5c2b-4109-973c-2a45f230e4bc",
        client=api["client"],
    )
    assert result["status"] == "failed"
    assert len(result["requests"]) == 1


def test_config_mode_rejected_without_http(tmp_path):
    mod = module()
    path = tmp_path / "config.json"
    path.write_text("{}")
    path.chmod(0o644)
    result = mod.preflight(
        path,
        tmp_path / "receipt.json",
        run_id="6dc1b038-5c2b-4109-973c-2a45f230e4bc",
        client=None,
    )
    assert result["status"] == "failed"
    assert result["requests"] == []


def test_existing_invalid_body_shape(api):
    response = api["client"].request(
        "POST",
        "https://localhost/api/v1/attachments/uploads",
        headers={
            "Cookie": "__Host-platform_session=" + api["config"]["session_cookie"],
            "Origin": "https://localhost",
            "X-CSRF-Token": api["config"]["csrf"],
        },
        json={},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "attachment request invalid"}


def test_real_slow_http_deadline_persists_unknown_without_retry(tmp_path):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import httpx

    mod = module()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(1)
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {
        "owner_id": "d2bb175d-c5b7-4d0b-a173-e62b07c9a166",
        "session_cookie": "synthetic-cookie",
        "csrf": "synthetic-csrf",
        "public_origin": "https://localhost",
        "api_base_url": "https://localhost",
    }
    try:
        with httpx.Client(trust_env=False) as http:

            class Loopback:
                def request(self, method, url, **kwargs):
                    kwargs.pop(
                        "headers"
                    )  # No synthetic credential sent to this socket test.
                    return http.request(
                        method, f"http://127.0.0.1:{server.server_port}/", **kwargs
                    )

            start = time.monotonic()
            result = mod.preflight(
                config_file(tmp_path, config),
                tmp_path / "receipt.json",
                run_id="6dc1b038-5c2b-4109-973c-2a45f230e4bc",
                client=Loopback(),
                timeout=0.15,
            )
            elapsed = time.monotonic() - start
        assert result["status"] == "failed"
        assert result["requests"][0]["status"] == "transport_unknown"
        assert not result["csrf_verified"]
        assert calls == [1]
        assert elapsed < 0.8
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_arbitrary_422_does_not_prove_csrf(api, tmp_path):
    import httpx

    mod = module()
    real = api["client"]

    class Changed:
        def request(self, *args, **kwargs):
            response = real.request(*args, **kwargs)
            if response.status_code == 422:
                return httpx.Response(422, json={"passed": True})
            return response

    result = mod.preflight(
        config_file(tmp_path, api["config"]),
        tmp_path / "receipt.json",
        run_id="6dc1b038-5c2b-4109-973c-2a45f230e4bc",
        client=Changed(),
    )
    assert result["status"] == "failed"
    assert result["failure"] == "csrf_positive_unproven"
