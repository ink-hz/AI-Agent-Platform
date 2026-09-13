"""Health from the actual Worker process; serving probes never renews progress."""

from __future__ import annotations

import json
import os
import re
import socket
import socketserver
import stat
import threading
import time
from pathlib import Path
from uuid import uuid4

from .readiness import unavailable

DEFAULT_STATUS = Path("/tmp/platform-hr-agent-readiness.json")


class WorkerHealth:
    def __init__(self, readiness, path=DEFAULT_STATUS, *, max_age=45):
        self.readiness = readiness
        self.path = Path(path)
        self.max_age = max_age
        self.last_progress = None
        self.lock = threading.Lock()
        self.identity = {
            "pid": os.getpid(),
            "started_at_ns": time.time_ns(),
            "nonce": uuid4().hex,
        }
        self.identity["socket"] = "/tmp/hr-ready-" + self.identity["nonce"] + ".sock"

    def progress(self):
        """Called only after a successful poll or validated active lease renewal."""
        with self.lock:
            self.last_progress = time.monotonic()

    def report(self):
        report = self.readiness.check()
        with self.lock:
            age = (
                None
                if self.last_progress is None
                else time.monotonic() - self.last_progress
            )
        if age is None or not 0 <= age <= self.max_age:
            report["ready"] = False
            report["new_admission_enabled"] = False
            report["blockers"].append("worker_progress_stale")
        report["worker"] = {**self.identity, "progress_age_seconds": age}
        return report

    def __enter__(self):
        health = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(5)
                try:
                    try:
                        payload = health.report()
                    except Exception:  # noqa: BLE001 - socketserver must not log dependency exceptions
                        payload = unavailable("readiness_unavailable")
                        payload.update(role="worker", worker=health.identity)
                    self.request.sendall(json.dumps(payload).encode() + b"\n")
                except OSError:
                    pass

        self.server = socketserver.UnixStreamServer(self.identity["socket"], Handler)
        os.chmod(self.identity["socket"], 0o600)
        temporary = self.path.with_name(self.path.name + "." + self.identity["nonce"])
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(self.identity, stream)
            os.replace(temporary, self.path)
        except Exception:
            self.server.server_close()
            Path(self.identity["socket"]).unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            raise
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=6)
        Path(self.identity["socket"]).unlink(missing_ok=True)
        try:
            if json.loads(self.path.read_text()).get("nonce") == self.identity["nonce"]:
                self.path.unlink()
        except (OSError, ValueError):
            pass


def check_worker(path=DEFAULT_STATUS):
    try:
        path = Path(path)
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("worker identity unavailable")
        if metadata.st_size > 4096:
            raise ValueError("worker identity invalid")
        identity = json.loads(path.read_text())
        if set(identity) != {"pid", "started_at_ns", "nonce", "socket"}:
            raise ValueError("worker identity invalid")
        if (
            type(identity["pid"]) is not int
            or identity["pid"] <= 0
            or type(identity["started_at_ns"]) is not int
            or identity["started_at_ns"] <= 0
            or not isinstance(identity["nonce"], str)
            or re.fullmatch(r"[0-9a-f]{32}", identity["nonce"]) is None
            or identity["socket"] != "/tmp/hr-ready-" + identity["nonce"] + ".sock"
        ):
            raise ValueError("worker identity invalid")
        os.kill(identity["pid"], 0)
        socket_metadata = Path(identity["socket"]).lstat()
        if (
            not stat.S_ISSOCK(socket_metadata.st_mode)
            or socket_metadata.st_uid != os.getuid()
            or stat.S_IMODE(socket_metadata.st_mode) != 0o600
        ):
            raise ValueError("worker socket unavailable")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(6)
            client.connect(identity["socket"])
            data = b""
            while not data.endswith(b"\n"):
                chunk = client.recv(8192)
                if not chunk or len(data) + len(chunk) > 65536:
                    raise ValueError("worker report unavailable")
                data += chunk
        report = json.loads(data)
        if any(report["worker"][key] != value for key, value in identity.items()):
            raise ValueError("worker instance changed")
        if report.get("role") != "worker" or type(report.get("ready")) is not bool:
            raise ValueError("worker report invalid")
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return unavailable("worker_unavailable")
