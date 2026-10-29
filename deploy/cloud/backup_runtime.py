#!/usr/bin/env python3
"""Runtime-bound encrypted backup; never recreate services or write plaintext dumps."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

PROJECT = "orbbec-agent-platform"
UTC = timezone.utc
SERVICES = ("platform-api", "platform-postgres", "platform-minio")
BACKUP_VOLUME = PROJECT + "-backups"
PUBLIC_VOLUME = PROJECT + "-backup-secrets"
POSTGRES_SECRET_VOLUME = PROJECT + "-postgres-secrets"
POSTGRES_SECRET_PATH = Path("/var/lib/docker/volumes") / POSTGRES_SECRET_VOLUME / "_data/postgres-owner-password"
POSTGRES_SECRET_UID = 999
POSTGRES_SECRET_GID = 999
IMPORT_VOLUME = PROJECT + "-import-secrets"
INTERNAL_NETWORK = PROJECT + "-internal"
CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
STAGES = {
    "preflight", "lock", "runtime_metadata", "runtime_identity", "runtime_changed",
    "configuration", "resource_mount", "backup_volume", "public_key", "secret_copy",
    "replica_producer", "replica_encryptor", "replica_receipt", "replica_artifact",
    "control_producer", "control_encryptor", "control_receipt", "control_artifact",
    "attachments_producer", "attachments_encryptor", "attachments_receipt", "attachments_artifact",
    "capture_producer", "capture_encryptor", "retention", "retention_receipt",
    "retention_resources", "manifest", "marker", "unexpected", "check_marker",
    "check_manifest", "check_artifact", "check_stale", "check_age", "artifact_sync",
    "interrupted", "unresolved_work", "lifecycle_create", "lifecycle_identity",
    "lifecycle_cleanup", "postgres_credential",
}
# Do not collect Config.Env or credential contents.
INSPECT_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},'
    '"image_reference":{{json .Config.Image}},"running":{{json .State.Running}},'
    '"started_at":{{json .State.StartedAt}},"labels":{{json .Config.Labels}},'
    '"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}}}'
)
VOLUME_FORMAT = '{"name":{{json .Name}},"driver":{{json .Driver}},"options":{{json .Options}},"mountpoint":{{json .Mountpoint}}}'
IMAGE_FORMAT = '{"id":{{json .Id}},"tags":{{json .RepoTags}}}'
LIFECYCLE_FORMAT = ('{"id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},'
                    '"labels":{{json .Config.Labels}},"running":{{json .State.Running}},'
                    '"log_driver":{{json .HostConfig.LogConfig.Type}},'
                    '"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}}}')
PG_DUMP_CLIENT = r'''set -eu
umask 077
case "$1" in agent_platform|agent_platform_control) ;; *) exit 64 ;; esac
exec 3< "$PG_SOURCE_PASSWORD_FILE"
password=''
IFS= read -r password <&3 || [ -n "$password" ]
extra=''
if IFS= read -r extra <&3 || [ -n "$extra" ]; then exit 65; fi
exec 3<&-
[ -n "$password" ]
case "$password" in *"$(printf '\r')"*) exit 65 ;; esac
escaped=$(printf '%s' "$password" | sed 's/\\/\\\\/g;s/:/\\:/g')
host_field=$(printf '%s' "$PGHOST" | sed 's/\\/\\\\/g;s/:/\\:/g')
printf '%s:%s:*:%s:%s\n' "$host_field" "$PGPORT" "$PGUSER" "$escaped" > "$PGPASSFILE"
unset password escaped host_field
exec pg_dump --format=custom --no-password --dbname="$1"
'''
COPY_PUBLIC_KEY = """import os, pathlib, tempfile
key = pathlib.Path('/source/recovery-public-key').read_bytes()
assert len(key) == 32
descriptor, name = tempfile.mkstemp(prefix='.recovery-public-', dir='/target')
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(key)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchown(stream.fileno(), 10001, 10001)
    os.replace(name, '/target/recovery-public-key')
finally:
    if os.path.exists(name): os.unlink(name)
"""


class BackupError(RuntimeError):
    """Stable stage only, never child stderr, commands or exception messages."""

    def __init__(self, stage, *, producer_returncode=None, encryptor_returncode=None):
        self.stage = stage if stage in STAGES else "unexpected"
        self.producer_returncode = producer_returncode
        self.encryptor_returncode = encryptor_returncode
        super().__init__(self.stage)


@dataclass(frozen=True)
class BackupPaths:
    private_dir: Path = Path("/opt/orbbec-agent-platform/private")
    backup_dir: Path = Path("/data/orbbec-agent-platform/backups")
    attachment_dir: Path = Path("/data/orbbec-agent-platform/attachments")
    postgres_dir: Path = Path("/data/orbbec-agent-platform/postgres")


class ProcessRunner:
    """Injectable subprocess boundary; stdout contains metadata, never dump bytes."""

    def capture(self, command, *, stage, timeout=60):
        child = None
        try:
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
            output, _ = child.communicate(timeout=timeout)
            if child.returncode or len(output) > 1024 * 1024:
                raise BackupError(stage)
            return output
        except (OSError, subprocess.SubprocessError):
            raise BackupError(stage) from None
        finally:
            self._reap(child)

    @staticmethod
    def _reap(child):
        if child is None:
            return
        # Reap native producers and CLI descendants, separately from daemon work.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()

    def pipeline(self, producer, encryptor, *, stage, timeout=1800):
        source = target = None
        try:
            try:
                source = subprocess.Popen(producer, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          start_new_session=True)
            except OSError:
                raise BackupError(stage + "_producer") from None
            try:
                target = subprocess.Popen(encryptor, stdin=source.stdout, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                raise BackupError(stage + "_encryptor") from None
            source.stdout.close()
            try:
                output, _ = target.communicate(timeout=timeout)
            except subprocess.SubprocessError:
                raise BackupError(stage + "_encryptor") from None
            try:
                source.wait(timeout=timeout)
            except subprocess.SubprocessError:
                raise BackupError(stage + "_producer") from None
            # Encryption can succeed on an incomplete producer stream.
            if source.returncode:
                raise BackupError(stage + "_producer", producer_returncode=source.returncode,
                                  encryptor_returncode=target.returncode)
            if target.returncode or len(output) > 65536:
                raise BackupError(stage + "_encryptor", producer_returncode=source.returncode,
                                  encryptor_returncode=target.returncode)
            return output
        finally:
            for child in (source, target):
                self._reap(child)
            if source is not None and source.stdout is not None:
                source.stdout.close()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def timestamp(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def private_directory(path, *, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    metadata = path.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise BackupError("preflight")


def file_identity(path, *, stage, private=False):
    """Hash the exact regular file without disclosing its potentially secret bytes."""
    descriptor = None
    try:
        resolved = path.resolve(strict=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BackupError(stage)
        if private and (before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600):
            raise BackupError(stage)
        if stage == "configuration" and (before.st_uid != os.geteuid() or before.st_mode & 0o022):
            raise BackupError(stage)
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        version = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if version(before) != version(after) or version(after) != version(path.stat()):
            raise BackupError(stage)
        return {"path": str(path), "resolved_path": str(resolved), "sha256": digest.hexdigest(),
                "size_bytes": before.st_size, "device": before.st_dev, "inode": before.st_ino,
                "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns}
    except (OSError, ValueError):
        raise BackupError(stage) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def atomic_write(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ContainerLifecycle:
    """Own daemon work, not merely Docker CLI lifetimes. All calls are under flock."""

    def __init__(self, private_dir, runner, docker, clock):
        self.path = private_dir / "backup-inflight.json"
        if self.path.exists() or self.path.is_symlink():
            raise BackupError("unresolved_work")
        self.runner, self.docker, self.clock = runner, docker, clock
        self.attempt_id = uuid.uuid4().hex
        self.entries = []
        self.cleanup_confirmed = False
        self._save()

    def _save(self):
        atomic_write(self.path, canonical(self.receipt()))

    def receipt(self):
        return {"attempt_id": self.attempt_id, "cleanup_confirmed": self.cleanup_confirmed,
                "containers": self.entries}

    def _capture(self, args, *, stage="lifecycle_cleanup", timeout=5):
        return self.runner.capture([self.docker, *args], stage=stage, timeout=timeout)

    def _inspect(self, identity):
        try:
            value = json.loads(self._capture(["inspect", "--format", LIFECYCLE_FORMAT, identity]))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError):
            raise BackupError("lifecycle_identity") from None

    def _owned(self, value, entry):
        labels = value.get("labels") or {}
        if (not isinstance(labels, dict) or value.get("name") != "/" + entry["name"] or value.get("image_id") != entry["image_id"]
                or value.get("log_driver") != "none"
                or labels.get("io.orbbec.backup.owner") != PROJECT
                or labels.get("io.orbbec.backup.attempt") != self.attempt_id
                or labels.get("io.orbbec.backup.role") != entry["role"]
                or not isinstance(value.get("id"), str) or not CONTAINER_ID.fullmatch(value["id"])
                or (entry.get("id") and value["id"] != entry["id"])):
            raise BackupError("lifecycle_identity")

    def create(self, role, options, image_id, command):
        entry = {"name": "platform-backup-" + self.attempt_id + "-" + role,
                 "role": role, "image_id": image_id, "id": None, "state": "creating",
                 "exit_code": None, "started": False}
        self.entries.append(entry)
        self._save()  # Intent survives a CLI timeout or hard process kill.
        labels = ["--label", "io.orbbec.backup.owner=" + PROJECT,
                  "--label", "io.orbbec.backup.attempt=" + self.attempt_id,
                  "--label", "io.orbbec.backup.role=" + role]
        identity = self._capture(["create", "--pull=never", "--restart=no", "--log-driver", "none", "--name", entry["name"],
                                  *labels, *options, image_id, *command], stage="lifecycle_create", timeout=30).decode("ascii").strip()
        if not CONTAINER_ID.fullmatch(identity):
            raise BackupError("lifecycle_identity")
        value = self._inspect(identity)
        self._owned(value, entry)
        entry.update(id=identity, state="created", log_driver=value["log_driver"])
        self._save()
        return entry

    def start_command(self, entry, *, interactive=False):
        entry.update(started=True, state="starting")
        self._save()
        return [self.docker, "start", "--attach", *(["--interactive"] if interactive else []), entry["id"]]

    def exited(self, entry, stage):
        value = self._inspect(entry["id"])
        self._owned(value, entry)
        entry.update(state=value.get("status"), exit_code=value.get("exit_code"))
        self._save()
        if (value.get("running") is not False or value.get("status") != "exited"
                or type(value.get("exit_code")) is not int or value["exit_code"] != 0):
            raise BackupError(stage)

    def capture(self, role, options, image_id, command, *, stage, timeout=60):
        entry = self.create(role, options, image_id, command)
        output = self.runner.capture(self.start_command(entry), stage=stage, timeout=timeout)
        self.exited(entry, stage)
        return output, entry

    def _ids(self):
        output = self._capture(["ps", "--all", "--no-trunc", "--filter",
                                "label=io.orbbec.backup.attempt=" + self.attempt_id, "--format", "{{.ID}}"])
        identities = output.decode("ascii").split()
        if any(not CONTAINER_ID.fullmatch(identity) for identity in identities):
            raise BackupError("lifecycle_cleanup")
        return identities

    def close(self):
        try:
            identities = self._ids()
            for identity in identities:
                value = self._inspect(identity)
                entry = next((item for item in self.entries if "/" + item["name"] == value.get("name")), None)
                if entry is None:
                    raise BackupError("lifecycle_identity")
                self._owned(value, entry)
                entry["id"] = identity
                if value.get("running") is True:
                    try:
                        self._capture(["stop", "--time", "3", identity], timeout=8)
                    except BackupError:
                        # A failed client is not the terminal fact; inspect daemon state.
                        pass
                    value = self._inspect(identity)
                    self._owned(value, entry)
                if value.get("running") is not False:
                    raise BackupError("lifecycle_cleanup")
                entry.update(state=value.get("status"), exit_code=(value.get("exit_code") if value.get("status") != "created" else None))
                self._save()  # Keep actual daemon ExitCode before removal, no AutoRemove.
                self._capture(["rm", identity])
                entry["removed"] = True
            if self._ids() or any(item["state"] == "creating" for item in self.entries):
                raise BackupError("lifecycle_cleanup")
            self.cleanup_confirmed = True
            self._save()
            self.path.unlink()
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError, BackupError):
            raise BackupError("lifecycle_cleanup") from None


class RuntimeBackup:
    def __init__(self, paths=None, *, runner=None, clock=None):
        self.paths = paths or BackupPaths()
        self.runner = runner or ProcessRunner()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.docker = "/usr/bin/docker"

    def _capture(self, args, stage, *, timeout=60):
        return self.runner.capture([self.docker, *args], stage=stage, timeout=timeout)

    def _json(self, args, stage):
        try:
            value = json.loads(self._capture(args, stage))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError):
            raise BackupError(stage) from None

    def _volume(self, name, stage, *, bind=False):
        value = self._json(["volume", "inspect", "--format", VOLUME_FORMAT, name], stage)
        options = value.get("options") or {}
        expected = {"type": "none", "o": "bind", "device": str(self.paths.backup_dir)} if bind else {}
        if value.get("name") != name or value.get("driver") != "local" or options != expected:
            raise BackupError(stage)
        return value

    def _mounted_data(self, container, target, source):
        matches = [item for item in container["mounts"] if item.get("Destination") == target]
        if (len(matches) != 1 or matches[0].get("Type") != "bind"
                or matches[0].get("Source") != str(source) or matches[0].get("RW") is not True):
            raise BackupError("resource_mount")

    def _postgres_credential(self, postgres):
        volume = self._volume(POSTGRES_SECRET_VOLUME, "postgres_credential")
        mountpoint = str(POSTGRES_SECRET_PATH.parent)
        mounts = [item for item in postgres["mounts"] if item.get("Destination") == "/run/secrets"]
        if (volume.get("mountpoint") != mountpoint or len(mounts) != 1
                or mounts[0].get("Type") != "volume" or mounts[0].get("Name") != POSTGRES_SECRET_VOLUME
                or mounts[0].get("Source") != mountpoint or mounts[0].get("RW") is not False):
            raise BackupError("postgres_credential")
        try:
            # Metadata only: never open/read the production password on the host.
            item = POSTGRES_SECRET_PATH.lstat()
            if (not stat.S_ISREG(item.st_mode) or item.st_uid != POSTGRES_SECRET_UID
                    or item.st_gid != POSTGRES_SECRET_GID or stat.S_IMODE(item.st_mode) != 0o400
                    or item.st_size != 65):
                raise BackupError("postgres_credential")
            return {"path": str(POSTGRES_SECRET_PATH), "volume": volume, "uid": item.st_uid,
                    "gid": item.st_gid, "mode": "0400", "size": item.st_size,
                    "device": item.st_dev, "inode": item.st_ino, "mtime_ns": item.st_mtime_ns,
                    "ctime_ns": item.st_ctime_ns}
        except OSError:
            raise BackupError("postgres_credential") from None

    def resolve(self):
        containers = {}
        for service in SERVICES:
            ids = self._capture(["ps", "--no-trunc", "--filter", "label=com.docker.compose.project=" + PROJECT,
                                 "--filter", "label=com.docker.compose.service=" + service,
                                 "--format", "{{.ID}}"], "runtime_metadata").decode("ascii").split()
            if len(ids) != 1 or not CONTAINER_ID.fullmatch(ids[0]):
                raise BackupError("runtime_identity")
            item = self._json(["inspect", "--format", INSPECT_FORMAT, ids[0]], "runtime_metadata")
            labels = item.get("labels") or {}
            if (item.get("id") != ids[0] or item.get("running") is not True
                    or not isinstance(item.get("image_id"), str) or not IMAGE_ID.fullmatch(item["image_id"])
                    or labels.get("com.docker.compose.project") != PROJECT
                    or labels.get("com.docker.compose.service") != service
                    or labels.get("com.docker.compose.oneoff", "").lower() != "false"
                    or not item.get("started_at") or not isinstance(item.get("mounts"), list)
                    or INTERNAL_NETWORK not in item.get("networks", {})):
                raise BackupError("runtime_identity")
            containers[service] = item
        api = containers["platform-api"]
        reference = api.get("image_reference", "")
        if not isinstance(reference, str) or not reference.startswith("orbbec-agent-platform:"):
            raise BackupError("runtime_identity")
        image = self._json(["image", "inspect", "--format", IMAGE_FORMAT, api["image_id"]], "runtime_metadata")
        if image.get("id") != api["image_id"]:
            raise BackupError("runtime_identity")
        self._mounted_data(containers["platform-postgres"], "/var/lib/postgresql/data", self.paths.postgres_dir)
        self._mounted_data(containers["platform-minio"], "/data", self.paths.attachment_dir)
        labels = api["labels"]
        config_label = labels.get("com.docker.compose.project.config_files", "")
        working_dir = labels.get("com.docker.compose.project.working_dir", "")
        if (not isinstance(config_label, str) or not config_label or len(config_label.split(",")) > 256
                or not isinstance(working_dir, str) or not Path(working_dir).is_absolute()):
            raise BackupError("configuration")
        config_files = []
        for name in config_label.split(","):
            path = Path(name)
            if not path.is_absolute():
                raise BackupError("configuration")
            config_files.append(file_identity(path, stage="configuration"))
        # API ordered inputs describe the current deployment. Older PG/MinIO
        # creation labels remain recorded but never drive service recreation.
        snapshot = {"containers": containers, "compose_files": config_files,
                    "compose_working_dir": working_dir,
                    "backup_volume": self._volume(BACKUP_VOLUME, "backup_volume", bind=True),
                    "postgres_credential": self._postgres_credential(containers["platform-postgres"]),
                    "recovery_public_key": file_identity(self.paths.private_dir / "backup-recovery-x25519.pub",
                                                         stage="public_key", private=True)}
        if snapshot["recovery_public_key"]["size_bytes"] != 32:
            raise BackupError("public_key")
        snapshot["configuration_sha256"] = hashlib.sha256(canonical(config_files)).hexdigest()
        return snapshot

    def _unchanged(self, snapshot):
        try:
            if self.resolve() != snapshot:
                raise BackupError("runtime_changed")
        except BackupError:
            raise BackupError("runtime_changed") from None

    def _prepare_public_key(self, snapshot):
        self._capture(["volume", "create", PUBLIC_VOLUME], "secret_copy")
        self._volume(PUBLIC_VOLUME, "secret_copy")
        self.lifecycle.capture("public-copy", ["--user", "0:0", "--read-only",
                               "--cap-drop", "ALL", "--cap-add", "CHOWN", "--security-opt", "no-new-privileges:true",
                               "--network", "none", "-v", PUBLIC_VOLUME + ":/target",
                               "-v", str(self.paths.private_dir / "backup-recovery-x25519.pub") + ":/source/recovery-public-key:ro"],
                               snapshot["containers"]["platform-api"]["image_id"],
                               ["python", "-c", COPY_PUBLIC_KEY], stage="secret_copy")

    def _postgres_source(self, snapshot, kind, database):
        postgres = snapshot["containers"]["platform-postgres"]
        try:
            address = str(ipaddress.ip_address(postgres["networks"][INTERNAL_NETWORK]["IPAddress"]))
        except (KeyError, ValueError, TypeError):
            raise BackupError("postgres_credential") from None
        options = ["--user", "999:999", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges:true", "--network", "container:" + postgres["id"],
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=1m,uid=999,gid=999,mode=0700",
                   "--mount", "type=bind,source=" + snapshot["postgres_credential"]["path"] + ",target=/run/pg-password,readonly",
                   "--entrypoint", "/bin/sh", "-e", "PG_SOURCE_PASSWORD_FILE=/run/pg-password",
                   "-e", "PGPASSFILE=/tmp/pgpass", "-e", "PGHOST=" + address, "-e", "PGPORT=5432",
                   "-e", "PGUSER=platform_owner", "-e", "PGCONNECT_TIMEOUT=10",
                   "-e", "PGREQUIREAUTH=scram-sha-256",
                   "-e", "PGOPTIONS=-c default_transaction_read_only=on -c application_name=platform-encrypted-backup"]
        entry = self.lifecycle.create(kind + "-source", options, postgres["image_id"],
                                      ["-c", PG_DUMP_CLIENT, "backup-pg-client", database])
        return self.lifecycle.start_command(entry), entry

    def _artifact(self, snapshot, set_id, kind, scope, producer, producer_entry=None):
        self._unchanged(snapshot)
        started = timestamp(self.clock())
        filename = kind + "-" + set_id + ".orb"
        if (self.paths.backup_dir / filename).exists():
            raise BackupError(kind + "_artifact")
        options = ["-i", "--user", "10001:10001", "--read-only", "--cap-drop", "ALL",
                     "--security-opt", "no-new-privileges:true", "--network", "none",
                     "-v", PUBLIC_VOLUME + ":/run/backup-secrets:ro", "-v", BACKUP_VOLUME + ":/backups",
                     "-e", "TMPDIR=/backups",
                     "-e", "PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE=/run/backup-secrets/recovery-public-key",
                     "-e", "PLATFORM_REPLICA_BACKUP_PATH=/backups/" + filename]
        encryptor_entry = self.lifecycle.create(kind + "-encryptor", options,
                                               snapshot["containers"]["platform-api"]["image_id"],
                                               ["python", "-m", "app.cloud_replica.cli", "backup"])
        encryptor = self.lifecycle.start_command(encryptor_entry, interactive=True)
        output = self.runner.pipeline(producer, encryptor, stage=kind)
        # start/attach CLI success is not the daemon-side exit fact.
        if producer_entry is not None:
            self.lifecycle.exited(producer_entry, kind + "_producer")
        self.lifecycle.exited(encryptor_entry, kind + "_encryptor")
        self._unchanged(snapshot)
        try:
            receipt = json.loads(output)
            if (receipt.get("status") != "backed_up"
                    or any(type(receipt.get(field)) is not int or receipt[field] <= 0
                           for field in ("encrypted_size", "plaintext_size"))):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise BackupError(kind + "_receipt") from None
        identity = file_identity(self.paths.backup_dir / filename, stage=kind + "_artifact")
        if identity["size_bytes"] < receipt["encrypted_size"]:
            raise BackupError(kind + "_artifact")
        return {"scope": scope, "filename": filename, "capture_started_at": started,
                "capture_finished_at": timestamp(self.clock()), "ciphertext_sha256": identity["sha256"],
                "file_size": identity["size_bytes"], "encrypted_size": receipt["encrypted_size"],
                "plaintext_size": receipt["plaintext_size"], "producer_returncode": 0, "encryptor_returncode": 0,
                "producer_container_id": producer_entry["id"] if producer_entry else None,
                "producer_daemon_exit_code": producer_entry["exit_code"] if producer_entry else None,
                "encryptor_container_id": encryptor_entry["id"], "encryptor_daemon_exit_code": encryptor_entry["exit_code"]}

    def _retention(self, snapshot, set_id):
        receipt = {"set_id": set_id, "backup_status": "complete", "started_at": timestamp(self.clock()),
                   "status": "failed", "stage": "retention", "operation_completion": "unknown",
                   "execution_stopped": False}
        self.retention_receipt = receipt
        failure = None
        try:
            self._unchanged(snapshot)
            self._volume(IMPORT_VOLUME, "retention_resources")
            output, entry = self.lifecycle.capture("retention", ["--user", "10001:10001", "--read-only",
                                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                                    "--network", INTERNAL_NETWORK,
                                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=8m,uid=10001,gid=10001,mode=0700",
                                    "-v", IMPORT_VOLUME + ":/run/import-secrets:ro",
                                    "-e", "PLATFORM_REPLICA_DATABASE_URL_FILE=/run/import-secrets/replica-database-url",
                                    "-e", "PLATFORM_REPLICA_ENCRYPTION_KEY_FILE=/run/import-secrets/replica-encryption-key"],
                                    snapshot["containers"]["platform-api"]["image_id"],
                                    ["python", "-m", "app.cloud_replica.cli", "retention"], stage="retention", timeout=1800)
            value = json.loads(output)
            if (value.get("status") != "completed" or value.get("dry_run") is not False
                    or any(type(value.get(field)) is not int or value[field] < 0
                           for field in ("session_count", "agent_count"))):
                raise BackupError("retention_receipt")
            receipt.update(status="completed", operation_completion="reported_completed", execution=entry,
                           result={field: value[field] for field in (
                "status", "dry_run", "session_count", "agent_count")})
            self._unchanged(snapshot)
        except BackupError as error:
            failure = error
            receipt.update(status="failed", stage=error.stage)
        except (ValueError, TypeError, AttributeError, UnicodeError):
            failure = BackupError("retention_receipt")
            receipt["stage"] = failure.stage
        receipt["finished_at"] = timestamp(self.clock())
        try:
            atomic_write(self.paths.private_dir / "backup-manifests" / (set_id + ".retention.json"), canonical(receipt))
            atomic_write(self.paths.private_dir / "last-backup-retention.json", canonical(receipt))
        except OSError:
            raise failure or BackupError("retention_receipt") from None
        if failure:
            raise failure
        return receipt

    def run(self):
        private_directory(self.paths.private_dir)
        private_directory(self.paths.private_dir / "backup-manifests", create=True)
        for directory in (self.paths.backup_dir, self.paths.attachment_dir, self.paths.postgres_dir):
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise BackupError("preflight")
        lock = os.open(self.paths.private_dir / "backup.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise BackupError("lock") from None
            self.lifecycle = ContainerLifecycle(self.paths.private_dir, self.runner, self.docker, self.clock)
            self.retention_receipt = None
            result = primary = cleanup_error = None
            old_handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}

            def interrupted(_number, _frame):
                raise BackupError("interrupted")

            for number in old_handlers:
                signal.signal(number, interrupted)
            try:
                result = self._run_locked()
            except BaseException as error:
                primary = error if isinstance(error, BackupError) else BackupError(
                    "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "unexpected")
            finally:
                # A second termination request cannot skip owned-daemon cleanup.
                for number in old_handlers:
                    signal.signal(number, signal.SIG_IGN)
                try:
                    try:
                        self.lifecycle.close()
                    except BackupError as error:
                        cleanup_error = error
                    if self.retention_receipt is not None:
                        receipt = self.retention_receipt
                        receipt["execution_stopped"] = cleanup_error is None
                        entries = [item for item in self.lifecycle.entries if item["role"] == "retention"]
                        if entries:
                            receipt["execution"] = entries[0]
                        if cleanup_error:
                            receipt.update(status="failed", stage=cleanup_error.stage)
                        try:
                            self._write_retention(receipt)
                        except BackupError as error:
                            primary = primary or error
                    if result is not None:
                        result["lifecycle"] = self.lifecycle.receipt()
                        try:
                            atomic_write(self.paths.private_dir / "backup-manifests" / (result["set_id"] + ".json"), canonical(result))
                        except OSError:
                            primary = primary or BackupError("manifest")
                    error = cleanup_error or primary
                    if error:
                        failure = {"status": "failed", "failed_at": timestamp(self.clock()), "stage": error.stage,
                                   "primary_stage": primary.stage if primary else None,
                                   "producer_returncode": primary.producer_returncode if primary else None,
                                   "encryptor_returncode": primary.encryptor_returncode if primary else None,
                                   "lifecycle": self.lifecycle.receipt()}
                        try:
                            atomic_write(self.paths.private_dir / "last-backup-failure.json", canonical(failure))
                        except OSError:
                            pass  # Preserve primary failure; in-flight journal already exists if unresolved.
                finally:
                    for number, handler in old_handlers.items():
                        signal.signal(number, handler)
            if cleanup_error or primary:
                raise cleanup_error or primary
            print("CLOUD_BACKUP_OK", flush=True)
            return result
        finally:
            os.close(lock)

    def _write_retention(self, receipt):
        try:
            atomic_write(self.paths.private_dir / "backup-manifests" / (receipt["set_id"] + ".retention.json"), canonical(receipt))
            atomic_write(self.paths.private_dir / "last-backup-retention.json", canonical(receipt))
        except OSError:
            raise BackupError("retention_receipt") from None

    def _run_locked(self):
        snapshot = self.resolve()
        self._prepare_public_key(snapshot)
        created = self.clock()
        set_id = created.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8]
        artifacts = []
        for kind, database in (("replica", "agent_platform"), ("control", "agent_platform_control")):
            producer, producer_entry = self._postgres_source(snapshot, kind, database)
            artifacts.append(self._artifact(snapshot, set_id, kind, "database:" + database, producer, producer_entry))
        artifacts.append(self._artifact(snapshot, set_id, "attachments", "minio_data_directory",
                                        ["/usr/bin/tar", "-C", str(self.paths.attachment_dir), "-cf", "-", "."]))
        # The existing CLI fsyncs ciphertext before rename. Flush the resulting
        # files and host data-directory entry before publishing a durable marker
        # on the separate /opt filesystem.
        try:
            for path in [*(self.paths.backup_dir / item["filename"] for item in artifacts), self.paths.backup_dir]:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError:
            raise BackupError("artifact_sync") from None
        self._unchanged(snapshot)
        manifest = {"version": 1, "status": "complete", "set_id": set_id,
                    "created_at": timestamp(created), "completed_at": timestamp(self.clock()),
                    "snapshot_consistency": "individual_captures_not_cross_resource_atomic",
                    "runtime": snapshot, "artifacts": artifacts,
                    "lifecycle": self.lifecycle.receipt(),
                    "retention": {"status": "pending", "receipt": set_id + ".retention.json"}}
        try:
            manifest_path = self.paths.private_dir / "backup-manifests" / (set_id + ".json")
            atomic_write(manifest_path, canonical(manifest))
        except OSError:
            raise BackupError("manifest") from None
        try:
            # Existing consumers expect three filenames in this marker.
            atomic_write(self.paths.private_dir / "last-backup-success",
                         (" ".join(item["filename"] for item in artifacts) + "\n").encode())
        except OSError:
            raise BackupError("marker") from None
        # Durably complete before independent retention starts.
        print("CLOUD_BACKUP_COMPLETE set=" + set_id, flush=True)
        manifest["retention"] = self._retention(snapshot, set_id)
        try:
            atomic_write(manifest_path, canonical(manifest))
        except OSError:
            raise BackupError("manifest") from None
        return manifest

    def check(self, *, max_age_seconds):
        """Read-only integrity/freshness check; no Docker, retention or backup calls."""
        private_directory(self.paths.private_dir)
        active_job = False
        if (self.paths.private_dir / "backup-inflight.json").exists():
            descriptor = None
            try:
                descriptor = os.open(self.paths.private_dir / "backup.lock", os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    active_job = True
            except OSError:
                raise BackupError("unresolved_work") from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            if not active_job:
                raise BackupError("unresolved_work")
        if type(max_age_seconds) is not int or max_age_seconds <= 0:
            raise BackupError("check_age")
        marker = self.paths.private_dir / "last-backup-success"
        file_identity(marker, stage="check_marker", private=True)
        try:
            names = marker.read_text().split()
            if len(names) != 3:
                raise ValueError()
            matched = re.fullmatch(r"replica-(\d{8}T\d{12}Z-[0-9a-f]{8})\.orb", names[0])
            if matched is None:
                raise ValueError()
            set_id = matched[1]
            expected = [kind + "-" + set_id + ".orb" for kind in ("replica", "control", "attachments")]
            if names != expected:
                raise ValueError()
        except (OSError, ValueError, UnicodeError):
            raise BackupError("check_marker") from None
        manifest_path = self.paths.private_dir / "backup-manifests" / (set_id + ".json")
        file_identity(manifest_path, stage="check_manifest", private=True)
        try:
            manifest = json.loads(manifest_path.read_bytes())
            if (manifest.get("status") != "complete" or manifest.get("version") != 1
                    or manifest.get("set_id") != set_id
                    or [item["filename"] for item in manifest["artifacts"]] != expected
                    or [item["scope"] for item in manifest["artifacts"]] != [
                        "database:agent_platform", "database:agent_platform_control", "minio_data_directory"]):
                raise ValueError()
            created = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
            if created.tzinfo is None:
                raise ValueError()
            age = (self.clock() - created).total_seconds()
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            raise BackupError("check_manifest") from None
        if age < 0 or age > max_age_seconds:
            raise BackupError("check_stale")
        for artifact in manifest["artifacts"]:
            identity = file_identity(self.paths.backup_dir / artifact["filename"], stage="check_artifact")
            if (identity["sha256"] != artifact.get("ciphertext_sha256")
                    or identity["size_bytes"] != artifact.get("file_size")):
                raise BackupError("check_artifact")
        # A valid recent set can coexist with failed/pending retention.
        retention_status = "pending"
        receipt_path = self.paths.private_dir / "backup-manifests" / (set_id + ".retention.json")
        if receipt_path.exists():
            file_identity(receipt_path, stage="check_manifest", private=True)
            try:
                receipt = json.loads(receipt_path.read_bytes())
                if receipt.get("set_id") != set_id or receipt.get("status") not in {"completed", "failed"}:
                    raise ValueError()
                retention_status = receipt["status"]
            except (OSError, ValueError, AttributeError):
                raise BackupError("check_manifest") from None
        return {"status": "in_progress" if active_job and retention_status == "pending" else "fresh",
                "backup_status": "fresh", "active_job": active_job,
                "set_id": set_id, "created_at": manifest["created_at"],
                "age_seconds": age, "max_age_seconds": max_age_seconds, "artifacts_verified": 3,
                "retention_status": retention_status}


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read-only archive integrity and freshness")
    parser.add_argument("--max-age-seconds", type=int, default=26 * 3600,
                        help="operational freshness threshold, not a confirmed business RPO")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise BackupError("preflight")
        if args.check:
            result = RuntimeBackup().check(max_age_seconds=args.max_age_seconds)
            print(canonical(result).decode())
            return 0 if result["retention_status"] == "completed" or result["status"] == "in_progress" else 1
        RuntimeBackup().run()
        return 0
    except BackupError as error:
        if args.check:
            print(canonical({"status": "failed", "stage": error.stage}).decode())
        else:
            print("CLOUD_BACKUP_FAILED stage=" + error.stage, file=sys.stderr)
        return 1
    except Exception:
        if args.check:
            print('{"status":"failed","stage":"unexpected"}')
        else:
            print("CLOUD_BACKUP_FAILED stage=unexpected", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
