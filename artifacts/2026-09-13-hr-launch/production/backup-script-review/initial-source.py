"""Private pre-migration backup on the fixed production host; no database writes."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from uuid import uuid4

RELEASE = "ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7"
POSTGRES = "12ef0c782a5e458b9e52dbe40ffaa3aa877e07269ddea52051e93d258ebdbb0e"
PRIVATE = Path("/opt/orbbec-agent-platform/private")
HELPER = Path("/opt/orbbec-agent-platform/releases") / RELEASE / "deploy/cloud/deploy-input-lock.py"
HELPER_SHA = "45c2d3fce8a5ef2e3dd6306eaa15b7cdbfc85072bed164033684f84b425a4eb4"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args, *, output=None, input_file=None, timeout=180):
    result = subprocess.run(args, stdout=output or subprocess.PIPE,
                            stdin=input_file, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError("backup_command_failed")
    return result.stdout


def protected(path, mode, directory=False):
    info = path.lstat()
    assert not path.is_symlink() and info.st_uid == 0
    assert stat.S_IMODE(info.st_mode) == mode
    assert stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)


def main():
    assert os.getuid() == 0
    os.umask(0o077)
    protected(PRIVATE, 0o700, True)
    assert not HELPER.is_symlink() and hashlib.sha256(HELPER.read_bytes()).hexdigest() == HELPER_SHA
    assert run(["docker", "inspect", "--format", "{{.Id}}", "orbbec-agent-platform-platform-postgres-1"]).decode().strip() == POSTGRES
    assert shutil.disk_usage("/data").free > 20 * 1024**3
    token = str(uuid4())
    deployment = uuid4().hex
    action = PRIVATE / "agent-brain-action.lock"
    acquire_attempted = False
    backup = None
    receipt = {"started_at": now(), "deployment_id": deployment, "release": RELEASE,
               "database_writes": False, "restoration_performed": False, "status": "failed"}
    action.mkdir(mode=0o700, exist_ok=False)
    try:
        with (action / "owner").open("x") as handle:
            handle.write(token + "\n")
        acquire_attempted = True
        run(["python3", str(HELPER), "acquire", RELEASE, deployment], timeout=15)
        parent = Path("/data/orbbec-agent-platform/private-backups")
        if not parent.exists():
            parent.mkdir(mode=0o700)
        protected(parent, 0o700, True)
        backup = parent / ("hr-cloud-" + deployment)
        backup.mkdir(mode=0o700, exist_ok=False)
        receipt["private_backup_path"] = str(backup)
        prefix = ["docker", "exec", "-e", "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=3000", POSTGRES]
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
            assert part.stat().st_size > 0
            target = backup / name
            part.rename(target)
            receipt["files"][name] = {"bytes": target.stat().st_size, "sha256": file_hash(target)}
        with (backup / "production.dump").open("rb") as handle, open(os.devnull, "wb") as sink:
            run(["docker", "exec", "-i", POSTGRES, "pg_restore", "--list"], input_file=handle, output=sink)
        ledger = json.loads((backup / "production-ledger.json").read_text())
        assert [row["version"] for row in ledger] == list(range(1, 96))
        receipt.update(status="completed", archive_list_verified=True, ledger_count=95,
                       limitation="Archive listing verified; no restore rehearsal. Global role backup is private. Preview database is not migrated or dumped.")
    except Exception as error:
        receipt["failure_type"] = type(error).__name__
    finally:
        clean = True
        if acquire_attempted:
            try:
                run(["python3", str(HELPER), "validate", RELEASE, deployment], timeout=15)
                run(["python3", str(HELPER), "release", RELEASE, deployment], timeout=15)
            except Exception:
                clean = False
        try:
            protected(action, 0o700, True)
            protected(action / "owner", 0o600)
            assert (action / "owner").read_text().strip() == token
            assert {p.name for p in action.iterdir()} == {"owner"}
            tombstone = action.with_name(action.name + ".releasing." + token)
            assert not tombstone.exists() and not tombstone.is_symlink()
            action.rename(tombstone)
            (tombstone / "owner").unlink()
            tombstone.rmdir()
        except Exception:
            clean = False
        receipt.update(finished_at=now(), cleanup_verified=clean)
        if not clean:
            receipt["status"] = "failed"
        if backup:
            (backup / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
