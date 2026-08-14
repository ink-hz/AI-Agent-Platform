from __future__ import annotations

import asyncio
import os
from pathlib import Path
import uuid

import psycopg
import pytest

from app.control_plane.crypto import ProtectedProviderId, ProviderIdentityCodec
from app.control_plane.demo_bootstrap import (
    DemoBootstrapError,
    DemoDirectoryBootstrap,
    read_demo_userids,
)
from app.control_plane.dingtalk import DingTalkMember, DingTalkProviderError
from app.control_plane.identity import IdentityResolver
from test_control_plane_migration import control_database
from test_identity_crypto import _codec


MIGRATION = (
    Path(__file__).parents[1]
    / "control_migrations/019_demo_preview_bootstrap.sql"
)


class MemberClient:
    def __init__(
        self,
        members: dict[str, DingTalkMember | Exception],
        *,
        corp_id: str = "test-corp",
    ) -> None:
        self.members = members
        self._corp_id = corp_id
        self.calls: list[str] = []

    async def get_member(self, userid: str) -> DingTalkMember:
        self.calls.append(userid)
        selected = self.members[userid]
        if isinstance(selected, Exception):
            raise selected
        return selected


def _userid_file(tmp_path: Path, value: str, mode: int = 0o600) -> Path:
    path = tmp_path / "demo-userids"
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_userid_file_accepts_only_one_to_three_unique_stable_ids(
    tmp_path: Path, mode: int
) -> None:
    path = _userid_file(tmp_path, "employee-1\nemployee-2\n", mode)
    assert read_demo_userids(path) == ("employee-1", "employee-2")


def test_userid_file_accepts_single_line_without_trailing_newline(
    tmp_path: Path,
) -> None:
    path = _userid_file(tmp_path, "employee-1")
    assert read_demo_userids(path) == ("employee-1",)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "employee-1\n\nemployee-2\n",
        "employee-1\nemployee-1\n",
        "a\nb\nc\nd\n",
        "x" * 513 + "\n",
        " padded\n",
        "nul\x00value\n",
    ],
)
def test_userid_file_rejects_blank_duplicate_oversized_or_unstable_values(
    tmp_path: Path, payload: str
) -> None:
    with pytest.raises(DemoBootstrapError, match="demo userid file invalid"):
        read_demo_userids(_userid_file(tmp_path, payload))


@pytest.mark.parametrize("mode", [0o644, 0o440, 0o200])
def test_userid_file_rejects_other_modes(tmp_path: Path, mode: int) -> None:
    with pytest.raises(DemoBootstrapError, match="demo userid file unavailable"):
        read_demo_userids(_userid_file(tmp_path, "employee-1\n", mode))


def test_userid_file_rejects_symlink(tmp_path: Path) -> None:
    target = _userid_file(tmp_path, "employee-1\n")
    link = tmp_path / "userids-link"
    link.symlink_to(target)
    with pytest.raises(DemoBootstrapError, match="demo userid file unavailable"):
        read_demo_userids(link)


def test_userid_file_rejects_wrong_owner(tmp_path: Path, monkeypatch) -> None:
    path = _userid_file(tmp_path, "employee-1\n")
    real_fstat = os.fstat

    class WrongOwner:
        def __init__(self, source):
            for name in (
                "st_mode", "st_ino", "st_dev", "st_nlink", "st_size",
            ):
                setattr(self, name, getattr(source, name))
            self.st_uid = source.st_uid + 1

    monkeypatch.setattr(
        "app.control_plane.demo_bootstrap.os.fstat",
        lambda descriptor: WrongOwner(real_fstat(descriptor)),
    )
    with pytest.raises(DemoBootstrapError, match="demo userid file unavailable"):
        read_demo_userids(path)


def test_begin_boundary_is_nonblocking_and_contains_no_provider_identifier_input() -> None:
    migration = MIGRATION.read_text(encoding="utf-8").lower()
    signature = migration.split(
        "create function platform_control.begin_demo_directory_generation(", 1
    )[1].split(") returns void", 1)[0]
    assert "pg_try_advisory_xact_lock" in migration
    assert "userid" not in signature
    assert "unionid" not in signature
    assert "corp" not in signature


def _member(userid: str, *, active: bool = True, unionid: str | None = None):
    return DingTalkMember(
        userid,
        unionid if unionid is not None else f"union-{userid}",
        f"Demo {userid[-1]}",
        active,
        (1,),
    )


def _set_policy(environment, versions=(1, 2)) -> None:
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance_preview"]
    ) as connection:
        connection.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'dingtalk', %s)",
            (list(versions),),
        )


def _active_generation(environment):
    with psycopg.connect(environment["admin"]) as connection:
        return connection.execute(
            "select active_generation_id from platform_control.directory_state "
            "where singleton"
        ).fetchone()[0]


def _bootstrap(environment, tmp_path: Path, client: MemberClient):
    codec = _codec(tmp_path)
    _set_policy(environment)
    return DemoDirectoryBootstrap(
        environment["urls"]["platform_directory_worker_preview"],
        corp_id="test-corp",
        client=client,
        identity_codec=codec,
    ), codec


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_bootstrap_rejects_production_dsn_and_wrong_client_corporation(
    control_database, tmp_path: Path
) -> None:
    production = control_database["environments"]["production"]
    preview = control_database["environments"]["preview"]
    client = MemberClient({"employee-1": _member("employee-1")})
    with pytest.raises(ValueError, match="exact preview directory-worker DSN required"):
        DemoDirectoryBootstrap(
            production["urls"]["platform_directory_worker"],
            corp_id="test-corp",
            client=client,
            identity_codec=_codec(tmp_path),
        )
    with pytest.raises(DemoBootstrapError, match="organization mismatch"):
        DemoDirectoryBootstrap(
            preview["urls"]["platform_directory_worker_preview"],
            corp_id="test-corp",
            client=MemberClient(client.members, corp_id="wrong-corp"),
            identity_codec=_codec(tmp_path),
        )


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_all_provider_reads_finish_before_database_work_and_errors_are_redacted(
    control_database, tmp_path: Path
) -> None:
    preview = control_database["environments"]["preview"]
    secret = "sensitive-employee-two"
    client = MemberClient(
        {
            "employee-1": _member("employee-1"),
            secret: DingTalkProviderError(
                f"provider rejected {secret}",
                request_id=f"request-{secret}",
                error_code="not_found",
            ),
        }
    )
    bootstrap, _ = _bootstrap(preview, tmp_path, client)
    before = _active_generation(preview)
    with pytest.raises(DemoBootstrapError) as captured:
        await bootstrap.run(("employee-1", secret))
    assert str(captured.value) == "demo_provider_unavailable"
    assert secret not in repr(captured.value)
    assert _active_generation(preview) == before
    with psycopg.connect(preview["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.directory_generations"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize(
    "bad_member,error",
    [
        (_member("employee-1", active=False), "demo_member_inactive"),
        (_member("employee-1", unionid=""), "demo_member_invalid"),
    ],
)
async def test_inactive_or_missing_union_member_is_rejected_before_database(
    control_database, tmp_path: Path, bad_member: DingTalkMember, error: str
) -> None:
    preview = control_database["environments"]["preview"]
    bootstrap, _ = _bootstrap(
        preview, tmp_path, MemberClient({"employee-1": bad_member})
    )
    with pytest.raises(DemoBootstrapError, match=error):
        await bootstrap.run(("employee-1",))
    with psycopg.connect(preview["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.directory_generations"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_bootstrap_stages_exact_protected_corporate_and_union_facts(
    control_database, tmp_path: Path
) -> None:
    preview = control_database["environments"]["preview"]
    member = _member("employee-1")
    bootstrap, codec = _bootstrap(
        preview, tmp_path, MemberClient({member.userid: member})
    )
    result = await bootstrap.run((member.userid,))
    assert result.member_count == 1
    with psycopg.connect(preview["admin"]) as connection:
        row = connection.execute(
            "select generation.status,generation.member_count,"
            "generation.department_count,generation.content_sha256,"
            "member.subject_kind,member.lookup_hmac,member.lookup_key_version,"
            "member.encrypted_provider_id,member.encryption_key_version,"
            "member.union_lookup_hmac,member.union_lookup_key_version,"
            "member.status,member.display_name "
            "from platform_control.directory_generations generation join "
            "platform_control.directory_members member using (generation_id) "
            "where generation.generation_id=%s",
            (result.generation_id,),
        ).fetchone()
    assert row[0:4] == ("complete", 1, 0, result.digest_hex)
    assert row[4] == "employee"
    assert codec.unseal(
        ProtectedProviderId(
            subject_kind=row[4],
            lookup_hmac=row[5],
            lookup_key_version=row[6],
            ciphertext=row[7],
            encryption_key_version=row[8],
        )
    ) == IdentityResolver.corporate_provider_id("test-corp", member.userid)
    expected_union = codec.seal("employee_union", member.unionid)
    assert row[9:11] == (
        expected_union.lookup_hmac,
        expected_union.lookup_key_version,
    )
    assert row[11:13] == ("active", member.display_name)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_key_policy_mismatch_and_staging_failure_preserve_prior_active(
    control_database, tmp_path: Path, monkeypatch
) -> None:
    preview = control_database["environments"]["preview"]
    first = _member("employee-1")
    bootstrap, _ = _bootstrap(
        preview, tmp_path, MemberClient({first.userid: first})
    )
    prior = (await bootstrap.run((first.userid,))).generation_id

    _set_policy(preview, (1,))
    with pytest.raises(DemoBootstrapError, match="demo_key_policy_mismatch"):
        await bootstrap.run((first.userid,))
    assert _active_generation(preview) == prior

    _set_policy(preview)
    monkeypatch.setattr(bootstrap, "_stage_member", lambda *args: (_ for _ in ()).throw(psycopg.OperationalError("stage failed")))
    with pytest.raises(DemoBootstrapError, match="demo_database_unavailable"):
        await bootstrap.run((first.userid,))
    assert _active_generation(preview) == prior


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_rerun_leaves_one_complete_active_generation(
    control_database, tmp_path: Path
) -> None:
    preview = control_database["environments"]["preview"]
    members = {key: _member(key) for key in ("employee-1", "employee-2")}
    bootstrap, _ = _bootstrap(preview, tmp_path, MemberClient(members))
    first = await bootstrap.run(tuple(members))
    second = await bootstrap.run(tuple(members))
    assert first.generation_id != second.generation_id
    assert _active_generation(preview) == second.generation_id
    with psycopg.connect(preview["admin"]) as connection:
        rows = connection.execute(
            "select status,count(*) from platform_control.directory_generations "
            "where generation_id = any(%s) group by status order by status",
            ([first.generation_id, second.generation_id],),
        ).fetchall()
    assert rows == [("complete", 1), ("superseded", 1)]


@pytest.mark.postgres
def test_demo_sql_boundary_is_preview_worker_only(control_database) -> None:
    function = "platform_control.begin_demo_directory_generation(uuid,integer,bytea)"
    for environment_name, environment in control_database["environments"].items():
        with psycopg.connect(environment["admin"]) as connection:
            privileges = {
                role: connection.execute(
                    "select has_function_privilege(%s,%s,'execute')",
                    (role, function),
                ).fetchone()[0]
                for role in (*environment["roles"], "public")
            }
        expected = "platform_directory_worker_preview"
        assert privileges == {
            role: environment_name == "preview" and role == expected
            for role in (*environment["roles"], "public")
        }


@pytest.mark.postgres
def test_begin_boundary_rejects_invalid_inputs_and_existing_staging_generation(
    control_database,
) -> None:
    preview = control_database["environments"]["preview"]
    worker_url = preview["urls"]["platform_directory_worker_preview"]
    for member_count, digest in ((0, b"x" * 32), (4, b"x" * 32), (1, b"x" * 31)):
        with psycopg.connect(worker_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "select platform_control.begin_demo_directory_generation(%s,%s,%s)",
                    (uuid.uuid4(), member_count, digest),
                )

    blocking_generation = uuid.uuid4()
    with psycopg.connect(preview["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256) "
            "values (%s,'staging',1,0,%s)",
            (blocking_generation, "b" * 64),
        )
    try:
        with psycopg.connect(worker_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "select platform_control.begin_demo_directory_generation(%s,%s,%s)",
                    (uuid.uuid4(), 1, b"x" * 32),
                )
    finally:
        with psycopg.connect(preview["admin"]) as connection:
            connection.execute(
                "delete from platform_control.directory_generations "
                "where generation_id=%s",
                (blocking_generation,),
            )


@pytest.mark.asyncio
async def test_promotion_commit_ambiguity_requires_authoritative_reconciliation(
    tmp_path: Path,
) -> None:
    member = _member("employee-1")
    client = MemberClient({member.userid: member})
    codec = _codec(tmp_path)
    generation = uuid.uuid4()

    class AmbiguousConnection:
        def __init__(self, active):
            self.active = active
            self.executed = []
        def execute(self, query, values=None):
            self.executed.append((query, values))
            return self
        def fetchone(self):
            query = self.executed[-1][0]
            if "provider_identity_key_policies" in query:
                return ([1, 2],)
            return (self.active, "complete")
        def commit(self):
            raise psycopg.OperationalError("commit result unknown")
        def rollback(self):
            pass
        def close(self):
            pass

    primary = AmbiguousConnection(None)
    authoritative = AmbiguousConnection(generation)
    connections = iter((primary, authoritative))
    bootstrap = DemoDirectoryBootstrap(
        "postgresql://platform_directory_worker_preview:x@localhost/agent_platform_control_preview",
        corp_id="test-corp",
        client=client,
        identity_codec=codec,
        connection_factory=lambda *_args, **_kwargs: next(connections),
        generation_id_factory=lambda: generation,
        validate_connected_role=False,
    )
    result = await bootstrap.run((member.userid,))
    assert result.generation_id == generation

    connections = iter((AmbiguousConnection(None), AmbiguousConnection(None)))
    bootstrap = DemoDirectoryBootstrap(
        "postgresql://platform_directory_worker_preview:x@localhost/agent_platform_control_preview",
        corp_id="test-corp",
        client=client,
        identity_codec=codec,
        connection_factory=lambda *_args, **_kwargs: next(connections),
        generation_id_factory=lambda: generation,
        validate_connected_role=False,
    )
    with pytest.raises(DemoBootstrapError, match="demo_promotion_indeterminate"):
        await bootstrap.run((member.userid,))
