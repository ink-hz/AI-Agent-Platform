"""Private pre-migration backup on the fixed production host; no database writes."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
from uuid import uuid4

RELEASE = "394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76"
POSTGRES = "12ef0c782a5e458b9e52dbe40ffaa3aa877e07269ddea52051e93d258ebdbb0e"
PRIVATE = Path("/opt/orbbec-agent-platform/private")
HELPER = Path("/opt/orbbec-agent-platform/releases") / RELEASE / "deploy/cloud/deploy-input-lock.py"
HELPER_SHA = "45c2d3fce8a5ef2e3dd6306eaa15b7cdbfc85072bed164033684f84b425a4eb4"


def require(condition):
    if not condition:
        raise RuntimeError("backup_guard_failed")


class BackupInterrupted(Exception):
    pass


def interrupted(number, _frame):
    raise BackupInterrupted(str(number))


def atomic_receipt(path, receipt):
    part = path.with_name(path.name + ".part-" + uuid4().hex)
    with part.open("x") as handle:
        json.dump(receipt, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    protected(part, 0o600)
    os.replace(part, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args, *, output=None, input_file=None, timeout=180):
    if args[0] == "docker":
        args = ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *args[1:]]
    elif args[0] == "python3":
        args = ["/usr/bin/python3", "-I", *args[1:]]
    else:
        raise RuntimeError("backup_command_invalid")
    result = subprocess.run(args, stdout=output or subprocess.PIPE,
                            stdin=input_file, stderr=subprocess.PIPE, timeout=timeout,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    if result.returncode:
        raise RuntimeError("backup_command_failed")
    return result.stdout


def protected(path, mode, directory=False):
    info = path.lstat()
    require(not path.is_symlink() and info.st_uid == 0)
    require(stat.S_IMODE(info.st_mode) == mode)
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))


def main():
    require(os.getuid() == 0)
    os.umask(0o077)
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
    for number in signals:
        signal.signal(number, interrupted)
    protected(PRIVATE, 0o700, True)
    require(not HELPER.is_symlink() and hashlib.sha256(HELPER.read_bytes()).hexdigest() == HELPER_SHA)
    require(run(["docker", "inspect", "--format", "{{.Id}}", "orbbec-agent-platform-platform-postgres-1"]).decode().strip() == POSTGRES)
    require(shutil.disk_usage("/data").free > 20 * 1024**3)
    token = str(uuid4())
    deployment = uuid4().hex
    action = PRIVATE / "agent-brain-action.lock"
    acquire_attempted = False
    action_created = False
    owner_inode = None
    backup = None
    application = "hr_backup_" + deployment
    receipt = {"started_at": now(), "deployment_id": deployment, "release": RELEASE,
               "database_writes": False, "restoration_performed": False, "status": "running",
               "application_name": application}
    metadata = Path("/data/orbbec-agent-platform/release-metadata") / RELEASE
    protected(metadata, 0o700, True)
    receipt_path = metadata / ("backup-" + deployment + ".json")
    require(not receipt_path.exists())
    atomic_receipt(receipt_path, receipt)
    try:
        action.mkdir(mode=0o700, exist_ok=False)
        action_created = True
        with (action / "owner").open("x") as handle:
            owner_inode = os.fstat(handle.fileno()).st_ino
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        acquire_attempted = True
        run(["python3", str(HELPER), "acquire", RELEASE, deployment], timeout=15)
        parent = Path("/data/orbbec-agent-platform/private-backups")
        if not parent.exists():
            parent.mkdir(mode=0o700)
        protected(parent, 0o700, True)
        backup = parent / ("hr-cloud-" + deployment)
        backup.mkdir(mode=0o700, exist_ok=False)
        receipt["private_backup_path"] = str(backup)
        prefix = ["docker", "exec", "-e", "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=3000 -c application_name=" + application, POSTGRES]
        jobs = {
            "production.dump": ["pg_dump", "-U", "platform_owner", "-d", "agent_platform_control", "-Fc", "--lock-wait-timeout=3s"],
            "globals.sql": ["pg_dumpall", "-U", "platform_owner", "--globals-only"],
            "production-schema.sql": ["pg_dump", "-U", "platform_owner", "-d", "agent_platform_control", "--schema-only", "--no-owner", "--no-privileges", "--lock-wait-timeout=3s"],
            "production-ledger.json": ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "platform_owner", "-d", "agent_platform_control", "-c", "select json_agg(row_to_json(m) order by version) from (select version,sha256 from platform_control.schema_migrations) m"],
        }
        receipt["files"] = {}
        for name, argv in jobs.items():
            run(["python3", str(HELPER), "validate", RELEASE, deployment], timeout=15)
            part = backup / (name + ".part")
            with part.open("xb") as handle:
                run(prefix + argv, output=handle)
                handle.flush()
                os.fsync(handle.fileno())
            protected(part, 0o600)
            require(part.stat().st_size > 0)
            target = backup / name
            part.rename(target)
            receipt["files"][name] = {"bytes": target.stat().st_size, "sha256": file_hash(target)}
        with (backup / "production.dump").open("rb") as handle, open(os.devnull, "wb") as sink:
            run(["docker", "exec", "-i", POSTGRES, "pg_restore", "--list"], input_file=handle, output=sink)
        ledger = json.loads((backup / "production-ledger.json").read_text())
        require([row["version"] for row in ledger] == list(range(1, 96)))
        receipt.update(status="completed", archive_list_verified=True, ledger_count=95,
                       limitation="Archive listing verified; no restore rehearsal. Global role backup is private. Preview database is not migrated or dumped.")
    except Exception as error:
        receipt["status"] = "failed"
        receipt["failure_type"] = type(error).__name__
    finally:
        for number in signals:
            signal.signal(number, signal.SIG_IGN)
        clean = True
        # A terminated Docker client is not proof its own read-only pg_dump ended.
        try:
            check = ["docker", "exec", "-e", "PGOPTIONS=-c statement_timeout=3000 -c lock_timeout=2000", POSTGRES,
                     "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "platform_owner", "-d", "postgres", "-c"]
            predicate = "application_name='" + application + "' and usename='platform_owner' and pid<>pg_backend_pid()"
            count = run(check + ["select count(*) from pg_stat_activity where " + predicate], timeout=10).decode().strip()
            if count != "0":
                run(check + ["select pg_terminate_backend(pid) from pg_stat_activity where " + predicate], timeout=10)
                receipt["own_readonly_sessions_terminated"] = True
                count = run(check + ["select count(*) from pg_stat_activity where " + predicate], timeout=10).decode().strip()
            require(count == "0")
            receipt["backup_sessions_zero"] = True
        except Exception:
            clean = False
            receipt["backup_sessions_zero"] = False
        if acquire_attempted:
            try:
                run(["python3", str(HELPER), "validate", RELEASE, deployment], timeout=15)
                run(["python3", str(HELPER), "release", RELEASE, deployment], timeout=15)
            except Exception:
                clean = False
        if action_created:
            try:
                protected(action, 0o700, True)
                owner = action / "owner"
                if owner.exists() or owner.is_symlink():
                    protected(owner, 0o600)
                    require(owner.lstat().st_ino == owner_inode)
                    require(owner.read_text() in {"", token + "\n"})
                    require({p.name for p in action.iterdir()} == {"owner"})
                    tombstone = action.with_name(action.name + ".releasing." + token)
                    require(not tombstone.exists() and not tombstone.is_symlink())
                    action.rename(tombstone)
                    (tombstone / "owner").unlink()
                    tombstone.rmdir()
                else:
                    action.rmdir()
            except Exception:
                clean = False
        receipt.update(finished_at=now(), cleanup_verified=clean)
        if not clean:
            receipt["status"] = "failed"
        if backup:
            atomic_receipt(backup / "receipt.json", receipt)
        atomic_receipt(receipt_path, receipt)
        print(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
