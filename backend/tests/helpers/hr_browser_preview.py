"""Opt-in loopback preview of the actual ASGI app with its owned test account.

Not a login bypass in the application: every forwarded request traverses the
real persisted session middleware in TestClient. This helper never contacts a
production origin and is used only while the disposable PG fixture is alive.
TestClient buffers response bodies, so this preview deliberately rejects SSE;
only snapshot-based UI interactions are validated here, never live streaming.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def preview_until_released(client, control_directory, csrf):
    control = Path(control_directory)
    if not control.is_absolute() or not control.is_dir() or control.is_symlink():
        raise ValueError("owned browser control unavailable")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def forward(self):
            if self.path.split("?", 1)[0].endswith("/events"):
                self.send_error(503, "Snapshot-only owned preview")
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size > 52 * 1024 * 1024:
                self.send_error(413)
                return
            response = client.request(
                self.command,
                self.path,
                content=self.rfile.read(size) if size else None,
                headers={
                    name: value
                    for name, value in self.headers.items()
                    if name.lower()
                    in {
                        "content-type",
                        "idempotency-key",
                        "last-event-id",
                        "x-platform-account-contract",
                    }
                },
            )
            self.send_response(response.status_code)
            for name in ("content-type", "content-disposition", "location"):
                if name in response.headers:
                    self.send_header(name, response.headers[name])
            self.send_header("Content-Length", str(len(response.content)))
            self.send_header("Cache-Control", "no-store")
            # Only this disposable localhost account, never the user's cookies.
            self.send_header(
                "Set-Cookie",
                f"__Host-platform_csrf={csrf}; Path=/; Secure; SameSite=Lax",
            )
            self.end_headers()
            try:
                self.wfile.write(response.content)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ready = control / "ready-url"
    ready.write_text(f"http://localhost:{server.server_port}/hr")
    try:
        for _ in range(1200):
            if (control / "release").exists():
                return
            threading.Event().wait(0.25)
        raise AssertionError("owned browser preview deadline")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        ready.unlink(missing_ok=True)
