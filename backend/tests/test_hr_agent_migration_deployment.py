import os
import subprocess
import sys
from pathlib import Path

import psycopg
from app.control_plane.migrate import migrate_control_database
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from tests.hr_agent_support import hr_agent_database

ROOT = Path(__file__).parents[2]
HELPER = ROOT / "deploy/cloud/migrate-hr-agent.sh"


def _write_fake_docker(path: Path):
    path.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import os, pathlib, sys
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from app.control_plane.migrate import migrate_control_database

args=sys.argv[1:]
if args[0]=='exec':
    args=args[2:]
    database=args[args.index('-d')+1]
    query=args[args.index('-c')+1] if '-c' in args else sys.stdin.read()
    values=conninfo_to_dict(os.environ['TEST_ADMIN_DSN']); values['dbname']=database
    with psycopg.connect(make_conninfo(**values),autocommit=True) as connection:
        cursor=connection.execute(query)
        rows=cursor.fetchall() if cursor.description else []
        if rows:
            print('\\n'.join(str(row[0]) for row in rows))
elif args[0]=='run':
    environment={}
    index=1
    while index < len(args):
        if args[index]=='-e':
            name,value=args[index+1].split('=',1); environment[name]=value; index+=2
        elif args[index] in ('-v','--network','--user'):
            index+=2
        elif args[index].startswith('-'):
            index+=1
        else:
            break
    secret=pathlib.Path(os.environ['TEST_PRIVATE'])/pathlib.Path(environment['PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE']).name
    migration_dir=pathlib.Path(os.environ['TEST_RELEASE'])/'backend/control_migrations/hr_agent'
    migrate_control_database(secret.read_text(),migration_dir,owner_role=environment['PLATFORM_CONTROL_OWNER_ROLE'])
else:
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    path.chmod(0o700)


def _memberships(database):
    with database.admin_connection() as connection:
        return connection.execute(
            "select count(*) from pg_auth_members m join pg_roles r on r.oid=m.roleid "
            "where r.rolname in ('platform_control_owner','platform_control_owner_preview')"
        ).fetchone()[0]


def _prepare_preview_and_revoke(database):
    values = conninfo_to_dict(database.admin_dsn)
    values["dbname"] = "postgres"
    with psycopg.connect(make_conninfo(**values), autocommit=True) as connection:
        connection.execute(
            "create database agent_platform_control_preview "
            "owner platform_control_owner_preview"
        )
        connection.execute(
            "grant platform_control_owner_preview to platform_control_migrator_preview"
        )
    preview_dsn = database.migrator_dsn.replace(
        "user=platform_control_migrator", "user=platform_control_migrator_preview"
    ).replace("dbname=agent_platform_control", "dbname=agent_platform_control_preview")
    migrations = ROOT / "backend/control_migrations"
    migrate_control_database(
        preview_dsn, migrations, owner_role="platform_control_owner_preview"
    )
    with database.admin_connection() as connection:
        connection.execute(
            "revoke platform_control_owner from platform_control_migrator"
        )
        connection.execute(
            "revoke platform_control_owner_preview from platform_control_migrator_preview"
        )
    return preview_dsn


def test_helper_grants_only_transiently_and_applies_hr_migrations(tmp_path):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"
        private.mkdir()
        (private / "control-migrator-database-url").write_text(database.migrator_dsn)
        (private / "preview-control-migrator-database-url").write_text(preview_dsn)
        for item in private.iterdir():
            item.chmod(0o600)
        fake = tmp_path / "docker"
        _write_fake_docker(fake)
        environment = {
            **os.environ,
            "HR_MIGRATION_DOCKER": str(fake),
            "TEST_ADMIN_DSN": database.admin_dsn,
            "TEST_PRIVATE": str(private),
            "TEST_RELEASE": str(ROOT),
            "PYTHONPATH": str(ROOT / "backend"),
        }
        result = subprocess.run(
            [str(HELPER), str(ROOT), str(private), "test-image", "test-postgres"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "HR_AGENT_MIGRATIONS_OK versions=096-099,101"
        assert _memberships(database) == 0
        with database.connection() as connection:
            assert (
                connection.execute(
                    "select count(*) from platform_control.schema_migrations "
                    "where version in (96,97,98,99,101)"
                ).fetchone()[0]
                == 5
            )


def test_helper_revokes_owner_membership_when_hr_migration_fails(tmp_path):
    with hr_agent_database(migrate_hr=False) as database:
        preview_dsn = _prepare_preview_and_revoke(database)
        private = tmp_path / "private"
        private.mkdir()
        (private / "control-migrator-database-url").write_text(database.migrator_dsn)
        (private / "preview-control-migrator-database-url").write_text(preview_dsn)
        for item in private.iterdir():
            item.chmod(0o600)
        fake = tmp_path / "docker"
        _write_fake_docker(fake)
        broken_release = tmp_path / "release"
        (broken_release / "backend/control_migrations/hr_agent").mkdir(parents=True)
        for source in (ROOT / "backend/control_migrations/hr_agent").glob("*.sql"):
            target = (
                broken_release / "backend/control_migrations/hr_agent" / source.name
            )
            target.write_bytes(source.read_bytes())
        (
            broken_release
            / "backend/control_migrations/hr_agent/096_hr_agent_runtime.sql"
        ).write_text("invalid sql")
        # Root gate files are immutable release inputs too.
        for name in (
            "100_attachment_erasure_worker_access.sql",
            "102_hr_execution_cutover.sql",
        ):
            target = broken_release / "backend/control_migrations" / name
            target.write_bytes(
                (ROOT / "backend/control_migrations" / name).read_bytes()
            )
        result = subprocess.run(
            [
                str(HELPER),
                str(broken_release),
                str(private),
                "test-image",
                "test-postgres",
            ],
            env={
                **os.environ,
                "HR_MIGRATION_DOCKER": str(fake),
                "TEST_ADMIN_DSN": database.admin_dsn,
                "TEST_PRIVATE": str(private),
                "TEST_RELEASE": str(broken_release),
                "PYTHONPATH": str(ROOT / "backend"),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert _memberships(database) == 0
        assert "invalid sql" not in result.stderr


def test_helper_never_runs_root_migration_directory():
    source = HELPER.read_text(encoding="utf-8")
    assert (
        "PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations/hr_agent"
        in source
    )
    assert "100_attachment_erasure_worker_access.sql" in source
    assert "102_hr_execution_cutover.sql" in source
    assert "trap cleanup EXIT" in source
    assert "revoke_owner_memberships" in source
