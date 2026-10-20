#!/usr/bin/env python3
"""Validate and preserve non-secret SDK settings in the cloud Compose env file."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


FIELDS = (
    "PLATFORM_SDK_WORKBENCH_ENABLED",
    "PLATFORM_SDK_ZENTAO_BASE_URL",
    "PLATFORM_SDK_ZENTAO_ACCOUNT",
    "PLATFORM_SDK_ZENTAO_PRODUCT_ID",
    "PLATFORM_SDK_ZENTAO_EXECUTION_ID",
    "PLATFORM_SDK_PLAN_ENABLED",
    "PLATFORM_SDK_NATIVE_ONLY",
)
DEFAULTS = ("0", "", "", "0", "0", "0", "0")
ACCOUNT = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")
POSITIVE_ID = re.compile(r"[1-9][0-9]{0,9}\Z")


def _private_text(path: Path, *, optional: bool = False) -> str | None:
    if not path.is_absolute():
        raise ValueError("path")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if optional:
            return None
        raise ValueError("missing") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or metadata.st_size > 65_536
    ):
        raise ValueError("private_file")
    return path.read_text(encoding="utf-8")


def _settings(text: str, *, complete: bool) -> tuple[str, ...]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("PLATFORM_SDK_"):
            continue
        if "=" not in line:
            raise ValueError("syntax")
        name, value = line.split("=", 1)
        if name not in FIELDS or name in found:
            raise ValueError("field")
        found[name] = value
    if complete and not set(FIELDS[:5]).issubset(found):
        raise ValueError("missing_field")
    values = tuple(found.get(name, fallback) for name, fallback in zip(FIELDS, DEFAULTS))
    enabled, base_url, account, product_id, execution_id, plan_enabled, native_only = values
    if enabled not in {"0", "1"}:
        raise ValueError("flag")
    if plan_enabled not in {"0", "1"}:
        raise ValueError("plan_flag")
    if native_only not in {"0", "1"} or (native_only == "1" and enabled != "1"):
        raise ValueError("native_only_flag")
    if enabled == "0":
        if values[:5] != DEFAULTS[:5]:
            raise ValueError("disabled_values")
    elif (
        base_url != "http://zentao:80"
        or ACCOUNT.fullmatch(account) is None
        or POSITIVE_ID.fullmatch(product_id) is None
        or POSITIVE_ID.fullmatch(execution_id) is None
        or int(product_id) > 2_147_483_647
        or int(execution_id) > 2_147_483_647
    ):
        raise ValueError("enabled_values")
    return values


def _render(values: tuple[str, ...]) -> str:
    return "".join(f"{name}={value}\n" for name, value in zip(FIELDS, values))


def _apply(current: Path, settings: Path, compose: Path | None = None) -> None:
    # deploy.sh owns this directory for its entire release. mkdir makes the
    # manual SDK switch mutually exclusive with release staging and cutover.
    lock = current.parent / "agent-brain-action.lock"
    token = str(uuid4())
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("deployment_busy") from None
    try:
        owner = lock / "owner"
        descriptor = os.open(owner, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(token + "\n")
        if compose is not None:
            if current != Path("/opt/orbbec-agent-platform/private/platform.env") or compose != Path("/opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml"):
                raise ValueError("activation_path")
            if not compose.is_file():
                raise ValueError("compose_missing")
            _activate_locked(current, settings, compose)
        else:
            _apply_locked(current, settings)
    finally:
        if lock.is_dir() and not lock.is_symlink() and owner.is_file() and owner.read_text(encoding="ascii") == token + "\n":
            tombstone = current.parent / f"agent-brain-action.lock.releasing.{token}"
            lock.rename(tombstone)
            (tombstone / "owner").unlink()
            tombstone.rmdir()
        elif lock.is_dir() and not lock.is_symlink() and not owner.exists():
            lock.rmdir()


def _apply_locked(current: Path, settings: Path) -> None:
    previous = _private_text(current)
    proposed = _private_text(settings)
    assert previous is not None and proposed is not None
    if any(line and not line.startswith("PLATFORM_SDK_") for line in proposed.splitlines()):
        raise ValueError("extra_field")
    previous_values = _settings(previous, complete=False)
    values = _settings(proposed, complete=True)
    values = list(values)
    if "PLATFORM_SDK_PLAN_ENABLED=" not in proposed:
        values[5] = previous_values[5]
    if "PLATFORM_SDK_NATIVE_ONLY=" not in proposed:
        values[6] = previous_values[6]
    if values[0] == "0":
        values[6] = "0"
    values = tuple(values)
    retained = []
    for line in previous.splitlines():
        if not line.startswith("PLATFORM_SDK_"):
            retained.append(line)
        elif line.split("=", 1)[0] not in FIELDS:
            raise ValueError("unknown_field")
    payload = "\n".join(retained).rstrip("\n") + "\n" + _render(values)
    _write_private(current, payload)


def _activate_locked(current: Path, settings: Path, compose: Path) -> None:
    previous = _private_text(current)
    assert previous is not None
    recreate_started = False
    try:
        _apply_locked(current, settings)
        recreate_started = True
        _recreate_api(current, compose)
    except BaseException as error:
        try:
            changed = _private_text(current) != previous
        except (OSError, UnicodeError, ValueError):
            changed = True
        if not changed and not recreate_started:
            raise
        try:
            if changed:
                _write_private(current, previous)
            if recreate_started:
                _recreate_api(current, compose)
        except BaseException:
            raise ValueError("sdk_activation_and_rollback_failed") from error
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise ValueError("sdk_activation_failed") from error


def _write_private(current: Path, payload: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=".platform-sdk-env-", dir=current.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, current)
        directory = os.open(current.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _recreate_api(current: Path, compose: Path) -> None:
    expected = dict(zip(FIELDS, _settings(_private_text(current) or "", complete=False)))
    if expected["PLATFORM_SDK_WORKBENCH_ENABLED"] == "1":
        subprocess.run([
            "/usr/bin/docker", "run", "--rm", "--network", "none",
            "-v", "orbbec-agent-platform-sdk-secrets:/run/sdk-secrets:ro", "alpine:3.22",
            "sh", "-ceu",
            'file=/run/sdk-secrets/password; test -f "$file" && test ! -L "$file" && test -s "$file" && test "$(stat -c "%u:%g:%a" "$file")" = "10001:10001:600"',
        ], check=True, timeout=30, capture_output=True)
    command = ["/usr/bin/docker", "compose", "--project-name", "orbbec-agent-platform", "--env-file", str(current), "-f", str(compose)]
    compose_env = {name: value for name, value in os.environ.items() if not name.startswith("PLATFORM_SDK_")}
    rendered = subprocess.run([*command, "config", "--format", "json"], check=True, timeout=30, capture_output=True, env=compose_env)
    actual = json.loads(rendered.stdout)["services"]["platform-api"]["environment"]
    if any(str(actual.get(name)) != value for name, value in expected.items()):
        raise ValueError("sdk_compose_mismatch")
    subprocess.run([*command, "up", "-d", "--force-recreate", "--no-deps", "platform-api"], check=True, timeout=180, capture_output=True, env=compose_env)
    container = subprocess.run([*command, "ps", "-q", "platform-api"], check=True, timeout=30, capture_output=True, env=compose_env)
    container_id = container.stdout.decode("ascii").strip()
    if not container_id or "\n" in container_id:
        raise ValueError("sdk_container_missing")
    inspection = subprocess.run(["/usr/bin/docker", "inspect", "--format", "{{json .Config.Env}}", container_id], check=True, timeout=30, capture_output=True)
    values = dict(entry.split("=", 1) for entry in json.loads(inspection.stdout) if "=" in entry)
    if any(values.get(name) != value for name, value in expected.items()):
        raise ValueError("sdk_container_mismatch")
    healthy_streak = 0
    for _ in range(30):
        health = subprocess.run(
            ["/usr/bin/curl", "--silent", "--fail", "--max-time", "2", "http://127.0.0.1:8080/api/health"],
            check=False, timeout=5, capture_output=True,
        )
        if health.returncode == 0:
            healthy_streak += 1
            if healthy_streak >= 3:
                return
        else:
            healthy_streak = 0
        time.sleep(2)
    raise ValueError("api_unhealthy")


def main(args: list[str]) -> int:
    try:
        if len(args) == 2 and args[0] == "render":
            previous = _private_text(Path(args[1]), optional=True)
            sys.stdout.write(_render(_settings(previous or "", complete=False)))
        elif len(args) == 3 and args[0] == "apply":
            _apply(Path(args[1]), Path(args[2]))
        elif len(args) == 4 and args[0] == "activate":
            _apply(Path(args[1]), Path(args[2]), Path(args[3]))
        else:
            raise ValueError("usage")
    except (OSError, UnicodeError, ValueError):
        print("SDK_CLOUD_ENV_INVALID", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
