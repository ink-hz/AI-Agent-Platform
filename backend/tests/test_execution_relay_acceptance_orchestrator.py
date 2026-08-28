from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from app.execution_relay import acceptance_orchestrator as subject


AGENTS = [
    "hr-bot",
    "fae-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "marketing-intelligence-bot",
    "marketing-gtm-bot",
    "agent-brain-bot",
]


def _secure_write(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o600)


def _fixture(tmp_path: Path, *, key_id: str = "worker-v1") -> tuple[Path, Path, str]:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    key = Ed25519PrivateKey.generate()
    private_value = key.private_bytes_raw()
    public_value = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    fingerprint = hashlib.sha256(public_value).hexdigest()
    key_path = private / "execution-worker-ed25519.key"
    public_path = private / "execution-worker-public.json"
    supervisor = private / "worker-pm2.sh"
    ssh_key = private / "cloud-admin-ed25519"
    _secure_write(key_path, private_value)
    supervisor.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    supervisor.chmod(0o700)
    _secure_write(ssh_key, b"bounded test key")
    _secure_write(
        public_path,
        (json.dumps({
            "worker_id": "agentops-mac-primary",
            "key_id": key_id,
            "public_key_base64url": base64.urlsafe_b64encode(public_value).decode().rstrip("="),
            "allowed_agent_ids": AGENTS,
        }) + "\n").encode(),
    )
    config = private / "acceptance-config.json"
    _secure_write(
        config,
        (json.dumps({
            "schema_version": 1,
            "cloud_admin_host": "root@47.106.112.69",
            "cloud_admin_key": str(ssh_key),
        }) + "\n").encode(),
    )
    return config, public_path, fingerprint


class Runner:
    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint
        self.calls: list[tuple[tuple[str, ...], bytes | None, int]] = []
        self.worker_listeners = (
            b"COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
            b"python 4242 agentops 7u IPv4 0x1 0t0 TCP 127.0.0.1:9120 (LISTEN)\n"
        )
        self.forbidden_listeners = b""
        self.cloud_snapshot = self._cloud()

    def _cloud(self) -> bytes:
        return (json.dumps({
            "schema_version": 1,
            "cloud_api_healthy": True,
            "cloud_database_healthy": True,
            "worker_heartbeat_fresh": True,
            "registered_public_key_sha256": self.fingerprint,
            "public_listeners": ["0.0.0.0:22", "0.0.0.0:80", "0.0.0.0:443"],
            "platform_loopback_listeners": ["127.0.0.1:8000", "127.0.0.1:8080"],
        }, sort_keys=True) + "\n").encode()

    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        input_bytes: bytes | None = None,
        timeout: int,
    ) -> subject.CommandResult:
        self.calls.append((arguments, input_bytes, timeout))
        if arguments[-1:] == ("inspect",):
            return subject.CommandResult(
                0,
                b'{"name":"orbbec-agent-execution-worker","pid":4242,'
                b'"status":"online","pm_exec_path":"/fixed/python",'
                b'"pm_cwd":"/fixed/backend","args":["-m","app.execution_relay.worker"]}\n',
            )
        if arguments[0] == "/usr/sbin/lsof":
            if "-p" in arguments:
                return subject.CommandResult(0, self.worker_listeners)
            return subject.CommandResult(
                0 if self.forbidden_listeners else 1,
                self.forbidden_listeners,
            )
        if arguments[0] == "/usr/bin/ssh":
            return subject.CommandResult(0, self.cloud_snapshot)
        raise AssertionError(arguments)


def test_secure_config_is_exact_owner_only_and_bounds_ssh_identity(tmp_path: Path) -> None:
    config, _public, _fingerprint = _fixture(tmp_path)
    loaded = subject.load_config(config, private_root=config.parent)
    assert loaded.cloud_admin_host == "root@47.106.112.69"
    assert loaded.cloud_admin_key == config.parent / "cloud-admin-ed25519"

    value = json.loads(config.read_text())
    for mutation in (
        {**value, "extra": True},
        {**value, "schema_version": True},
        {**value, "cloud_admin_host": "root@example.invalid"},
        {**value, "cloud_admin_key": str(tmp_path / "outside")},
    ):
        _secure_write(config, json.dumps(mutation).encode())
        with pytest.raises(ValueError, match="acceptance configuration unavailable"):
            subject.load_config(config, private_root=config.parent)


def test_secure_config_rejects_open_parent_file_and_symlink(tmp_path: Path) -> None:
    config, _public, _fingerprint = _fixture(tmp_path)
    config.chmod(0o644)
    with pytest.raises(ValueError, match="acceptance configuration unavailable"):
        subject.load_config(config, private_root=config.parent)
    config.chmod(0o600)
    config.parent.chmod(0o755)
    with pytest.raises(ValueError, match="acceptance configuration unavailable"):
        subject.load_config(config, private_root=config.parent)
    config.parent.chmod(0o700)
    target = tmp_path / "target.json"
    target.write_bytes(config.read_bytes())
    config.unlink()
    os.symlink(target, config)
    with pytest.raises(ValueError, match="acceptance configuration unavailable"):
        subject.load_config(config, private_root=config.parent)


def test_gate_01_to_03_uses_pinned_ssh_and_process_owned_listener_probe(tmp_path: Path) -> None:
    config_path, public_path, fingerprint = _fixture(tmp_path, key_id="worker-v2")
    runner = Runner(fingerprint)
    result = subject.run_gates_01_to_03(
        config_path,
        runner=runner,
        private_root=config_path.parent,
        private_key_path=config_path.parent / "execution-worker-ed25519.key",
        public_document_path=public_path,
        worker_supervisor_path=config_path.parent / "worker-pm2.sh",
        current_user="agentops",
        uid=501,
    )

    assert result.worker_id == "agentops-mac-primary"
    assert result.key_id == "worker-v2"
    assert result.registered_public_key_sha256 == fingerprint
    assert result.public_ports_added == 0
    ssh_calls = [call for call in runner.calls if call[0][0] == "/usr/bin/ssh"]
    assert len(ssh_calls) == 1
    for arguments, remote_script, timeout in ssh_calls:
        assert arguments[:2] == ("/usr/bin/ssh", "-o")
        assert "BatchMode=yes" in arguments
        assert "IdentitiesOnly=yes" in arguments
        assert "StrictHostKeyChecking=yes" in arguments
        assert "ConnectTimeout=8" in arguments
        assert arguments[-4:] == (
            "root@47.106.112.69",
            "/bin/bash -s",
            "--",
            "worker-v2",
        )
        assert remote_script is not None
        assert b"cloud_api_healthy" in remote_script
        assert b"cloud_database_healthy" in remote_script
        assert b"worker_heartbeat_fresh" in remote_script
        assert b'expected_key_id="$1"' in remote_script
        assert b"worker_key.key_id=:'expected_key_id'" in remote_script
        assert b'/usr/bin/docker exec -i "$postgres_id" psql' in remote_script
        assert b' -c \\\n  "select concat' not in remote_script
        assert b"9101-9108" in remote_script
        assert b"127.0.0.1:8000" in remote_script
        assert b"127.0.0.1:8080" in remote_script
        assert timeout == 30
    local_calls = [call for call in runner.calls if call[0][0] == "/usr/sbin/lsof"]
    assert any(("-p", "4242") == call[0][call[0].index("-p"):call[0].index("-p") + 2] for call in local_calls)
    assert any("-iTCP:9101-9108" in call[0] for call in local_calls)


@pytest.mark.parametrize(
    "field",
    ["cloud_api_healthy", "cloud_database_healthy", "worker_heartbeat_fresh"],
)
def test_gate_01_fails_when_any_live_cloud_probe_is_false(tmp_path: Path, field: str) -> None:
    config, public, fingerprint = _fixture(tmp_path)
    runner = Runner(fingerprint)
    value = json.loads(runner.cloud_snapshot)
    value[field] = False
    runner.cloud_snapshot = json.dumps(value).encode()
    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=runner,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="agentops",
            uid=501,
        )


def test_gate_02_rejects_changed_or_forbidden_local_and_cloud_listeners(tmp_path: Path) -> None:
    config, public, fingerprint = _fixture(tmp_path)
    runner = Runner(fingerprint)
    runner.worker_listeners += b"python 4242 agentops 8u IPv4 TCP *:9101 (LISTEN)\n"
    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=runner,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="agentops",
            uid=501,
        )

    runner = Runner(fingerprint)
    runner.forbidden_listeners = b"python 12 agentops 8u IPv4 TCP *:9101 (LISTEN)\n"
    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=runner,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="agentops",
            uid=501,
        )

    runner = Runner(fingerprint)
    cloud = json.loads(runner.cloud_snapshot)
    cloud["public_listeners"].append("0.0.0.0:9108")
    runner.cloud_snapshot = json.dumps(cloud).encode()
    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=runner,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="agentops",
            uid=501,
        )


def test_gate_02_allows_existing_metabot_loopback_listeners(tmp_path: Path) -> None:
    config, public, fingerprint = _fixture(tmp_path)
    runner = Runner(fingerprint)
    runner.forbidden_listeners = (
        b"COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
        b"python 12 agentops 8u IPv4 0x1 0t0 TCP 127.0.0.1:9101 (LISTEN)\n"
        b"python 13 agentops 8u IPv6 0x2 0t0 TCP [::1]:9102 (LISTEN)\n"
    )

    result = subject.run_gates_01_to_03(
        config,
        runner=runner,
        private_root=config.parent,
        private_key_path=config.parent / "execution-worker-ed25519.key",
        public_document_path=public,
        worker_supervisor_path=config.parent / "worker-pm2.sh",
        current_user="agentops",
        uid=501,
    )

    assert result.public_ports_added == 0


def test_gate_03_rejects_remote_or_public_document_fingerprint_mismatch(tmp_path: Path) -> None:
    config, public, fingerprint = _fixture(tmp_path)
    runner = Runner("0" * 64)
    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=runner,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="agentops",
            uid=501,
        )

    runner = Runner(fingerprint)
    value = json.loads(public.read_text())
    value["worker_id"] = "other-worker"
    _secure_write(public, json.dumps(value).encode())
    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=runner,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="agentops",
            uid=501,
        )


def test_gate_rejects_wrong_user_malformed_remote_json_and_failed_command(tmp_path: Path) -> None:
    config, public, fingerprint = _fixture(tmp_path)
    runner = Runner(fingerprint)
    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=runner,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="neo",
            uid=501,
        )

    runner = Runner(fingerprint)
    runner.cloud_snapshot = b"not json"
    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=runner,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="agentops",
            uid=501,
        )

    runner = Runner(fingerprint)
    original = runner.__call__

    def failed(arguments, *, input_bytes=None, timeout):
        if arguments[-1:] == ("inspect",):
            return subject.CommandResult(1, b"")
        return original(arguments, input_bytes=input_bytes, timeout=timeout)

    with pytest.raises(ValueError, match="acceptance gate failed"):
        subject.run_gates_01_to_03(
            config,
            runner=failed,
            private_root=config.parent,
            private_key_path=config.parent / "execution-worker-ed25519.key",
            public_document_path=public,
            worker_supervisor_path=config.parent / "worker-pm2.sh",
            current_user="agentops",
            uid=501,
        )
