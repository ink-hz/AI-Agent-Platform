from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import httpx
import psycopg
from psycopg.rows import dict_row

from .models import RelayEvent
from .worker_auth import WorkerRequestSigner


_WORKER_ID = "agentops-mac-primary"
_WORKER_SUPERVISOR = Path(
    "/Users/agentops/AgentRuntime/platform/deploy/local-execution-worker/worker-pm2.sh"
)
_CLOUD_HOST = "root@47.106.112.69"
_CLOUD_KNOWN_HOSTS = Path(
    "/Users/agentops/AgentRuntime/private/cloud-known-hosts"
)
_AGENTS = (
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
    "agent-brain-bot",
)
_CONFIG_KEYS = {"schema_version", "cloud_admin_host", "cloud_admin_key"}
_PUBLIC_KEYS = {
    "worker_id",
    "key_id",
    "public_key_base64url",
    "allowed_agent_ids",
}
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PRODUCTION_KEY_ID = re.compile(r"worker-v[1-9][0-9]*\Z")
_SESSION_LIST_PATH = "/api/sessions?limit=1"
_SESSION_DETAIL_PREFIX = "/api/sessions/"
_REGISTER_ACTION = "register"
_REVOKE_WORKER_ACTION = "revoke-worker"


class AcceptanceGateError(ValueError):
    pass


def _configuration_error() -> AcceptanceGateError:
    return AcceptanceGateError("acceptance configuration unavailable")


def _gate_error() -> AcceptanceGateError:
    return AcceptanceGateError("acceptance gate failed")


def _cleanup_error() -> AcceptanceGateError:
    return AcceptanceGateError("acceptance cleanup failed")


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
    key_id: str
    registered_public_key_sha256: str
    public_ports_added: int


@dataclass(frozen=True)
class LocalRunState:
    state: str
    event_count: int
    first_seq: int | None
    last_seq: int | None
    undelivered_count: int


@dataclass(frozen=True)
class DuplicateUploadResult:
    status_code: int
    accepted: int
    inserted: int


@dataclass(frozen=True)
class ExecutionGateResult:
    hr_run_id: UUID
    marketing_intelligence_run_id: UUID
    completion_crash_run_id: UUID
    dispatching_crash_run_id: UUID
    duplicate_dispatches: int


@dataclass(frozen=True)
class SignedGateResponse:
    status_code: int
    json_body: dict[str, object]


@dataclass(frozen=True)
class SessionProbeResult:
    sessions_status: int
    history_status: int


@dataclass(frozen=True)
class ExternalFaeProbeResult:
    status_code: int
    body_sha256: str


@dataclass(frozen=True)
class FinalGateResult:
    disposable_worker_id: str
    lease_status: int
    upload_status: int
    sessions_status: int
    history_status: int


@dataclass(frozen=True)
class ReplicaGeneration:
    last_sequence: int
    committed_at: datetime


@dataclass(frozen=True)
class RegressionSnapshot:
    fae_identity: tuple[str, str, str, str, str]
    generations: tuple[tuple[str, ReplicaGeneration], ...]
    management_count: int
    management_max_updated_at: datetime | None


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
) -> tuple[str, str]:
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
        key_id = document["key_id"]
        if (
            document["worker_id"] != _WORKER_ID
            or not isinstance(key_id, str)
            or _PRODUCTION_KEY_ID.fullmatch(key_id) is None
            or document["public_key_base64url"] != encoded
            or document["allowed_agent_ids"] != list(_AGENTS)
        ):
            raise _gate_error()
        return key_id, fingerprint
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
expected_key_id="$1"
[[ "$expected_key_id" =~ ^worker-v[1-9][0-9]*$ ]]
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
relay_identity="$(/usr/bin/docker exec -i "$postgres_id" psql -X -A -t -U platform_owner -d agent_platform_control -v ON_ERROR_STOP=1 -v expected_key_id="$expected_key_id" <<'SQL'
select concat(worker.last_seen_at > clock_timestamp() - interval '60 seconds', ':', encode(sha256(worker_key.public_key), 'hex')) from platform_control.execution_workers worker join platform_control.execution_worker_keys worker_key using(worker_id) where worker.worker_id='agentops-mac-primary' and worker_key.key_id=:'expected_key_id' and worker.status='active' and worker_key.status='active';
SQL
)"
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
sensitive_nonpublic = {5432, 8000, 8080, *range(9101, 9121)}
for value in listeners:
    if value.startswith("127.0.0.1:"):
        if value in {"127.0.0.1:8000", "127.0.0.1:8080"}:
            loopback.append(value)
        continue
    match = re.search(r":([0-9]+)$", value)
    if not match:
        continue
    port = int(match.group(1))
    if port in {22, 80, 443}:
        public.append(value)
    elif port in sensitive_nonpublic:
        raise SystemExit(1)
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


def _worker_pid(runner: CommandRunner, worker_supervisor_path: Path) -> str:
    raw = _require_command(
        runner, (str(worker_supervisor_path), "inspect"), timeout=10
    )
    try:
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {
                "name", "pid", "status", "pm_exec_path", "pm_cwd", "args"
            }
            or value["name"] != "orbbec-agent-execution-worker"
            or value["status"] != "online"
            or not isinstance(value["pid"], int)
            or isinstance(value["pid"], bool)
            or value["pid"] < 1
        ):
            raise ValueError
        return str(value["pid"])
    except Exception:
        raise _gate_error() from None


def _check_local_listener(
    runner: CommandRunner, worker_supervisor_path: Path
) -> None:
    pid = _worker_pid(runner, worker_supervisor_path)
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
    if forbidden.returncode not in {0, 1}:
        raise _gate_error()
    metabot_listeners = [
        line for line in forbidden.stdout.splitlines() if b"(LISTEN)" in line
    ]
    if forbidden.returncode == 1 and forbidden.stdout:
        raise _gate_error()
    if forbidden.returncode == 0 and not metabot_listeners:
        raise _gate_error()
    for line in metabot_listeners:
        endpoint = line.rsplit(b" TCP ", 1)[-1].removesuffix(b" (LISTEN)")
        if not endpoint.startswith((b"127.0.0.1:", b"[::1]:")):
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
    worker_supervisor_path: Path = _WORKER_SUPERVISOR,
    current_user: str,
    uid: int,
) -> InitialGateResult:
    try:
        if current_user != "agentops" or isinstance(uid, bool) or not isinstance(uid, int) or uid < 1:
            raise _gate_error()
        config = load_config(config_path, private_root=private_root)
        _validate_runtime_file(worker_supervisor_path, executable=True)
        key_id, fingerprint = _local_identity(
            private_root, private_key_path, public_document_path
        )
        _check_local_listener(runner, worker_supervisor_path)
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
                f"UserKnownHostsFile={_CLOUD_KNOWN_HOSTS}",
                "-o",
                "ConnectTimeout=8",
                "-i",
                str(config.cloud_admin_key),
                config.cloud_admin_host,
                "/bin/bash -s",
                "--",
                key_id,
            ),
            input_bytes=_remote_probe_script(),
            timeout=30,
        )
        _parse_cloud(remote, fingerprint)
        return InitialGateResult(
            worker_id=_WORKER_ID,
            registered_public_key_sha256=fingerprint,
            public_ports_added=0,
            key_id=key_id,
        )
    except AcceptanceGateError as error:
        if str(error) == "acceptance gate failed":
            raise
        raise _gate_error() from None
    except Exception:
        raise _gate_error() from None


def _remote_cli_script() -> bytes:
    return br'''#!/bin/bash
set -euo pipefail
action="${1:-}"
shift || true
platform_root=/opt/orbbec-agent-platform
release_path="$(/usr/bin/readlink -f "$platform_root/current")"
environment_path="$platform_root/private/platform.env"
compose_path="$release_path/deploy/cloud/compose.yaml"
compose=(/usr/bin/docker compose --env-file "$environment_path" -f "$compose_path")
api_id="$("${compose[@]}" ps -q platform-api)"
[[ -n "$api_id" ]]
api_image="$(/usr/bin/docker inspect --format '{{.Image}}' "$api_id")"
secret_volume="$(/usr/bin/docker inspect "$api_id" | /usr/bin/python3 -c '
import json,sys
value=json.load(sys.stdin)
items=[m["Name"] for m in value[0]["Mounts"] if m.get("Type")=="volume" and m.get("Destination")=="/run/secrets"]
if len(items)!=1: raise SystemExit(1)
print(items[0])
')"
[[ -n "$api_image" && -n "$secret_volume" ]]
helper=(/usr/bin/docker run --rm --pull=never --network none --user 0:0 --entrypoint /bin/sh -v "$secret_volume:/secrets" "$api_image")
case "$action" in
  setup)
    [[ $# -eq 0 ]]
    "${helper[@]}" -ec '
      umask 077
      test ! -e /secrets/execution-relay-acceptance
      mkdir -m 700 /secrets/execution-relay-acceptance
      cp /secrets/control-database-url /secrets/execution-relay-acceptance/control-database-url
      cp /secrets/content-encryption-keyring /secrets/execution-relay-acceptance/content-keyring
      printf "AGENT_EXECUTION_RELAY_ACCEPTANCE_V1\n" > /secrets/execution-relay-acceptance/enabled
      chown -R 10001:10001 /secrets/execution-relay-acceptance
      chmod 700 /secrets/execution-relay-acceptance
      chmod 600 /secrets/execution-relay-acceptance/*
    '
    printf '{"status":"ready"}\n'
    ;;
  cleanup)
    [[ $# -eq 0 ]]
    "${helper[@]}" -ec '
      rm -f /secrets/execution-relay-acceptance/control-database-url /secrets/execution-relay-acceptance/content-keyring /secrets/execution-relay-acceptance/enabled
      if test -d /secrets/execution-relay-acceptance; then
        rmdir /secrets/execution-relay-acceptance
      fi
    '
    printf '{"status":"removed"}\n'
    ;;
  enqueue|inspect|interrupt)
    case "$action" in
      enqueue)
        [[ $# -eq 4 && "$1" =~ ^(hr-bot|marketing-intelligence-bot)$ ]]
        for value in "${@:2}"; do [[ "$value" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]; done
        ;;
      inspect|interrupt)
        [[ $# -eq 1 && "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
        ;;
    esac
    /usr/bin/docker exec --user 10001:10001 \
      -e PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ENABLED=1 \
      -e PLATFORM_EXECUTION_RELAY_ACCEPTANCE_ROOT=/run/secrets/execution-relay-acceptance \
      -e PLATFORM_EXECUTION_RELAY_ACCEPTANCE_MARKER_FILE=/run/secrets/execution-relay-acceptance/enabled \
      -e PLATFORM_CONTROL_DATABASE_URL_FILE=/run/secrets/execution-relay-acceptance/control-database-url \
      -e PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE=/run/secrets/execution-relay-acceptance/content-keyring \
      "$api_id" python -m app.execution_relay.acceptance_cli "$action" "$@"
    ;;
  *) exit 1 ;;
esac
'''


def _remote_action(
    config: AcceptanceConfig,
    runner: CommandRunner,
    action: str,
    *values: str,
) -> dict[str, object]:
    if action not in {"setup", "cleanup", "enqueue", "inspect", "interrupt"}:
        raise _gate_error()
    output = _require_command(
        runner,
        (
            "/usr/bin/ssh",
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={_CLOUD_KNOWN_HOSTS}",
            "-o", "ConnectTimeout=8",
            "-i", str(config.cloud_admin_key),
            config.cloud_admin_host,
            "/bin/bash -s --",
            action,
            *values,
        ),
        input_bytes=_remote_cli_script(),
        timeout=45,
    )
    try:
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except Exception:
        raise _gate_error() from None


def _validate_runtime_file(path: Path, *, executable: bool = False) -> None:
    try:
        metadata = path.lstat()
        if (
            not path.is_absolute()
            or not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_size > 1_048_576
            or (executable and not os.access(path, os.X_OK))
            or (not executable and stat.S_IMODE(metadata.st_mode) & 0o022 != 0)
        ):
            raise ValueError
    except Exception:
        raise _gate_error() from None


def _validate_runtime_python(path: Path) -> None:
    """Validate a venv interpreter without rejecting its standard symlink chain."""
    try:
        metadata = path.lstat()
        if not path.is_absolute():
            raise ValueError
        if stat.S_ISREG(metadata.st_mode):
            _validate_runtime_file(path, executable=True)
            return
        if not stat.S_ISLNK(metadata.st_mode) or metadata.st_uid not in {0, os.geteuid()}:
            raise ValueError
        for directory in (path.parent.parent, path.parent):
            directory_metadata = directory.lstat()
            if (
                not stat.S_ISDIR(directory_metadata.st_mode)
                or directory.is_symlink()
                or directory_metadata.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(directory_metadata.st_mode) & 0o022 != 0
            ):
                raise ValueError
        trusted_owner_uids = {0, os.geteuid()}
        sudo_uid = os.environ.get("SUDO_UID", "")
        if sudo_uid.isascii() and sudo_uid.isdigit():
            trusted_owner_uids.add(int(sudo_uid))
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.stat()
        if (
            not stat.S_ISREG(resolved_metadata.st_mode)
            or resolved_metadata.st_uid not in trusted_owner_uids
            or resolved_metadata.st_size > 1_048_576
            or stat.S_IMODE(resolved_metadata.st_mode) & 0o022 != 0
            or not os.access(resolved, os.X_OK)
        ):
            raise ValueError
    except AcceptanceGateError:
        raise
    except Exception:
        raise _gate_error() from None


def _default_process_factory(**values: object):
    environment = values.pop("environment")
    return subprocess.Popen(env=environment, **values)


def _default_kill_process(pid: int, selected_signal: signal.Signals) -> None:
    os.kill(pid, selected_signal)


def _default_local_state(dsn: str, run_id: UUID) -> LocalRunState:
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as connection:
        row = connection.execute(
            "select r.state,count(o.seq) event_count,min(o.seq) first_seq,max(o.seq) last_seq,"
            "count(o.seq) filter(where o.delivered_at is null) undelivered_count "
            "from execution_worker.local_runs r left join execution_worker.event_outbox o using(run_id) "
            "where r.run_id=%s group by r.state",
            (run_id,),
        ).fetchone()
    if row is None:
        raise _gate_error()
    return LocalRunState(**row)


def _default_local_events(dsn: str, run_id: UUID) -> tuple[RelayEvent, ...]:
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as connection:
        rows = connection.execute(
            "select run_id,seq,event_type,created_at,payload from execution_worker.event_outbox "
            "where run_id=%s order by seq", (run_id,)
        ).fetchall()
    return tuple(RelayEvent.model_validate(row) for row in rows)


def _default_duplicate_upload(
    run_id: UUID, events: tuple[RelayEvent, ...], key_id: str, private_key: bytes
) -> DuplicateUploadResult:
    path = f"/api/v1/execution-worker/runs/{run_id}/events"
    body = json.dumps(
        {"events": [event.model_dump(mode="json") for event in events]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    signer = WorkerRequestSigner(
        _WORKER_ID, key_id, Ed25519PrivateKey.from_private_bytes(private_key)
    )
    headers = {**signer.sign("POST", path, body), "Content-Type": "application/json"}
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.post("https://agent.orbbec.com.cn" + path, content=body, headers=headers)
    try:
        value = response.json()
        return DuplicateUploadResult(response.status_code, value["accepted"], value["inserted"])
    except Exception:
        raise _gate_error() from None


# Policy vocabulary: duplicate_reupload is the exact second upload and its
# response["inserted"] != 0 condition is a hard Gate 06 failure.


def _write_hook_control(directory: Path, dispatch_run: UUID, completion_run: UUID) -> Path:
    directory_fd: int | None = None
    try:
        os.mkdir(directory, 0o700)
        directory_fd = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        control = directory / "control.json"
        descriptor = os.open(
            control.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            value = json.dumps({
                "schema_version": 1,
                "dispatching_crash_run_id": str(dispatch_run),
                "completion_crash_run_id": str(completion_run),
            }, sort_keys=True, separators=(",", ":")).encode()
            os.write(descriptor, value)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        state_descriptor = os.open(
            "state.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            state = json.dumps({
                "schema_version": 1,
                "metabot_posts": {},
                "dispatch_pause_complete": False,
                "completion_pause_complete": False,
            }, sort_keys=True, separators=(",", ":")).encode()
            os.write(state_descriptor, state)
            os.fsync(state_descriptor)
        finally:
            os.close(state_descriptor)
        os.fsync(directory_fd)
        return control
    except Exception:
        raise _gate_error() from None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _wait_marker(path: Path, run_id: UUID, sleep: Callable[[float], None]) -> None:
    for _ in range(120):
        try:
            if _read_owner_file(path.parent, path).decode() == str(run_id):
                return
        except AcceptanceGateError:
            pass
        sleep(0.25)
    raise _gate_error()


def _inspect_terminal(
    config: AcceptanceConfig, runner: CommandRunner, run_id: UUID,
    *, ordered: bool, sleep: Callable[[float], None]
) -> dict[str, object]:
    for _ in range(240):
        result = _remote_action(config, runner, "inspect", str(run_id))
        if result.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            if ordered and result.get("ordered_terminal") is not True:
                raise _gate_error()
            return result
        sleep(0.5)
    raise _gate_error()


def _stop_child(process: Any, kill_process: Callable[[int, signal.Signals], None], selected: signal.Signals) -> None:
    if process is None or process.poll() is not None:
        return
    kill_process(process.pid, selected)
    try:
        process.wait(timeout=10)
    except Exception:
        if selected == signal.SIGKILL:
            raise _cleanup_error() from None
        try:
            kill_process(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        except Exception:
            raise _cleanup_error() from None


def _single_dispatch_post(state_path: Path, dispatch_run: UUID, completion_run: UUID) -> bool:
    try:
        state = json.loads(_read_owner_file(state_path.parent, state_path))
        posts = state.get("metabot_posts")
        return (
            isinstance(posts, dict)
            and set(posts).issubset({str(dispatch_run), str(completion_run)})
            and posts.get(str(dispatch_run)) == 1
            and all(isinstance(value, int) and not isinstance(value, bool) and value == 1 for value in posts.values())
        )
    except Exception:
        return False


def run_gates_04_to_08(
    config_path: Path,
    *,
    runner: CommandRunner = _run_command,
    process_factory: Callable[..., Any] = _default_process_factory,
    kill_process: Callable[[int, signal.Signals], None] = _default_kill_process,
    local_state_reader: Callable[[str, UUID], LocalRunState] = _default_local_state,
    local_events_reader: Callable[[str, UUID], tuple[RelayEvent, ...]] = _default_local_events,
    duplicate_uploader: Callable[[UUID, tuple[RelayEvent, ...], str, bytes], DuplicateUploadResult] = _default_duplicate_upload,
    sleep: Callable[[float], None] = time.sleep,
    uuid_factory: Callable[[], UUID] = uuid4,
    private_root: Path = Path("/Users/agentops/AgentRuntime/private"),
    worker_private_key_path: Path = Path("/Users/agentops/AgentRuntime/private/execution-worker-ed25519.key"),
    worker_public_document_path: Path = Path("/Users/agentops/AgentRuntime/execution-worker-public.json"),
    runtime_dsn_path: Path = Path("/Users/agentops/AgentRuntime/private/execution-worker-postgres-dsn"),
    hook_directory: Path = Path("/Users/agentops/AgentRuntime/private/execution-relay-acceptance"),
    backend_root: Path = Path("/Users/agentops/AgentRuntime/platform/backend"),
    worker_supervisor_path: Path = _WORKER_SUPERVISOR,
    metabot_contract_path: Path = Path("/Users/agentops/AgentRuntime/metabot/runtime-contract.json"),
    metabot_token_path: Path = Path("/Users/agentops/AgentRuntime/private/metabot-api-token"),
    current_user: str,
    uid: int,
) -> ExecutionGateResult:
    config = load_config(config_path, private_root=private_root)
    if current_user != "agentops" or not isinstance(uid, int) or isinstance(uid, bool):
        raise _gate_error()
    try:
        backend_metadata = backend_root.lstat()
        if (
            not backend_root.is_absolute()
            or not stat.S_ISDIR(backend_metadata.st_mode)
            or backend_root.is_symlink()
            or backend_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(backend_metadata.st_mode) & 0o022 != 0
        ):
            raise ValueError
    except Exception:
        raise _gate_error() from None
    python = backend_root / ".venv/bin/python"
    _validate_runtime_python(python)
    for path, executable in ((worker_supervisor_path, True), (metabot_contract_path, False)):
        _validate_runtime_file(path, executable=executable)
    worker_key = _read_owner_file(private_root, worker_private_key_path, maximum_size=32)
    worker_key_id, _worker_fingerprint = _local_identity(
        private_root, worker_private_key_path, worker_public_document_path
    )
    dsn = _read_owner_file(private_root, runtime_dsn_path, maximum_size=16_384).decode().strip()
    _read_owner_file(private_root, metabot_token_path, maximum_size=16_384)
    if len(worker_key) != 32 or not dsn:
        raise _gate_error()
    run_ids = tuple(uuid_factory() for _ in range(4))
    extras = tuple(uuid_factory() for _ in range(8))
    if len(set((*run_ids, *extras))) != 12:
        raise _gate_error()
    hr_run, intelligence_run, completion_run, dispatch_run = run_ids
    control: Path | None = None
    foreground: Any | None = None
    remote_ready = False
    setup_attempted = False
    worker_stopped = False
    cleanup_failed = False
    terminal: set[UUID] = set()

    def enqueue(index: int, agent: str, run_id: UUID) -> None:
        result = _remote_action(
            config, runner, "enqueue", agent, str(run_id),
            str(extras[index * 2]), str(extras[index * 2 + 1]),
        )
        if result != {"job_id": result.get("job_id"), "run_id": str(run_id), "status": "queued"}:
            raise _gate_error()

    def start_foreground() -> Any:
        environment = {
            "HOME": "/Users/agentops",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PLATFORM_WORKER_ID": _WORKER_ID,
            "PLATFORM_WORKER_KEY_ID": worker_key_id,
            "PLATFORM_WORKER_PRIVATE_KEY_FILE": str(worker_private_key_path),
            "PLATFORM_WORKER_DATABASE_URL_FILE": str(runtime_dsn_path),
            "PLATFORM_WORKER_CALLBACK_PORT": "9120",
            "PLATFORM_WORKER_CLOUD_URL": "https://agent.orbbec.com.cn",
            "PLATFORM_METABOT_RUNTIME_CONTRACT": str(metabot_contract_path),
            "PLATFORM_METABOT_API_SECRET_FILE": str(metabot_token_path),
            "PLATFORM_WORKER_ACCEPTANCE_HOOKS": "1",
            "PLATFORM_WORKER_ACCEPTANCE_CONTROL_FILE": str(control),
        }
        return process_factory(
            args=(str(python), "-m", "app.execution_relay.worker"),
            cwd=str(backend_root), environment=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    body_error: BaseException | None = None
    try:
        setup_attempted = True
        if _remote_action(config, runner, "setup") != {"status": "ready"}:
            raise _gate_error()
        remote_ready = True
        # Gate 04 and Gate 05: real HR and Marketing Intelligence terminal runs.
        enqueue(0, "hr-bot", hr_run)
        first = _inspect_terminal(config, runner, hr_run, ordered=True, sleep=sleep)
        if (
            first.get("agent_id") != "hr-bot"
            or first.get("status") != "completed"
            or not isinstance(first.get("event_count"), int)
            or isinstance(first.get("event_count"), bool)
            or first["event_count"] < 2
            or first.get("ordered_terminal") is not True
        ):
            raise _gate_error()
        terminal.add(hr_run)
        enqueue(1, "marketing-intelligence-bot", intelligence_run)
        second = _inspect_terminal(config, runner, intelligence_run, ordered=True, sleep=sleep)
        if (
            second.get("agent_id") != "marketing-intelligence-bot"
            or second.get("status") != "completed"
            or not isinstance(second.get("event_count"), int)
            or isinstance(second.get("event_count"), bool)
            or second["event_count"] < 2
            or second.get("ordered_terminal") is not True
        ):
            raise _gate_error()
        terminal.add(intelligence_run)
        # Gate 06: exact stored event replay must be accepted but insert zero rows.
        events = local_events_reader(dsn, hr_run)
        replay = duplicate_uploader(hr_run, events, worker_key_id, worker_key)
        if replay.status_code != 200 or replay.accepted != len(events) or replay.inserted != 0:
            raise _gate_error()
        _worker_pid(runner, worker_supervisor_path)
        worker_stopped = True
        _require_command(runner, (str(worker_supervisor_path), "stop"), timeout=20)
        control = _write_hook_control(hook_directory, dispatch_run, completion_run)
        foreground = start_foreground()
        # Gate 07: crash after local terminal persistence and resume the same outbox.
        enqueue(2, "hr-bot", completion_run)
        _wait_marker(hook_directory / "completion-paused", completion_run, sleep)
        before = local_state_reader(dsn, completion_run)
        if before.state != "completed" or before.event_count < 1 or before.undelivered_count != before.event_count:
            raise _gate_error()
        _stop_child(foreground, kill_process, signal.SIGKILL)
        foreground = start_foreground()
        completed = _inspect_terminal(config, runner, completion_run, ordered=True, sleep=sleep)
        if completed.get("status") != "completed":
            raise _gate_error()
        terminal.add(completion_run)
        after = local_state_reader(dsn, completion_run)
        if after.undelivered_count != 0 or (after.first_seq, after.last_seq) != (1, after.event_count):
            raise _gate_error()
        # Gate 08: crash after real MetaBot POST and never dispatch it twice.
        enqueue(3, "marketing-intelligence-bot", dispatch_run)
        _wait_marker(hook_directory / "dispatching-paused", dispatch_run, sleep)
        if not _single_dispatch_post(
            hook_directory / "state.json", dispatch_run, completion_run
        ):
            raise _gate_error()
        if local_state_reader(dsn, dispatch_run).state != "dispatching":
            raise _gate_error()
        _stop_child(foreground, kill_process, signal.SIGKILL)
        foreground = start_foreground()
        interrupted = _inspect_terminal(config, runner, dispatch_run, ordered=False, sleep=sleep)
        if interrupted.get("status") != "interrupted":
            raise _gate_error()
        terminal.add(dispatch_run)
        if local_state_reader(dsn, dispatch_run).state != "interrupted":
            raise _gate_error()
        if not _single_dispatch_post(
            hook_directory / "state.json", dispatch_run, completion_run
        ):
            raise _gate_error()
        result = ExecutionGateResult(hr_run, intelligence_run, completion_run, dispatch_run, 0)
    except BaseException as error:
        body_error = error
        result = None
    finally:
        try:
            _stop_child(foreground, kill_process, signal.SIGTERM)
        except Exception:
            cleanup_failed = True
        for run_id in run_ids:
            if remote_ready and run_id not in terminal:
                try:
                    _remote_action(config, runner, "interrupt", str(run_id))
                except Exception:
                    cleanup_failed = True
        if setup_attempted:
            try:
                if _remote_action(config, runner, "cleanup") != {"status": "removed"}:
                    cleanup_failed = True
            except Exception:
                cleanup_failed = True
        if worker_stopped:
            try:
                _require_command(
                    runner, (str(worker_supervisor_path), "restore", "online"), timeout=20
                )
            except Exception:
                cleanup_failed = True
        if hook_directory.exists():
            try:
                for name in ("control.json", "state.json", "completion-paused", "dispatching-paused"):
                    path = hook_directory / name
                    if path.exists() and not path.is_symlink():
                        path.unlink()
                hook_directory.rmdir()
            except Exception:
                cleanup_failed = True
    if cleanup_failed:
        raise _cleanup_error()
    if body_error is not None:
        if isinstance(body_error, AcceptanceGateError):
            raise body_error
        raise _gate_error() from None
    assert result is not None
    return result


def _final_remote_script() -> bytes:
    # Gate 09 uses only a disposable relay-acceptance-* worker and audited
    # register_worker register/revoke-worker maintenance commands. Gate 10
    # probes https://fae.orbbec.com.cn and the management replica before/after.
    return br'''#!/bin/bash
set -euo pipefail
action="$1"; shift
root=/opt/orbbec-agent-platform
release="$(readlink -f "$root/current")"
envfile="$root/private/platform.env"
compose=(/usr/bin/docker compose --env-file "$envfile" -f "$release/deploy/cloud/compose.yaml")
api_id="$("${compose[@]}" ps -q platform-api)"
postgres_id="$("${compose[@]}" ps -q platform-postgres)"
image="$(/usr/bin/docker inspect --format '{{.Config.Image}}' "$api_id")"
[[ -n "$api_id" && -n "$postgres_id" && -n "$image" ]]
case "$action" in
 regression-probe)
  fae_id="$(/usr/bin/docker inspect --format '{{.Id}}' ai-fae-backend)"
  fae_image="$(/usr/bin/docker inspect --format '{{.Image}}' ai-fae-backend)"
  fae_started="$(/usr/bin/docker inspect --format '{{.State.StartedAt}}' ai-fae-backend)"
  fae_health="$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' ai-fae-backend)"
  fae_hash="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve fae.orbbec.com.cn:443:127.0.0.1 https://fae.orbbec.com.cn/ | /usr/bin/sha256sum | /usr/bin/awk '{print $1}')"
  platform_health="$(/usr/bin/curl --noproxy '*' -fsS --max-time 8 --resolve agent.orbbec.com.cn:443:127.0.0.1 https://agent.orbbec.com.cn/api/health)"
  /usr/bin/python3 - "$platform_health" <<'PY'
import json,sys
value=json.loads(sys.argv[1])
if not isinstance(value,dict) or value.get("status") != "ok": raise SystemExit(1)
PY
  replica="$(/usr/bin/docker exec "$postgres_id" psql -X -A -t -U platform_owner -d agent_platform -v ON_ERROR_STOP=1 -c \
    "select jsonb_build_object(
      'freshness',case
        when (select max(committed_at) from platform_replica.generations) >= clock_timestamp() - interval '15 minutes' then 'current'
        when (select max(committed_at) from platform_replica.generations) is null then 'unavailable'
        else 'stale' end,
      'generations',coalesce((select jsonb_object_agg(source_instance_id,jsonb_build_object('last_sequence',last_sequence,'committed_at',committed_at)) from platform_replica.generations),'{}'::jsonb),
      'management_count',(select count(*) from platform_replica.management_projections),
      'management_max_updated_at',(select max(updated_at) from platform_replica.management_projections)
    )")"
  /usr/bin/python3 - "$fae_id" "$fae_image" "$fae_started" "$fae_health" "$fae_hash" "$replica" <<'PY'
import json,sys
a,b,c,d,e,raw=sys.argv[1:]
replica=json.loads(raw)
document={"schema_version":1,"fae_external_domain_healthy":d=="healthy","fae_container_id":a,"fae_image_id":b,"fae_started_at":c,"fae_health":d,"fae_https_sha256":e,"platform_health_healthy":True,"replica_freshness":replica["freshness"],"replica_generations":replica["generations"],"management_count":replica["management_count"],"management_max_updated_at":replica["management_max_updated_at"]}
sys.stdout.write(json.dumps(document,sort_keys=True,separators=(",",":"))+"\n")
PY
  ;;
 register-disposable)
  worker="$1"; key_id="$2"; public="$3"; agents="$4"; reference="$5"
  [[ "$worker" =~ ^relay-acceptance-[0-9a-f]{16}$ && "$key_id" == worker-v1 && "$public" =~ ^[A-Za-z0-9_-]{43}$ && "$agents" == hr-bot && "$reference" =~ ^RELAY_ACCEPT_REGISTER_[A-F0-9]{16}$ ]]
  registration_root="$root/private/execution-relay-$worker"
  document="$registration_root/worker.json"
  cleanup_registration() {
    [[ ! -L "$registration_root" ]] || return 1
    /bin/rm -f -- "$document"
    if [[ -d "$registration_root" ]]; then /bin/rmdir -- "$registration_root"; fi
  }
  cleanup_registration
  /usr/bin/install -d -o root -g root -m 700 "$registration_root"
  trap cleanup_registration EXIT HUP INT TERM
  /usr/bin/python3 - "$document" "$worker" "$public" <<'PY'
import json,os,sys
descriptor=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
with os.fdopen(descriptor,"w") as stream:
    json.dump({"worker_id":sys.argv[2],"key_id":"worker-v1","public_key_base64url":sys.argv[3],"allowed_agent_ids":["hr-bot"]},stream,separators=(",",":"),sort_keys=True)
    stream.flush(); os.fsync(stream.fileno())
PY
  /bin/chown root:root "$document"
  /bin/chmod 600 "$document"
  /usr/bin/docker run --rm --pull=never --network orbbec-agent-platform-internal --user 0:0 -v "$root/private:/run/control-secrets:ro" -v "$registration_root:/run/worker-registration:ro" -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url "$image" python -m app.execution_relay.register_worker register /run/worker-registration/worker.json "$reference" >/dev/null
  printf '{"status":"registered","worker_id":"%s"}\n' "$worker"
  ;;
 revoke-disposable)
  worker="$1"; reference="$2"; [[ "$worker" =~ ^relay-acceptance-[0-9a-f]{16}$ && "$reference" =~ ^RELAY_ACCEPT_REVOKE_[A-F0-9]{16}$ ]]
  /usr/bin/docker run --rm --pull=never --network orbbec-agent-platform-internal --user 0:0 -v "$root/private:/run/control-secrets:ro" -e PLATFORM_CONTROL_MAINTENANCE_DATABASE_URL_FILE=/run/control-secrets/control-maintenance-database-url "$image" python -m app.execution_relay.register_worker revoke-worker "$worker" "$reference" >/dev/null
  registration_root="$root/private/execution-relay-$worker"
  [[ ! -L "$registration_root" ]]
  /bin/rm -f -- "$registration_root/worker.json"
  if [[ -d "$registration_root" ]]; then /bin/rmdir -- "$registration_root"; fi
  printf '{"status":"revoked","worker_id":"%s"}\n' "$worker"
  ;;
 *) exec /bin/bash -s -- "$action" "$@" <<'NOOP'
exit 1
NOOP
esac
'''


def _final_remote_action(config: AcceptanceConfig, runner: CommandRunner, action: str, *values: str) -> dict[str, object]:
    output = _require_command(runner, (
        "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={_CLOUD_KNOWN_HOSTS}",
        "-o", "ConnectTimeout=8", "-i",
        str(config.cloud_admin_key), config.cloud_admin_host, "/bin/bash -s --", action, *values,
    ), input_bytes=_final_remote_script(), timeout=60)
    try:
        value = json.loads(output)
        if not isinstance(value, dict): raise ValueError
        return value
    except Exception:
        raise _gate_error() from None


def _default_signed_request(worker_id: str, key_id: str, private_key: bytes, method: str, path: str, body: bytes) -> SignedGateResponse:
    signer = WorkerRequestSigner(worker_id, key_id, Ed25519PrivateKey.from_private_bytes(private_key))
    headers = {**signer.sign(method, path, body), "Content-Type": "application/json"}
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.request(method, "https://agent.orbbec.com.cn" + path, content=body, headers=headers)
    try: value = response.json() if response.content else {}
    except Exception: value = {}
    return SignedGateResponse(response.status_code, value if isinstance(value, dict) else {})


def _default_session_probe(cookie: bytes) -> SessionProbeResult:
    headers = {"Cookie": cookie.decode()}
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, headers=headers) as client:
        sessions = client.get("https://agent.orbbec.com.cn" + _SESSION_LIST_PATH)
        if sessions.status_code != 200: return SessionProbeResult(sessions.status_code, 0)
        try: key = sessions.json()["items"][0]["session_key"]
        except Exception: return SessionProbeResult(200, 0)
        history = client.get("https://agent.orbbec.com.cn" + _SESSION_DETAIL_PREFIX + key)
    return SessionProbeResult(sessions.status_code, history.status_code)


def _regression_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise _gate_error()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except Exception:
        raise _gate_error() from None


def _parse_regression_snapshot(value: dict[str, object]) -> RegressionSnapshot:
    keys = {
        "schema_version",
        "fae_external_domain_healthy",
        "fae_container_id",
        "fae_image_id",
        "fae_started_at",
        "fae_health",
        "fae_https_sha256",
        "platform_health_healthy",
        "replica_freshness",
        "replica_generations",
        "management_count",
        "management_max_updated_at",
    }
    try:
        if (
            set(value) != keys
            or value["schema_version"] != 1
            or isinstance(value["schema_version"], bool)
        ):
            raise _gate_error()
        if (
            value["fae_external_domain_healthy"] is not True
            or value["platform_health_healthy"] is not True
            or value["fae_health"] != "healthy"
            or value["replica_freshness"] != "current"
        ):
            raise _gate_error()
        fae_id = value["fae_container_id"]
        fae_image = value["fae_image_id"]
        fae_started = value["fae_started_at"]
        fae_hash = value["fae_https_sha256"]
        if (
            not isinstance(fae_id, str)
            or _HEX_SHA256.fullmatch(fae_id) is None
            or not isinstance(fae_image, str)
            or not fae_image
            or len(fae_image) > 256
            or any(character in fae_image for character in "\r\n\0")
            or not isinstance(fae_started, str)
            or not isinstance(fae_hash, str)
            or _HEX_SHA256.fullmatch(fae_hash) is None
        ):
            raise _gate_error()
        _regression_timestamp(fae_started)
        generations = value["replica_generations"]
        if not isinstance(generations, dict) or not generations or len(generations) > 32:
            raise _gate_error()
        parsed_generations: list[tuple[str, ReplicaGeneration]] = []
        for source, generation in generations.items():
            if (
                not isinstance(source, str)
                or not source
                or len(source) > 128
                or not isinstance(generation, dict)
                or set(generation) != {"last_sequence", "committed_at"}
            ):
                raise _gate_error()
            sequence = generation["last_sequence"]
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
                raise _gate_error()
            parsed_generations.append((
                source,
                ReplicaGeneration(
                    last_sequence=sequence,
                    committed_at=_regression_timestamp(generation["committed_at"]),
                ),
            ))
        management_count = value["management_count"]
        if (
            not isinstance(management_count, int)
            or isinstance(management_count, bool)
            or management_count < 0
        ):
            raise _gate_error()
        management_updated = value["management_max_updated_at"]
        parsed_management_updated = (
            None if management_updated is None else _regression_timestamp(management_updated)
        )
        if (management_count == 0) != (parsed_management_updated is None):
            raise _gate_error()
        return RegressionSnapshot(
            fae_identity=(fae_id, fae_image, fae_started, "healthy", fae_hash),
            generations=tuple(sorted(parsed_generations)),
            management_count=management_count,
            management_max_updated_at=parsed_management_updated,
        )
    except AcceptanceGateError:
        raise
    except Exception:
        raise _gate_error() from None


def _require_monotonic_regression(
    before_value: dict[str, object], after_value: dict[str, object]
) -> RegressionSnapshot:
    before = _parse_regression_snapshot(before_value)
    after = _parse_regression_snapshot(after_value)
    if before.fae_identity != after.fae_identity:
        raise _gate_error()
    before_generations = dict(before.generations)
    after_generations = dict(after.generations)
    if not set(before_generations).issubset(after_generations):
        raise _gate_error()
    for source, earlier in before_generations.items():
        later = after_generations[source]
        if (
            later.last_sequence < earlier.last_sequence
            or later.committed_at < earlier.committed_at
        ):
            raise _gate_error()
    if after.management_count < before.management_count:
        raise _gate_error()
    if (
        before.management_max_updated_at is not None
        and (
            after.management_max_updated_at is None
            or after.management_max_updated_at < before.management_max_updated_at
        )
    ):
        raise _gate_error()
    # Management projections may legitimately advance during acceptance. Their
    # query success and bounded shape above prove the replica remains readable;
    # generation monotonicity proves management_replica_synchronization_unchanged
    # was not replaced by a stale or rolled-back source.
    return after


def _default_external_fae_probe() -> ExternalFaeProbeResult:
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = client.get("https://fae.orbbec.com.cn/")
    return ExternalFaeProbeResult(
        status_code=response.status_code,
        body_sha256=hashlib.sha256(response.content).hexdigest(),
    )


def run_gates_09_to_10(
    config_path: Path, *, runner: CommandRunner = _run_command,
    signed_requester: Callable[..., SignedGateResponse] = _default_signed_request,
    session_probe: Callable[[bytes], SessionProbeResult] = _default_session_probe,
    external_fae_probe: Callable[[], ExternalFaeProbeResult] = _default_external_fae_probe,
    token_factory: Callable[[], str] = lambda: os.urandom(8).hex(),
    disposable_key_factory: Callable[[], bytes] = lambda: os.urandom(32),
    uuid_factory: Callable[[], UUID] = uuid4,
    private_root: Path = Path("/Users/agentops/AgentRuntime/private"),
    session_cookie_path: Path = Path("/Users/agentops/AgentRuntime/private/acceptance-session-cookie"),
    worker_supervisor_path: Path = _WORKER_SUPERVISOR,
    current_user: str, uid: int,
) -> FinalGateResult:
    config = load_config(config_path, private_root=private_root)
    if current_user != "agentops" or not isinstance(uid, int) or isinstance(uid, bool): raise _gate_error()
    try:
        session_cookie_file = session_cookie_path
        cookie = _read_owner_file(private_root, session_cookie_file, maximum_size=4096)
        _validate_runtime_file(worker_supervisor_path, executable=True)
    except Exception:
        raise _gate_error() from None
    token = token_factory()
    key = disposable_key_factory()
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{16}", token) is None or not isinstance(key, bytes) or len(key) != 32: raise _gate_error()
    worker_id = "relay-acceptance-" + token
    public = Ed25519PrivateKey.from_private_bytes(key).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    encoded = base64.urlsafe_b64encode(public).decode().rstrip("=")
    run_id, conversation_id, message_id = (uuid_factory() for _ in range(3))
    disposable_registered = False
    registration_attempted = False
    revoked = False
    stopped = False
    setup = False
    setup_attempted = False
    enqueue_attempted = False
    terminal_proven = False
    cleanup_failed = False
    body_error = None
    before = _final_remote_action(config, runner, "regression-probe")
    lease_body = json.dumps(
        {"acceptance_run_id": str(run_id)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    def terminal_evidence() -> bool:
        evidence = _remote_action(config, runner, "inspect", str(run_id))
        return (
            evidence.get("run_id") == str(run_id)
            and evidence.get("agent_id") == "hr-bot"
            and evidence.get("status")
            in {"completed", "failed", "cancelled", "interrupted"}
        )

    def lease_is_target(
        response: SignedGateResponse, *, cancel_requested: bool
    ) -> bool:
        payload = response.json_body.get("payload")
        return (
            response.status_code == 200
            and isinstance(payload, dict)
            and payload.get("run_id") == str(run_id)
            and payload.get("conversation_id") == str(conversation_id)
            and payload.get("trigger_message_id") == str(message_id)
            and payload.get("agent_id") == "hr-bot"
            and payload.get("prompt") == f"relay acceptance synthetic run {run_id}"
            and response.json_body.get("cancel_requested") is cancel_requested
        )

    def prove_cleanup_terminal() -> bool:
        try:
            if terminal_evidence():
                return True
        except Exception:
            pass
        try:
            interrupted = _remote_action(config, runner, "interrupt", str(run_id))
            status = interrupted.get("status")
            if status == "cancel_requested":
                cleanup_lease = signed_requester(
                    worker_id, "worker-v1", key, "POST",
                    "/api/v1/execution-worker/lease", lease_body,
                )
                if not lease_is_target(cleanup_lease, cancel_requested=True):
                    return False
                cleanup_terminal = signed_requester(
                    worker_id, "worker-v1", key, "POST",
                    f"/api/v1/execution-worker/runs/{run_id}/terminal",
                    b'{"status":"cancelled"}',
                )
                if cleanup_terminal.status_code != 200 or cleanup_terminal.json_body != {"status": "accepted"}:
                    return False
            elif status != "interrupted":
                return False
            return terminal_evidence()
        except Exception:
            return False

    try:
        setup_attempted = True
        setup_result = _remote_action(config, runner, "setup")
        if setup_result != {"status": "ready"}:
            raise _gate_error()
        setup = True
        _worker_pid(runner, worker_supervisor_path)
        stopped = True
        _require_command(runner, (str(worker_supervisor_path), "stop"), timeout=20)
        reference = "RELAY_ACCEPT_REGISTER_" + token.upper()
        registration_attempted = True
        result = _final_remote_action(config, runner, "register-disposable", worker_id, "worker-v1", encoded, "hr-bot", reference)
        if result != {"status":"registered","worker_id":worker_id}: raise _gate_error()
        disposable_registered = True
        enqueue_attempted = True
        _remote_action(config, runner, "enqueue", "hr-bot", str(run_id), str(conversation_id), str(message_id))
        empty = b"{}"
        lease = signed_requester(worker_id, "worker-v1", key, "POST", "/api/v1/execution-worker/lease", lease_body)
        if not lease_is_target(lease, cancel_requested=False): raise _gate_error()
        event = RelayEvent(run_id=run_id, seq=1, event_type="run.interrupted", created_at=datetime.now(timezone.utc), payload={"status":"interrupted"})
        events_body = json.dumps({"events":[event.model_dump(mode="json")]}, sort_keys=True, separators=(",",":"), default=str).encode()
        for path, body in ((f"/api/v1/execution-worker/runs/{run_id}/dispatched", empty), (f"/api/v1/execution-worker/runs/{run_id}/events", events_body), (f"/api/v1/execution-worker/runs/{run_id}/terminal", b'{"status":"interrupted"}')):
            response = signed_requester(worker_id, "worker-v1", key, "POST", path, body)
            if response.status_code != 200: raise _gate_error()
        if not terminal_evidence():
            raise _gate_error()
        terminal_proven = True
        revoke_ref = "RELAY_ACCEPT_REVOKE_" + token.upper()
        result = _final_remote_action(config, runner, "revoke-disposable", worker_id, revoke_ref)
        if result != {"status":"revoked","worker_id":worker_id}: raise _gate_error()
        revoked = True
        lease_status = signed_requester(worker_id,"worker-v1",key,"POST","/api/v1/execution-worker/lease",lease_body).status_code
        upload_status = signed_requester(worker_id,"worker-v1",key,"POST",f"/api/v1/execution-worker/runs/{run_id}/events",events_body).status_code
        sessions = session_probe(cookie)
        after = _final_remote_action(config, runner, "regression-probe")
        if lease_status != 401 or upload_status != 401 or sessions.sessions_status != 200 or sessions.history_status != 200:
            raise _gate_error()
        regression = _require_monotonic_regression(before, after)
        external_fae = external_fae_probe()
        if (
            external_fae.status_code != 200
            or _HEX_SHA256.fullmatch(external_fae.body_sha256) is None
            or external_fae.body_sha256 != regression.fae_identity[4]
        ):
            raise _gate_error()
        result_final = FinalGateResult(worker_id, lease_status, upload_status, sessions.sessions_status, sessions.history_status)
    except BaseException as error:
        body_error = error; result_final = None
    finally:
        if registration_attempted and not revoked:
            if enqueue_attempted and not terminal_proven:
                terminal_proven = prove_cleanup_terminal()
                if not terminal_proven:
                    cleanup_failed = True
            try: _final_remote_action(config, runner, "revoke-disposable", worker_id, "RELAY_ACCEPT_REVOKE_" + token.upper())
            except Exception: cleanup_failed = True
        if setup_attempted:
            try: _remote_action(config, runner, "cleanup")
            except Exception: cleanup_failed = True
        if stopped:
            try:
                _require_command(
                    runner, (str(worker_supervisor_path), "restore", "online"), timeout=20
                )
            except Exception:
                cleanup_failed = True
    if cleanup_failed: raise _cleanup_error()
    if body_error is not None:
        if isinstance(body_error, AcceptanceGateError): raise body_error
        raise _gate_error() from None
    assert result_final is not None
    return result_final


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    try:
        if len(values) != 1 or not Path(values[0]).is_absolute():
            raise _gate_error()
        config = Path(values[0])
        user = os.environ.get("USER", "")
        uid = os.getuid()
        initial = run_gates_01_to_03(config, current_user=user, uid=uid)
        final = run_gates_09_to_10(config, current_user=user, uid=uid)
        execution = run_gates_04_to_08(config, current_user=user, uid=uid)
        final_boundary = run_gates_01_to_03(config, current_user=user, uid=uid)
        if (
            initial.worker_id != _WORKER_ID
            or initial.public_ports_added != 0
            or execution.duplicate_dispatches != 0
            or final.lease_status != 401
            or final.upload_status != 401
            or final.sessions_status != 200
            or final.history_status != 200
            or final_boundary.public_ports_added != 0
            or final_boundary.key_id != initial.key_id
            or final_boundary.registered_public_key_sha256
            != initial.registered_public_key_sha256
        ):
            raise _gate_error()
        return 0
    except Exception:
        print("AGENT_EXECUTION_RELAY_ACCEPTANCE_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
