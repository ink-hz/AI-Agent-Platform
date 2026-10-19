"""The cloud release keeps SDK activation state without exposing its password."""

import os
from pathlib import Path
import subprocess
import sys
import importlib.util
import json

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "deploy" / "sdk" / "cloud_env.py"
STAGE = ROOT / "deploy" / "cloud" / "remote-stage.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _private_file(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def test_missing_previous_env_defaults_to_disabled(tmp_path: Path) -> None:
    result = _run("render", str(tmp_path / "missing.env"))
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "PLATFORM_SDK_WORKBENCH_ENABLED=0",
        "PLATFORM_SDK_ZENTAO_BASE_URL=",
        "PLATFORM_SDK_ZENTAO_ACCOUNT=",
        "PLATFORM_SDK_ZENTAO_PRODUCT_ID=0",
        "PLATFORM_SDK_ZENTAO_EXECUTION_ID=0",
    ]


def test_apply_then_render_preserves_enabled_sdk_values_and_other_settings(tmp_path: Path) -> None:
    current = tmp_path / "platform.env"
    settings = tmp_path / "sdk.env"
    _private_file(current, "PLATFORM_IMAGE=platform:old\nPLATFORM_CLOUD_AUTH_MODE=dingtalk\n")
    _private_file(settings, "\n".join([
        "PLATFORM_SDK_WORKBENCH_ENABLED=1",
        "PLATFORM_SDK_ZENTAO_BASE_URL=http://zentao:80",
        "PLATFORM_SDK_ZENTAO_ACCOUNT=sdk-api",
        "PLATFORM_SDK_ZENTAO_PRODUCT_ID=21",
        "PLATFORM_SDK_ZENTAO_EXECUTION_ID=34",
        "",
    ]))

    applied = _run("apply", str(current), str(settings))
    assert applied.returncode == 0, applied.stderr
    assert current.stat().st_mode & 0o777 == 0o600
    assert current.stat().st_uid == os.getuid()
    assert current.read_text().startswith("PLATFORM_IMAGE=platform:old\nPLATFORM_CLOUD_AUTH_MODE=dingtalk\n")
    rendered = _run("render", str(current))
    assert rendered.returncode == 0, rendered.stderr
    assert rendered.stdout == settings.read_text()
    assert _run("apply", str(current), str(settings)).returncode == 0
    assert current.read_text().count("PLATFORM_SDK_WORKBENCH_ENABLED=") == 1
    assert not (tmp_path / "agent-brain-action.lock").exists()


def test_apply_refuses_while_release_owns_shared_lock(tmp_path: Path) -> None:
    current = tmp_path / "platform.env"
    settings = tmp_path / "sdk.env"
    _private_file(current, "PLATFORM_IMAGE=platform:old\n")
    _private_file(settings, "PLATFORM_SDK_WORKBENCH_ENABLED=0\nPLATFORM_SDK_ZENTAO_BASE_URL=\nPLATFORM_SDK_ZENTAO_ACCOUNT=\nPLATFORM_SDK_ZENTAO_PRODUCT_ID=0\nPLATFORM_SDK_ZENTAO_EXECUTION_ID=0\n")
    lock = tmp_path / "agent-brain-action.lock"
    lock.mkdir(mode=0o700)
    _private_file(lock / "owner", "11111111-1111-4111-8111-111111111111\n")
    result = _run("apply", str(current), str(settings))
    assert result.returncode == 1
    assert current.read_text() == "PLATFORM_IMAGE=platform:old\n"
    assert (lock / "owner").exists()


def test_invalid_or_duplicate_sdk_values_are_rejected(tmp_path: Path) -> None:
    current = tmp_path / "platform.env"
    settings = tmp_path / "sdk.env"
    _private_file(current, "PLATFORM_IMAGE=platform:old\n")
    _private_file(settings, "PLATFORM_SDK_WORKBENCH_ENABLED=1\nPLATFORM_SDK_ZENTAO_BASE_URL=http://platform-postgres:5432\nPLATFORM_SDK_ZENTAO_ACCOUNT=sdk-api\nPLATFORM_SDK_ZENTAO_PRODUCT_ID=1\nPLATFORM_SDK_ZENTAO_EXECUTION_ID=2\n")
    assert _run("apply", str(current), str(settings)).returncode != 0
    assert current.read_text() == "PLATFORM_IMAGE=platform:old\n"
    _private_file(settings, "PLATFORM_SDK_WORKBENCH_ENABLED=0\nPLATFORM_SDK_WORKBENCH_ENABLED=1\n")
    assert _run("apply", str(current), str(settings)).returncode != 0
    settings.chmod(0o644)
    assert _run("apply", str(current), str(settings)).returncode != 0


def test_release_render_rejects_bad_existing_sdk_state(tmp_path: Path) -> None:
    current = tmp_path / "platform.env"
    _private_file(current, "PLATFORM_IMAGE=platform:old\nPLATFORM_SDK_WORKBENCH_ENABLED=1\n")
    assert _run("render", str(current)).returncode != 0
    _private_file(current, "PLATFORM_SDK_WORKBENCH_ENABLED=0\nPLATFORM_SDK_WORKBENCH_ENABLED=0\n")
    assert _run("render", str(current)).returncode != 0


def test_activation_restores_previous_configuration_if_api_recreation_fails(tmp_path: Path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("sdk_cloud_env_trial", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    current = tmp_path / "platform.env"
    settings = tmp_path / "sdk.env"
    compose = tmp_path / "compose.yaml"
    original = "PLATFORM_IMAGE=platform:old\n"
    _private_file(current, original)
    _private_file(settings, "PLATFORM_SDK_WORKBENCH_ENABLED=0\nPLATFORM_SDK_ZENTAO_BASE_URL=\nPLATFORM_SDK_ZENTAO_ACCOUNT=\nPLATFORM_SDK_ZENTAO_PRODUCT_ID=0\nPLATFORM_SDK_ZENTAO_EXECUTION_ID=0\n")
    compose.write_text("services: {}\n", encoding="utf-8")
    seen = []

    def recreate(environment: Path, _compose: Path) -> None:
        seen.append(environment.read_text(encoding="utf-8"))
        if len(seen) == 1:
            raise ValueError("trial_api_failed")

    monkeypatch.setattr(module, "_recreate_api", recreate)
    with pytest.raises(ValueError, match="sdk_activation_failed"):
        module._activate_locked(current, settings, compose)
    assert len(seen) == 2
    assert "PLATFORM_SDK_WORKBENCH_ENABLED=0" in seen[0]
    assert seen[1] == original
    assert current.read_text(encoding="utf-8") == original


def test_activation_restores_configuration_after_partial_write_or_interrupt(tmp_path: Path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("sdk_cloud_env_failure_trial", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    current = tmp_path / "platform.env"
    settings = tmp_path / "sdk.env"
    compose = tmp_path / "compose.yaml"
    original = "PLATFORM_IMAGE=platform:old\n"
    _private_file(current, original)
    _private_file(settings, "PLATFORM_SDK_WORKBENCH_ENABLED=0\nPLATFORM_SDK_ZENTAO_BASE_URL=\nPLATFORM_SDK_ZENTAO_ACCOUNT=\nPLATFORM_SDK_ZENTAO_PRODUCT_ID=0\nPLATFORM_SDK_ZENTAO_EXECUTION_ID=0\n")
    compose.write_text("services: {}\n", encoding="utf-8")
    write = module._write_private
    writes = 0

    def partial_write(path: Path, content: str) -> None:
        nonlocal writes
        writes += 1
        write(path, content)
        if writes == 1:
            raise OSError("directory_fsync_failed")

    monkeypatch.setattr(module, "_write_private", partial_write)
    with pytest.raises(ValueError, match="sdk_activation_failed"):
        module._activate_locked(current, settings, compose)
    assert current.read_text(encoding="utf-8") == original

    monkeypatch.setattr(module, "_write_private", write)
    recreations = 0

    def interrupted(_environment: Path, _compose: Path) -> None:
        nonlocal recreations
        recreations += 1
        if recreations == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(module, "_recreate_api", interrupted)
    with pytest.raises(KeyboardInterrupt):
        module._activate_locked(current, settings, compose)
    assert recreations == 2
    assert current.read_text(encoding="utf-8") == original


def test_compose_recreation_ignores_exported_sdk_variables_and_inspects_container(tmp_path: Path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("sdk_cloud_env_compose_trial", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    current = tmp_path / "platform.env"
    _private_file(current, "PLATFORM_IMAGE=platform:old\n")
    monkeypatch.setenv("PLATFORM_SDK_WORKBENCH_ENABLED", "1")
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        if "config" in command:
            values = dict(zip(module.FIELDS, module.DEFAULTS))
            return subprocess.CompletedProcess(command, 0, json.dumps({"services": {"platform-api": {"environment": values}}}).encode())
        if "ps" in command:
            return subprocess.CompletedProcess(command, 0, b"abc123\n")
        if "inspect" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps([f"{name}={value}" for name, value in zip(module.FIELDS, module.DEFAULTS)]).encode())
        return subprocess.CompletedProcess(command, 0, b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    module._recreate_api(current, tmp_path / "compose.yaml")
    compose_calls = [options for command, options in commands if "compose" in command]
    assert compose_calls and all("PLATFORM_SDK_WORKBENCH_ENABLED" not in options["env"] for options in compose_calls)
    assert sum("curl" in command[0] for command, _options in commands) == 3


def test_release_stage_renders_sdk_state_before_replacing_platform_env() -> None:
    stage = STAGE.read_text(encoding="utf-8")
    render = stage.index('cloud_env.py" render "$environment_path"')
    replace = stage.index(' > "$environment_path"')
    append = stage.index('"$sdk_environment" >> "$environment_path"')
    assert render < replace < append
    assert stage.index("unset PLATFORM_SDK_WORKBENCH_ENABLED") < stage.index("previous_compose=(")
