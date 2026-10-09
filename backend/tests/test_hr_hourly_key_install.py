from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "deploy/cloud/install-hr-position-sync-key.py"
SPEC = importlib.util.spec_from_file_location("hr_key_installer", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_installs_only_one_forced_command_without_rewriting_existing_keys(tmp_path) -> None:
    authorized = tmp_path / "authorized_keys"
    authorized.write_text("ssh-ed25519 OTHER existing\n")
    authorized.chmod(0o600)
    key = "ssh-ed25519 " + "A" * 68 + " orbbec-hr-position-sync"
    assert MODULE.install(key, authorized, expected_uid=os.getuid()) is True
    installed = authorized.read_text()
    assert installed.startswith("ssh-ed25519 OTHER existing\n")
    assert 'command="/opt/orbbec-agent-platform/current/deploy/cloud/forced-hr-position-import.sh"' in installed
    assert MODULE.install(key, authorized, expected_uid=os.getuid()) is False
    assert authorized.read_text() == installed
    authorized.write_text(installed.replace(
        'command="/opt/orbbec-agent-platform/current/deploy/cloud/forced-hr-position-import.sh"',
        'command="/bin/false"',
    ))
    with pytest.raises(ValueError, match="different command"):
        MODULE.install(key, authorized, expected_uid=os.getuid())
