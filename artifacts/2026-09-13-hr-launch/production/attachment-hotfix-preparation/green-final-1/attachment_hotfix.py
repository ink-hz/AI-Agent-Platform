"""Fixed host-only attachment maintenance; no SSH, credential issuance or canary.

Run only in the existing authorized maintenance window after independent review.
Any failure retains owned locks and a fail-closed marker; never restore old code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path("/opt/orbbec-agent-platform")
PRIVATE = ROOT / "private"
RELEASE = "ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7"
CURRENT = "65e7fbd14a3cbe99883b0b31e31b5705d183d1f9"
IMAGE = "sha256:2631b6a5090d49a51d187b88c6c38acc0da867ebc985c44cf79bd2db338d2e59"
OLD = "908c2f63e1de01f5226d6ddf8eb87dd4d1d3ff1edc45e534193f08203f81f819"
OLD_IMAGE = "sha256:ee24975e38f8ef51cac4e6036aa92c42e46d508c91615773eeba181ee38eb1b1"
POSTGRES = "12ef0c782a5e458b9e52dbe40ffaa3aa877e07269ddea52051e93d258ebdbb0e"
SERVICE = "orbbec-agent-platform-platform-attachments-1"
INPUTS_SHA = "a1740c60f8d10890ad56f84cb8d5760cfa82a678c8bf4d7d2c8459a41eccac93"
LOCK_SHA = "45c2d3fce8a5ef2e3dd6306eaa15b7cdbfc85072bed164033684f84b425a4eb4"
ERASURE_SHA = "e4862777c8e256fc3a37c6fe73cb8ad401329b660cbf69c05194768f60bd93be"
HELPERS = {
    "hr_agent_migrate.py": "381dbfbcf89bdc286dc1544982705f656ba9b1a0f2ba4442769f7498a56455b2",
    "preflight-execution-job-kind.sh": "88644e92079949a49e695474ab0c5d15e77cc238d13116e76db880f14d2e3b2e",
}
MIGRATIONS = {
    "100_attachment_erasure_worker_access.sql": "15355874fce1ea58d00056eb07233a0fb3ef4a3cd7e807ea6e6608deb3668177",
    "102_hr_execution_cutover.sql": "70fd110bb17c5471e0206ccb62886822578af19182cad67caa97708e23ac0ce6",
    "103_hr_execution_drain_occupancy.sql": "1795af66ae8ae5034bc0cb7385cd51ac3485601611a4be7258517bd40aadd6d0",
    "104_hr_execution_drain_terminal_contract.sql": "cb25b4f81b01bfd7ee3ea3056604b32794c3925ff4c336254d238456fa7a4a8d",
    "105_hr_cloud_resume.sql": "cef71adb2bee76cc2dc234a07c4f2a20e7f247010bf27f84fd57ec77defda20f",
}
BACKUP = Path(
    "/data/orbbec-agent-platform/private-backups/hr-cloud-35ae14bc5f974a318c9c8ff1a4e4a785"
)
BACKUP_FILES = {
    "production.dump": (
        291124812,
        "bddb8caf9ac7c70d96c27d472a414d3682f658c4295548aff43bffeb1723671e",
    ),
    "globals.sql": (
        6458,
        "38863abce6d2b02e905e1f81a47c874a44c0e33d6e769a1ef6219a7211f704f6",
    ),
    "production-schema.sql": (
        1450129,
        "d8eb99087238a71eb56cdc877e184b2d2262989eb2e8f6051ad9aacc224c1a46",
    ),
    "production-ledger.json": (
        8732,
        "53a37cfcc9575a875725343481b1f814d7a8f8e45ef610dbd69d5ff0fbdd0ce8",
    ),
}
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "DOCKER_HOST": "unix:///var/run/docker.sock",
    "PLATFORM_IMAGE": IMAGE,
}


class HotfixFailure(Exception):
    pass


class HotfixInterrupted(BaseException):
    def __init__(self, number):
        self.number = number


def interrupted(number, _frame):
    raise HotfixInterrupted(number)


def require(condition, code):
    if not condition:
        raise HotfixFailure(code)


def command(argv, *, timeout=10, grace=5):
    """Reap this exact client group; daemon/container state needs separate proof."""
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=ENV,
        start_new_session=True,
    )
    try:
        stdout, _stderr = proc.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, HotfixInterrupted) as error:
        previous = {number: signal.getsignal(number) for number in SIGNALS}
        for number in SIGNALS:
            signal.signal(number, signal.SIG_IGN)
        try:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.communicate(timeout=grace)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate(timeout=5)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        if isinstance(error, HotfixInterrupted):
            raise
        raise HotfixFailure("command_timeout") from None
    require(proc.returncode == 0, "command_failed")
    return stdout.decode().strip()


def digest(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), "file_invalid")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def protected(path, directory=False):
    info = path.lstat()
    require(
        info.st_uid == 0
        and not path.is_symlink()
        and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
        and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
        "private_path_invalid",
    )


def atomic(path, value):
    temp = path.with_name(path.name + "." + uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def match_snapshot(expected, actual):
    require(expected == actual, "peer_changed")


class Hotfix:
    def __init__(self, inputs, helpers):
        self.inputs_path, self.helper_input = Path(inputs), Path(helpers)
        self.run_id = uuid4().hex
        self.work = PRIVATE / "maintenance" / ("attachment-hotfix-" + self.run_id)
        self.release = ROOT / "releases" / RELEASE
        self.compose_file = ROOT / "releases" / CURRENT / "deploy/cloud/compose.yaml"
        self.lock_helper = self.release / "deploy/cloud/deploy-input-lock.py"
        self.action = PRIVATE / "agent-brain-action.lock"
        self.marker = PRIVATE / "attachment-hotfix.failclosed"
        self.token, self.owner_inode = str(uuid4()), None
        self.new_id, self.stopping_old, self.migration_started = None, False, False
        self.action_owned, self.input_attempted, self.probe_attempted = (
            False,
            False,
            False,
        )
        self.marker_created = False
        self.receipt = {
            "run_id": self.run_id,
            "status": "running",
            "stage": "validation",
            "events": [],
        }

    def record(self, stage, **values):
        self.receipt.update(stage=stage, **values)
        self.receipt["events"].append({"stage": stage, "time": time.time()})
        if self.work.is_dir():
            atomic(self.work / "receipt.json", self.receipt)

    def docker(self, *args, timeout=10):
        return command(
            ["/usr/bin/docker", "--host", ENV["DOCKER_HOST"], *args], timeout=timeout
        )

    def compose(self, *args):
        return self.docker(
            "compose",
            "--project-name",
            "orbbec-agent-platform",
            "--env-file",
            str(PRIVATE / "platform.env"),
            "-f",
            str(self.compose_file),
            *args,
            timeout=60,
        )

    def rows(self):
        ids = self.docker("ps", "-aq", "--no-trunc").split()
        return json.loads(self.docker("inspect", *ids)) if ids else []

    def peers(self):
        result = {}
        for row in self.rows():
            name = row["Name"].lstrip("/")
            if name == SERVICE or name == "attachment-hotfix-probe-" + self.run_id:
                continue
            result[name] = {
                "id": row["Id"],
                "image": row["Image"],
                "running": row["State"]["Running"],
                "started_at": row["State"]["StartedAt"],
                "restart_count": row["RestartCount"],
                "restart_policy": row["HostConfig"]["RestartPolicy"]["Name"],
            }
        return result

    def at(self, identity):
        return next(
            (
                row
                for row in self.rows()
                if row["Id"] == identity or row["Name"] == "/" + identity
            ),
            None,
        )

    def admin(self, sql, *, write=False):
        options = "-c statement_timeout=5000 -c lock_timeout=2000" + (
            "" if write else " -c default_transaction_read_only=on"
        )
        return self.docker(
            "exec",
            "-e",
            "PGOPTIONS=" + options,
            POSTGRES,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "platform_owner",
            "-d",
            "agent_platform_control",
            "-c",
            sql,
        )

    def at_rest(self):
        value = self.admin(
            "select (select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid where r.rolname in ('platform_control_owner','platform_control_owner_preview'))::text || '|' || (select count(*) from pg_stat_activity where usename in ('platform_control_migrator','platform_control_migrator_preview'))::text"
        )
        require(value == "0|0", "migration_cleanup_unverified")

    def check_inputs(self):
        require(
            (ROOT / "current").resolve() == ROOT / "releases" / CURRENT,
            "current_changed",
        )
        protected(PRIVATE / "platform.env")
        require(
            digest(PRIVATE / "platform.env") == self.inputs["platform_env_sha256"],
            "environment_changed",
        )
        require(
            digest(self.compose_file) == self.inputs["current_compose_sha256"],
            "compose_changed",
        )
        require(digest(self.lock_helper) == LOCK_SHA, "lock_helper_changed")
        for name, expected in MIGRATIONS.items():
            require(
                digest(self.release / "backend/control_migrations" / name) == expected,
                "migration_changed",
            )
        for name, expected in HELPERS.items():
            require(digest(self.work / name) == expected, "helper_changed")
        match_snapshot(
            {k: v for k, v in self.inputs["containers"].items() if k != SERVICE},
            self.peers(),
        )

    def preflight(self):
        require(os.getuid() == 0, "root_required")
        protected(PRIVATE, True)
        require(
            not self.marker.exists() and not self.marker.is_symlink(),
            "failclosed_present",
        )
        protected(self.inputs_path)
        require(digest(self.inputs_path) == INPUTS_SHA, "input_snapshot_changed")
        self.inputs = json.loads(self.inputs_path.read_text())
        parent = self.work.parent
        if not parent.exists():
            parent.mkdir(mode=0o700)
        protected(parent, True)
        self.work.mkdir(mode=0o700)
        (self.work / "migration-receipts").mkdir(mode=0o700)
        for name, expected in HELPERS.items():
            source = self.helper_input / name
            require(digest(source) == expected, "helper_input_changed")
            fd = os.open(self.work / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        self.record(
            "inputs",
            inputs_sha256=INPUTS_SHA,
            helper_sha256=HELPERS,
            image=IMAGE,
            release=RELEASE,
        )
        self.check_inputs()
        protected(BACKUP, True)
        protected(BACKUP / "receipt.json")
        backup = json.loads((BACKUP / "receipt.json").read_text())
        require(
            backup.get("deployment_id") == "35ae14bc5f974a318c9c8ff1a4e4a785"
            and backup.get("status") == "completed"
            and backup.get("cleanup_verified") is True
            and backup.get("backup_sessions_zero") is True
            and backup.get("archive_list_verified") is True
            and backup.get("ledger_count") == 95,
            "backup_unverified",
        )
        for name, (size, checksum) in BACKUP_FILES.items():
            path = BACKUP / name
            protected(path)
            require(
                path.stat().st_size == size and digest(path) == checksum,
                "backup_changed",
            )
        self.baseline = json.loads((BACKUP / "production-ledger.json").read_text())
        require(
            [row["version"] for row in self.baseline] == list(range(1, 96)),
            "baseline_invalid",
        )
        old = self.at(OLD)
        require(
            old
            and old["Image"] == OLD_IMAGE
            and old["State"]["Running"]
            and old["Name"] == "/" + SERVICE,
            "old_identity_changed",
        )
        require(self.at(POSTGRES) is not None, "postgres_changed")
        require(
            self.docker("image", "inspect", "--format", "{{.Id}}", IMAGE) == IMAGE,
            "image_changed",
        )
        help_text = self.compose("up", "--help")
        require(
            all(
                flag in help_text
                for flag in ("--no-start", "--no-deps", "--force-recreate")
            ),
            "compose_command_unavailable",
        )
        rendered = json.loads(self.compose("config", "--format", "json"))["services"][
            "platform-attachments"
        ]
        require(
            rendered["image"] == IMAGE
            and rendered["command"]
            == ["python", "-m", "app.attachments.worker_runtime", "all"],
            "compose_target_invalid",
        )
        self.record("preflight_complete", backup_verified=True, restore_rehearsal=False)

    def acquire(self):
        self.action.mkdir(mode=0o700, exist_ok=False)
        self.action_owned = True
        fd = os.open(self.action / "owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.owner_inode = os.fstat(fd).st_ino
        with os.fdopen(fd, "w") as stream:
            stream.write(self.token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.input_attempted = True
        command(
            [
                "/usr/bin/python3",
                "-I",
                str(self.lock_helper),
                "acquire",
                RELEASE,
                self.run_id,
            ],
            timeout=15,
        )
        self.validate_locks()
        self.check_inputs()
        self.at_rest()
        ledger = json.loads(
            self.admin(
                "select json_agg(json_build_object('version',version,'sha256',sha256) order by version) from platform_control.schema_migrations"
            )
        )
        require(ledger == self.baseline, "baseline_changed")
        self.probe_attempted = True
        name = "attachment-hotfix-probe-" + self.run_id
        value = self.docker(
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            "org.orbbec.hotfix=" + self.run_id,
            "--network",
            "none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--user",
            "10001:10001",
            IMAGE,
            "python",
            "-c",
            "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('/app/backend/app/attachments/erasure.py').read_bytes()).hexdigest())",
            timeout=20,
        )
        require(value == ERASURE_SHA, "image_erasure_source_changed")
        self.probe_attempted = False

    def validate_locks(self):
        protected(self.action, True)
        owner = self.action / "owner"
        protected(owner)
        require(
            owner.stat().st_ino == self.owner_inode
            and owner.read_text() == self.token + "\n"
            and {p.name for p in self.action.iterdir()} == {"owner"},
            "action_lock_changed",
        )
        command(
            [
                "/usr/bin/python3",
                "-I",
                str(self.lock_helper),
                "validate",
                RELEASE,
                self.run_id,
            ],
            timeout=15,
        )

    def verify_old_stopped(self):
        row = self.at(OLD)
        require(
            row
            and row["Id"] == OLD
            and row["Image"] == OLD_IMAGE
            and row["Name"] == "/" + SERVICE
            and row["State"]["Running"] is False
            and row["HostConfig"]["RestartPolicy"]["Name"] == "no",
            "old_stop_unverified",
        )

    def stop_old(self):
        row = self.at(OLD)
        require(
            row
            and row["Id"] == OLD
            and row["Image"] == OLD_IMAGE
            and row["Name"] == "/" + SERVICE,
            "old_stop_unverified",
        )
        fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.marker_created = True
        with os.fdopen(fd, "w") as stream:
            stream.write(self.run_id + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.stopping_old = True
        self.record("old_stop_pending", old_id=OLD, old_image=OLD_IMAGE)
        self.stop_exact(OLD, OLD_IMAGE)
        self.verify_old_stopped()
        self.record("old_stopped")

    def stop_exact(self, identity, image):
        row = self.at(identity)
        if row is None:
            return
        require(
            row["Id"] == identity and row["Image"] == image, "cleanup_identity_changed"
        )
        self.docker("update", "--restart=no", identity)
        self.docker("stop", "--time", "5", identity, timeout=15)
        row = self.at(identity)
        require(
            row
            and not row["State"]["Running"]
            and row["HostConfig"]["RestartPolicy"]["Name"] == "no",
            "stop_unverified",
        )

    def child_receipt(self):
        files = list((self.work / "migration-receipts").glob("*.json"))
        require(len(files) == 1, "migration_receipt_unverified")
        value = json.loads(files[0].read_text())
        require(
            value.get("helper_sha256") == HELPERS["hr_agent_migrate.py"]
            and value.get("image") == IMAGE
            and value.get("migration_set") == "root"
            and value.get("requested_environment") == "production",
            "migration_receipt_unverified",
        )
        return value

    def migrate(self):
        self.validate_locks()
        self.check_inputs()
        self.verify_old_stopped()
        self.migration_started = True
        self.record("migration_pending")
        command(
            [
                "/usr/bin/python3",
                "-I",
                str(self.work / "hr_agent_migrate.py"),
                str(self.release),
                str(PRIVATE),
                IMAGE,
                POSTGRES,
                "--migration-set",
                "root",
                "--environment",
                "production",
                "--migration-timeout",
                "900",
                "--command-timeout",
                "10",
                "--receipt-dir",
                str(self.work / "migration-receipts"),
            ],
            timeout=1000,
            grace=120,
        )
        receipt = self.child_receipt()
        require(
            receipt.get("status") == "completed"
            and receipt.get("cleanup_verified") is True,
            "migration_cleanup_unverified",
        )
        self.at_rest()
        expected = {int(name[:3]): checksum for name, checksum in MIGRATIONS.items()}
        ledger = json.loads(
            self.admin(
                "select json_agg(json_build_object('version',version,'sha256',sha256) order by version) from platform_control.schema_migrations"
            )
        )
        require(
            {row["version"]: row["sha256"] for row in ledger}
            == {**{row["version"]: row["sha256"] for row in self.baseline}, **expected},
            "root_ledger_changed",
        )
        columns = (
            ("uploads", "attachment_id"),
            ("uploads", "write_attempt_id"),
            ("upload_write_attempts", "attachment_id"),
            ("upload_write_attempts", "attempt_id"),
            ("upload_write_attempts", "object_ref_ciphertext"),
            ("upload_write_attempts", "object_ref_key_version"),
        )
        query = "select " + " and ".join(
            "has_column_privilege('platform_control_maintenance','platform_attachments."
            + table
            + "','"
            + col
            + "','SELECT')"
            for table, col in columns
        )
        require(self.admin(query) == "t", "migration100_permissions_missing")
        self.record(
            "root_verified",
            migration100_permissions=6,
            root_ledger=ledger,
            migration_cleanup_verified=True,
        )

    def recreate(self):
        self.validate_locks()
        self.check_inputs()
        self.verify_old_stopped()
        self.record("recreate_stopped_pending")
        self.compose(
            "up", "--no-start", "--no-deps", "--force-recreate", "platform-attachments"
        )
        ids = self.compose("ps", "-aq", "platform-attachments").split()
        require(len(ids) == 1 and ids[0] != OLD, "new_identity_unverified")
        row = self.at(ids[0])
        require(
            row
            and row["Image"] == IMAGE
            and not row["State"]["Running"]
            and row["Name"] == "/" + SERVICE
            and row["Config"]["Labels"].get("com.docker.compose.project")
            == "orbbec-agent-platform"
            and row["Config"]["Labels"].get("com.docker.compose.service")
            == "platform-attachments"
            and row["Config"]["Cmd"]
            == ["python", "-m", "app.attachments.worker_runtime", "all"],
            "new_identity_unverified",
        )
        self.new_id = row["Id"]
        self.record("new_identity_owned", new_id=self.new_id)
        self.docker("update", "--restart=no", self.new_id)
        row = self.at(self.new_id)
        require(
            row
            and row["Image"] == IMAGE
            and not row["State"]["Running"]
            and row["HostConfig"]["RestartPolicy"]["Name"] == "no",
            "new_stop_unverified",
        )
        self.record("new_stopped_verified")
        self.check_inputs()
        self.validate_locks()
        self.record("new_start_pending")
        self.docker("start", self.new_id)
        deadline = time.monotonic() + 90
        while True:
            row = self.at(self.new_id)
            require(
                row and row["Image"] == IMAGE and row["State"]["Running"],
                "new_not_running",
            )
            if row["State"].get("Health", {}).get("Status") == "healthy":
                break
            require(time.monotonic() < deadline, "new_health_timeout")
            time.sleep(2)
        self.check_inputs()
        self.validate_locks()
        self.at_rest()
        self.record("desired_restart_policy_pending")
        self.docker("update", "--restart=unless-stopped", self.new_id)
        row = self.at(self.new_id)
        require(
            row
            and row["Image"] == IMAGE
            and row["State"]["Running"]
            and row["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped",
            "new_restart_policy_unverified",
        )
        command(
            [
                "/usr/bin/python3",
                "-I",
                str(self.lock_helper),
                "release",
                RELEASE,
                self.run_id,
            ],
            timeout=15,
        )
        self.input_attempted = False
        protected(self.action / "owner")
        require(
            (self.action / "owner").stat().st_ino == self.owner_inode
            and (self.action / "owner").read_text() == self.token + "\n",
            "action_lock_changed",
        )
        tombstone = self.action.with_name(self.action.name + ".releasing." + self.token)
        self.action.rename(tombstone)
        (tombstone / "owner").unlink()
        tombstone.rmdir()
        self.action_owned = False
        self.record(
            "installed",
            status="installed_awaiting_erasure_canary",
            failclosed_marker_retained=True,
            physical_erasure_verified=False,
        )

    def fail_closed(self):
        verified = True
        for identity, image in (
            (self.new_id, IMAGE),
            (OLD if self.stopping_old else None, OLD_IMAGE),
        ):
            if identity:
                try:
                    self.stop_exact(identity, image)
                except (
                    HotfixFailure,
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    subprocess.TimeoutExpired,
                ):
                    verified = False
        if self.probe_attempted:
            try:
                name = "attachment-hotfix-probe-" + self.run_id
                row = self.at(name)
                if row:
                    require(
                        row["Image"] == IMAGE
                        and row["Config"]["Labels"].get("org.orbbec.hotfix")
                        == self.run_id,
                        "probe_identity_changed",
                    )
                    self.stop_exact(row["Id"], IMAGE)
                    self.docker("rm", row["Id"])
            except (
                HotfixFailure,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                subprocess.TimeoutExpired,
            ):
                verified = False
        if self.migration_started:
            receipt = None
            try:
                receipt = self.child_receipt()
                name = receipt.get("container_name")
                if name:
                    require(
                        re.fullmatch(r"hr-migration-[0-9a-f-]{36}-production", name),
                        "migration_identity_unverified",
                    )
                    row = self.at(name)
                    if row:
                        require(
                            row["Image"] == IMAGE
                            and (receipt.get("container_id") in (None, row["Id"]))
                            and row["Config"]["Cmd"]
                            == ["python", "-m", "app.control_plane.migrate"],
                            "migration_identity_unverified",
                        )
                        self.stop_exact(row["Id"], IMAGE)
                        self.docker("rm", row["Id"])
            except (
                HotfixFailure,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                subprocess.TimeoutExpired,
            ):
                verified = False
            # Docker failure cannot skip revocation of this proved grant. Existing
            # SET ROLE sessions still require zero-session proof; never kill peers.
            try:
                require(receipt is not None, "migration_receipt_unverified")
                if any(
                    event.get("stage") == "grant_pending"
                    and event.get("environment") == "production"
                    for event in receipt.get("events", [])
                ):
                    self.admin(
                        "revoke platform_control_owner from platform_control_migrator",
                        write=True,
                    )
                self.at_rest()
            except (
                HotfixFailure,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                subprocess.TimeoutExpired,
            ):
                verified = False
        # No lock or fail-closed marker removal on failure, even if stops succeeded.
        return verified

    def run(self):
        try:
            self.preflight()
            self.acquire()
            self.stop_old()
            self.migrate()
            self.recreate()
            return 0
        except (
            HotfixFailure,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.TimeoutExpired,
            HotfixInterrupted,
        ) as error:
            prior = {number: signal.getsignal(number) for number in SIGNALS}
            for number in SIGNALS:
                signal.signal(number, signal.SIG_IGN)
            try:
                clean = self.fail_closed()
                self.record(
                    "failed_keep_stopped",
                    status="failed",
                    failure=str(error)
                    if isinstance(error, HotfixFailure)
                    else type(error).__name__,
                    cleanup_verified=clean,
                    action_lock_owned=self.action_owned,
                    input_lock_acquisition_attempted=self.input_attempted,
                    failclosed_marker_created=self.marker_created,
                )
            finally:
                for number, handler in prior.items():
                    signal.signal(number, handler)
            return 128 + error.number if isinstance(error, HotfixInterrupted) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-file", required=True)
    parser.add_argument("--helper-input-dir", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    for number in SIGNALS:
        signal.signal(number, interrupted)
    runner = Hotfix(args.inputs_file, args.helper_input_dir)
    code = runner.run()
    print(json.dumps({"status": runner.receipt["status"], "run_id": runner.run_id}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
