from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
CONTROL = ROOT / "deploy/local-execution-worker/agentops-control.sh"
ALLOWED = {
    "relay-canary",
    "worker-stop",
    "worker-restore",
    "metabot-release-sha",
    "agent-team-release-sha",
    "status",
}


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o700)


def _materialize_control(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "agentops"
    runtime = home / "AgentRuntime"
    relay = runtime / "platform/deploy/local-execution-worker/accept.sh"
    supervisor = runtime / "platform/deploy/local-execution-worker/worker-pm2.sh"
    fake_git = tmp_path / "git"
    fake_logger = tmp_path / "logger"
    relay_config = runtime / "private/acceptance-config.json"
    relay_config.parent.mkdir(parents=True, exist_ok=True)
    relay_config.write_text("{}\n", encoding="utf-8")
    relay_config.chmod(0o600)
    _write_executable(
        relay,
        'printf "relay:%s|leak=%s|home=%s|user=%s|logname=%s|pwd=%s\\n" '
        '"$1" "${LEAK_ME-unset}" "$HOME" "$USER" "$LOGNAME" "$PWD"',
    )
    _write_executable(supervisor, 'printf "worker:%s:%s\\n" "$1" "${2-}"')
    _write_executable(fake_git, 'printf "git:%s:%s:%s:%s\\n" "$1" "$2" "$3" "$4"')
    _write_executable(fake_logger, 'printf "logger:%s\\n" "$*" >/dev/null')

    target = tmp_path / "agentops-control"
    source = CONTROL.read_text(encoding="utf-8")
    replacements = {
        "required_user=agentops": f"required_user={os.environ['USER']}",
        "required_owner=root": f"required_owner={os.environ['USER']}",
        "required_mode=755": "required_mode=700",
        "required_home=/Users/agentops": f"required_home={home}",
        "runtime_root=/Users/agentops/AgentRuntime": f"runtime_root={runtime}",
        "agent_team_root=/Users/agentops/Developer/work/Orbbec-Agent-Team": (
            f"agent_team_root={tmp_path / 'agent-team'}"
        ),
        "installed_path=/Library/PrivilegedHelperTools/orbbec-agentops-control": (
            f"installed_path={target}"
        ),
        "git_bin=/usr/bin/git": f"git_bin={fake_git}",
        "logger_bin=/usr/bin/logger": f"logger_bin={fake_logger}",
    }
    for before, after in replacements.items():
        assert before in source
        source = source.replace(before, after)
    target.write_text(source, encoding="utf-8")
    target.chmod(0o700)
    return target, home


def _run(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["LEAK_ME"] = "must-not-cross"
    env["HOME"] = str(path.parent / "agentops")
    return subprocess.run(
        [str(path), *arguments],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_dispatcher_exposes_only_the_frozen_command_set() -> None:
    assert CONTROL.exists()
    source = CONTROL.read_text(encoding="utf-8")
    for command in ALLOWED:
        assert f"{command})" in source
    for forbidden in ("eval ", 'exec "$@"', "sudo -S", "find-generic-password"):
        assert forbidden not in source


def test_dispatcher_runs_only_fixed_commands_with_sanitized_environment(
    tmp_path: Path,
) -> None:
    dispatcher, home = _materialize_control(tmp_path)

    relay = _run(dispatcher, "relay-canary")
    assert relay.returncode == 0
    assert (
        relay.stdout.strip()
        == "relay:"
        f"{home}/AgentRuntime/private/acceptance-config.json"
        f"|leak=unset|home={home}|user={os.environ['USER']}"
        f"|logname={os.environ['USER']}|pwd={home}"
    )
    assert _run(dispatcher, "worker-stop").stdout.strip() == "worker:stop:"
    assert _run(dispatcher, "worker-restore").stdout.strip() == "worker:restore:online"
    assert _run(dispatcher, "metabot-release-sha").stdout.strip() == (
        f"git:-C:{home}/AgentRuntime/metabot:rev-parse:HEAD"
    )
    assert _run(dispatcher, "agent-team-release-sha").stdout.strip() == (
        f"git:-C:{tmp_path / 'agent-team'}:rev-parse:HEAD"
    )
    assert _run(dispatcher, "status").stdout.strip() == (
        "AGENTOPS_CONTROL_OK commands=6"
    )


def test_dispatcher_rejects_unknown_extra_symlink_and_wrong_mode(tmp_path: Path) -> None:
    dispatcher, _ = _materialize_control(tmp_path)
    assert _run(dispatcher, "unknown").returncode == 1
    assert _run(dispatcher, "status", "extra").returncode == 1

    symlink = tmp_path / "control-link"
    symlink.symlink_to(dispatcher)
    source = dispatcher.read_text(encoding="utf-8").replace(
        f"installed_path={dispatcher}", f"installed_path={symlink}"
    )
    dispatcher.write_text(source, encoding="utf-8")
    assert _run(symlink, "status").returncode == 1

    dispatcher.unlink()
    dispatcher, _ = _materialize_control(tmp_path)
    dispatcher.chmod(0o755)
    assert _run(dispatcher, "status").returncode == 1
