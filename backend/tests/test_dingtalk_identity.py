from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

import app.control_plane.identity as identity_module

from app.control_plane.dingtalk import DingTalkAuthResult, DingTalkMember
from app.control_plane.identity import (
    IdentityResolutionError,
    IdentityResolver,
)
from app.control_plane.models import DirectoryFreshness
from test_control_plane_migration import control_database
from test_identity_crypto import _codec


class DirectoryClient:
    def __init__(self, member: DingTalkMember):
        self.member = member

    async def resolve_union_member(self, unionid: str) -> DingTalkMember:
        return self.member

    async def get_member(self, userid: str) -> DingTalkMember:
        return self.member


@pytest.fixture
def production_environment(control_database):
    return control_database["environments"]["production"]


def _provider_value(corp_id: str, userid: str) -> str:
    return IdentityResolver.corporate_provider_id(corp_id, userid)


def _seed_generation(environment, codec, member: DingTalkMember) -> UUID:
    generation_id = uuid4()
    protected = codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        _provider_value("test-corp", member.userid),
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',1,0,%s,now())",
            (generation_id, "a" * 64),
        )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s, "
            "last_complete_at=now(), updated_at=now() where singleton",
            (generation_id,),
        )
        connection.execute(
            "insert into platform_control.directory_members "
            "(generation_id,member_key,subject_kind,lookup_hmac,lookup_key_version,"
            "encrypted_provider_id,encryption_key_version,display_name,status) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                generation_id, uuid4(), protected.subject_kind,
                protected.lookup_hmac, protected.lookup_key_version,
                protected.ciphertext, protected.encryption_key_version,
                member.display_name, "active" if member.active else "inactive",
            ),
        )
    return generation_id


def _resolver(environment, tmp_path: Path, member: DingTalkMember) -> IdentityResolver:
    codec = _codec(tmp_path)
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as connection:
        connection.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'dingtalk', array[1,2])"
        )
    _seed_generation(environment, codec, member)
    return IdentityResolver(
        environment["urls"]["platform_control_app"],
        corp_id="test-corp",
        client=DirectoryClient(member),
        identity_codec=codec,
    )


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_qr_unionid_and_in_client_userid_converge_on_one_internal_identity(
    production_environment, tmp_path: Path
) -> None:
    member = DingTalkMember("employee-1", "union-1", "Employee", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)

    qr_id = await resolver.resolve_active_member(
        DingTalkAuthResult("union-1", None, "test-corp"), DirectoryFreshness.FRESH
    )
    in_client_id = await resolver.resolve_active_member(
        DingTalkAuthResult("union-1", "employee-1", "test-corp"),
        DirectoryFreshness.FRESH,
    )

    assert qr_id == in_client_id
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*), count(distinct internal_user_id) from "
            "platform_control.provider_identities where internal_user_id=%s",
            (qr_id,),
        ).fetchone() == (2, 1)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_display_name_change_updates_data_without_changing_identity(
    production_environment, tmp_path: Path
) -> None:
    member = DingTalkMember("employee-2", "union-2", "Old Name", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)
    first = await resolver.resolve_active_member(
        DingTalkAuthResult("union-2", "employee-2", "test-corp"),
        DirectoryFreshness.FRESH,
    )
    resolver.client.member = DingTalkMember(
        "employee-2", "union-2", "New Name", True, (1,)
    )

    second = await resolver.resolve_active_member(
        DingTalkAuthResult("union-2", None, "test-corp"), DirectoryFreshness.FRESH
    )

    assert second == first
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select display_name from platform_control.internal_users "
            "where internal_user_id=%s", (first,)
        ).fetchone() == ("New Name",)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize("freshness", [DirectoryFreshness.HARD_STALE])
async def test_normal_login_requires_current_usable_directory_generation(
    production_environment, tmp_path: Path, freshness: DirectoryFreshness
) -> None:
    member = DingTalkMember("employee-3", "union-3", "Employee", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)
    with psycopg.connect(production_environment["admin"]) as connection:
        before = connection.execute(
            "select count(*) from platform_control.internal_users"
        ).fetchone()

    with pytest.raises(IdentityResolutionError, match="directory unavailable"):
        await resolver.resolve_active_member(
            DingTalkAuthResult("union-3", "employee-3", "test-corp"), freshness
        )

    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.internal_users"
        ).fetchone() == before


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_inactive_absent_wrong_corp_and_mismatched_provider_fail_without_partial_user(
    production_environment, tmp_path: Path
) -> None:
    member = DingTalkMember("employee-4", "union-4", "Employee Four", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)
    attempts = (
        DingTalkAuthResult("union-4", "employee-4", "wrong-corp"),
        DingTalkAuthResult("other-union", "employee-4", "test-corp"),
        DingTalkAuthResult("union-4", "other-user", "test-corp"),
    )
    for auth in attempts:
        with pytest.raises(IdentityResolutionError):
            await resolver.resolve_active_member(auth, DirectoryFreshness.FRESH)

    resolver.client.member = DingTalkMember(
        "employee-4", "union-4", "Employee Four", False, (1,)
    )
    with pytest.raises(IdentityResolutionError, match="member inactive"):
        await resolver.resolve_active_member(
            DingTalkAuthResult("union-4", "employee-4", "test-corp"),
            DirectoryFreshness.FRESH,
        )

    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.internal_users "
            "where display_name='Employee Four'"
        ).fetchone() == (0,)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_name_only_or_ambiguous_provider_mapping_never_creates_identity(
    production_environment, tmp_path: Path
) -> None:
    member = DingTalkMember("employee-5", "union-5", "Shared Name", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Shared Name','active'),(%s,'Shared Name','active')",
            (uuid4(), uuid4()),
        )

    with pytest.raises(IdentityResolutionError):
        await resolver.resolve_active_member(
            DingTalkAuthResult("", None, "test-corp"), DirectoryFreshness.FRESH
        )
    assert not hasattr(resolver, "resolve_by_name")


def test_resolver_requires_exact_control_application_role(tmp_path: Path) -> None:
    member = DingTalkMember("employee-6", "union-6", "Employee", True, (1,))
    with pytest.raises(ValueError, match="exact control app DSN required"):
        IdentityResolver(
            "postgresql://platform_control_app_preview@127.0.0.1/agent_platform_control",
            corp_id="test-corp", client=DirectoryClient(member),
            identity_codec=_codec(tmp_path),
        )


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_concurrent_first_login_creates_one_user_and_two_stable_mappings(
    production_environment, tmp_path: Path
) -> None:
    member = DingTalkMember("employee-7", "union-7", "Concurrent", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)
    auth = DingTalkAuthResult("union-7", "employee-7", "test-corp")

    results = await asyncio.gather(*(
        resolver.resolve_active_member(auth, DirectoryFreshness.FRESH)
        for _ in range(8)
    ))

    assert len(set(results)) == 1
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.internal_users "
            "where internal_user_id=%s", (results[0],)
        ).fetchone() == (1,)
        assert connection.execute(
            "select count(*) from platform_control.provider_identities "
            "where internal_user_id=%s", (results[0],)
        ).fetchone() == (2,)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_conflicting_corporate_and_union_mappings_leave_zero_partial_state(
    production_environment, tmp_path: Path
) -> None:
    member = DingTalkMember("employee-conflict", "union-conflict", "Conflict", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)
    codec = resolver.identity_codec
    corporate = codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        _provider_value("test-corp", member.userid),
    )
    union = codec.seal(IdentityResolver.UNION_SUBJECT_KIND, member.unionid)
    first_user, second_user = uuid4(), uuid4()
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'First','active'),(%s,'Second','active')",
            (first_user, second_user),
        )
        for protected, user_id in ((corporate, first_user), (union, second_user)):
            connection.execute(
                "insert into platform_control.provider_identities "
                "(provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
                "lookup_key_version,encrypted_provider_id,encryption_key_version) "
                "values (%s,%s,%s,%s,%s,%s,%s)",
                (
                    uuid4(), user_id, protected.subject_kind,
                    protected.lookup_hmac, protected.lookup_key_version,
                    protected.ciphertext, protected.encryption_key_version,
                ),
            )
        before = connection.execute(
            "select (select count(*) from platform_control.internal_users),"
            "(select count(*) from platform_control.provider_identities),"
            "(select count(*) from platform_control.directory_members "
            "where internal_user_id is not null)"
        ).fetchone()

    with pytest.raises(IdentityResolutionError, match="collision"):
        await resolver.resolve_active_member(
            DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
            DirectoryFreshness.FRESH,
        )

    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select (select count(*) from platform_control.internal_users),"
            "(select count(*) from platform_control.provider_identities),"
            "(select count(*) from platform_control.directory_members "
            "where internal_user_id is not null)"
        ).fetchone() == before


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_failed_final_refresh_rolls_back_user_and_provider_mappings(
    production_environment, tmp_path: Path
) -> None:
    member = DingTalkMember("employee-8", "union-8", "Rollback Member", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "create function platform_control.reject_test_directory_bind() "
            "returns trigger language plpgsql as $$ begin "
            "if new.internal_user_id is not null then "
            "raise exception 'forced tail failure'; end if; return new; end $$"
        )
        connection.execute(
            "create trigger reject_test_directory_bind before update on "
            "platform_control.directory_members for each row execute function "
            "platform_control.reject_test_directory_bind()"
        )
        before = connection.execute(
            "select (select count(*) from platform_control.internal_users),"
            "(select count(*) from platform_control.provider_identities)"
        ).fetchone()

    try:
        with pytest.raises(IdentityResolutionError, match="persistence unavailable"):
            await resolver.resolve_active_member(
                DingTalkAuthResult("union-8", "employee-8", "test-corp"),
                DirectoryFreshness.FRESH,
            )

        with psycopg.connect(production_environment["admin"]) as connection:
            assert connection.execute(
                "select (select count(*) from platform_control.internal_users),"
                "(select count(*) from platform_control.provider_identities)"
            ).fetchone() == before
    finally:
        with psycopg.connect(production_environment["admin"]) as connection:
            connection.execute(
                "drop trigger if exists reject_test_directory_bind on "
                "platform_control.directory_members"
            )
            connection.execute(
                "drop function if exists platform_control.reject_test_directory_bind()"
            )


@pytest.mark.postgres
def test_obsolete_refresh_function_is_revoked_and_still_hardened(
    control_database,
) -> None:
    signature = (
        "platform_control.refresh_verified_internal_member("
        "uuid,text,uuid,text,integer[],bytea[])"
    )
    for name, environment in control_database["environments"].items():
        app = environment["roles"][1]
        other_app = (
            "platform_control_app_preview"
            if name == "production"
            else "platform_control_app"
        )
        with psycopg.connect(environment["admin"]) as connection:
            privilege = connection.execute(
                "select has_function_privilege(%s,%s,'execute'),"
                "has_function_privilege(%s,%s,'execute'),"
                "has_function_privilege('public',%s,'execute'),"
                "prosecdef,proconfig from pg_proc where oid=%s::regprocedure",
                (app, signature, other_app, signature, signature, signature),
            ).fetchone()
        assert privilege[:4] == (False, False, False, True)
        assert privilege[4] == ["search_path=pg_catalog, platform_control"]


@pytest.mark.postgres
def test_app_role_has_only_narrow_verified_identity_mutation_boundary(
    control_database,
) -> None:
    forbidden_function = (
        "platform_control.create_internal_member(uuid,text)"
    )
    obsolete_function = (
        "platform_control.refresh_verified_internal_member("
        "uuid,text,uuid,text,integer[],bytea[])"
    )
    for name, environment in control_database["environments"].items():
        app = environment["roles"][1]
        other_app = (
            "platform_control_app_preview"
            if name == "production"
            else "platform_control_app"
        )
        with psycopg.connect(environment["admin"]) as connection:
            privileges = connection.execute(
                "select "
                "has_table_privilege(%s,'platform_control.provider_identities','insert'),"
                "has_table_privilege(%s,'platform_control.provider_identities','update'),"
                "has_table_privilege(%s,'platform_control.provider_identities','delete'),"
                "has_table_privilege(%s,'platform_control.directory_members','update'),"
                "has_table_privilege(%s,'platform_control.internal_users','insert'),"
                "has_function_privilege(%s,%s,'execute'),"
                "has_function_privilege(%s,%s,'execute'),"
                "exists (select 1 from pg_proc where pronamespace="
                "'platform_control'::regnamespace and proname="
                "'resolve_verified_dingtalk_member'),"
                "exists (select 1 from pg_proc procedure join pg_namespace namespace "
                "on namespace.oid=procedure.pronamespace where namespace.nspname="
                "'platform_control' and procedure.proname="
                "'resolve_verified_dingtalk_member' and "
                "has_function_privilege(%s,procedure.oid,'execute')),"
                "exists (select 1 from pg_proc procedure join pg_namespace namespace "
                "on namespace.oid=procedure.pronamespace where namespace.nspname="
                "'platform_control' and procedure.proname="
                "'resolve_verified_dingtalk_member' and "
                "has_function_privilege(%s,procedure.oid,'execute'))",
                (
                    app, app, app, app, app,
                    app, forbidden_function,
                    app, obsolete_function,
                    app, other_app,
                ),
            ).fetchone()
        assert privileges == (
            False, False, False, False, False,
            False, False, True, True, False,
        )


@pytest.mark.postgres
def test_app_role_cannot_directly_create_rebind_or_copy_identity_rows(
    production_environment,
) -> None:
    app_url = production_environment["urls"]["platform_control_app"]
    statements = (
        (
            "select platform_control.create_internal_member(%s,'Bypass')",
            (uuid4(),),
        ),
        (
            "insert into platform_control.provider_identities "
            "(provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version) "
            "select %s,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version "
            "from platform_control.provider_identities limit 1",
            (uuid4(),),
        ),
        (
            "update platform_control.provider_identities set verified_at=now()",
            (),
        ),
        (
            "update platform_control.directory_members set internal_user_id=%s",
            (uuid4(),),
        ),
    )
    for statement, parameters in statements:
        with psycopg.connect(app_url) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement, parameters)


@pytest.mark.asyncio
async def test_cancellation_waits_for_database_mutation_to_finish_then_propagates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    member = DingTalkMember("employee-9", "union-9", "Employee", True, (1,))
    resolver = IdentityResolver(
        "postgresql://platform_control_app@127.0.0.1/agent_platform_control",
        corp_id="test-corp",
        client=DirectoryClient(member),
        identity_codec=_codec(tmp_path),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    expected = uuid4()

    async def delayed_to_thread(function, *args):
        started.set()
        await release.wait()
        return expected

    monkeypatch.setattr(identity_module.asyncio, "to_thread", delayed_to_thread)
    task = asyncio.create_task(resolver.resolve_active_member(
        DingTalkAuthResult("union-9", "employee-9", "test-corp"),
        DirectoryFreshness.FRESH,
    ))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
