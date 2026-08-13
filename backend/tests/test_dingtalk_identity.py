from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

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
async def test_failed_final_refresh_rolls_back_user_and_provider_mappings(
    production_environment, tmp_path: Path
) -> None:
    member = DingTalkMember("employee-8", "union-8", "Rollback Member", True, (1,))
    resolver = _resolver(production_environment, tmp_path, member)
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "revoke execute on function "
            "platform_control.refresh_verified_internal_member("
            "uuid,text,uuid,text,integer[],bytea[]) from platform_control_app"
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
                "grant execute on function "
                "platform_control.refresh_verified_internal_member("
                "uuid,text,uuid,text,integer[],bytea[]) to platform_control_app"
            )


@pytest.mark.postgres
def test_verified_refresh_function_is_hardened_and_environment_scoped(
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
        assert privilege[:4] == (True, False, False, True)
        assert privilege[4] == ["search_path=pg_catalog, platform_control"]
