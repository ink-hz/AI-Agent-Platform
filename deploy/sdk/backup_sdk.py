#!/usr/bin/env python3
"""Fixed ZenTao cold capture followed by recovery of the same original services."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid


def _runtime():
    path = Path(__file__).resolve().parents[1] / "cloud/backup_runtime.py"
    spec = importlib.util.spec_from_file_location("sdk_shared_backup_runtime", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runtime = _runtime()
PROJECT = "orbbec-sdk-workbench-trial"
PUBLIC_VOLUME = runtime.PUBLIC_VOLUME
UTC = timezone.utc
DOCKER = "/usr/bin/docker"
WEB_TIMEOUT, DB_TIMEOUT, READY_TIMEOUT = 30, 120, 300
MAX_CONFIG_BYTES = 16 * 1024 * 1024
MAX_CONFIG_ARCHIVE_BYTES = MAX_CONFIG_BYTES + 1024 * 1024
ARTIFACT_KINDS = ("db-volume", "web-volume", "app-config", "php-config", "apache-config", "deployment-config")
CONFIG_HASHES = {
    "/apps/zentao/config/config.php": "2d44c9598fda676255b8a5185d5a02b37001fd7be2873d8aac6340eb2415584f",
    "/opt/zbox/etc/php/php.ini": "80ef531d92c5b7da9da33114672b38b64c73f77255fbfd0e98030192c7a2785a",
    "/opt/zbox/etc/apache/httpd.conf": "45dffa3ffe2dcf334223f3635115dfe77b532201ddc62f767745d41bca48d185",
}


def _pinned(role, identity, image, volume, destination, networks):
    return {"id": identity, "name": "/" + PROJECT + ("-zentao-1" if role == "web" else "-zentao-db-1"),
            "image_id": "sha256:" + image,
            "mounts": [{"Type": "volume", "Name": volume, "Driver": "local",
                        "Source": "/var/lib/docker/volumes/" + volume + "/_data",
                        "Destination": destination, "RW": True, "Mode": "rw", "Propagation": ""}],
            "networks": {name: {} for name in networks}}


PINNED_SOURCES = {
    "web": _pinned("web", "8f30af40ca3143ffbd1813262aff8d45a1f3d0a80c0b2d90ce3e22445c929162",
                   "1deeb004b41a691ef45fd8670a4794ef446bb3c0362e402a2e87a705cf26d5ce",
                   PROJECT + "_zentao-data", "/data",
                   [PROJECT + "_sdk-private", PROJECT + "_sdk-admin", "orbbec-agent-platform-sdk"]),
    "db": _pinned("db", "8de67a9b98076ba7605285c90affa66acc0ee2fe76d2764b512a5ffd6015769b",
                  "88ac718dafee1e8fb415233c04f314f3c3866af99177ce25d436106c62090461",
                  PROJECT + "_zentao-db", "/var/lib/mysql", [PROJECT + "_sdk-private"]),
}
INSPECT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},'
    '"running":{{json .State.Running}},"status":{{json .State.Status}},"pid":{{json .State.Pid}},'
    '"exit_code":{{json .State.ExitCode}},"oom_killed":{{json .State.OOMKilled}},'
    '"finished_at":{{json .State.FinishedAt}},'
    '"health":{{with index .State "Health"}}{"Status":{{json .Status}}}{{else}}null{{end}},'
    '"labels":{{json .Config.Labels}},"user":{{json .Config.User}},'
    '"log_driver":{{json .HostConfig.LogConfig.Type}},"host_config":{{json .HostConfig}},'
    '"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}}}'
)


class SDKError(RuntimeError):
    """Fixed stages only; never child output, credential values or upstream messages."""


@dataclass(frozen=True)
class SDKPaths:
    root: Path
    deployment: Path
    public_key: Path
    encryptor_image_id: str
    cipher_uid: int = 10001
    inventory: Path | None = None

    @property
    def private(self):
        return self.root / "private"

    @property
    def ciphertext(self):
        return self.root / "ciphertext"


class SDKProcessRunner(runtime.ProcessRunner):
    """Reuse the reviewed native process-group reaper, preserving termination evidence."""

    def __init__(self):
        self.uncertain_groups = set()

    def _reap(self, child):
        if child is None:
            return
        self.uncertain_groups.add(child.pid)
        super()._reap(child)
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            self.uncertain_groups.discard(child.pid)

    def native_stopped(self):
        for group in list(self.uncertain_groups):
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                self.uncertain_groups.discard(group)
        return not self.uncertain_groups


def mounts(value):
    """Only ordering is irrelevant: preserve every mount field."""
    return sorted(value, key=lambda item: runtime.canonical(item))


def local_volume_identity(value, name, *, mountpoint=None, stage="source_volume"):
    """Match RuntimeBackup._volume's null/{} no-options meaning, narrowly.

    Preserve raw evidence; never normalize missing/unknown fields or other false
    values into a known local volume identity.
    """
    if not isinstance(value, dict) or set(value) != {"name", "driver", "options", "mountpoint"}:
        raise SDKError(stage)
    options, path = value["options"], value["mountpoint"]
    if (value["name"] != name or value["driver"] != "local"
            or not (options is None or (type(options) is dict and not options))
            or not isinstance(path, str) or not Path(path).is_absolute()
            or (mountpoint is not None and path != mountpoint)):
        raise SDKError(stage)
    return {**value, "options": {}}


def tree_metadata(path):
    path = Path(path)
    root = path.lstat()
    if not stat.S_ISDIR(root.st_mode) or path.resolve() != path or root.st_mode & 0o002:
        raise SDKError("source_directory")
    digest, estimated = hashlib.sha256(), 10240
    for current, directories, files in os.walk(path, followlinks=False):
        directories.sort()
        for name in sorted(directories + files):
            item = Path(current) / name
            value = item.lstat()
            if not (stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode)):
                raise SDKError("source_directory")
            record = [str(item.relative_to(path)), value.st_dev, value.st_ino, value.st_mode,
                      value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns, value.st_ctime_ns]
            digest.update(runtime.canonical(record) + b"\n")
            estimated += 2048 + ((value.st_size + 511) // 512) * 512
    return {"device": root.st_dev, "inode": root.st_ino, "mode": stat.S_IMODE(root.st_mode),
            "uid": root.st_uid, "gid": root.st_gid, "metadata_sha256": digest.hexdigest(),
            "estimated_tar_bytes": estimated}


def validate_config_tar(source, destination, basename, expected_sha256):
    """Forward original bytes; exit success only for one pinned regular member.

    Forwarding before validation is intentional: a failure makes the producer
    nonzero, so even a successful encryptor cannot publish a complete set.
    """
    class Forward:
        size = 0

        def read(self, size):
            chunk = source.read(min(size, 65536))
            self.size += len(chunk)
            if self.size > MAX_CONFIG_ARCHIVE_BYTES:
                raise SDKError("configuration_archive")
            pending = memoryview(chunk)
            while pending:
                written = destination.write(pending)
                if not written:
                    raise SDKError("configuration_archive")
                pending = pending[written:]
            return chunk

    forwarded, members = Forward(), 0
    try:
        with tarfile.open(fileobj=forwarded, mode="r|", bufsize=512) as archive:
            for member in archive:
                members += 1
                if (members != 1 or member.name != basename or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                        or member.linkname or member.sparse is not None
                        or any(key.startswith("GNU.sparse") for key in member.pax_headers)
                        or not 0 <= member.size <= MAX_CONFIG_BYTES):
                    raise SDKError("configuration_archive")
                digest, size = hashlib.sha256(), 0
                with archive.extractfile(member) as content:
                    while chunk := content.read(65536):
                        size += len(chunk)
                        digest.update(chunk)
                if size != member.size or digest.hexdigest() != expected_sha256:
                    raise SDKError("configuration_archive")
            # TarFile consumed the first zero EOF block. Drain its stream,
            # including buffered bytes, and require the second plus zero padding.
            padding = 0
            while chunk := archive.fileobj.read(65536):
                if any(chunk):
                    raise SDKError("configuration_archive")
                padding += len(chunk)
            if members != 1 or padding < 512 or forwarded.size % 512:
                raise SDKError("configuration_archive")
        destination.flush()
    except (tarfile.TarError, OSError, ValueError, EOFError):
        raise SDKError("configuration_archive") from None


def copy_config(identity, path, *, output=None):
    """Internal producer only; cp stays in its parent's owned process group."""
    if identity != PINNED_SOURCES["web"]["id"] or path not in CONFIG_HASHES:
        raise SDKError("configuration_scope")
    child = None
    try:
        child = subprocess.Popen([DOCKER, "cp", identity + ":" + path, "-"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        validate_config_tar(child.stdout, output if output is not None else sys.stdout.buffer,
                            Path(path).name, CONFIG_HASHES[path])
        if child.wait(timeout=30) != 0:
            raise SDKError("configuration_copy")
    except (OSError, subprocess.SubprocessError):
        raise SDKError("configuration_copy") from None
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
            finally:
                child.stdout.close()


class HostProbe:
    def tree(self, value):
        pid = value["pid"]
        if not isinstance(pid, int) or pid <= 0:
            raise SDKError("source_process_tree")
        lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
        unified = next((line.split(":", 2)[2] for line in lines if line.startswith("0::")), None)
        if not unified or value["id"] not in unified:
            raise SDKError("source_process_tree")
        group = Path("/sys/fs/cgroup") / unified.lstrip("/")
        pids = []
        for child in (group / "cgroup.procs").read_text().split():
            value = Path("/proc") / child / "stat"
            pids.append([int(child), value.read_text().rsplit(")", 1)[1].split()[19]])
        return {"pids": pids, "cgroup": str(group / "cgroup.procs")}

    def tree_exited(self, tree):
        for pid, started in tree["pids"]:
            try:
                value = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
                if value == started:
                    return False
            except FileNotFoundError:
                pass
        try:
            return not Path(tree["cgroup"]).read_text().split()
        except FileNotFoundError:
            return True

    def writers(self, roots, ignored_pids):
        found = []
        for process in Path("/proc").iterdir():
            if not process.name.isdigit() or int(process.name) in ignored_pids:
                continue
            try:
                for descriptor in (process / "fd").iterdir():
                    try:
                        target = os.readlink(descriptor)
                        if not any(target == root or target.startswith(root.rstrip("/") + "/") for root in roots):
                            continue
                        info = (process / "fdinfo" / descriptor.name).read_text()
                        flags = re.search(r"^flags:\s*([0-7]+)", info, re.M)
                        if flags and (int(flags.group(1), 8) & os.O_ACCMODE) != os.O_RDONLY:
                            found.append({"pid": int(process.name)})
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                pass
        return found

    def web_ready(self):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:18131/sdk/index.php?m=user&f=login", timeout=5) as reply:
                return 200 <= reply.status < 400
        except (OSError, urllib.error.URLError):
            return False


class SDKColdBackup:
    def __init__(self, paths, *, runner=None, host=None, sources=None, clock=None,
                 sleep=time.sleep, monotonic=time.monotonic, expected_base=None, expected_views=34):
        self.paths, self.runner, self.host = paths, runner or SDKProcessRunner(), host or HostProbe()
        self.expected = sources or PINNED_SOURCES
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleep, self.monotonic = sleep, monotonic
        self.expected_base, self.expected_views = expected_base, expected_views
        self.journal_path = paths.private / "source-inflight.json"
        self.lifecycle, self.journal = None, {}

    def _save(self, phase):
        self.journal.update(phase=phase, updated_at=runtime.timestamp(self.clock()))
        runtime.atomic_write(self.journal_path, runtime.canonical(self.journal))

    def _recovery_save(self, phase):
        try:
            self._save(phase)
        except OSError:
            # Disk/journal failure cannot by itself prevent a safe same-ID restart.
            self.journal["recovery_journal_failed"] = True

    def _capture(self, args, stage, timeout=60):
        try:
            return self.runner.capture([DOCKER, *args], stage="capture", timeout=timeout)
        except runtime.BackupError:
            raise SDKError(stage) from None

    def _json(self, args, stage, *, timeout=60):
        try:
            value = json.loads(self._capture(args, stage, timeout=timeout))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise SDKError(stage) from None

    def _source(self, role, *, stable=True, timeout=60):
        expected = self.expected[role]
        value = self._json(["inspect", "--format", INSPECT, expected["id"]], "source_identity", timeout=timeout)
        labels, config = value.get("labels") or {}, value.get("host_config") or {}
        if (any(value.get(field) != expected[field] for field in ("id", "name", "image_id"))
                or mounts(value.get("mounts", [])) != mounts(expected["mounts"])
                or set(value.get("networks") or {}) != set(expected["networks"])
                or labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.service") != ("zentao" if role == "web" else "zentao-db")
                or config.get("Privileged") is not False or config.get("CapAdd") or config.get("Devices")
                or config.get("VolumesFrom") or config.get("PidMode") or config.get("IpcMode") == "host"
                or config.get("NetworkMode") not in set(expected["networks"])
                or config.get("RestartPolicy") != {"Name": "unless-stopped", "MaximumRetryCount": 0}):
            raise SDKError("source_identity")
        expected_ports = {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18131"}]} if role == "web" else {}
        if (config.get("PortBindings") or {}) != expected_ports:
            raise SDKError("source_identity")
        if stable and self.journal.get("sources") and config != self.journal["sources"][role]["host_config"]:
            raise SDKError("source_identity")
        value["mounts"] = mounts(value["mounts"])
        return value

    def _probe(self, flag, *arguments):
        try:
            output = self.runner.capture([sys.executable, str(Path(__file__).resolve()), flag,
                                          self.expected["db"]["id"], *arguments], stage="capture", timeout=180)
            return json.loads(output)
        except (runtime.BackupError, ValueError, TypeError):
            raise SDKError("database_evidence") from None

    def _writers(self, cold=False):
        source_ids = {value["id"] for value in self.expected.values()}
        roots = [value["mounts"][0]["Source"] for value in self.expected.values()]
        identities = self._capture(["ps", "--all", "--no-trunc", "--format", "{{.ID}}"], "writer_check").decode().split()
        for identity in identities:
            value = self._json(["inspect", "--format", INSPECT, identity], "writer_check")
            if value["id"] in source_ids:
                continue
            if set(value.get("networks") or {}) & set(self.expected["db"]["networks"]):
                raise SDKError("external_db_peer")
            for mount in value.get("mounts") or []:
                origin = mount.get("Source", "")
                if mount.get("RW") and any(origin == root or origin.startswith(root + "/") or root.startswith(origin.rstrip("/") + "/") for root in roots if origin):
                    raise SDKError("external_writer")
        ignored = set() if cold else {pid for tree in self.journal.get("process_trees", {}).values() for pid, _ in tree["pids"]}
        if self.host.writers(roots, ignored):
            raise SDKError("external_writer")

    def _preflight(self):
        if not runtime.IMAGE_ID.fullmatch(self.paths.encryptor_image_id):
            raise SDKError("encryptor_image")
        for directory, uid in ((self.paths.root, os.geteuid()), (self.paths.private, os.geteuid()),
                               (self.paths.ciphertext, self.paths.cipher_uid)):
            metadata = directory.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or directory.resolve() != directory or metadata.st_uid != uid or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise SDKError("directory_mode")
        public = runtime.file_identity(self.paths.public_key, stage="public_key", private=True)
        if public["size_bytes"] != 32:
            raise SDKError("public_key")
        public_volume = local_volume_identity(
            self._json(["volume", "inspect", "--format", runtime.VOLUME_FORMAT, PUBLIC_VOLUME], "public_key"),
            PUBLIC_VOLUME, stage="public_key")
        key_path = Path(public_volume["mountpoint"]) / "recovery-public-key"
        mounted_key = runtime.file_identity(key_path, stage="public_key")
        if mounted_key["sha256"] != public["sha256"] or mounted_key["size_bytes"] != 32:
            raise SDKError("public_key")
        configuration = {}
        for name in ("sdk.env", "sdk-compose.yaml"):
            value = (self.paths.deployment / name).lstat()
            if not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid() or stat.S_IMODE(value.st_mode) != 0o600:
                raise SDKError("configuration")
            configuration[name] = [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]
        if self.expected_base is None:
            runtime.file_identity(self.paths.inventory, stage="configuration", private=True)
            inventory = json.loads(self.paths.inventory.read_bytes())
            self.expected_base = {(row["schema"], row["table"]) for row in inventory["database_tables"]}
            if len(self.expected_base) != 307 or self.expected_views != 34:
                raise SDKError("schema_scope")
        values = {role: self._source(role, stable=False) for role in ("web", "db")}
        if any(value.get("running") is not True for value in values.values()):
            raise SDKError("initial_health")
        if (values["db"].get("health") or {}).get("Status") != "healthy" or not self.host.web_ready():
            raise SDKError("initial_health")
        self.journal.update(sources=values, configuration=configuration,
                            recovery_public_key={"sha256": public["sha256"], "size_bytes": 32},
                            public_key_volume_file=str(key_path),
                            process_trees={role: self.host.tree(value) for role, value in values.items()})
        trees = {}
        for role, value in self.expected.items():
            mount = value["mounts"][0]
            volume = self._json(["volume", "inspect", "--format", runtime.VOLUME_FORMAT, mount["Name"]], "source_volume")
            local_volume_identity(volume, mount["Name"], mountpoint=mount["Source"])
            trees[role] = tree_metadata(mount["Source"])
            if self.paths.root.is_relative_to(Path(mount["Source"])) or Path(mount["Source"]).is_relative_to(self.paths.root):
                raise SDKError("source_directory")
        self.journal["source_directories"] = trees
        required = 2 * sum(tree["estimated_tar_bytes"] for tree in trees.values()) + 64 * 1024 * 1024
        if shutil.disk_usage(self.paths.ciphertext).free < required:
            raise SDKError("disk_space")
        for image in {value["image_id"] for value in self.expected.values()} | {self.paths.encryptor_image_id}:
            if self._json(["image", "inspect", "--format", runtime.IMAGE_FORMAT, image], "image")["id"] != image:
                raise SDKError("image")
        for path, digest in CONFIG_HASHES.items():
            value = self._capture(["exec", self.expected["web"]["id"], "sha256sum", path], "configuration").decode()
            if value.split()[0] != digest:
                raise SDKError("configuration")
        if self._probe("--db-proof", "scheduler").get("event_scheduler") != "OFF":
            raise SDKError("event_scheduler")
        self._writers()
        self._save("preflight_verified")

    def _helper(self, kind, filename=None):
        options = ["--user", "10001:10001", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges:true", "--network", "none",
                   "-v", PUBLIC_VOLUME + ":/run/backup-secrets:ro"]
        if filename:
            options += ["-i", "-v", str(self.paths.ciphertext) + ":/backups", "-e", "TMPDIR=/backups",
                        "-e", "PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE=/run/backup-secrets/recovery-public-key",
                        "-e", "PLATFORM_REPLICA_BACKUP_PATH=/backups/" + filename]
            command = ["python", "-m", "app.cloud_replica.cli", "backup"]
        else:
            command = ["python", "-c", "import hashlib,json,pathlib;p=pathlib.Path('/run/backup-secrets/recovery-public-key');v=p.read_bytes();print(json.dumps({'sha256':hashlib.sha256(v).hexdigest(),'size_bytes':len(v)}))"]
        entry = self.lifecycle.create("sdk-" + kind, options, self.paths.encryptor_image_id, command)
        value = self._json(["inspect", "--format", INSPECT, entry["id"]], "helper_scope")
        config, actual = value["host_config"], value["mounts"]
        if (value.get("user") != "10001:10001" or value.get("log_driver") != "none"
                or config.get("NetworkMode") != "none" or config.get("Privileged") is not False
                or config.get("ReadonlyRootfs") is not True or config.get("CapDrop") != ["ALL"]
                or config.get("CapAdd") or config.get("RestartPolicy", {}).get("Name") != "no"
                or config.get("Devices") or config.get("DeviceRequests") or config.get("VolumesFrom")
                or config.get("PidMode") or config.get("IpcMode") == "host"
                or config.get("PortBindings") or config.get("PublishAllPorts")
                or config.get("SecurityOpt") not in (["no-new-privileges:true"], ["no-new-privileges"])
                or len(actual) != (2 if filename else 1)):
            raise SDKError("helper_scope")
        if {item["Destination"] for item in actual} != (
                {"/run/backup-secrets", "/backups"} if filename else {"/run/backup-secrets"}):
            raise SDKError("helper_scope")
        for mount in actual:
            if mount["Destination"] == "/run/backup-secrets":
                if mount.get("Name") != PUBLIC_VOLUME or mount.get("RW") is not False:
                    raise SDKError("helper_scope")
            elif mount["Destination"] == "/backups" and filename:
                if mount.get("Type") != "bind" or mount.get("Source") != str(self.paths.ciphertext) or mount.get("RW") is not True:
                    raise SDKError("helper_scope")
            else:
                raise SDKError("helper_scope")
        return entry

    def _stop(self, role):
        action = self.journal["source_actions"][role]
        self._source(role)
        action.update(stop_intent=True, stop_requested_at=runtime.timestamp(self.clock()))
        self._save(role + "_stop_intent")
        action["stop_call_started"] = True
        limit = WEB_TIMEOUT if role == "web" else DB_TIMEOUT
        try:
            self._capture(["stop", "--time", str(limit), self.expected[role]["id"]], role + "_stop", timeout=limit + 30)
            action["stop_cli_ok"] = True
        except SDKError as error:
            action["stop_cli_ok"] = False
            if str(error) == "interrupted":
                raise
        value = self._source(role)
        deadline = self.monotonic() + limit + 5
        while value.get("running") is True and self.monotonic() < deadline:
            self.sleep(1)
            value = self._source(role)
        action.update(stopped_observed=(value.get("running") is False and value.get("pid") == 0 and value.get("status") == "exited"),
                      daemon_state={key: value.get(key) for key in ("running", "status", "pid", "exit_code", "oom_killed", "finished_at")})
        self._save(role + "_stop_observed")
        if not action["stopped_observed"] or not self.host.tree_exited(self.journal["process_trees"][role]):
            raise SDKError(role + "_stop_unconfirmed")
        if role == "db":
            proof = self._probe("--shutdown-proof", action["stop_requested_at"], runtime.timestamp(self.clock()))
            action["shutdown_proof"] = proof
            self._save("db_shutdown_proof")
            if value.get("exit_code") != 0 or value.get("oom_killed") is not False or proof.get("shutdown_complete") is not True:
                raise SDKError("db_unclean_shutdown")

    def _quiesced(self):
        self._source("db")
        evidence = self._probe("--db-proof", "quiesced")
        objects = evidence.get("objects", [])
        base = {(row["schema"], row["table"]) for row in objects if row["type"] == "BASE TABLE"}
        views = [(row["schema"], row["table"]) for row in objects if row["type"] == "VIEW"]
        if (evidence.get("event_scheduler") != "OFF"
                or evidence.get("transaction") != "repeatable_read_read_only" or base != self.expected_base
                or len(views) != self.expected_views or len(set(views)) != len(views)
                or len(objects) != len(base) + len(views)
                or any(type(row.get("rows")) is not int or row["rows"] < 0 for row in objects)
                or any(row.get("engine") != "InnoDB" for row in objects if row["type"] == "BASE TABLE")):
            raise SDKError("quiesced_scope")
        content = runtime.canonical(evidence)
        filename = self.journal["set_id"] + "-quiesced.json"
        runtime.atomic_write(self.paths.private / filename, content)
        self.journal.update(quiesced_baseline=evidence, quiesced_baseline_evidence={
            "filename": filename, "sha256": hashlib.sha256(content).hexdigest()})
        self._save("quiesced_baseline_verified")

    def _cold(self):
        for role in ("web", "db"):
            value = self._source(role)
            if value.get("running") is not False or value.get("pid") != 0 or value.get("status") != "exited":
                raise SDKError("cold_state")
        self._writers(cold=True)
        key = runtime.file_identity(Path(self.journal["public_key_volume_file"]), stage="public_key")
        if key["sha256"] != self.journal["recovery_public_key"]["sha256"] or key["size_bytes"] != 32:
            raise SDKError("public_key")
        current = {}
        for role, value in self.expected.items():
            current[role] = tree_metadata(value["mounts"][0]["Source"])
            original = self.journal["source_directories"][role]
            if any(current[role][field] != original[field] for field in ("device", "inode", "mode", "uid", "gid")):
                raise SDKError("source_directory")
        if "cold_tree_snapshots" not in self.journal:
            self.journal["cold_tree_snapshots"] = current
            self.journal["snapshot_consistency"] = "two_sources_stopped_with_db_clean_shutdown"
            self._save("cold_sources_verified")
        elif current != self.journal["cold_tree_snapshots"]:
            raise SDKError("cold_directory_changed")
        for name, expected in self.journal["configuration"].items():
            value = (self.paths.deployment / name).lstat()
            if [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns] != expected:
                raise SDKError("configuration_changed")

    def _artifact(self, kind, scope, producer, *, source_role=None, file_path=None):
        self._cold()
        before = tree_metadata(self.expected[source_role]["mounts"][0]["Source"]) if source_role else None
        filename = kind + "-" + self.journal["set_id"] + ".orb"
        destination = self.paths.ciphertext / filename
        if destination.exists() or destination.is_symlink():
            raise SDKError("artifact_exists")
        entry = self._helper(kind, filename)
        started = runtime.timestamp(self.clock())
        output = self.runner.pipeline(producer, self.lifecycle.start_command(entry, interactive=True),
                                      stage="capture", timeout=1800)
        self.lifecycle.exited(entry, "capture_encryptor")
        self._cold()
        if source_role and before != tree_metadata(self.expected[source_role]["mounts"][0]["Source"]):
            raise SDKError("cold_directory_changed")
        receipt = json.loads(output)
        if receipt.get("status") != "backed_up" or any(type(receipt.get(key)) is not int or receipt[key] <= 0 for key in ("encrypted_size", "plaintext_size")):
            raise SDKError("encryption_receipt")
        identity = runtime.file_identity(destination, stage="capture_encryptor")
        file_stat = destination.lstat()
        if (identity["size_bytes"] <= receipt["encrypted_size"] or file_stat.st_uid != self.paths.cipher_uid
                or stat.S_IMODE(file_stat.st_mode) != 0o600):
            raise SDKError("ciphertext")
        artifact = {"kind": kind, "scope": scope, "filename": filename,
                    "capture_started_at": started, "capture_finished_at": runtime.timestamp(self.clock()),
                    "ciphertext_sha256": identity["sha256"], "file_size": identity["size_bytes"],
                    "encrypted_size": receipt["encrypted_size"], "plaintext_size": receipt["plaintext_size"],
                    "producer_returncode": 0, "encryptor_returncode": 0,
                    "encryptor_container_id": entry["id"], "encryptor_daemon_exit_code": entry["exit_code"]}
        if file_path:
            artifact.update(original_path=file_path, archive_member_basename=Path(file_path).name,
                            expected_file_sha256=CONFIG_HASHES[file_path])
        self.journal["artifacts"].append(artifact)
        self._save("captured_" + kind)

    def _capture_all(self):
        for role, kind, scope in (("db", "db-volume", "mariadb_data_directory"),
                                  ("web", "web-volume", "zentao_data_directory")):
            self._artifact(kind, scope, ["/usr/bin/tar", "--numeric-owner", "-C",
                                        self.expected[role]["mounts"][0]["Source"], "-cf", "-", "."], source_role=role)
        for kind, path in zip(ARTIFACT_KINDS[2:5], CONFIG_HASHES):
            self._artifact(kind, "container_file:" + path,
                           [sys.executable, str(Path(__file__).resolve()), "--copy-config",
                            self.expected["web"]["id"], path], file_path=path)
        self._artifact("deployment-config", "sdk_private_configuration",
                       ["/usr/bin/tar", "--numeric-owner", "-C", str(self.paths.deployment), "-cf", "-", "sdk.env", "sdk-compose.yaml"])

    def _await_db(self):
        deadline = self.monotonic() + READY_TIMEOUT
        while True:
            value = self._source("db")
            if value.get("running") is True and (value.get("health") or {}).get("Status") == "healthy":
                return
            if self.monotonic() >= deadline:
                raise SDKError("db_not_ready")
            self.sleep(2)

    def _settle_pending_stop(self, role):
        action = self.journal["source_actions"][role]
        if not action.get("stop_call_started") or action.get("stopped_observed"):
            return self._source(role)
        # A returned/lost Docker CLI is not proof that daemon stop has settled.
        # Only observe; never send a second stop or start against pending work.
        limit = (WEB_TIMEOUT if role == "web" else DB_TIMEOUT) + 35
        deadline = self.monotonic() + limit
        action["recovery_stop_observation_started_at"] = runtime.timestamp(self.clock())
        self._recovery_save(role + "_stop_pending")
        while True:
            remaining = max(0, deadline - self.monotonic())
            try:
                value = self._source(role, timeout=max(1, min(5, remaining)))
            except SDKError:
                value = None
            if value is not None:
                action["daemon_state"] = {key: value.get(key) for key in (
                    "running", "status", "pid", "exit_code", "oom_killed", "finished_at")}
                if value.get("running") is False and value.get("pid") == 0 and value.get("status") == "exited":
                    action.update(stopped_observed=True, reconciled_in_finally=True)
                    self._recovery_save(role + "_stop_reconciled")
                    return value
            if self.monotonic() >= deadline:
                action["stop_observation_unresolved"] = True
                self._recovery_save(role + "_stop_unresolved")
                raise SDKError(role + "_stop_unresolved")
            self.sleep(min(1, remaining))

    def _resume(self):
        actions = self.journal.get("source_actions", {})
        if not any(action.get("stop_call_started") for action in actions.values()):
            return True
        db, action = self._settle_pending_stop("db"), actions["db"]
        if action.get("stopped_observed") and db.get("running") is not True:
            action["restart_intent"] = True
            self._recovery_save("db_restart_intent")
            try:
                self._capture(["start", db["id"]], "db_restart")
                action["restart_cli_ok"] = True
            except SDKError:
                action["restart_cli_ok"] = False
        self._await_db()
        action["ready"] = True
        self._recovery_save("db_ready")
        web, action = self._settle_pending_stop("web"), actions["web"]
        if action.get("stopped_observed"):
            if web.get("running") is not True:
                action["restart_intent"] = True
                self._recovery_save("web_restart_intent")
                try:
                    self._capture(["start", web["id"]], "web_restart")
                    action["restart_cli_ok"] = True
                except SDKError:
                    action["restart_cli_ok"] = False
            deadline = self.monotonic() + 60
            while self._source("web").get("running") is not True or not self.host.web_ready():
                if self.monotonic() >= deadline:
                    raise SDKError("web_not_ready")
                self.sleep(2)
        action["ready"] = True
        self._recovery_save("services_restored")
        return True

    def run(self):
        runtime.private_directory(self.paths.root)
        runtime.private_directory(self.paths.private)
        if self.journal_path.exists() or self.journal_path.is_symlink():
            raise SDKError("unresolved")
        lock = os.open(self.paths.private / "sdk-cold.lock",
                       os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        old = {}
        try:
            value = os.fstat(lock)
            if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
                    or value.st_uid != os.geteuid() or stat.S_IMODE(value.st_mode) != 0o600):
                raise SDKError("lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise SDKError("lock") from None
            if any(path.exists() or path.is_symlink() for path in (
                    self.journal_path, self.paths.private / "backup-inflight.json")):
                raise SDKError("unresolved")
            set_id = "sdk-cold-" + self.clock().strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8]
            self.journal = {"version": 1, "set_id": set_id, "backup_kind": "sdk_cold_onsite_rollback",
                            "status": "in_progress", "created_at": runtime.timestamp(self.clock()),
                            "snapshot_consistency": "not_established",
                            "source_actions": {role: {} for role in ("web", "db")}, "artifacts": [],
                            "off_machine_sdk_dr_validated": False}
            self._save("created")
            primary = cleanup = recovery = None
            def interrupted(_number, _frame):
                raise SDKError("interrupted")
            old = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
            for number in old:
                signal.signal(number, interrupted)
            try:
                self._preflight()
                self.lifecycle = runtime.ContainerLifecycle(self.paths.private, self.runner, DOCKER, self.clock)
                entry = self._helper("public-proof")
                receipt = json.loads(self.runner.capture(self.lifecycle.start_command(entry), stage="public_key"))
                self.lifecycle.exited(entry, "public_key")
                if receipt != {"sha256": self.journal["recovery_public_key"]["sha256"], "size_bytes": 32}:
                    raise SDKError("public_key")
                self._stop("web")
                self._quiesced()
                self._stop("db")
                self._capture_all()
                for path in [*(self.paths.ciphertext / item["filename"] for item in self.journal["artifacts"]), self.paths.ciphertext]:
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            except BaseException as error:
                primary = error if isinstance(error, SDKError) else SDKError(
                    "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "capture_failed")
            finally:
                for number in old:
                    signal.signal(number, signal.SIG_IGN)
                try:
                    native_clear = self.runner.native_stopped()
                except Exception:
                    native_clear = False
                self.journal["native_producers_stopped"] = native_clear
                if not native_clear:
                    cleanup = SDKError("native_cleanup_unconfirmed")
                if self.lifecycle:
                    try:
                        self.lifecycle.close()
                    except Exception:
                        cleanup = cleanup or SDKError("helper_cleanup_unconfirmed")
                    self.journal["lifecycle"] = self.lifecycle.receipt()
                try:
                    # Native tar/cp/RR producers cannot write business sources.
                    # Unknown cleanup blocks backup success, never safe same-ID recovery.
                    self.journal["services_restored"] = self._resume()
                except Exception as error:
                    recovery = error if isinstance(error, SDKError) else SDKError("service_recovery")
                if self.journal.get("recovery_journal_failed"):
                    recovery = recovery or SDKError("recovery_journal")
                self.journal.update(status="failed" if primary or cleanup or recovery else "complete",
                                    completed_at=runtime.timestamp(self.clock()),
                                    failure_stage=str(recovery or cleanup or primary) if primary or cleanup or recovery else None,
                                    blocked=bool(cleanup or recovery))
                self._save("complete" if self.journal["status"] == "complete" else "failed")
                runtime.atomic_write(self.paths.private / (set_id + ".json"), runtime.canonical(self.journal))
                if not self.journal["blocked"]:
                    self.journal_path.unlink()
                    descriptor = os.open(self.paths.private, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            if primary or cleanup or recovery:
                raise recovery or cleanup or primary
            return self.journal
        finally:
            for number, handler in old.items():
                signal.signal(number, handler)
            os.close(lock)


def database_proof(identity, mode):
    if identity != PINNED_SOURCES["db"]["id"]:
        raise SDKError("database_identity")
    command = [DOCKER, "exec", "-i", identity, "sh", "-c",
               'export MYSQL_PWD="$MARIADB_ROOT_PASSWORD"; exec timeout --signal=TERM --kill-after=5s 120s mariadb -u root --batch --skip-column-names --raw --default-character-set=utf8mb4']
    def sql(statement):
        result = subprocess.run(command, input=statement, capture_output=True, text=True, timeout=150)
        if result.returncode:
            raise SDKError("database_query")
        return result.stdout.splitlines()
    scheduler = sql("SELECT @@GLOBAL.event_scheduler;")[0]
    if mode == "scheduler":
        return {"event_scheduler": scheduler}
    if mode != "quiesced" or scheduler != "OFF":
        raise SDKError("event_scheduler")
    schema_sql = ("SELECT TABLE_SCHEMA,TABLE_NAME,TABLE_TYPE,COALESCE(ENGINE,'') FROM information_schema.TABLES "
                  "WHERE TABLE_SCHEMA NOT IN ('mysql','information_schema','performance_schema','sys') "
                  "ORDER BY TABLE_SCHEMA,TABLE_NAME;")
    before = sql(schema_sql)
    objects = [dict(zip(("schema", "table", "type", "engine"), row.split("\t"))) for row in before]
    statements = ["SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;",
                  "SET TRANSACTION READ ONLY;", "START TRANSACTION WITH CONSISTENT SNAPSHOT;"]
    for index, item in enumerate(objects):
        quote = lambda value: chr(96) + value.replace(chr(96), chr(96) * 2) + chr(96)
        statements.append(f"SELECT {index},COUNT(*) FROM {quote(item['schema'])}.{quote(item['table'])};")
    statements.append("COMMIT;")
    for line in sql("\n".join(statements)):
        index, rows = line.split("\t")
        objects[int(index)]["rows"] = int(rows)
    if sql(schema_sql) != before or sql("SELECT @@GLOBAL.event_scheduler;")[0] != "OFF" or any("rows" not in item for item in objects):
        raise SDKError("quiesced_changed")
    return {"event_scheduler": "OFF", "transaction": "repeatable_read_read_only",
            "observed_at": runtime.timestamp(datetime.now(UTC)), "objects": objects}


def shutdown_proof(identity, since, until):
    if identity != PINNED_SOURCES["db"]["id"]:
        raise SDKError("database_identity")
    result = subprocess.run([DOCKER, "logs", "--timestamps", "--since", since, "--until", until, identity],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise SDKError("shutdown_log")
    begin = datetime.fromisoformat(since.replace("Z", "+00:00"))
    end = datetime.fromisoformat(until.replace("Z", "+00:00"))
    if begin.tzinfo is None or end.tzinfo is None or begin > end:
        raise SDKError("shutdown_log")
    pattern = (r"(?:\d{4}-\d{2}-\d{2}(?:T\d{2}|[ \t]+\d{1,2}):\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\s+)?"
               r"(?:\d+\s+)?\[Note\]\s+(?:/\S*/)?(?:mariadbd|mysqld):\s+Shutdown complete\s*")
    for line in result.stdout.splitlines():
        try:
            stamp, body = line.split(" ", 1)
            observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if (observed.tzinfo is not None and begin <= observed <= end
                    and re.fullmatch(pattern, body, re.I)):
                return {"shutdown_complete": True, "marker_timestamp": runtime.timestamp(observed)}
        except ValueError:
            pass
    return {"shutdown_complete": False, "marker_timestamp": None}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--copy-config":
        try:
            copy_config(*argv[1:])
            return 0
        except Exception:
            return 1
    if argv and argv[0] in {"--db-proof", "--shutdown-proof"}:
        try:
            value = database_proof(*argv[1:]) if argv[0] == "--db-proof" else shutdown_proof(*argv[1:])
            print(json.dumps(value, sort_keys=True))
            return 0
        except Exception:
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--deployment-dir", type=Path, default=Path("/opt/orbbec-agent-platform/private"))
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--encryptor-image-id", required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    paths = SDKPaths(arguments.work_root, arguments.deployment_dir, arguments.public_key,
                     arguments.encryptor_image_id, inventory=arguments.inventory)
    backup = SDKColdBackup(paths)
    try:
        report = backup.run()
        print(json.dumps({"status": "complete", "set_id": report["set_id"], "artifacts": 6,
                          "off_machine_sdk_dr_validated": False}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "stage": str(error) if isinstance(error, SDKError) else "unexpected",
                          "blocked": backup.journal_path.exists()}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
