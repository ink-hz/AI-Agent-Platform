from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .models import RemoteAgentStatus, RemoteOpsResult


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], float], Awaitable[CommandResult]]


def build_remote_ops_program() -> str:
    return r'''
import json
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone

units = ("ai-admin-agent", "ai-admin-job-worker", "ai-admin-dingtalk-bot")
with urllib.request.urlopen("http://127.0.0.1:8011/health", timeout=4) as response:
    admin_health = json.load(response)

unit_states = {}
for unit in units:
    result = subprocess.run(
        ["systemctl", "is-active", unit], capture_output=True, text=True, check=False
    )
    unit_states[unit] = result.stdout.strip() or "unknown"

host_uptime = float(open("/proc/uptime", encoding="utf-8").read().split()[0])
active_usec = subprocess.check_output(
    ["systemctl", "show", "ai-admin-agent", "-p", "ActiveEnterTimestampMonotonic", "--value"],
    text=True,
).strip()
admin_started_at = None
if active_usec.isdigit():
    elapsed = max(0.0, host_uptime - int(active_usec) / 1_000_000)
    admin_started_at = (datetime.now(timezone.utc) - timedelta(seconds=elapsed)).isoformat()

fae_started_at = subprocess.check_output(
    ["docker", "inspect", "--format", "{{.State.StartedAt}}", "ai-fae-backend"],
    text=True,
).strip()

print(json.dumps({
    "admin_health": admin_health,
    "units": unit_states,
    "admin_started_at": admin_started_at,
    "fae_started_at": fae_started_at,
}, ensure_ascii=False))
'''.strip()


def build_ssh_command(ssh_host: str, ssh_key_path: str) -> list[str]:
    encoded = base64.b64encode(build_remote_ops_program().encode()).decode()
    remote = f"python3 -c 'import base64;exec(base64.b64decode(\"{encoded}\"))'"
    return [
        "ssh", "-i", ssh_key_path,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        ssh_host,
        remote,
    ]


async def run_command(command: list[str], timeout: float) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return CommandResult(124, "", "")
    return CommandResult(
        process.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def probe_fae(
    client: httpx.AsyncClient,
    url: str,
    timeout: float,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> RemoteAgentStatus:
    checked_at = now()
    try:
        response = await client.get(url, timeout=timeout)
    except httpx.TimeoutException:
        return RemoteAgentStatus(
            id="ai-fae-agent", name="AI FAE Agent", status="unknown",
            checked_at=checked_at, error="timeout",
        )
    except Exception:
        return RemoteAgentStatus(
            id="ai-fae-agent", name="AI FAE Agent", status="unknown",
            checked_at=checked_at, error="transport",
        )
    if response.status_code != 200:
        return RemoteAgentStatus(
            id="ai-fae-agent", name="AI FAE Agent", status="degraded",
            checked_at=checked_at, error="http_status",
        )
    try:
        payload = response.json()
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return RemoteAgentStatus(
            id="ai-fae-agent", name="AI FAE Agent", status="degraded",
            checked_at=checked_at, error="invalid_response",
        )
    return RemoteAgentStatus(
        id="ai-fae-agent",
        name="AI FAE Agent",
        status="healthy" if payload.get("status") == "ok" else "degraded",
        checked_at=checked_at,
        error=None if payload.get("status") == "ok" else "service_status",
        details=payload,
    )


async def probe_remote_ops(
    ssh_host: str,
    ssh_key_path: str,
    *,
    runner: Runner = run_command,
    timeout: float = 12,
) -> tuple[RemoteOpsResult | None, str | None]:
    result = await runner(build_ssh_command(ssh_host, ssh_key_path), timeout)
    if result.returncode != 0:
        return None, "timeout" if result.returncode == 124 else "transport"
    try:
        payload = json.loads(result.stdout)
        return RemoteOpsResult.model_validate(payload), None
    except Exception:
        return None, "invalid_response"

