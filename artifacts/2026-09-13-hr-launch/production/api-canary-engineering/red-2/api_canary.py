"""Bounded public/synthetic HTTP canary. No credential issuance or service control.

Requires an existing owner session in an absolute, regular 0600 JSON file.
See api-canary-engineering/README.md before explicitly executing against production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx


class CanaryError(Exception):
    """Fixed safe diagnosis, never raw server exceptions or credential values."""


def private_json(path):
    path = Path(path)
    if not path.is_absolute():
        raise CanaryError("private_file_required")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 2_000_000:
                raise CanaryError("private_file_required")
            value = json.load(stream)
        if not isinstance(value, dict):
            raise CanaryError("private_file_required")
        return value
    except (OSError, ValueError):
        raise CanaryError("private_file_required") from None


def load_config(path):
    value = private_json(path)
    fields = ("owner_id", "session_cookie", "csrf", "public_origin", "api_base_url")
    if any(not isinstance(value.get(k), str) or not value[k] for k in fields):
        raise CanaryError("credentials_required")
    try:
        value["owner_id"] = str(UUID(value["owner_id"]))
    except ValueError:
        raise CanaryError("owner_invalid") from None
    for key in ("session_cookie", "csrf"):
        if any(ord(c) < 33 or ord(c) > 126 or c in ';,"\\' for c in value[key]):
            raise CanaryError("credential_invalid")
    for key in ("public_origin", "api_base_url"):
        parsed = urlsplit(value[key])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise CanaryError("https_origin_required")
        value[key] = value[key].rstrip("/")
    if value["api_base_url"] != value["public_origin"]:
        raise CanaryError("same_origin_required")
    return {k: value[k] for k in fields}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write_private(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n"
    temp = path.with_name(path.name + "." + str(uuid4()))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Canary:
    def __init__(self, config, directory, client, *, resume=False):
        self.config, self.client = config, client
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise CanaryError("absolute_evidence_directory_required")
        if resume:
            if self.directory.is_symlink():
                raise CanaryError("evidence_directory_invalid")
            self.ledger = private_json(self.directory / "ledger.json")
            if any(self.ledger.get(k) != config[k] for k in ("owner_id", "public_origin")):
                raise CanaryError("ledger_identity_mismatch")
        else:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
            self.ledger = {"run_id": str(uuid4()), "owner_id": config["owner_id"],
                           "public_origin": config["public_origin"], "deadline": time.time() + 900,
                           "operations": {}, "http": [], "turns": [], "status": "created"}
            self.save()

    def save(self):
        write_private(self.directory / "ledger.json", self.ledger)

    def request(self, method, path, *, key=None, body=None, content=None, accepted=(200,)):
        remaining = self.ledger["deadline"] - time.time()
        if remaining <= 0:
            raise CanaryError("deadline_exhausted")
        if not path.startswith("/api/") or ".." in path or "?" in path or "#" in path:
            raise CanaryError("request_path_invalid")
        request_id = str(uuid4())
        receipt = {"request_id": request_id, "method": method, "path": path, "status": "sending"}
        self.ledger["http"].append(receipt)
        self.save()
        headers = {"Cookie": "__Host-platform_session=" + self.config["session_cookie"],
                   "Origin": self.config["public_origin"], "X-CSRF-Token": self.config["csrf"],
                   "X-Request-ID": request_id}
        if key:
            headers["Idempotency-Key"] = key
        if content is not None:
            headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(len(content))})
        try:
            response = self.client.request(method, self.config["api_base_url"] + path,
                                           headers=headers, json=body, content=content,
                                           timeout=min(20, remaining), follow_redirects=False)
        except httpx.HTTPError:
            receipt["status"] = "transport_unknown"
            self.save()
            raise CanaryError("outcome_unknown") from None
        receipt["status"] = response.status_code
        # Caller-generated UUID is always retained; server request IDs only if UUID.
        try:
            receipt["server_request_id"] = str(UUID(response.headers.get("X-Request-ID", "")))
        except ValueError:
            pass
        self.save()
        if response.status_code not in accepted:
            raise CanaryError("http_" + str(response.status_code))
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ValueError:
            raise CanaryError("response_invalid") from None

    def mutate(self, name, path, body, accepted, *, method="POST", content=None, replay=False):
        operations = self.ledger["operations"]
        if name not in operations:
            operations[name] = {"key": str(uuid4()), "path": path, "body": body,
                                "content_sha256": sha(content) if content is not None else None,
                                "method": method, "status": "prepared"}
            self.save()
        op = operations[name]
        if (op["path"], op["body"], op["method"], op["content_sha256"]) != (path, body, method, sha(content) if content is not None else None):
            raise CanaryError("operation_mismatch")
        if op["status"] == "received" and not replay:
            return op["response"]
        if op["status"] != "prepared" and not (replay and name in {"work", "input"}):
            raise CanaryError("outcome_unknown")
        op["status"] = "sending"
        self.save()
        try:
            value = self.request(method, path, key=op["key"], body=body, content=content, accepted=accepted)
        except CanaryError:
            op["status"] = "outcome_unknown"
            self.save()
            raise
        op.update(status="received", response=value)
        self.save()
        return value
