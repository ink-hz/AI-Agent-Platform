#!/usr/bin/env python3
"""Bounded local supervisor for explicitly authorized HR schema deployment.

Owner membership is cluster-wide and permits the whole control DB. It is never
an HR-only grant. SIGKILL/host loss require external reconciliation of receipt,
container, sessions and membership; no in-process supervisor can trap them.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4


class DeploymentFailure(Exception):
    pass


class DeploymentSignal(BaseException):
    def __init__(self, number):
        self.number = number


class Supervisor:
    def __init__(self, args):
        self.args = args
        self.migration_set = args.migration_set
        self.environments = (0, 1) if args.environment == "all" else (0,)
        self.docker = os.environ.get("HR_MIGRATION_DOCKER", "/usr/bin/docker")
        self.release = Path(args.release).resolve(strict=True)
        self.private = Path(args.private)
        self.run_id = str(uuid4())
        self.container = None
        self.name = None
        self.granted_owner = None
        self.granted_migrator = None
        self.cleaning = False
        self.signal_number = None
        self.lock_descriptor = None
        self.at_rest_unresolved = False
        self.receipt_path = None
        self.receipt = {
            "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "image": args.image if hasattr(args, "image") else None,
            "migration_set": self.migration_set,
            "requested_environment": args.environment,
            "status": "running",
            "stage": "validation",
            "environment": None,
            "container_id": None,
            "container_name": None,
            "cleanup_verified": True,
            "events": [],
        }

    def record(self, stage, **fields):
        self.receipt.update(stage=stage, **fields)
        self.receipt["events"].append(
            {
                "stage": stage,
                "time": time.time(),
                "environment": self.receipt["environment"],
                "cleanup_verified": self.receipt["cleanup_verified"],
            }
        )
        if self.receipt_path is not None:
            temporary = self.receipt_path.with_suffix(".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(self.receipt, stream, sort_keys=True, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.receipt_path)
            finally:
                temporary.unlink(missing_ok=True)

    def command(self, arguments, *, timeout=None):
        try:
            result = subprocess.run(
                [self.docker, *arguments],
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout or self.args.command_timeout,
                start_new_session=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise DeploymentFailure("command_failed") from None
        if result.returncode:
            raise DeploymentFailure("command_failed")
        return result.stdout.strip()

    def admin(self, statement, database="postgres"):
        # Server deadlines also cover the case where a killed docker client
        # leaves its exec process alive briefly inside the database container.
        milliseconds = max(1, int(self.args.command_timeout * 500))
        sql = f"SET statement_timeout='{milliseconds}ms'; SET lock_timeout='{milliseconds}ms'; {statement}"
        return self.command(
            [
                "exec",
                self.args.postgres,
                "psql",
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "platform_owner",
                "-d",
                database,
                "-c",
                sql,
            ]
        )

    def membership_count(self):
        return self.admin(
            "select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid "
            "where r.rolname in ('platform_control_owner','platform_control_owner_preview')"
        )

    def session_count(self):
        return self.admin(
            "select count(*) from pg_stat_activity where usename in "
            "('platform_control_migrator','platform_control_migrator_preview')"
        )

    def validate(self):
        if (
            not self.release.is_dir()
            or not self.private.is_absolute()
            or self.private.is_symlink()
        ):
            raise DeploymentFailure("path_invalid")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.args.postgres):
            raise DeploymentFailure("container_invalid")
        if not re.fullmatch(
            r"(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})", self.args.image
        ):
            raise DeploymentFailure("immutable_image_required")
        for index in self.environments:
            name = ("control-migrator-database-url", "preview-control-migrator-database-url")[index]
            path = self.private / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise DeploymentFailure("secret_file_invalid")
        directory = (
            Path(self.args.receipt_dir)
            if self.args.receipt_dir
            else self.private / "hr-agent-migration-receipts"
        )
        if not directory.is_absolute() or directory.is_symlink():
            raise DeploymentFailure("receipt_directory_invalid")
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise DeploymentFailure("receipt_directory_invalid")
        # Serialize this helper on the deployment host. Other deployment tools
        # still require the exclusive maintenance window documented in runbook.
        descriptor = os.open(
            self.private / "hr-agent-migration.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise DeploymentFailure("another_migration_supervisor_active") from None
        self.lock_descriptor = descriptor
        self.receipt_path = directory / f"{self.run_id}.json"
        self.record("preflight")
        if self.membership_count() != "0" or self.session_count() != "0":
            self.at_rest_unresolved = True
            raise DeploymentFailure("at_rest_not_clean")
        root = self.release / "backend/control_migrations"
        if self.migration_set == "root":
            self.migrations = root
            self.container_migrations = "/app/backend/control_migrations"
            self.verify_root_ledger("ledger_before")
            self.job_kind_preflight()
            return
        checks = []
        for number, name in (
            (100, "100_attachment_erasure_worker_access.sql"),
            (102, "102_hr_execution_cutover.sql"),
            (103, "103_hr_execution_drain_occupancy.sql"),
            (104, "104_hr_execution_drain_terminal_contract.sql"),
            (105, "105_hr_cloud_resume.sql"),
            (106, "106_attachment_erasure_write_fence.sql"),
        ):
            path = root / name
            if path.is_symlink() or not path.is_file():
                raise DeploymentFailure("migration_file_invalid")
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            checks.append(f"(version={number} and sha256='{checksum}')")
        self.migrations = root / "hr_agent"
        self.container_migrations = "/app/backend/control_migrations/hr_agent"
        if self.migrations.is_symlink() or not self.migrations.is_dir():
            raise DeploymentFailure("migration_directory_invalid")
        for index in self.environments:
            database = ("agent_platform_control", "agent_platform_control_preview")[index]
            if self.admin(
                "select count(*) from platform_control.schema_migrations where "
                + " or ".join(checks),
                database,
            ) != str(len(checks)):
                raise DeploymentFailure("root_migration_gate_failed")

    def root_checksums(self):
        root = self.release / "backend/control_migrations"
        expected = {}
        for directory, baseline_only in ((root, False), (root / "hr_web", True)):
            if directory.is_symlink() or not directory.is_dir():
                raise DeploymentFailure("root_baseline_invalid")
            for path in sorted(directory.iterdir()):
                match = re.fullmatch(r"([0-9]{3})_[a-z0-9_]+\.sql", path.name)
                if match is None:
                    continue
                version = int(match[1])
                if (path.is_symlink() or not path.is_file() or version in expected
                        or (baseline_only and not 89 <= version <= 95)):
                    raise DeploymentFailure("root_ledger_invalid")
                expected[version] = hashlib.sha256(path.read_bytes()).hexdigest()
        if not (set(range(1, 96)) | {100, 102, 103, 104, 105, 106}).issubset(expected):
            raise DeploymentFailure("root_baseline_invalid")
        return expected

    def verify_root_ledger(self, field):
        expected = self.root_checksums()
        if field == "ledger_after" and expected != self.receipt.get("root_file_checksums"):
            raise DeploymentFailure("root_ledger_invalid")
        value = self.admin(
            "select coalesce(json_agg(json_build_object('version',version,'sha256',sha256) "
            "order by version),'[]'::json)::text from platform_control.schema_migrations",
            "agent_platform_control",
        )
        try:
            rows = json.loads(value)
            if not isinstance(rows, list):
                raise TypeError
            actual = {}
            for row in rows:
                version, checksum = row["version"], row["sha256"]
                if type(version) is not int or version in actual or expected.get(version) != checksum:
                    raise ValueError
                actual[version] = checksum
        except (ValueError, KeyError, TypeError):
            raise DeploymentFailure("root_ledger_invalid") from None
        if not set(range(1, 96)).issubset(actual):
            raise DeploymentFailure("root_baseline_invalid")
        if field == "ledger_after" and actual != expected:
            raise DeploymentFailure("root_ledger_invalid")
        self.record(field, **{field: rows}, root_file_checksums=expected)

    def job_kind_preflight(self):
        # Host-reviewed companion; never invoke a release's arbitrary shell script.
        script = Path(__file__).with_name("preflight-execution-job-kind.sh")
        if script.is_symlink() or not script.is_file():
            raise DeploymentFailure("job_kind_preflight_failed")
        checksum = hashlib.sha256(script.read_bytes()).hexdigest()
        self.record("job_kind_preflight_pending", job_kind_preflight={
            "state": "pending", "script_sha256": checksum,
        })
        try:
            result = subprocess.run(
                ["/bin/bash", str(script), self.args.postgres, "agent_platform_control", "--baseline95"],
                capture_output=True, text=True, check=False,
                timeout=self.args.command_timeout, start_new_session=True,
                env={**os.environ, "HR_MIGRATION_DOCKER": self.docker},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise DeploymentFailure("job_kind_preflight_failed") from None
        if result.returncode or result.stdout.strip() != (
            "EXECUTION_JOB_KIND_PREFLIGHT_OK database=agent_platform_control state=classified"
        ):
            raise DeploymentFailure("job_kind_preflight_failed")
        self.record("job_kind_preflight", job_kind_preflight={
            "state": "classified", "script_sha256": checksum,
        })

    def state(self):
        value = json.loads(
            self.command(
                ["inspect", "--format", "{{json .State}}", self.container or self.name]
            )
        )
        if type(value) is not dict or type(value.get("Running")) is not bool:
            raise DeploymentFailure("container_state_invalid")
        return value

    def cleanup(self):
        self.cleaning = True
        stopped = not self.name
        revoked = not self.granted_owner
        try:
            if self.name:
                identity = self.container or self.name
                try:
                    stopped = not self.state()["Running"]
                except (DeploymentFailure, ValueError):
                    stopped = False
                if not stopped:
                    try:
                        self.command(["stop", "--time", "2", identity])
                    except DeploymentFailure:
                        pass
                    try:
                        stopped = not self.state()["Running"]
                    except (DeploymentFailure, ValueError):
                        stopped = False
                if not stopped:
                    try:
                        self.command(["kill", identity])
                    except DeploymentFailure:
                        pass
                    try:
                        stopped = not self.state()["Running"]
                    except (DeploymentFailure, ValueError):
                        stopped = False
            # Revoke even if the daemon cannot confirm stop. In that case a
            # pre-existing SET ROLE session may remain: never report verified.
            if self.granted_owner:
                try:
                    self.admin(
                        f"revoke {self.granted_owner} from {self.granted_migrator}"
                    )
                    revoked = (
                        self.membership_count() == "0" and self.session_count() == "0"
                    )
                except DeploymentFailure:
                    revoked = False
            verified = stopped and revoked and not self.at_rest_unresolved
            if stopped and self.name:
                try:
                    self.command(["rm", self.container or self.name])
                except DeploymentFailure:
                    verified = False
            try:
                self.record("cleanup", cleanup_verified=verified)
            except (OSError, ValueError):
                verified = False
                print("HR_AGENT_MIGRATIONS_RECEIPT_UNAVAILABLE", file=sys.stderr)
            if verified:
                self.container = self.name = None
                self.granted_owner = self.granted_migrator = None
            return verified
        finally:
            self.cleaning = False

    def migrate(self, index):
        environment = ("production", "preview")[index]
        suffix = "" if index == 0 else "_preview"
        secret_name = (
            "control-migrator-database-url",
            "preview-control-migrator-database-url",
        )[index]
        self.name = f"hr-migration-{self.run_id}-{environment}"
        self.record(
            "create",
            environment=environment,
            container_name=self.name,
            container_id=None,
            cleanup_verified=False,
        )
        self.container = self.command(
            [
                "create",
                "--name",
                self.name,
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--user",
                "0:0",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m",
                "--network",
                "orbbec-agent-platform-internal",
                "-v",
                f"{self.private / secret_name}:/run/control-secrets/{secret_name}:ro",
                "-v",
                f"{self.migrations}:{self.container_migrations}:ro",
                "-e",
                f"PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/run/control-secrets/{secret_name}",
                "-e",
                f"PLATFORM_CONTROL_OWNER_ROLE=platform_control_owner{suffix}",
                "-e",
                f"PLATFORM_CONTROL_MIGRATION_DIR={self.container_migrations}",
                self.args.image,
                "python",
                "-m",
                "app.control_plane.migrate",
            ]
        )
        if not re.fullmatch("[0-9a-f]{12,64}", self.container):
            self.container = None  # trusted name remains available for cleanup
            raise DeploymentFailure("container_identity_invalid")
        self.granted_owner = f"platform_control_owner{suffix}"
        self.granted_migrator = f"platform_control_migrator{suffix}"
        # Persist the possible exposure before GRANT: a lost reply is ambiguous.
        self.record("grant_pending", container_id=self.container)
        self.admin(f"grant {self.granted_owner} to {self.granted_migrator}")
        self.record("migration")
        self.command(
            ["start", "--attach", self.container], timeout=self.args.migration_timeout
        )
        state = self.state()
        if state["Running"] or state.get("ExitCode") != 0:
            raise DeploymentFailure("migration_failed")
        if not self.cleanup():
            raise DeploymentFailure("cleanup_unverified")
        if self.migration_set == "root":
            self.verify_root_ledger("ledger_after")

    def on_signal(self, number, _frame):
        self.signal_number = number
        if not self.cleaning:
            raise DeploymentSignal(number)

    def run(self):
        status, code = "failed", 1
        try:
            self.validate()
            for index in self.environments:
                if self.signal_number:
                    raise DeploymentSignal(self.signal_number)
                self.migrate(index)
                if self.signal_number:
                    raise DeploymentSignal(self.signal_number)
            status, code = "completed", 0
        except DeploymentSignal as error:
            status, code = "interrupted", 128 + error.number
        except DeploymentFailure as error:
            self.receipt["failure_code"] = str(
                error
            )  # fixed internal code, never child output
        except (OSError, ValueError, KeyError):
            self.receipt["failure_code"] = "supervisor_io_or_state_invalid"
        finally:
            verified = self.cleanup()
            if not verified:
                print(
                    "HR_AGENT_MIGRATIONS_CLEANUP_UNRESOLVED external_recovery_required=1",
                    file=sys.stderr,
                )
                code = 1
            if self.signal_number:
                status, code = "interrupted", 128 + self.signal_number
            try:
                self.record("finished", status=status, cleanup_verified=verified)
            except OSError:
                code = 1
                print("HR_AGENT_MIGRATIONS_RECEIPT_UNAVAILABLE", file=sys.stderr)
            if self.lock_descriptor is not None:
                os.close(self.lock_descriptor)
        if not code:
            print(f"HR_AGENT_MIGRATIONS_OK environments={len(self.environments)} cleanup=verified")
        else:
            print("HR_AGENT_MIGRATIONS_FAILED", file=sys.stderr)
        return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("release")
    parser.add_argument("private")
    parser.add_argument("image")
    parser.add_argument("postgres")
    parser.add_argument("--migration-timeout", type=float, default=900)
    parser.add_argument("--command-timeout", type=float, default=10)
    parser.add_argument("--receipt-dir")
    parser.add_argument("--migration-set", choices=("hr", "root"), default="hr")
    parser.add_argument("--environment", choices=("all", "production"), default="all")
    args = parser.parse_args()
    if args.migration_set == "root" and args.environment != "production":
        parser.error("root requires --environment production")
    if not (0 < args.migration_timeout <= 3600 and 0 < args.command_timeout <= 30):
        parser.error("invalid timeouts")
    try:
        supervisor = Supervisor(args)
    except (OSError, ValueError):
        print("HR_AGENT_MIGRATIONS_FAILED", file=sys.stderr)
        return 1
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(number, supervisor.on_signal)
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
