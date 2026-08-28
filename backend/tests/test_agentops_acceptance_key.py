from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
PROVISION = ROOT / "deploy/cloud/provision-agentops-acceptance-key.sh"
REVOKE = ROOT / "deploy/cloud/revoke-agentops-acceptance-key.sh"
INSTALL = ROOT / "deploy/local-execution-worker/install-agentops-control.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(0o700)


def test_key_policy_is_dedicated_restricted_and_atomic() -> None:
    assert PROVISION.exists() and REVOKE.exists()
    source = PROVISION.read_text(encoding="utf-8")
    for required in (
        "ssh_keygen_bin=/usr/bin/ssh-keygen",
        "-q -t ed25519",
        'source_ip="${SSH_CONNECTION%% *}"',
            'restrict,from=\\\"$source_ip\\\"',
        "BEGIN ORBBEC AGENTOPS ACCEPTANCE KEY",
        "END ORBBEC AGENTOPS ACCEPTANCE KEY",
        "cloud-admin-ed25519.pending",
        "StrictHostKeyChecking=yes",
        "IdentitiesOnly=yes",
        "BatchMode=yes",
        "AGENTOPS_ACCEPTANCE_KEY_STAGED_OK",
    ):
        assert required in source
    assert "ssh-rsa" not in source
    assert "sudo -S" not in source

    revoke = REVOKE.read_text(encoding="utf-8")
    assert "BEGIN ORBBEC AGENTOPS ACCEPTANCE KEY" in revoke
    assert "END ORBBEC AGENTOPS ACCEPTANCE KEY" in revoke
    assert "AGENTOPS_ACCEPTANCE_KEY_REVOKED_OK" in revoke


def test_key_provision_has_remote_prepare_commit_and_rollback() -> None:
    source = PROVISION.read_text(encoding="utf-8")
    for required in (
        "transaction_token=",
        "remote_transaction_active=1",
        "prepare_remote_transaction",
        "commit_remote_transaction",
        "rollback_remote_transaction",
        "authorized_keys.backup",
    ):
        assert required in source
    assert source.index("prepare_remote_transaction") < source.index(
        "commit_remote_transaction"
    )
    assert '"$transaction_token" "$public_blob" "$fingerprint"' in source
    assert '"$transaction_token" "$public_line" "$fingerprint"' not in source
    assert 'public_line="ssh-ed25519 $public_blob orbbec-agentops-acceptance"' in source


def test_root_installer_consumes_pending_key_without_copying_neos_key() -> None:
    source = INSTALL.read_text(encoding="utf-8")
    assert "cloud-admin-ed25519.pending" in source
    assert "agentops_private=/Users/agentops/AgentRuntime/private" in source
    assert 'cloud_key_target="$agentops_private/cloud-admin-ed25519"' in source
    assert '$ssh_keygen_bin -y' in source
    assert "orbbec_aliyun_ed25519" not in source


def test_provision_stages_real_ed25519_key_and_private_config(tmp_path: Path) -> None:
    state = tmp_path / "state"
    cloud_key = tmp_path / "bootstrap-ed25519"
    cloud_key.write_text("test-bootstrap-key\n", encoding="utf-8")
    cloud_key.chmod(0o600)
    fake_ssh = tmp_path / "ssh"
    calls = tmp_path / "ssh-calls"
    _write_executable(
        fake_ssh,
        f'printf "%s\\n" "$*" >> {str(calls)!r}\n'
        "/bin/cat >/dev/null || true\n"
        "exit 0",
    )

    script = tmp_path / "provision"
    source = PROVISION.read_text(encoding="utf-8")
    replacements = {
        "required_user=neo": f"required_user={os.environ['USER']}",
        "state_root=/Users/neo/.orbbec-agent-platform/agentops-control": (
            f"state_root={state}"
        ),
        "cloud_admin_key=/Users/neo/.ssh/orbbec_aliyun_ed25519": (
            f"cloud_admin_key={cloud_key}"
        ),
        "ssh_bin=/usr/bin/ssh": f"ssh_bin={fake_ssh}",
    }
    for before, after in replacements.items():
        assert before in source
        source = source.replace(before, after)
    script.write_text(source, encoding="utf-8")
    script.chmod(0o700)

    result = subprocess.run(
        [str(script)], text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AGENTOPS_ACCEPTANCE_KEY_STAGED_OK"
    private = state / "cloud-admin-ed25519.pending"
    public = state / "cloud-admin-ed25519.pending.pub"
    config = state / "acceptance-config.pending.json"
    fingerprint = state / "cloud-admin-ed25519.fingerprint"
    for path in (private, public, config, fingerprint):
        assert path.is_file() and not path.is_symlink()
        assert path.stat().st_mode & 0o777 == 0o600
    derived = subprocess.run(
        ["/usr/bin/ssh-keygen", "-y", "-f", str(private)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    published = public.read_text(encoding="utf-8").split()
    assert derived[:2] == published[:2]
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "cloud_admin_host": "root@47.106.112.69",
        "cloud_admin_key": "/Users/agentops/AgentRuntime/private/cloud-admin-ed25519",
    }
    assert calls.read_text(encoding="utf-8").count("root@47.106.112.69") >= 2
