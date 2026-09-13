"""Actual slow socket response; no authentication or production acceptance claim."""
import importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import threading
import time
from uuid import uuid4

import httpx
import pytest

PRODUCTION = Path(__file__).parents[2] / "artifacts/2026-09-13-hr-launch/production"


@pytest.mark.parametrize("kind", ["hr", "attachment"])
def test_slow_stream_is_cut_off_and_ambiguous_upload_never_repeats(tmp_path, monkeypatch, kind):
    monkeypatch.syspath_prepend(str(PRODUCTION))
    common = importlib.import_module("api_canary")
    cls = common.Canary if kind == "hr" else importlib.import_module("attachment_erasure_canary").AttachmentCanary
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            received.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = b" " * 24 + b"{}"
            self.send_response(201)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                for byte in payload:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.04)  # Every chunk arrives inside httpx's read timeout.
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(trust_env=False) as actual:
            class LocalTransport:
                def request(self, method, url, **kwargs):
                    # Only map the test origin to our owned cleartext loopback socket.
                    return actual.request(method, "http://127.0.0.1:" + str(server.server_port) + "/upload", **kwargs)
            config = {"owner_id": str(uuid4()), "session_cookie": "local-test-token", "csrf": "local-test-csrf",
                      "public_origin": "https://localhost", "api_base_url": "https://localhost"}
            runner = cls(config, tmp_path / "run", LocalTransport())
            runner.ledger["deadline"] = time.time() + 0.15
            runner.save()
            previous = signal.getsignal(signal.SIGALRM)
            started = time.monotonic()
            with pytest.raises(common.CanaryError, match="outcome_unknown"):
                runner.mutate("upload_begin", "/api/v1/attachments/uploads", {"synthetic": True}, (201,))
            assert time.monotonic() - started < 0.8
            assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
            assert signal.getsignal(signal.SIGALRM) == previous
            assert runner.ledger["operations"]["upload_begin"]["status"] == "outcome_unknown"
            resumed = cls(config, runner.directory, LocalTransport(), resume=True)
            with pytest.raises(common.CanaryError, match="outcome_unknown"):
                resumed.mutate("upload_begin", "/api/v1/attachments/uploads", {"synthetic": True}, (201,))
            assert received == ["/upload"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_existing_timer_is_not_replaced(monkeypatch):
    monkeypatch.syspath_prepend(str(PRODUCTION))
    common = importlib.import_module("api_canary")
    assert hasattr(common, "bounded_request"), "wall-clock request supervisor missing"
    before = signal.getsignal(signal.SIGALRM)
    signal.setitimer(signal.ITIMER_REAL, 10)
    try:
        with pytest.raises(common.CanaryError, match="deadline_supervision_unavailable"):
            common.bounded_request(None, "GET", "https://localhost", timeout=0.1)
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 9
        assert signal.getsignal(signal.SIGALRM) == before
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
