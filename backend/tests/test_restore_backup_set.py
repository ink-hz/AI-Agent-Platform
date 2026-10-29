"""Offline-only restore boundaries, using genuine synthetic backup ciphertext."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

ROOT = Path(__file__).parents[2]
HELPER = ROOT / "deploy/cloud/restore_backup_set.py"
SET_ID = "20261029T023548123456Z-1234abcd"
CONTAINER = "a" * 64
IMAGE = "sha256:" + "b" * 64


def restore_module():
    assert HELPER.is_file(), "offline backup-set restore tool is missing"
    spec = importlib.util.spec_from_file_location("restore_backup_set", HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tar_bytes(name="./bucket/object", value=b"synthetic attachment", *, link=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        info = tarfile.TarInfo(name)
        if link:
            info.type = tarfile.SYMTYPE
            info.linkname = "/private/secret"
            archive.addfile(info)
        else:
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    return output.getvalue()


class FakeDocker:
    def __init__(self):
        self.metadata = {
            "id": CONTAINER, "image": IMAGE, "running": True, "network_mode": "none",
            "port_bindings": {}, "ports": {"5432/tcp": None}, "mounts": [],
            "log_driver": "none", "read_only": True, "privileged": False,
            "cap_drop": ["ALL"], "cap_add": [], "security_opt": ["no-new-privileges:true"],
            "user": "999", "tmpfs": {"/var/lib/postgresql/data": "rw,size=4g,uid=999"},
        }
        self.calls = []
        self.existing = ""
        self.restore_fail = False
        self.schemas = {"restore_agent_platform": [("platform_replica", "sessions")],
                        "restore_agent_platform_control": [("platform_control", "audit_events")]}
        self.plaintext = {}

    def inspect(self, container):
        self.calls.append(("inspect", container))
        return deepcopy(self.metadata)

    def sql(self, container, database, sql):
        self.calls.append(("sql", container, database, sql))
        if "FROM pg_database" in sql:
            return self.existing
        if "FROM pg_roles" in sql:
            return ""
        if "FROM pg_class" in sql:
            return json.dumps(self.schemas[database])
        if sql.startswith("SELECT count(*)"):
            return "3\n"
        return ""

    def restore(self, container, database, producer):
        self.calls.append(("restore", container, database))
        output = io.BytesIO()
        metadata = producer(output)
        if self.restore_fail:
            raise RuntimeError("secret-sentinel: PostgreSQL business diagnostic")
        self.plaintext[database] = output.getvalue()
        return metadata


def fixture_set(tmp_path, *, attachment=None):
    module = restore_module()
    crypto = module.load_crypto()
    private = X25519PrivateKey.generate()
    raw_private = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = tmp_path / "recovery.key"
    key.write_bytes(raw_private)
    key.chmod(0o600)
    payloads = {"replica": b"synthetic replica dump" * 1000,
                "control": b"synthetic control dump" * 1000,
                "attachments": attachment if attachment is not None else tar_bytes()}
    artifacts = []
    for kind, payload in payloads.items():
        filename = f"{kind}-{SET_ID}.orb"
        encrypted = io.BytesIO()
        receipt = crypto.encrypt_stream(io.BytesIO(payload), encrypted, public, chunk_size=4096)
        body = encrypted.getvalue()
        (tmp_path / filename).write_bytes(body)
        artifacts.append({
            "filename": filename,
            "scope": {"replica": "database:agent_platform", "control": "database:agent_platform_control",
                      "attachments": "minio_data_directory"}[kind],
            "file_size": len(body), "ciphertext_sha256": hashlib.sha256(body).hexdigest(),
            "encrypted_size": receipt.encrypted_size, "plaintext_size": receipt.plaintext_size,
            "producer_returncode": 0, "encryptor_returncode": 0,
        })
    manifest = {"version": 1, "status": "complete", "set_id": SET_ID,
                "snapshot_consistency": "individual_captures_not_cross_resource_atomic",
                "runtime": {"recovery_public_key": {"sha256": hashlib.sha256(public).hexdigest()}},
                "artifacts": artifacts}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"databases": {
        "agent_platform": [{"schema": "platform_replica", "table": "sessions"}],
        "agent_platform_control": [{"schema": "platform_control", "table": "audit_events"}],
    }}))
    return module, manifest_path, key, schema, manifest, payloads


def run_restore(values, docker=None, **kwargs):
    module, manifest, key, schema, _, _ = values
    return module.restore_set(manifest_path=manifest, artifacts_dir=manifest.parent,
                              private_key_path=key, schema_baseline_path=schema,
                              expected_container_id=CONTAINER, expected_image_id=IMAGE,
                              docker=docker or FakeDocker(), **kwargs)


@pytest.mark.parametrize("mutation,expected", [
    ("wrong_key", "recovery_key"), ("key_mode", "recovery_key"),
    ("ciphertext_hash", "artifact_integrity"), ("filename", "manifest"),
    ("scope", "manifest"), ("incomplete", "manifest"), ("size", "artifact_integrity"),
])
def test_rejects_invalid_inputs_before_any_database_mutation(tmp_path, mutation, expected):
    values = fixture_set(tmp_path)
    module, path, key, schema, manifest, payloads = values
    if mutation == "wrong_key":
        key.write_bytes(b"x" * 32)
    elif mutation == "key_mode":
        key.chmod(0o644)
    elif mutation == "ciphertext_hash":
        manifest["artifacts"][0]["ciphertext_sha256"] = "0" * 64
    elif mutation == "filename":
        manifest["artifacts"][0]["filename"] = "../replica.orb"
    elif mutation == "scope":
        manifest["artifacts"][0]["scope"] = "database:unrelated"
    elif mutation == "incomplete":
        manifest["status"] = "pending"
    elif mutation == "size":
        manifest["artifacts"][0]["file_size"] += 1
    path.write_text(json.dumps(manifest))
    docker = FakeDocker()
    with pytest.raises(module.RestoreError, match=expected):
        run_restore(values, docker)
    assert not any(call[0] in {"sql", "restore"} for call in docker.calls)


@pytest.mark.parametrize("mutation", ["network", "wrong_id", "ports", "image", "bind"])
def test_refuses_untrusted_or_connected_restore_container(tmp_path, mutation):
    values = fixture_set(tmp_path)
    module = values[0]
    docker = FakeDocker()
    if mutation == "network":
        docker.metadata["network_mode"] = "bridge"
    elif mutation == "wrong_id":
        docker.metadata["id"] = "c" * 64
    elif mutation == "ports":
        docker.metadata["ports"] = {"5432/tcp": [{"HostPort": "5432"}]}
    elif mutation == "image":
        docker.metadata["image"] = "sha256:" + "c" * 64
    else:
        docker.metadata["mounts"] = [{"Type": "bind", "Source": "/", "Destination": "/host"}]
    with pytest.raises(module.RestoreError, match="container_boundary"):
        run_restore(values, docker)
    assert not any(call[0] in {"sql", "restore"} for call in docker.calls)


def test_existing_database_is_never_overwritten(tmp_path):
    values = fixture_set(tmp_path)
    module = values[0]
    docker = FakeDocker()
    docker.existing = "restore_agent_platform\n"
    with pytest.raises(module.RestoreError, match="database_exists"):
        run_restore(values, docker)
    assert not any(call[0] == "restore" or "CREATE " in str(call) for call in docker.calls)


@pytest.mark.parametrize("name,link", [("../outside", False), ("/outside", False), ("object", True)])
def test_attachment_tar_paths_and_links_are_rejected_without_extract(tmp_path, name, link):
    values = fixture_set(tmp_path, attachment=tar_bytes(name, link=link))
    module = values[0]
    docker = FakeDocker()
    with pytest.raises(module.RestoreError, match="attachments"):
        run_restore(values, docker)
    assert not any(call[0] == "restore" for call in docker.calls)
    assert not (tmp_path / "outside").exists()


def test_tar_cannot_hide_unchecked_content_after_end_marker(tmp_path):
    values = fixture_set(tmp_path, attachment=tar_bytes() + b"unchecked trailing content")
    with pytest.raises(values[0].RestoreError, match="attachments"):
        run_restore(values)


def test_genuine_ciphertext_decrypts_to_database_streams_and_tar_summary(tmp_path):
    values = fixture_set(tmp_path)
    module, path, key, schema, manifest, payloads = values
    docker = FakeDocker()
    original_files = {entry.name for entry in tmp_path.iterdir()}
    report = run_restore(values, docker)
    assert docker.plaintext == {"restore_agent_platform": payloads["replica"],
                                "restore_agent_platform_control": payloads["control"]}
    assert report["status"] == "verified_local_restore"
    assert report["cross_resource_atomic"] is False
    assert report["attachments"]["files"] == 1
    assert report["attachments"]["bytes"] == len(b"synthetic attachment")
    assert report["databases"]["replica"]["schemas"] == {
        "platform_replica": {"tables": 1, "rows": 3}}
    assert report["artifacts_verified"] == 3
    assert "synthetic" not in json.dumps(report)
    assert "object" not in json.dumps(report)
    assert {entry.name for entry in tmp_path.iterdir()} == original_files


def test_unpublished_null_port_maps_are_allowed(tmp_path):
    values = fixture_set(tmp_path)
    docker = FakeDocker()
    docker.metadata.update(port_bindings=None, ports=None)
    assert run_restore(values, docker)["status"] == "verified_local_restore"


@pytest.mark.parametrize("changes", [
    {"log_driver": "json-file"}, {"read_only": False}, {"tmpfs": {}},
    {"privileged": True}, {"cap_drop": []}, {"cap_add": ["SYS_ADMIN"]},
    {"security_opt": []}, {"user": "0"},
])
def test_restore_container_cannot_persist_logs_or_have_excessive_privilege(tmp_path, changes):
    values = fixture_set(tmp_path)
    docker = FakeDocker()
    docker.metadata.update(changes)
    with pytest.raises(values[0].RestoreError, match="container_boundary"):
        run_restore(values, docker)
    assert not any(call[0] == "restore" for call in docker.calls)


@pytest.mark.parametrize("key_failure", ["length", "symlink", "owner"])
def test_local_private_key_file_boundary(tmp_path, monkeypatch, key_failure):
    values = fixture_set(tmp_path)
    module, _, key, _, _, _ = values
    if key_failure == "length":
        key.write_bytes(b"x" * 31)
    elif key_failure == "symlink":
        other = tmp_path / "other.key"
        key.rename(other)
        key.symlink_to(other)
    else:
        original = module.os.geteuid()
        # Model a file with a different owner without requiring root/chown.
        monkeypatch.setattr(module.os, "geteuid", lambda: original + 1)
        with pytest.raises(module.RestoreError, match="recovery_key"):
            module._private_key(key, "0" * 64)
        return
    with pytest.raises(module.RestoreError, match="recovery_key"):
        run_restore(values)


def test_ciphertext_symlink_is_rejected(tmp_path):
    values = fixture_set(tmp_path)
    module, path, key, schema, manifest, _ = values
    artifact = tmp_path / manifest["artifacts"][0]["filename"]
    other = tmp_path / "other.orb"
    artifact.rename(other)
    artifact.symlink_to(other)
    with pytest.raises(module.RestoreError, match="artifact_integrity"):
        run_restore(values)


def test_optional_attachment_baseline_mismatch_is_not_reported_as_match(tmp_path):
    values = fixture_set(tmp_path)
    baseline = tmp_path / "objects.json"
    baseline.write_text(json.dumps({"files": 2, "bytes": 1, "sha256": "0" * 64}))
    docker = FakeDocker()
    with pytest.raises(values[0].RestoreError, match="attachments_baseline"):
        run_restore(values, docker, objects_baseline_path=baseline)
    assert not any(call[0] == "sql" for call in docker.calls)


def test_authenticated_decryption_failure_stops_before_database_creation(tmp_path):
    values = fixture_set(tmp_path)
    module, path, key, schema, manifest, payloads = values
    artifact = path.parent / manifest["artifacts"][0]["filename"]
    data = bytearray(artifact.read_bytes())
    data[-1] ^= 1
    artifact.write_bytes(data)
    manifest["artifacts"][0]["ciphertext_sha256"] = hashlib.sha256(data).hexdigest()
    path.write_text(json.dumps(manifest))
    docker = FakeDocker()
    with pytest.raises(module.RestoreError, match="decrypt"):
        run_restore(values, docker)
    assert not any(call[0] == "sql" for call in docker.calls)


def test_schema_set_must_match_source_baseline_exactly(tmp_path):
    values = fixture_set(tmp_path)
    module = values[0]
    docker = FakeDocker()
    docker.schemas["restore_agent_platform"].append(("public", "unexpected"))
    with pytest.raises(module.RestoreError, match="schema_mismatch"):
        run_restore(values, docker)


def test_role_names_are_quoted_and_never_copy_passwords(tmp_path):
    values = fixture_set(tmp_path)
    roles = tmp_path / "roles.json"
    roles.write_text(json.dumps(['reader"quoted']))
    docker = FakeDocker()
    run_restore(values, docker, roles_path=roles)
    queries = [call[-1] for call in docker.calls if call[0] == "sql"]
    assert 'CREATE ROLE "reader""quoted" NOLOGIN' in "\n".join(queries)
    roles.write_text(json.dumps([{"name": "reader", "password": "secret-sentinel"}]))
    with pytest.raises(values[0].RestoreError, match="roles"):
        run_restore(values, FakeDocker(), roles_path=roles)


def test_docker_restore_command_is_transactional_and_stdin_only(monkeypatch):
    module = restore_module()
    observed = []

    class Process:
        stdin = io.BytesIO()
        returncode = 0
        def wait(self, timeout=None): return 0
        def poll(self): return 0

    def popen(command, **kwargs):
        observed.extend(command)
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        return Process()

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    runner = module.DockerRunner(endpoint="unix:///tmp/synthetic-docker.sock")
    runner.restore(CONTAINER, "restore_agent_platform", lambda out: out.write(b"synthetic"))
    assert "--single-transaction" in observed and "--exit-on-error" in observed
    assert "--no-owner" in observed and "--no-privileges" in observed
    assert CONTAINER in observed and "-i" in observed


def test_docker_runner_rejects_remote_daemon():
    module = restore_module()
    with pytest.raises(module.RestoreError, match="docker_endpoint"):
        module.DockerRunner(endpoint="ssh://production")


def test_cli_failures_never_output_upstream_business_details(monkeypatch, capsys):
    module = restore_module()
    def fail(**kwargs):
        raise RuntimeError("secret-sentinel business data")
    monkeypatch.setattr(module, "restore_set", fail)
    status = module.main(["--manifest", "a", "--artifacts-dir", "b", "--private-key", "c",
                          "--schema-baseline", "d", "--expected-container-id", CONTAINER,
                          "--expected-image-id", IMAGE])
    assert status == 1
    output = capsys.readouterr().out
    assert "sentinel" not in output
    assert json.loads(output)["stage"] == "unexpected"
