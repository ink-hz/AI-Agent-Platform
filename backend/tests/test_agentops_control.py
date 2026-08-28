from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).parents[2]
CONTROL = ROOT / "deploy/local-execution-worker/agentops-control.sh"
SUDOERS = ROOT / "deploy/local-execution-worker/agentops-control.sudoers"
INSTALL = ROOT / "deploy/local-execution-worker/install-agentops-control.sh"
REMOVE = ROOT / "deploy/local-execution-worker/remove-agentops-control.sh"
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


def test_sudoers_allows_only_exact_dispatcher_subcommands() -> None:
    assert SUDOERS.exists()
    lines = [line for line in SUDOERS.read_text(encoding="utf-8").splitlines() if line]
    assert lines == [
        "neo ALL=(agentops) NOPASSWD: "
        f"/Library/PrivilegedHelperTools/orbbec-agentops-control {command}"
        for command in (
            "relay-canary",
            "worker-stop",
            "worker-restore",
            "metabot-release-sha",
            "agent-team-release-sha",
            "status",
        )
    ]
    for forbidden in ("*", "/bin/sh", "/usr/bin/env", "ALL=(ALL)", "sudo -S"):
        assert forbidden not in SUDOERS.read_text(encoding="utf-8")


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _materialize_installation_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repository = tmp_path / "repository"
    local = repository / "deploy/local-execution-worker"
    local.mkdir(parents=True)
    helper_root = tmp_path / "helpers"
    dispatcher_target = helper_root / "orbbec-agentops-control"
    sudoers_target = tmp_path / "sudoers.d/orbbec-agentops-control"
    home = tmp_path / "agentops"
    runtime = home / "AgentRuntime"
    fake_visudo = tmp_path / "visudo"
    fake_sudo = tmp_path / "sudo"

    control_source = CONTROL.read_text(encoding="utf-8")
    replacements = {
        "required_user=agentops": f"required_user={os.environ['USER']}",
        "required_owner=root": f"required_owner={os.environ['USER']}",
        "required_home=/Users/agentops": f"required_home={home}",
        "runtime_root=/Users/agentops/AgentRuntime": f"runtime_root={runtime}",
        "agent_team_root=/Users/agentops/Developer/work/Orbbec-Agent-Team": (
            f"agent_team_root={tmp_path / 'agent-team'}"
        ),
        "installed_path=/Library/PrivilegedHelperTools/orbbec-agentops-control": (
            f"installed_path={dispatcher_target}"
        ),
    }
    for before, after in replacements.items():
        assert before in control_source
        control_source = control_source.replace(before, after)
    (local / CONTROL.name).write_text(control_source, encoding="utf-8")
    (local / CONTROL.name).chmod(0o755)
    shutil.copyfile(SUDOERS, local / SUDOERS.name)
    (local / SUDOERS.name).chmod(0o644)

    _write_executable(
        fake_visudo,
        'if [ "${FAIL_FULL_VISUDO-0}" = 1 ] && [ "$2" = "'
        + str(tmp_path / "sudoers")
        + '" ]; then exit 1; fi\nexit 0',
    )
    _write_executable(
        fake_sudo,
        'while [ "$#" -gt 0 ]; do case "$1" in -n|-H) shift;; -u) shift 2;; *) break;; esac; done\n'
        f'HOME={home} USER={os.environ["USER"]} LOGNAME={os.environ["USER"]} "$@"',
    )
    (tmp_path / "sudoers").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "sudoers.d").mkdir()
    helper_root.mkdir()

    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Tests")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "fixture")

    installer = tmp_path / "install-agentops-control"
    source = INSTALL.read_text(encoding="utf-8")
    install_replacements = {
        "required_uid=0": f"required_uid={os.getuid()}",
        "target_owner=root": f"target_owner={os.environ['USER']}",
        "target_group=wheel": f"target_group={subprocess.check_output(['/usr/bin/id', '-gn'], text=True).strip()}",
        'repository_root="$(cd "$(dirname "$0")/../.." && pwd)"': (
            f"repository_root={repository}"
        ),
        "agentops_user=agentops": f"agentops_user={os.environ['USER']}",
        "dispatcher_target=/Library/PrivilegedHelperTools/orbbec-agentops-control": (
            f"dispatcher_target={dispatcher_target}"
        ),
        "sudoers_target=/etc/sudoers.d/orbbec-agentops-control": (
            f"sudoers_target={sudoers_target}"
        ),
        "sudoers_root=/etc/sudoers": f"sudoers_root={tmp_path / 'sudoers'}",
        "visudo_bin=/usr/sbin/visudo": f"visudo_bin={fake_visudo}",
        "sudo_bin=/usr/bin/sudo": f"sudo_bin={fake_sudo}",
    }
    for before, after in install_replacements.items():
        assert before in source
        source = source.replace(before, after)
    installer.write_text(source, encoding="utf-8")
    installer.chmod(0o700)

    remover = tmp_path / "remove-agentops-control"
    source = REMOVE.read_text(encoding="utf-8")
    remove_replacements = {
        "required_uid=0": f"required_uid={os.getuid()}",
        "target_owner=root": f"target_owner={os.environ['USER']}",
        "target_group=wheel": f"target_group={subprocess.check_output(['/usr/bin/id', '-gn'], text=True).strip()}",
        "dispatcher_target=/Library/PrivilegedHelperTools/orbbec-agentops-control": (
            f"dispatcher_target={dispatcher_target}"
        ),
        "sudoers_target=/etc/sudoers.d/orbbec-agentops-control": (
            f"sudoers_target={sudoers_target}"
        ),
        "sudoers_root=/etc/sudoers": f"sudoers_root={tmp_path / 'sudoers'}",
        "visudo_bin=/usr/sbin/visudo": f"visudo_bin={fake_visudo}",
    }
    for before, after in remove_replacements.items():
        assert before in source
        source = source.replace(before, after)
    remover.write_text(source, encoding="utf-8")
    remover.chmod(0o700)
    return installer, remover, dispatcher_target, sudoers_target


def test_installer_is_idempotent_and_uninstaller_is_exact(tmp_path: Path) -> None:
    installer, remover, dispatcher, sudoers = _materialize_installation_fixture(
        tmp_path
    )
    first = _run(installer)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "AGENTOPS_CONTROL_INSTALL_OK"
    assert dispatcher.stat().st_mode & 0o777 == 0o755
    assert sudoers.stat().st_mode & 0o777 == 0o440

    second = _run(installer)
    assert second.returncode == 0, second.stderr
    assert dispatcher.exists() and sudoers.exists()

    removed = _run(remover)
    assert removed.returncode == 0, removed.stderr
    assert removed.stdout.strip() == "AGENTOPS_CONTROL_REMOVE_OK"
    assert not dispatcher.exists() and not sudoers.exists()


def test_installer_rolls_back_both_targets_when_global_visudo_fails(
    tmp_path: Path,
) -> None:
    installer, _, dispatcher, sudoers = _materialize_installation_fixture(tmp_path)
    dispatcher.write_text("old-dispatcher\n", encoding="utf-8")
    dispatcher.chmod(0o755)
    sudoers.write_text("old-sudoers\n", encoding="utf-8")
    sudoers.chmod(0o440)
    before_dispatcher = dispatcher.read_bytes()
    before_sudoers = sudoers.read_bytes()

    env = os.environ.copy()
    env["FAIL_FULL_VISUDO"] = "1"
    failed = subprocess.run(
        [str(installer)], text=True, capture_output=True, env=env, check=False
    )
    assert failed.returncode == 1
    assert dispatcher.read_bytes() == before_dispatcher
    assert sudoers.read_bytes() == before_sudoers
