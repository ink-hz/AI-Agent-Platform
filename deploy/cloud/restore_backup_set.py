#!/usr/bin/env python3
"""Offline validation of one fixed three-artifact backup set; not a business DR test."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import threading

KINDS = ("replica", "control", "attachments")
DATABASES = {"replica": ("agent_platform", "restore_agent_platform"),
             "control": ("agent_platform_control", "restore_agent_platform_control")}
SCOPES = ("database:agent_platform", "database:agent_platform_control", "minio_data_directory")
MAX_PLAINTEXT = 4 * 1024 ** 3
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
STAGES = {"manifest", "recovery_key", "artifact_integrity", "decrypt", "attachments",
          "attachments_baseline", "schema_baseline", "roles", "container_boundary",
          "docker_endpoint", "docker_command", "database_exists", "database_create",
          "database_restore", "schema_mismatch", "database_count", "python_root", "unexpected"}


class RestoreError(RuntimeError):
    def __init__(self, stage):
        self.stage = stage if stage in STAGES else "unexpected"
        super().__init__(self.stage)


def load_crypto(python_root=None):
    """Load this checkout's exact crypto file, never a cached app from another repo."""
    repository = Path(__file__).resolve().parents[2]
    backend = Path(python_root).resolve() if python_root else repository / "backend"
    if backend != (repository / "backend").resolve():
        raise RestoreError("python_root")
    source = backend / "app/cloud_replica/backup.py"
    if not source.is_file() or not source.resolve().is_relative_to(backend):
        raise RestoreError("python_root")
    spec = importlib.util.spec_from_file_location("offline_restore_crypto", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _open_regular(path, stage, *, private=False):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o022
                or private and stat.S_IMODE(metadata.st_mode) != 0o600):
            os.close(descriptor)
            raise RestoreError(stage)
        return os.fdopen(descriptor, "rb")
    except OSError:
        raise RestoreError(stage) from None


def _json_file(path, stage, *, limit=4 * 1024 * 1024):
    try:
        with _open_regular(path, stage) as source:
            data = source.read(limit + 1)
            if len(data) > limit:
                raise ValueError()
            return json.loads(data)
    except (ValueError, TypeError, UnicodeError):
        raise RestoreError(stage) from None


def _version(stream):
    value = os.fstat(stream.fileno())
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _manifest(path):
    value = _json_file(path, "manifest")
    try:
        set_id = value["set_id"]
        if (type(value["version"]) is not int or value["version"] != 1 or value["status"] != "complete"
                or not re.fullmatch(r"\d{8}T\d{12}Z-[0-9a-f]{8}", set_id)
                or value["snapshot_consistency"] != "individual_captures_not_cross_resource_atomic"
                or len(value["artifacts"]) != 3
                or not SHA256.fullmatch(value["runtime"]["recovery_public_key"]["sha256"])):
            raise ValueError()
        for kind, scope, item in zip(KINDS, SCOPES, value["artifacts"]):
            if (item["filename"] != f"{kind}-{set_id}.orb" or item["scope"] != scope
                    or not SHA256.fullmatch(item["ciphertext_sha256"])
                    or any(type(item[field]) is not int or item[field] <= 0
                           for field in ("file_size", "encrypted_size", "plaintext_size"))
                    or item["plaintext_size"] > MAX_PLAINTEXT
                    or item["encrypted_size"] >= item["file_size"]
                    or item["producer_returncode"] != 0 or item["encryptor_returncode"] != 0):
                raise ValueError()
        return value
    except (KeyError, ValueError, TypeError, AttributeError):
        raise RestoreError("manifest") from None


def _private_key(path, expected_public_hash):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    with _open_regular(path, "recovery_key", private=True) as source:
        raw = source.read(33)
    if len(raw) != 32:
        raise RestoreError("recovery_key")
    public = X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    if hashlib.sha256(public).hexdigest() != expected_public_hash:
        raise RestoreError("recovery_key")
    return raw


def _identifier(value):
    if not isinstance(value, str) or not value or "\0" in value or len(value.encode()) > 63:
        raise ValueError()
    return '"' + value.replace('"', '""') + '"'


def _baselines(schema_path, roles_path):
    try:
        data = _json_file(schema_path, "schema_baseline")["databases"]
        if set(data) != {item[0] for item in DATABASES.values()}:
            raise ValueError()
        tables = {}
        for database, rows in data.items():
            pairs = [(row["schema"], row["table"]) for row in rows]
            if not pairs or len(pairs) != len(set(pairs)):
                raise ValueError()
            for pair in pairs:
                for value in pair:
                    _identifier(value)
            tables[database] = set(pairs)
    except (KeyError, TypeError, ValueError):
        raise RestoreError("schema_baseline") from None
    try:
        roles = _json_file(roles_path, "roles", limit=65536) if roles_path else []
        if not isinstance(roles, list) or len(roles) > 512:
            raise ValueError()
        for role in roles:
            _identifier(role)
        if len(set(roles)) != len(roles):
            raise ValueError()
    except (TypeError, ValueError):
        raise RestoreError("roles") from None
    return tables, roles


def _container(docker, expected_id, image):
    if not SHA256.fullmatch(expected_id) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise RestoreError("container_boundary")
    value = docker.inspect(expected_id)
    bindings, ports = value.get("port_bindings") or {}, value.get("ports") or {}
    mounts = value.get("mounts") or []
    tmpfs = value.get("tmpfs") or {}
    if (not isinstance(bindings, dict) or not isinstance(ports, dict)
            or not isinstance(mounts, list) or not isinstance(tmpfs, dict)):
        raise RestoreError("container_boundary")
    if (value.get("id") != expected_id or value.get("image") != image or value.get("running") is not True
            or value.get("network_mode") != "none"
            or any(bindings.values()) or any(ports.values())
            or any(mount.get("Type") != "tmpfs" for mount in mounts)
            or value.get("log_driver") != "none" or value.get("read_only") is not True
            or value.get("privileged") is not False or value.get("cap_add")
            or "ALL" not in (value.get("cap_drop") or [])
            or not any(option in {"no-new-privileges", "no-new-privileges:true"}
                       for option in (value.get("security_opt") or []))
            or value.get("user") not in {"999", "999:999"}
            or "/var/lib/postgresql/data" not in tmpfs):
        raise RestoreError("container_boundary")


class DockerRunner:
    def __init__(self, *, endpoint=None):
        binary = shutil.which("docker") or "docker"
        try:
            endpoint = endpoint or os.getenv("DOCKER_HOST")
            if not endpoint:
                endpoint = json.loads(subprocess.run(
                    [binary, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                    check=True, capture_output=True, text=True, timeout=5,
                ).stdout)
            if not isinstance(endpoint, str) or not endpoint.startswith("unix:///"):
                raise ValueError()
        except (OSError, ValueError, subprocess.SubprocessError):
            raise RestoreError("docker_endpoint") from None
        self.prefix = [binary, "--host", endpoint]

    def _capture(self, args):
        try:
            return subprocess.run(self.prefix + args, check=True, capture_output=True,
                                  text=True, timeout=1800).stdout
        except (OSError, subprocess.SubprocessError):
            raise RestoreError("docker_command") from None

    def inspect(self, container):
        template = ('{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},'
                    '"network_mode":{{json .HostConfig.NetworkMode}},'
                    '"port_bindings":{{json .HostConfig.PortBindings}},'
                    '"ports":{{json .NetworkSettings.Ports}},"mounts":{{json .Mounts}},'
                    '"log_driver":{{json .HostConfig.LogConfig.Type}},'
                    '"read_only":{{json .HostConfig.ReadonlyRootfs}},'
                    '"privileged":{{json .HostConfig.Privileged}},"user":{{json .Config.User}},'
                    '"cap_add":{{json .HostConfig.CapAdd}},"cap_drop":{{json .HostConfig.CapDrop}},'
                    '"security_opt":{{json .HostConfig.SecurityOpt}},"tmpfs":{{json .HostConfig.Tmpfs}}}')
        return json.loads(self._capture(["inspect", "--format", template, container]))

    def sql(self, container, database, statement):
        return self._capture(["exec", container, "psql", "-U", "postgres", "-d", database,
                              "-X", "-A", "-t", "--no-password", "-v", "ON_ERROR_STOP=1", "-c", statement])

    def restore(self, container, database, producer):
        command = self.prefix + ["exec", "-i", container, "pg_restore", "-U", "postgres", "-d", database,
                                 "--no-password", "--no-owner", "--no-privileges", "--exit-on-error",
                                 "--single-transaction"]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        result, errors = [], []
        def feed():
            try:
                result.append(producer(process.stdin))
            except Exception:
                errors.append(True)
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    errors.append(True)
        thread = threading.Thread(target=feed, daemon=True)
        thread.start()
        try:
            process.wait(timeout=1800)
            thread.join(timeout=5)
            if process.returncode != 0 or thread.is_alive() or errors or not result:
                raise RestoreError("database_restore")
            return result[0]
        except subprocess.SubprocessError:
            raise RestoreError("database_restore") from None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            thread.join(timeout=5)


class _Discard:
    def write(self, value):
        return len(value)


def _decrypt(crypto, source, target, key, item, version):
    try:
        if _version(source) != version:
            raise ValueError()
        source.seek(0)
        metadata = crypto.decrypt_stream(source, target, key)
        if (metadata.plaintext_size != item["plaintext_size"]
                or metadata.encrypted_size != item["encrypted_size"] or _version(source) != version):
            raise ValueError()
        return metadata
    except Exception:
        raise RestoreError("decrypt") from None


def _attachments(producer, max_bytes):
    read_fd, write_fd = os.pipe()
    results, errors = [], []
    def feed():
        try:
            with os.fdopen(write_fd, "wb") as output:
                results.append(producer(output))
        except Exception:
            errors.append(True)
    thread = threading.Thread(target=feed, daemon=True)
    thread.start()
    records, seen, total = [], set(), 0
    try:
        with os.fdopen(read_fd, "rb") as source:
            with tarfile.open(fileobj=source, mode="r|") as archive:
                for entry in archive:
                    path = PurePosixPath(entry.name)
                    if (path.is_absolute() or ".." in path.parts or "\\" in entry.name or "\0" in entry.name
                            or not (entry.isdir() or entry.isfile()) or entry.sparse
                            or entry.size < 0 or entry.size > max_bytes or len(seen) >= 100000):
                        raise RestoreError("attachments")
                    name = str(path)
                    if name in seen or name == "." and not entry.isdir():
                        raise RestoreError("attachments")
                    seen.add(name)
                    if entry.isdir():
                        continue
                    digest, size = hashlib.sha256(), 0
                    with archive.extractfile(entry) as content:
                        while chunk := content.read(1024 * 1024):
                            digest.update(chunk)
                            size += len(chunk)
                    total += size
                    if size != entry.size or total > max_bytes:
                        raise RestoreError("attachments")
                    records.append((name, size, digest.hexdigest()))
                # Drain through tar's buffered stream, not its underlying pipe:
                # only zero padding may follow the first archive end marker.
                while padding := archive.fileobj.read(1024 * 1024):
                    if any(padding):
                        raise RestoreError("attachments")
    except Exception:
        raise RestoreError("attachments") from None
    finally:
        thread.join(timeout=30)
    if thread.is_alive() or errors or not results:
        raise RestoreError("decrypt")
    aggregate = hashlib.sha256()
    for record in sorted(records):
        aggregate.update(json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode() + b"\n")
    return {"files": len(records), "bytes": total, "sha256": aggregate.hexdigest()}, results[0]


def _schema_summary(docker, container, database, expected):
    query = ("SELECT coalesce(json_agg(json_build_array(n.nspname,c.relname) ORDER BY n.nspname,c.relname),"
             "'[]'::json)::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
             "WHERE c.relkind='r' AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'")
    try:
        actual = {tuple(pair) for pair in json.loads(docker.sql(container, database, query))}
        if actual != expected:
            raise RestoreError("schema_mismatch")
        schemas = {}
        for schema, table in sorted(actual):
            rows = int(docker.sql(container, database,
                                  f"SELECT count(*) FROM {_identifier(schema)}.{_identifier(table)}"))
            if rows < 0:
                raise ValueError()
            group = schemas.setdefault(schema, {"tables": 0, "rows": 0})
            group["tables"] += 1
            group["rows"] += rows
        return {"tables": len(actual), "rows": sum(group["rows"] for group in schemas.values()),
                "schemas": schemas}
    except (TypeError, ValueError):
        raise RestoreError("database_count") from None


def restore_set(*, manifest_path, artifacts_dir, private_key_path, schema_baseline_path,
                expected_container_id, expected_image_id, roles_path=None, objects_baseline_path=None,
                python_root=None, docker=None):
    crypto = load_crypto(python_root)
    manifest = _manifest(manifest_path)
    key = _private_key(private_key_path, manifest["runtime"]["recovery_public_key"]["sha256"])
    baseline, roles = _baselines(schema_baseline_path, roles_path)
    docker = docker or DockerRunner()
    _container(docker, expected_container_id, expected_image_id)
    verified = []
    with ExitStack() as stack:
        sources = []
        for item in manifest["artifacts"]:
            source = stack.enter_context(_open_regular(Path(artifacts_dir) / item["filename"], "artifact_integrity"))
            version, digest = _version(source), hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
            if (version != _version(source) or version[2] != item["file_size"]
                    or digest.hexdigest() != item["ciphertext_sha256"]):
                raise RestoreError("artifact_integrity")
            sources.append((source, version))
        # Authenticate every artifact before any role/database creation. No plaintext files.
        for index, (kind, item) in enumerate(zip(KINDS, manifest["artifacts"])):
            source, version = sources[index]
            def produce(target):
                return _decrypt(crypto, source, target, key, item, version)
            if kind == "attachments":
                attachments, metadata = _attachments(produce, item["plaintext_size"])
            else:
                metadata = produce(_Discard())
            verified.append({"kind": kind, "ciphertext_verified": True, "decrypt_verified": True,
                             "ciphertext_sha256": item["ciphertext_sha256"],
                             "ciphertext_bytes": item["file_size"],
                             "plaintext_sha256": metadata.plaintext_sha256,
                             "plaintext_bytes": metadata.plaintext_size})
        if objects_baseline_path:
            if _json_file(objects_baseline_path, "attachments_baseline") != attachments:
                raise RestoreError("attachments_baseline")
        _container(docker, expected_container_id, expected_image_id)
        names = ",".join("'" + database + "'" for _, database in DATABASES.values())
        if docker.sql(expected_container_id, "postgres", f"SELECT datname FROM pg_database WHERE datname IN ({names})").strip():
            raise RestoreError("database_exists")
        for role in roles:
            literal = "'" + role.replace("'", "''") + "'"
            if not docker.sql(expected_container_id, "postgres", "SELECT 1 FROM pg_roles WHERE rolname=" + literal).strip():
                docker.sql(expected_container_id, "postgres", f"CREATE ROLE {_identifier(role)} NOLOGIN")
        reports = {}
        for index, (kind, (original, target)) in enumerate(DATABASES.items()):
            docker.sql(expected_container_id, "postgres", f"CREATE DATABASE {_identifier(target)} TEMPLATE template0")
            source, version = sources[index]
            item = manifest["artifacts"][index]
            docker.restore(expected_container_id, target,
                           lambda output: _decrypt(crypto, source, output, key, item, version))
            reports[kind] = _schema_summary(docker, expected_container_id, target, baseline[original])
    return {"status": "verified_local_restore", "set_id": manifest["set_id"],
            "container_id": expected_container_id, "image_id": expected_image_id,
            "recovery_key_verified": True,
            "artifacts_verified": 3, "artifacts": verified, "databases": reports,
            "attachments": attachments, "attachments_baseline_compared": bool(objects_baseline_path),
            "cross_resource_atomic": False, "business_dr_validated": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, dest="manifest_path")
    parser.add_argument("--artifacts-dir", required=True, type=Path)
    parser.add_argument("--private-key", required=True, type=Path, dest="private_key_path")
    parser.add_argument("--schema-baseline", required=True, type=Path, dest="schema_baseline_path")
    parser.add_argument("--expected-container-id", required=True)
    parser.add_argument("--expected-image-id", required=True)
    parser.add_argument("--roles", type=Path, dest="roles_path")
    parser.add_argument("--objects-baseline", type=Path, dest="objects_baseline_path")
    parser.add_argument("--python-root", type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = restore_set(**vars(arguments))
    except Exception as error:
        report = {"status": "failed_local_restore", "stage": error.stage if isinstance(error, RestoreError) else "unexpected",
                  "business_dr_validated": False}
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "verified_local_restore" else 1


if __name__ == "__main__":
    raise SystemExit(main())
