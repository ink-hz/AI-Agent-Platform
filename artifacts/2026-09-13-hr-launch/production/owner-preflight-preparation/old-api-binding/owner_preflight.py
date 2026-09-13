"""Existing-session owner/CSRF probe; no business objects, S3, or session issuance.

Authentication/audit bookkeeping can occur. A verified receipt is a timed
observation, not a lease guaranteeing the session remains valid afterwards.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

HELPER_SHA = "13fcb02d28132afbe84268b0b5576946f2bc32dbaea8f1c2f74abaf702e0abd5"
EXPECTED_SERVER_CONTRACT = {
    "backend/app/attachments/conversation_routes.py": "48f0bd06e3b8531f67f9b3361561e656628ef3ef526bccc9f98d004689071b38",
    "backend/app/control_plane/middleware.py": "634b814b5ba1997deb27f8b1096805434e0cd946cfa09fefcad6e7a0494ec804",
}
PATH = "/api/v1/attachments/uploads"


class Denied(Exception):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def helper():
    path = Path(__file__).with_name("api_canary.py")
    if path.is_symlink() or sha(path.read_bytes()) != HELPER_SHA:
        raise Denied("helper_changed")
    spec = importlib.util.spec_from_file_location("owner_preflight_deadline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config(path):
    path = Path(path)
    if not path.is_absolute():
        raise Denied("config_invalid")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_size > 2_000_000
        ):
            raise Denied("config_invalid")
        raw = stream.read()
    value = json.loads(raw)
    fields = {"owner_id", "session_cookie", "csrf", "public_origin", "api_base_url"}
    if not isinstance(value, dict) or set(value) != fields:
        raise Denied("config_invalid")
    if any(not isinstance(value[k], str) or not value[k] for k in fields):
        raise Denied("config_invalid")
    value["owner_id"] = str(UUID(value["owner_id"]))
    for key in ("session_cookie", "csrf"):
        if any(ord(c) < 33 or ord(c) > 126 or c in ';,"\\' for c in value[key]):
            raise Denied("config_invalid")
    for key in ("public_origin", "api_base_url"):
        url = urlsplit(value[key])
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise Denied("config_invalid")
        value[key] = value[key].rstrip("/")
    if value["public_origin"] != value["api_base_url"]:
        raise Denied("config_invalid")
    return value, sha(raw)


def preflight(config_path, receipt_path, *, run_id, client=None, timeout=10):
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < timeout <= 30
    ):
        raise Denied("timeout_invalid")
    run_id = str(UUID(run_id))
    output = Path(receipt_path)
    if not output.is_absolute():
        raise Denied("receipt_invalid")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    receipt = {
        "schema": "owner-preflight-v1",
        "run_id": run_id,
        "script_sha256": sha(Path(__file__).read_bytes()),
        "helper_sha256": HELPER_SHA,
        "expected_server_contract": EXPECTED_SERVER_CONTRACT,
        "started_at": time.time(),
        "status": "pending",
        "owner_verified": False,
        "csrf_verified": False,
        "requests": [],
        "business_mutation_requested": False,
    }

    def persist():
        raw = (json.dumps(receipt, sort_keys=True) + "\n").encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        with os.fdopen(os.dup(fd), "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    owned = None
    try:
        persist()
        safe = helper()
        cfg, digest = config(config_path)
        receipt.update(
            config_sha256=digest, owner_id=cfg["owner_id"], origin=cfg["public_origin"]
        )
        persist()
        if client is None:
            owned = httpx.Client(trust_env=False, follow_redirects=False)
            client = owned

        class Invocation:
            def request(self, *unused, **kwargs):
                deadline = time.monotonic() + kwargs["timeout"]

                def request(method, path, token=None):
                    item = {
                        "request_id": str(uuid4()),
                        "method": method,
                        "path": path,
                        "started_at": time.time(),
                        "status": "sending",
                    }
                    receipt["requests"].append(item)
                    persist()
                    headers = {
                        "Cookie": "__Host-platform_session=" + cfg["session_cookie"],
                        "Origin": cfg["public_origin"],
                        "X-Request-ID": item["request_id"],
                    }
                    if token is not None:
                        headers["X-CSRF-Token"] = token
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Denied("deadline")
                    response = client.request(
                        method,
                        cfg["api_base_url"] + path,
                        headers=headers,
                        timeout=remaining,
                        follow_redirects=False,
                        **({"json": {}} if method == "POST" else {}),
                    )
                    item.update(status=response.status_code, finished_at=time.time())
                    persist()
                    return response

                response = request("GET", "/api/v1/account")
                if response.status_code != 200:
                    raise Denied("owner_denied")
                account = response.json()
                if {
                    k: account.get(k)
                    for k in ("internal_user_id", "role", "hard_stale_read_only")
                } != {
                    "internal_user_id": cfg["owner_id"],
                    "role": "platform_owner",
                    "hard_stale_read_only": False,
                }:
                    raise Denied("owner_denied")
                receipt["owner_verified"] = True
                bad = secrets.token_urlsafe(32)
                while bad == cfg["csrf"]:
                    bad = secrets.token_urlsafe(32)
                response = request("POST", PATH, bad)
                if (
                    response.status_code != 403
                    or response.json().get("detail") != "CSRF verification failed"
                ):
                    raise Denied("csrf_negative_unproven")
                response = request("POST", PATH, cfg["csrf"])
                if response.status_code != 422 or response.json() != {
                    "detail": "attachment request invalid"
                }:
                    raise Denied("csrf_positive_unproven")
                if config(config_path)[1] != digest:
                    raise Denied("config_changed")
                receipt["csrf_verified"] = True

        safe.bounded_request(Invocation(), timeout=timeout)
        receipt.update(
            status="verified", observed_at=time.time(), expires_at=time.time() + 60
        )
    except BaseException as error:  # noqa: BLE001 - persist hard deadline without secrets.
        for item in receipt["requests"]:
            if item["status"] == "sending":
                item["status"] = "transport_unknown"
        receipt.update(
            status="failed",
            csrf_verified=False,
            failure=str(error)
            if isinstance(error, Denied)
            else "preflight_unavailable",
        )
    finally:
        try:
            persist()
        finally:
            os.close(fd)
            if owned is not None:
                owned.close()
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-config", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = preflight(args.private_config, args.receipt, run_id=args.run_id)
    print(json.dumps({"status": result["status"], "run_id": result["run_id"]}))
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
