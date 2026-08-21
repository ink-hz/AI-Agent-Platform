from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


_WORKER_ID = "agentops-mac-primary"
_KEY_ID = "worker-v1"
_LABEL = "com.orbbec.agent-execution-worker"
_CLOUD_HOST = "root@47.106.112.69"
_AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
)
_CONFIG_KEYS = {"schema_version", "cloud_admin_host", "cloud_admin_key"}
_PUBLIC_KEYS = {
    "worker_id",
    "key_id",
    "public_key_base64url",
    "allowed_agent_ids",
}
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PID = re.compile(rb"(?m)^\s*pid\s*=\s*([1-9][0-9]*)\s*$")


class AcceptanceGateError(ValueError):
    pass


def _configuration_error() -> AcceptanceGateError:
    return AcceptanceGateError("acceptance configuration unavailable")


def _gate_error() -> AcceptanceGateError:
    return AcceptanceGateError("acceptance gate failed")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes


@dataclass(frozen=True)
class AcceptanceConfig:
    cloud_admin_host: str
    cloud_admin_key: Path


@dataclass(frozen=True)
class InitialGateResult:
    worker_id: str
    registered_public_key_sha256: str
    public_ports_added: int


CommandRunner = Callable[..., CommandResult]


def _open_directory(path: Path) -> int:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise _configuration_error()
        return descriptor
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise _configuration_error() from None


def _read_owner_file(root: Path, path: Path, *, maximum_size: int = 65_536) -> bytes:
    if not path.is_absolute() or path.parent != root or path.name in {"", ".", ".."}:
        raise _configuration_error()
    directory_fd = _open_directory(root)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.geteuid()
                or metadata.st_size > maximum_size
            ):
                raise _configuration_error()
            value = os.read(descriptor, maximum_size + 1)
            if len(value) > maximum_size or os.read(descriptor, 1):
                raise _configuration_error()
            return value
        finally:
            os.close(descriptor)
    except AcceptanceGateError:
        raise
    except Exception:
        raise _configuration_error() from None
    finally:
        os.close(directory_fd)


def load_config(config_path: Path, *, private_root: Path) -> AcceptanceConfig:
    try:
        value = json.loads(_read_owner_file(private_root, config_path))
        if not isinstance(value, dict) or set(value) != _CONFIG_KEYS:
            raise _configuration_error()
        if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
            raise _configuration_error()
        if value["cloud_admin_host"] != _CLOUD_HOST:
            raise _configuration_error()
        key = Path(value["cloud_admin_key"])
        _read_owner_file(private_root, key, maximum_size=16_384)
        return AcceptanceConfig(cloud_admin_host=_CLOUD_HOST, cloud_admin_key=key)
    except AcceptanceGateError:
        raise
    except Exception:
        raise _configuration_error() from None


def _run_command(
    arguments: tuple[str, ...], *, input_bytes: bytes | None = None, timeout: int
) -> CommandResult:
    completed = subprocess.run(
        arguments,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout)


def _require_command(
    runner: CommandRunner,
    arguments: tuple[str, ...],
    *,
    input_bytes: bytes | None = None,
    timeout: int,
) -> bytes:
    try:
        result = runner(arguments, input_bytes=input_bytes, timeout=timeout)
    except Exception:
        raise _gate_error() from None
    if result.returncode != 0:
        raise _gate_error()
    return result.stdout


def _local_identity(
    private_root: Path, private_key_path: Path, public_document_path: Path
) -> str:
    try:
        private = _read_owner_file(private_root, private_key_path, maximum_size=32)
        if len(private) != 32:
            raise _gate_error()
        public = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        fingerprint = hashlib.sha256(public).hexdigest()
        public_root = public_document_path.parent
        document = json.loads(_read_owner_file(public_root, public_document_path))
        if not isinstance(document, dict) or set(document) != _PUBLIC_KEYS:
            raise _gate_error()
        encoded = base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")
        if (
            document["worker_id"] != _WORKER_ID
            or document["key_id"] != _KEY_ID
            or document["public_key_base64url"] != encoded
            or document["allowed_agent_ids"] != list(_AGENTS)
        ):
            raise _gate_error()
        return fingerprint
    except AcceptanceGateError as error:
        if str(error) == "acceptance gate failed":
            raise
        raise _gate_error() from None
    except Exception:
        raise _gate_error() from None


def _remote_probe_script() -> bytes:
    # Gate 01: live cloud API, database and worker heartbeat.
    # Gate 02: public listeners stay on 22/80/443; Platform and FAE stay on
    # 127.0.0.1:8080 and 127.0.0.1:8000. The old MetaBot range 9101-9108
    # remains closed.
    # Gate 03: return only the registered_public_key_sha256, never key bytes.
    return br'''#!/bin/bash
set -euo pipefail
platform_root=/opt/orbbec-agent-platform
release_path="$(/usr/bin/readlink -f "$platform_root/current")"
environment_path="$platform_root/private/platform.env"
compose_path="$release_path/deploy/cloud/compose.yaml"
compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
postgres_id="$("${compose[@]}" ps -q platform-postgres)"
api_id="$("${compose[@]}" ps -q platform-api)"
[[ -n "$postgres_id" && -n "$api_id" ]]
cloud_api_healthy=false
cloud_database_healthy=false
worker_heartbeat_fresh=false
if /usr/bin/curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8080/api/health >/dev/null; then
  cloud_api_healthy=true
fi
if [[ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$postgres_id")" == healthy ]]; then
  cloud_database_healthy=true
fi
relay_identity="$(/usr/bin/docker exec "$postgres_id" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -c \
  "select concat(worker.last_seen_at > clock_timestamp() - interval '60 seconds', ':', encode(sha256(worker_key.public_key), 'hex')) from platform_control.execution_workers worker join platform_control.execution_worker_keys worker_key using(worker_id) where worker.worker_id='agentops-mac-primary' and worker_key.key_id='worker-v1' and worker.status='active' and worker_key.status='active'")"
registered_public_key_sha256="${relay_identity#*:}"
if [[ "$relay_identity" == t:* ]]; then worker_heartbeat_fresh=true; fi
listeners="$(/usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/sort -u)"
# 9101-9108 are the retired MetaBot public listener range.
! /usr/bin/grep -Eq ':(9101|9102|9103|9104|9105|9106|9107|9108)$' <<<"$listeners"
/usr/bin/python3 - "$cloud_api_healthy" "$cloud_database_healthy" "$worker_heartbeat_fresh" "$registered_public_key_sha256" "$listeners" <<'PY'
import json, re, sys
api, database, heartbeat, fingerprint, raw = sys.argv[1:]
listeners = [line for line in raw.splitlines() if line]
public = []
loopback = []
for value in listeners:
    if value.startswith("127.0.0.1:"):
        if value in {"127.0.0.1:8000", "127.0.0.1:8080"}:
            loopback.append(value)
        continue
    match = re.search(r":([0-9]+)$", value)
    if match and int(match.group(1)) in {22, 80, 443}:
        public.append(value)
    elif match:
        public.append(value)
print(json.dumps({
    "schema_version": 1,
    "cloud_api_healthy": api == "true",
    "cloud_database_healthy": database == "true",
    "worker_heartbeat_fresh": heartbeat == "true",
    "registered_public_key_sha256": fingerprint,
    "public_listeners": sorted(public),
    "platform_loopback_listeners": sorted(loopback),
}, sort_keys=True, separators=(",", ":")))
PY
'''


def _parse_cloud(value: bytes, fingerprint: str) -> None:
    keys = {
        "schema_version",
        "cloud_api_healthy",
        "cloud_database_healthy",
        "worker_heartbeat_fresh",
        "registered_public_key_sha256",
        "public_listeners",
        "platform_loopback_listeners",
    }
    try:
        document = json.loads(value)
        if not isinstance(document, dict) or set(document) != keys:
            raise _gate_error()
        if document["schema_version"] != 1 or isinstance(document["schema_version"], bool):
            raise _gate_error()
        if any(
            document[name] is not True
            for name in (
                "cloud_api_healthy",
                "cloud_database_healthy",
                "worker_heartbeat_fresh",
            )
        ):
            raise _gate_error()
        remote_fingerprint = document["registered_public_key_sha256"]
        if (
            not isinstance(remote_fingerprint, str)
            or _HEX_SHA256.fullmatch(remote_fingerprint) is None
            or remote_fingerprint != fingerprint
        ):
            raise _gate_error()
        listeners = document["public_listeners"]
        loopbacks = document["platform_loopback_listeners"]
        if (
            not isinstance(listeners, list)
            or not listeners
            or any(not isinstance(item, str) for item in listeners)
            or {int(item.rsplit(":", 1)[1]) for item in listeners} != {22, 80, 443}
            or loopbacks != ["127.0.0.1:8000", "127.0.0.1:8080"]
        ):
            raise _gate_error()
    except AcceptanceGateError:
        raise
    except Exception:
        raise _gate_error() from None


def _check_local_listener(runner: CommandRunner, uid: int) -> None:
    launch = _require_command(
        runner,
        ("/bin/launchctl", "print", f"gui/{uid}/{_LABEL}"),
        timeout=10,
    )
    match = _PID.search(launch)
    if match is None:
        raise _gate_error()
    pid = match.group(1).decode("ascii")
    owned = _require_command(
        runner,
        (
            "/usr/sbin/lsof",
            "-nP",
            "-a",
            "-p",
            pid,
            "-iTCP",
            "-sTCP:LISTEN",
        ),
        timeout=10,
    )
    listener_lines = [line for line in owned.splitlines() if b"(LISTEN)" in line]
    if len(listener_lines) != 1 or b"TCP 127.0.0.1:9120 (LISTEN)" not in listener_lines[0]:
        raise _gate_error()
    try:
        forbidden = runner(
            (
                "/usr/sbin/lsof",
                "-nP",
                "-iTCP:9101-9108",
                "-sTCP:LISTEN",
            ),
            input_bytes=None,
            timeout=10,
        )
    except Exception:
        raise _gate_error() from None
    if forbidden.returncode not in {0, 1} or forbidden.stdout or forbidden.returncode == 0:
        raise _gate_error()


def run_gates_01_to_03(
    config_path: Path,
    *,
    runner: CommandRunner = _run_command,
    private_root: Path = Path("/Users/agentops/AgentRuntime/private"),
    private_key_path: Path = Path(
        "/Users/agentops/AgentRuntime/private/execution-worker-ed25519.key"
    ),
    public_document_path: Path = Path(
        "/Users/agentops/AgentRuntime/execution-worker-public.json"
    ),
    current_user: str,
    uid: int,
) -> InitialGateResult:
    try:
        if current_user != "agentops" or isinstance(uid, bool) or not isinstance(uid, int) or uid < 1:
            raise _gate_error()
        config = load_config(config_path, private_root=private_root)
        fingerprint = _local_identity(private_root, private_key_path, public_document_path)
        _check_local_listener(runner, uid)
        remote = _require_command(
            runner,
            (
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=8",
                "-i",
                str(config.cloud_admin_key),
                config.cloud_admin_host,
                "/bin/bash -s",
            ),
            input_bytes=_remote_probe_script(),
            timeout=30,
        )
        _parse_cloud(remote, fingerprint)
        return InitialGateResult(
            worker_id=_WORKER_ID,
            registered_public_key_sha256=fingerprint,
            public_ports_added=0,
        )
    except AcceptanceGateError as error:
        if str(error) == "acceptance gate failed":
            raise
        raise _gate_error() from None
    except Exception:
        raise _gate_error() from None
