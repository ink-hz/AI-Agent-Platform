from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

import app.control_plane.identity as identity_module

from app.control_plane.dingtalk import DingTalkAuthResult, DingTalkMember
from app.control_plane.crypto import ProtectedProviderId
from app.control_plane.directory import (
    DIRECTORY_SOURCE_SCHEMA_VERSION,
    StagedDepartment,
    StagedMember,
    canonical_directory_digest,
)
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


def _set_directory_key_policy(environment) -> None:
    with psycopg.connect(
        environment["urls"]["platform_control_maintenance"]
    ) as connection:
        connection.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'dingtalk', array[1,2])"
        )


def _seed_generation(
    environment,
    codec,
    member: DingTalkMember,
    *,
    activate: bool = True,
) -> UUID:
    generation_id = uuid4()
    corporate = codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        _provider_value("test-corp", member.userid),
    )
    union = codec.seal(IdentityResolver.UNION_SUBJECT_KIND, member.unionid)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',1,0,%s,now())",
            (generation_id, "a" * 64),
        )
        if activate:
            connection.execute(
                "update platform_control.directory_state set active_generation_id=%s, "
                "last_complete_at=now(), updated_at=now() where singleton",
                (generation_id,),
            )
        connection.execute(
            "insert into platform_control.directory_members "
            "(generation_id,member_key,subject_kind,lookup_hmac,lookup_key_version,"
            "encrypted_provider_id,encryption_key_version,display_name,status,"
            "union_lookup_hmac,union_lookup_key_version) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                generation_id, uuid4(), corporate.subject_kind,
                corporate.lookup_hmac, corporate.lookup_key_version,
                corporate.ciphertext, corporate.encryption_key_version,
                member.display_name, "active" if member.active else "inactive",
                union.lookup_hmac, union.lookup_key_version,
            ),
        )
    return generation_id


def _resolver(environment, tmp_path: Path, member: DingTalkMember) -> IdentityResolver:
    codec = _codec(tmp_path)
    _set_directory_key_policy(environment)
    _seed_generation(environment, codec, member)
    return IdentityResolver(
        environment["urls"]["platform_control_app"],
        corp_id="test-corp",
        client=DirectoryClient(member),
        identity_codec=codec,
    )


def _seed_mapping(environment, protected, internal_user_id=None):
    selected_user_id = internal_user_id or uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        if internal_user_id is None:
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) values (%s,'Seed','active')",
                (selected_user_id,),
            )
        connection.execute(
            "insert into platform_control.provider_identities "
            "(provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version) "
            "values (%s,%s,%s,%s,%s,%s,%s)",
            (
                uuid4(), selected_user_id, protected.subject_kind,
                protected.lookup_hmac, protected.lookup_key_version,
                protected.ciphertext, protected.encryption_key_version,
            ),
        )
    return selected_user_id


def _at_lookup(protected, version: int, lookup_hmac: bytes):
    return ProtectedProviderId(
        subject_kind=protected.subject_kind,
        lookup_hmac=lookup_hmac,
        lookup_key_version=version,
        ciphertext=protected.ciphertext,
        encryption_key_version=protected.encryption_key_version,
    )


def _identity_state(environment):
    with psycopg.connect(environment["admin"]) as connection:
        return connection.execute(
            "select (select count(*) from platform_control.internal_users),"
            "(select count(*) from platform_control.provider_identities),"
            "(select count(*) from platform_control.directory_members "
            "where internal_user_id is not null)"
        ).fetchone()


def _stage_and_promote_generation(
    environment, codec, members, *, finalize=True, promote=True
) -> UUID:
    _set_directory_key_policy(environment)
    generation_id = uuid4()
    run_id = uuid4()
    worker_url = environment["urls"]["platform_directory_worker"]
    root_key = uuid4()
    root = codec.seal("department", "1")
    staged_department = StagedDepartment(root_key, None, root, "Organization")
    staged_members = []
    memberships = []
    for member in members:
        corporate = codec.seal(
            IdentityResolver.CORPORATE_SUBJECT_KIND,
            _provider_value("test-corp", member.userid),
        )
        union = codec.seal(IdentityResolver.UNION_SUBJECT_KIND, member.unionid)
        staged_member = StagedMember(
            uuid4(), corporate, union, member.display_name,
            "active" if member.active else "inactive",
            member.gender,
        )
        staged_members.append(staged_member)
        memberships.append((staged_member.member_key, root_key))
    closure = ((root_key, root_key, 0),)
    digest = canonical_directory_digest(
        DIRECTORY_SOURCE_SCHEMA_VERSION,
        (staged_department,), tuple(staged_members), tuple(memberships), closure,
    )
    with psycopg.connect(worker_url) as connection:
        connection.execute(
            "select platform_control.create_directory_staging_generation_v28("
            "%s,%s,'scheduled',%s,1,%s,1,%s,%s)",
            (generation_id, run_id, len(members), len(members),
             DIRECTORY_SOURCE_SCHEMA_VERSION, digest),
        )
        connection.execute(
            "select platform_control.stage_directory_department("
            "%s,%s,null,%s,%s,%s,%s,'Organization')",
            (generation_id, root_key, root.lookup_hmac, root.lookup_key_version,
             root.ciphertext, root.encryption_key_version),
        )
        connection.execute(
            "select platform_control.stage_department_closure(%s,%s,%s,0)",
            (generation_id, root_key, root_key),
        )
        for staged_member in staged_members:
            connection.execute(
                "select platform_control.stage_directory_member_v28("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    generation_id, staged_member.member_key,
                    staged_member.corporate.lookup_hmac,
                    staged_member.corporate.lookup_key_version,
                    staged_member.corporate.ciphertext,
                    staged_member.corporate.encryption_key_version,
                    staged_member.union.lookup_hmac,
                    staged_member.union.lookup_key_version,
                    staged_member.union.ciphertext,
                    staged_member.union.encryption_key_version,
                    staged_member.display_name, staged_member.status,
                    staged_member.gender,
                ),
            )
            connection.execute(
                "select platform_control.stage_directory_membership(%s,%s,%s)",
                (generation_id, staged_member.member_key, root_key),
            )
        if finalize:
            connection.execute(
                "select platform_control.finalize_directory_staging_generation(%s)",
                (generation_id,),
            )
        if promote:
            connection.execute(
                "select platform_control.promote_verified_directory_generation(%s)",
                (generation_id,),
            )
    return generation_id


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
async def test_new_current_generation_rebinds_same_verified_pair_then_departure_denies(
    production_environment,
    tmp_path: Path,
) -> None:
    original = DingTalkMember(
        "generation-user", "generation-union", "Original", True, (1,)
    )
    resolver = _resolver(production_environment, tmp_path, original)
    internal_user_id = await resolver.resolve_active_member(
        DingTalkAuthResult(original.unionid, original.userid, "test-corp"),
        DirectoryFreshness.FRESH,
    )

    renamed = DingTalkMember(
        original.userid, original.unionid, "Renamed", True, (1,)
    )
    second_generation = _stage_and_promote_generation(
        production_environment, resolver.identity_codec, (renamed,)
    )
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select member.internal_user_id,users.display_name from "
            "platform_control.directory_members member join "
            "platform_control.internal_users users using (internal_user_id) "
            "where member.generation_id=%s", (second_generation,)
        ).fetchone() == (internal_user_id, "Renamed")

    resolver.client.member = renamed
    assert await resolver.resolve_active_member(
        DingTalkAuthResult(renamed.unionid, renamed.userid, "test-corp"),
        DirectoryFreshness.FRESH,
    ) == internal_user_id
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select member.internal_user_id,users.display_name from "
            "platform_control.directory_members member join "
            "platform_control.internal_users users using (internal_user_id) "
            "where member.generation_id=%s", (second_generation,)
        ).fetchone() == (internal_user_id, "Renamed")

    departed_generation = _stage_and_promote_generation(
        production_environment, resolver.identity_codec, ()
    )
    before = _identity_state(production_environment)
    with pytest.raises(IdentityResolutionError, match="directory member"):
        await resolver.resolve_active_member(
            DingTalkAuthResult(renamed.unionid, renamed.userid, "test-corp"),
            DirectoryFreshness.FRESH,
        )
    assert _identity_state(production_environment) == before
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select active_generation_id from platform_control.directory_state"
        ).fetchone() == (departed_generation,)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_new_generation_with_transition_candidate_only_pair_is_denied(
    production_environment,
    tmp_path: Path,
) -> None:
    member = DingTalkMember(
        "candidate-user", "candidate-union", "Candidate", True, (1,)
    )
    resolver = _resolver(production_environment, tmp_path, member)
    internal_user_id = await resolver.resolve_active_member(
        DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
        DirectoryFreshness.FRESH,
    )
    corporate = resolver.identity_codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        _provider_value("test-corp", member.userid),
    )
    union = resolver.identity_codec.seal(
        IdentityResolver.UNION_SUBJECT_KIND, member.unionid
    )
    old_corporate = dict(resolver.identity_codec.lookup_candidates(
        corporate.subject_kind,
        _provider_value("test-corp", member.userid),
    ))[1]
    old_union = dict(resolver.identity_codec.lookup_candidates(
        union.subject_kind, member.unionid
    ))[1]
    generation_id = uuid4()
    worker_url = production_environment["urls"]["platform_directory_worker"]
    root_key = uuid4()
    member_key = uuid4()
    root = resolver.identity_codec.seal("department", "candidate-root")
    root = _at_lookup(
        root,
        1,
        dict(resolver.identity_codec.lookup_candidates(
            "department", "candidate-root"
        ))[1],
    )
    corporate_v1 = _at_lookup(corporate, 1, old_corporate)
    union_v1 = _at_lookup(union, 1, old_union)
    digest = canonical_directory_digest(
        DIRECTORY_SOURCE_SCHEMA_VERSION,
        (StagedDepartment(root_key, None, root, "Root"),),
        (StagedMember(
            member_key, corporate_v1, union_v1, member.display_name, "active",
            member.gender,
        ),),
        ((member_key, root_key),), ((root_key, root_key, 0),),
    )
    with psycopg.connect(worker_url) as connection:
        connection.execute(
            "select platform_control.create_directory_staging_generation_v28("
            "%s,%s,'scheduled',1,1,1,1,%s,%s)",
            (generation_id, uuid4(), DIRECTORY_SOURCE_SCHEMA_VERSION, digest),
        )
        connection.execute(
            "select platform_control.stage_directory_department("
            "%s,%s,null,%s,%s,%s,%s,'Root')",
            (generation_id, root_key, root.lookup_hmac, root.lookup_key_version,
             root.ciphertext, root.encryption_key_version),
        )
        connection.execute(
            "select platform_control.stage_directory_member_v28("
            "%s,%s,%s,1,%s,%s,%s,1,%s,%s,%s,'active',%s)",
            (
                generation_id, member_key, old_corporate,
                corporate.ciphertext, corporate.encryption_key_version,
                old_union, union.ciphertext, union.encryption_key_version,
                member.display_name, member.gender,
            ),
        )
        connection.execute(
            "select platform_control.stage_directory_membership(%s,%s,%s)",
            (generation_id, member_key, root_key),
        )
        connection.execute(
            "select platform_control.stage_department_closure(%s,%s,%s,0)",
            (generation_id, root_key, root_key),
        )
        connection.execute(
            "select platform_control.finalize_directory_staging_generation(%s)",
            (generation_id,),
        )
        connection.execute(
            "select platform_control.promote_verified_directory_generation(%s)",
            (generation_id,),
        )

    before = _identity_state(production_environment)
    with pytest.raises(IdentityResolutionError, match="directory member"):
        await resolver.resolve_active_member(
            DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
            DirectoryFreshness.FRESH,
        )
    assert _identity_state(production_environment) == before
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select internal_user_id from platform_control.directory_members "
            "where generation_id=%s", (generation_id,)
        ).fetchone() == (None,)
        assert connection.execute(
            "select status from platform_control.internal_users "
            "where internal_user_id=%s", (internal_user_id,)
        ).fetchone() == ("active",)


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
@pytest.mark.parametrize(
    "mapping_state",
    ("both_old_same_user", "one_old_one_exact", "exact_different_users"),
)
async def test_only_exact_current_pair_mappings_can_authorize_existing_user(
    production_environment,
    tmp_path: Path,
    mapping_state: str,
) -> None:
    member = DingTalkMember(
        f"exact-{mapping_state}",
        f"exact-union-{mapping_state}",
        "Exact Mapping",
        True,
        (1,),
    )
    resolver = _resolver(production_environment, tmp_path, member)
    corporate = resolver.identity_codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        _provider_value("test-corp", member.userid),
    )
    union = resolver.identity_codec.seal(
        IdentityResolver.UNION_SUBJECT_KIND, member.unionid
    )
    old_corporate = _at_lookup(
        corporate,
        1,
        dict(resolver.identity_codec.lookup_candidates(
            corporate.subject_kind,
            _provider_value("test-corp", member.userid),
        ))[1],
    )
    old_union = _at_lookup(
        union,
        1,
        dict(resolver.identity_codec.lookup_candidates(
            union.subject_kind, member.unionid
        ))[1],
    )

    seeded_users = []
    if mapping_state == "both_old_same_user":
        mapped_user = _seed_mapping(production_environment, old_corporate)
        _seed_mapping(production_environment, old_union, mapped_user)
        seeded_users.append(mapped_user)
    elif mapping_state == "one_old_one_exact":
        mapped_user = _seed_mapping(production_environment, old_corporate)
        _seed_mapping(production_environment, union, mapped_user)
        seeded_users.append(mapped_user)
    else:
        seeded_users.extend((
            _seed_mapping(production_environment, corporate),
            _seed_mapping(production_environment, union),
        ))
    before = _identity_state(production_environment)

    try:
        with pytest.raises(IdentityResolutionError, match="collision"):
            await resolver.resolve_active_member(
                DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
                DirectoryFreshness.FRESH,
            )

        assert _identity_state(production_environment) == before
    finally:
        with psycopg.connect(production_environment["admin"]) as connection:
            connection.execute(
                "delete from platform_control.internal_users "
                "where internal_user_id=any(%s)", (seeded_users,)
            )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize(
    ("directory_member", "presented_member", "seed_mapping_kind"),
    [
        (
            DingTalkMember("target-a", "target-union-a", "Target A", True, (1,)),
            DingTalkMember("target-a", "owner-union-a", "Target A", True, (1,)),
            "presented_union",
        ),
        (
            DingTalkMember("owner-b", "owner-union-b", "Owner B", True, (1,)),
            DingTalkMember("owner-b", "target-union-b", "Owner B", True, (1,)),
            "presented_corporate",
        ),
        (
            DingTalkMember("target-c", "target-union-c", "Target C", True, (1,)),
            DingTalkMember("target-c", "other-union-c", "Target C", True, (1,)),
            None,
        ),
    ],
    ids=(
        "target-corporate-owner-union",
        "owner-corporate-target-union",
        "both-absent-cross-person",
    ),
)
async def test_directory_pair_proof_rejects_all_cross_person_mixes_without_mutation(
    production_environment,
    tmp_path: Path,
    directory_member: DingTalkMember,
    presented_member: DingTalkMember,
    seed_mapping_kind: str | None,
) -> None:
    resolver = _resolver(production_environment, tmp_path, directory_member)
    resolver.client.member = presented_member
    if seed_mapping_kind == "presented_union":
        _seed_mapping(
            production_environment,
            resolver.identity_codec.seal(
                IdentityResolver.UNION_SUBJECT_KIND, presented_member.unionid
            ),
        )
    elif seed_mapping_kind == "presented_corporate":
        _seed_mapping(
            production_environment,
            resolver.identity_codec.seal(
                IdentityResolver.CORPORATE_SUBJECT_KIND,
                _provider_value("test-corp", presented_member.userid),
            ),
        )
    before = _identity_state(production_environment)

    with pytest.raises(IdentityResolutionError):
        await resolver.resolve_active_member(
            DingTalkAuthResult(
                presented_member.unionid,
                presented_member.userid,
                "test-corp",
            ),
            DirectoryFreshness.FRESH,
        )

    assert _identity_state(production_environment) == before


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize("mapping_kind", ["corporate", "union"])
async def test_one_sided_identity_mapping_is_never_completed_by_login(
    production_environment,
    tmp_path: Path,
    mapping_kind: str,
) -> None:
    member = DingTalkMember(
        f"one-sided-{mapping_kind}",
        f"one-sided-union-{mapping_kind}",
        "One Sided",
        True,
        (1,),
    )
    resolver = _resolver(production_environment, tmp_path, member)
    if mapping_kind == "corporate":
        protected = resolver.identity_codec.seal(
            IdentityResolver.CORPORATE_SUBJECT_KIND,
            _provider_value("test-corp", member.userid),
        )
    else:
        protected = resolver.identity_codec.seal(
            IdentityResolver.UNION_SUBJECT_KIND, member.unionid
        )
    _seed_mapping(production_environment, protected)
    before = _identity_state(production_environment)

    with pytest.raises(IdentityResolutionError, match="collision"):
        await resolver.resolve_active_member(
            DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
            DirectoryFreshness.FRESH,
        )

    assert _identity_state(production_environment) == before


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_stale_generation_pair_cannot_authorize_login(
    production_environment,
    tmp_path: Path,
) -> None:
    stale = DingTalkMember("stale-user", "stale-union", "Stale", True, (1,))
    active = DingTalkMember("active-user", "active-union", "Active", True, (1,))
    codec = _codec(tmp_path)
    with psycopg.connect(
        production_environment["urls"]["platform_control_maintenance"]
    ) as connection:
        connection.execute(
            "select platform_control.set_provider_identity_key_policy("
            "'dingtalk', array[1,2])"
        )
    _seed_generation(production_environment, codec, stale, activate=False)
    _seed_generation(production_environment, codec, active, activate=True)
    resolver = IdentityResolver(
        production_environment["urls"]["platform_control_app"],
        corp_id="test-corp",
        client=DirectoryClient(stale),
        identity_codec=codec,
    )
    before = _identity_state(production_environment)

    with pytest.raises(IdentityResolutionError):
        await resolver.resolve_active_member(
            DingTalkAuthResult(stale.unionid, stale.userid, "test-corp"),
            DirectoryFreshness.FRESH,
        )

    assert _identity_state(production_environment) == before


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_departure_promotion_serializes_before_resolution_commit(
    production_environment,
    tmp_path: Path,
) -> None:
    member = DingTalkMember(
        "promotion-race", "promotion-race-union", "Race", True, (1,)
    )
    resolver = _resolver(production_environment, tmp_path, member)
    await resolver.resolve_active_member(
        DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
        DirectoryFreshness.FRESH,
    )
    generation_id = _stage_and_promote_generation(
        production_environment, resolver.identity_codec, (), promote=False
    )
    worker_url = production_environment["urls"]["platform_directory_worker"]
    with psycopg.connect(worker_url) as worker:
        worker.execute(
            "select platform_control.lock_dingtalk_identity_directory()"
        )
        before = _identity_state(production_environment)
        resolving = asyncio.create_task(resolver.resolve_active_member(
            DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
            DirectoryFreshness.FRESH,
        ))
        for _ in range(200):
            with psycopg.connect(production_environment["admin"]) as admin:
                waiting = admin.execute(
                    "select exists (select 1 from pg_stat_activity where "
                    "usename=%s and wait_event_type='Lock' and "
                    "wait_event='advisory')",
                    (production_environment["roles"][1],),
                ).fetchone()[0]
            if waiting:
                break
            await asyncio.sleep(0.005)
        assert waiting is True
        assert not resolving.done()
        worker.execute(
            "select platform_control.promote_verified_directory_generation(%s)",
            (generation_id,),
        )

    with pytest.raises(IdentityResolutionError, match="directory member"):
        await resolving
    assert _identity_state(production_environment) == before
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select active_generation_id from platform_control.directory_state"
        ).fetchone() == (generation_id,)


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


@pytest.mark.postgres
def test_directory_worker_can_stage_pair_facts_only_through_narrow_boundary(
    production_environment,
    tmp_path: Path,
) -> None:
    _set_directory_key_policy(production_environment)
    worker_url = production_environment["urls"]["platform_directory_worker"]
    codec = _codec(tmp_path)
    corporate = codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        _provider_value("test-corp", "worker-member"),
    )
    union = codec.seal(IdentityResolver.UNION_SUBJECT_KIND, "worker-union")
    generation_id, member_key = uuid4(), uuid4()
    with psycopg.connect(worker_url) as connection:
        connection.execute(
            "select platform_control.create_directory_staging_generation_v28("
            "%s,%s,'scheduled',1,1,1,1,%s,%s)",
            (generation_id, uuid4(), DIRECTORY_SOURCE_SCHEMA_VERSION, "0" * 64),
        )
        result = connection.execute(
            "select platform_control.stage_directory_member_v28("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                generation_id, member_key,
                corporate.lookup_hmac, corporate.lookup_key_version,
                corporate.ciphertext, corporate.encryption_key_version,
                union.lookup_hmac, union.lookup_key_version,
                union.ciphertext, union.encryption_key_version,
                "Worker Member", "active", "female",
            ),
        ).fetchone()
    assert result == (member_key,)

    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select internal_user_id,lookup_hmac,lookup_key_version,gender,"
            "union_lookup_hmac,union_lookup_key_version from "
            "platform_control.directory_members where generation_id=%s "
            "and member_key=%s",
            (generation_id, member_key),
        ).fetchone() == (
            None,
            corporate.lookup_hmac,
            corporate.lookup_key_version,
            "female",
            union.lookup_hmac,
            union.lookup_key_version,
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("gender", "attribute_status"),
    (("male", "valid"), ("female", "valid"), (None, "missing")),
    ids=("male", "female", "missing"),
)
def test_schema_v2_gender_round_trip_checksum_and_promotion(
    production_environment,
    tmp_path: Path,
    gender: str | None,
    attribute_status: str,
) -> None:
    member = DingTalkMember(
        f"gender-{gender or 'missing'}",
        f"gender-union-{gender or 'missing'}",
        "Gender Member",
        True,
        (1,),
        gender,
        attribute_status,
    )
    generation_id = _stage_and_promote_generation(
        production_environment, _codec(tmp_path), (member,)
    )

    with psycopg.connect(
        production_environment["admin"], row_factory=dict_row
    ) as admin:
        row = admin.execute(
            "select member.gender,generation.source_schema_version,"
            "generation.status,generation.expected_content_sha256,"
            "generation.content_sha256,"
            "platform_control.directory_generation_checksum_v28("
            "generation.generation_id) as database_checksum,"
            "state.active_generation_id "
            "from platform_control.directory_members member "
            "join platform_control.directory_generations generation "
            "using (generation_id) cross join platform_control.directory_state state "
            "where member.generation_id=%s",
            (generation_id,),
        ).fetchone()
    assert row["gender"] == gender
    assert row["source_schema_version"] == 2
    assert row["status"] == "complete"
    assert row["expected_content_sha256"] == row["database_checksum"]
    assert row["content_sha256"] == row["database_checksum"]
    assert row["active_generation_id"] == generation_id


@pytest.mark.postgres
@pytest.mark.parametrize("gender", ("", "unknown"), ids=("blank", "unknown"))
def test_schema_v2_member_staging_rejects_invalid_gender_without_partial_row(
    production_environment,
    tmp_path: Path,
    gender: str,
) -> None:
    _set_directory_key_policy(production_environment)
    worker_url = production_environment["urls"]["platform_directory_worker"]
    codec = _codec(tmp_path)
    corporate = codec.seal("employee", f"invalid-gender-{gender or 'blank'}")
    union = codec.seal("employee_union", f"invalid-union-{gender or 'blank'}")
    generation_id = uuid4()
    with psycopg.connect(worker_url) as connection:
        with connection.transaction():
            connection.execute(
                "select platform_control.create_directory_staging_generation_v28("
                "%s,%s,'scheduled',1,1,1,1,2,%s)",
                (generation_id, uuid4(), "0" * 64),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    "select platform_control.stage_directory_member_v28("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        generation_id,
                        uuid4(),
                        corporate.lookup_hmac,
                        corporate.lookup_key_version,
                        corporate.ciphertext,
                        corporate.encryption_key_version,
                        union.lookup_hmac,
                        union.lookup_key_version,
                        union.ciphertext,
                        union.encryption_key_version,
                        "Invalid Gender",
                        "active",
                        gender,
                    ),
                )

    with psycopg.connect(production_environment["admin"]) as admin:
        assert admin.execute(
            "select count(*) from platform_control.directory_members "
            "where generation_id=%s",
            (generation_id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_schema_v1_creation_finalization_and_promotion_remain_compatible(
    production_environment,
    tmp_path: Path,
) -> None:
    _set_directory_key_policy(production_environment)
    worker_url = production_environment["urls"]["platform_directory_worker"]
    codec = _codec(tmp_path)
    generation_id, root_key, member_key = uuid4(), uuid4(), uuid4()
    root = codec.seal("department", "schema-v1-root")
    corporate = codec.seal("employee", "schema-v1-member")
    union = codec.seal("employee_union", "schema-v1-union")

    with psycopg.connect(worker_url) as worker:
        worker.execute(
            "select platform_control.create_directory_staging_generation_v20("
            "%s,%s,'scheduled',1,1,1,1,1,%s)",
            (generation_id, uuid4(), "0" * 64),
        )
        worker.execute(
            "select platform_control.stage_directory_department("
            "%s,%s,null,%s,%s,%s,%s,'Schema V1')",
            (
                generation_id,
                root_key,
                root.lookup_hmac,
                root.lookup_key_version,
                root.ciphertext,
                root.encryption_key_version,
            ),
        )
        worker.execute(
            "select platform_control.stage_directory_member_v19("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Schema V1','active')",
            (
                generation_id,
                member_key,
                corporate.lookup_hmac,
                corporate.lookup_key_version,
                corporate.ciphertext,
                corporate.encryption_key_version,
                union.lookup_hmac,
                union.lookup_key_version,
                union.ciphertext,
                union.encryption_key_version,
            ),
        )
        worker.execute(
            "select platform_control.stage_directory_membership(%s,%s,%s)",
            (generation_id, member_key, root_key),
        )
        worker.execute(
            "select platform_control.stage_department_closure(%s,%s,%s,0)",
            (generation_id, root_key, root_key),
        )

    with psycopg.connect(production_environment["admin"]) as admin:
        expected = admin.execute(
            "select platform_control.directory_generation_checksum_v20(%s)",
            (generation_id,),
        ).fetchone()[0]
        admin.execute(
            "update platform_control.directory_generations "
            "set expected_content_sha256=%s where generation_id=%s",
            (expected, generation_id),
        )

    with psycopg.connect(worker_url) as worker:
        assert worker.execute(
            "select platform_control.finalize_directory_staging_generation(%s)",
            (generation_id,),
        ).fetchone() == (expected,)
        assert worker.execute(
            "select platform_control.promote_verified_directory_generation(%s)",
            (generation_id,),
        ).fetchone() == (generation_id,)

    with psycopg.connect(production_environment["admin"]) as admin:
        assert admin.execute(
            "select generation.source_schema_version,generation.status,member.gender,"
            "state.active_generation_id from platform_control.directory_generations "
            "generation join platform_control.directory_members member "
            "using (generation_id) cross join platform_control.directory_state state "
            "where generation.generation_id=%s",
            (generation_id,),
        ).fetchone() == (1, "complete", None, generation_id)


@pytest.mark.postgres
def test_gender_tampering_after_staging_rejects_finalization(
    production_environment,
    tmp_path: Path,
) -> None:
    member = DingTalkMember(
        "gender-tamper", "gender-tamper-union", "Gender Tamper", True,
        (1,), "male", "valid",
    )
    with psycopg.connect(production_environment["admin"]) as admin:
        active_before = admin.execute(
            "select active_generation_id from platform_control.directory_state "
            "where singleton"
        ).fetchone()[0]
    generation_id = _stage_and_promote_generation(
        production_environment,
        _codec(tmp_path),
        (member,),
        finalize=False,
        promote=False,
    )
    with psycopg.connect(production_environment["admin"]) as admin:
        admin.execute(
            "update platform_control.directory_members set gender='female' "
            "where generation_id=%s",
            (generation_id,),
        )

    worker_url = production_environment["urls"]["platform_directory_worker"]
    with psycopg.connect(worker_url) as worker:
        with pytest.raises(psycopg.errors.CheckViolation):
            worker.execute(
                "select platform_control.finalize_directory_staging_generation(%s)",
                (generation_id,),
            )
    with psycopg.connect(production_environment["admin"]) as admin:
        assert admin.execute(
            "select generation.status,generation.content_sha256,"
            "state.active_generation_id from platform_control.directory_generations "
            "generation cross join platform_control.directory_state state "
            "where generation.generation_id=%s",
            (generation_id,),
        ).fetchone() == ("staging", None, active_before)


@pytest.mark.postgres
def test_directory_pair_rejects_mixed_key_versions_without_partial_row(
    production_environment,
    tmp_path: Path,
) -> None:
    worker_url = production_environment["urls"]["platform_directory_worker"]
    codec = _codec(tmp_path)
    corporate = codec.seal(
        IdentityResolver.CORPORATE_SUBJECT_KIND,
        _provider_value("test-corp", "mixed-key-member"),
    )
    union = codec.seal(IdentityResolver.UNION_SUBJECT_KIND, "mixed-key-union")
    generation_id = uuid4()
    old_union_hmac = dict(codec.lookup_candidates(
        IdentityResolver.UNION_SUBJECT_KIND, "mixed-key-union"
    ))[1]
    with psycopg.connect(worker_url) as connection:
        connection.execute(
            "select platform_control.create_directory_staging_generation_v28("
            "%s,%s,'scheduled',1,1,1,1,%s,%s)",
            (generation_id, uuid4(), DIRECTORY_SOURCE_SCHEMA_VERSION, "0" * 64),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.stage_directory_member_v28("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    generation_id, uuid4(),
                    corporate.lookup_hmac, corporate.lookup_key_version,
                    corporate.ciphertext, corporate.encryption_key_version,
                    old_union_hmac, 1, union.ciphertext,
                    union.encryption_key_version, "Mixed Key", "active", None,
                ),
            )
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.directory_members "
            "where generation_id=%s", (generation_id,)
        ).fetchone() == (0,)


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("declared_members", "content_sha256"),
    ((1, "e" * 64), (0, None)),
    ids=("count-mismatch", "missing-checksum"),
)
def test_directory_promotion_rejects_incomplete_generation_without_state_change(
    production_environment,
    declared_members: int,
    content_sha256: str | None,
) -> None:
    worker_url = production_environment["urls"]["platform_directory_worker"]
    generation_id = uuid4()
    with psycopg.connect(production_environment["admin"]) as connection:
        active_before = connection.execute(
            "select active_generation_id from platform_control.directory_state"
        ).fetchone()
    with psycopg.connect(worker_url) as connection:
        connection.execute(
            "select platform_control.create_directory_staging_generation_v28("
            "%s,%s,'scheduled',%s,1,0,1,%s,%s)",
            (generation_id, uuid4(), declared_members,
             DIRECTORY_SOURCE_SCHEMA_VERSION, "0" * 64),
        )
        if content_sha256 is not None:
            with psycopg.connect(production_environment["admin"]) as admin:
                admin.execute(
                    "update platform_control.directory_generations "
                    "set content_sha256=%s where generation_id=%s",
                    (content_sha256, generation_id),
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    "select platform_control.promote_verified_directory_generation(%s)",
                    (generation_id,),
                )
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select active_generation_id from platform_control.directory_state"
        ).fetchone() == active_before


@pytest.mark.postgres
def test_directory_worker_cannot_mutate_identity_or_bind_directory_rows(
    production_environment,
) -> None:
    worker_url = production_environment["urls"]["platform_directory_worker"]
    statements = (
        "insert into platform_control.provider_identities "
        "(provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
        "lookup_key_version,encrypted_provider_id,encryption_key_version) "
        "values (gen_random_uuid(),gen_random_uuid(),'employee',decode(repeat('00',32),'hex'),1,'x',1)",
        "update platform_control.provider_identities set verified_at=now()",
        "delete from platform_control.provider_identities",
        "update platform_control.internal_users set role='platform_owner'",
        "update platform_control.internal_users set status='inactive'",
        "insert into platform_control.directory_members "
        "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
        "lookup_key_version,encrypted_provider_id,encryption_key_version,"
        "display_name,status) values (gen_random_uuid(),gen_random_uuid(),"
        "gen_random_uuid(),'employee',decode(repeat('00',32),'hex'),1,'x',1,'x','active')",
        "update platform_control.directory_members set internal_user_id=gen_random_uuid()",
        "delete from platform_control.directory_members",
        "update platform_control.directory_state set active_generation_id=null",
        "insert into platform_control.directory_state (singleton) values (false)",
        "delete from platform_control.directory_state",
        "update platform_control.directory_generations set status='complete'",
    )
    for statement in statements:
        with psycopg.connect(worker_url) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)


@pytest.mark.postgres
def test_pair_and_worker_functions_have_exact_environment_grants(control_database) -> None:
    pair_name = "resolve_verified_dingtalk_member"
    legacy_pair_name = "resolve_verified_dingtalk_member_v12"
    legacy_v13_name = "resolve_verified_dingtalk_member_v13"
    stage_name = "stage_verified_directory_member"
    lock_name = "lock_dingtalk_identity_directory"
    promote_name = "promote_verified_directory_generation"
    for name, environment in control_database["environments"].items():
        app, worker = environment["roles"][1], environment["roles"][2]
        other = control_database["environments"][
            "preview" if name == "production" else "production"
        ]["roles"]
        with psycopg.connect(environment["admin"]) as connection:
            rows = connection.execute(
                "select proname,oidvectortypes(proargtypes),"
                "has_function_privilege(%s,oid,'execute'),"
                "has_function_privilege(%s,oid,'execute'),"
                "has_function_privilege(%s,oid,'execute'),"
                "has_function_privilege(%s,oid,'execute'),"
                "has_function_privilege('public',oid,'execute'),prosecdef,proconfig "
                "from pg_proc where pronamespace='platform_control'::regnamespace "
                "and proname=any(%s) order by proname",
                (
                    app, worker, other[1], other[2],
                    [
                        pair_name, legacy_pair_name, stage_name,
                        legacy_v13_name, lock_name, promote_name,
                    ],
                ),
            ).fetchall()
        assert rows == [
            (
                lock_name, "", True, True, False, False, False, True,
                ["search_path=pg_catalog, platform_control"],
            ),
            (
                promote_name, "uuid", False, True, False, False, False, True,
                ["search_path=pg_catalog, platform_control"],
            ),
            (
                pair_name,
                "uuid, text, uuid, bytea, integer, bytea, integer, uuid, "
                "bytea, integer, bytea, integer",
                True, False, False, False, False, True,
                ["search_path=pg_catalog, platform_control"],
            ),
            (
                legacy_pair_name,
                "uuid, text, uuid, bytea, integer, bytea, integer, integer[], "
                "bytea[], uuid, bytea, integer, bytea, integer, integer[], bytea[]",
                False, False, False, False, False, True,
                ["search_path=pg_catalog, platform_control"],
            ),
            (
                legacy_v13_name,
                "uuid, text, uuid, bytea, integer, bytea, integer, integer[], "
                "bytea[], uuid, bytea, integer, bytea, integer, integer[], bytea[]",
                False, False, False, False, False, True,
                ["search_path=pg_catalog, platform_control"],
            ),
            (
                stage_name,
                "uuid, uuid, text, bytea, integer, bytea, integer, bytea, "
                "integer, text, text",
                False, False, False, False, False, True,
                ["search_path=pg_catalog, platform_control"],
            ),
        ]


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_identical_generation_promotion_preserves_bound_session_then_departure_denies(
    production_environment,
    tmp_path: Path,
) -> None:
    member = DingTalkMember(
        "continuity-user", "continuity-union", "Continuity", True, (1,)
    )
    resolver = _resolver(production_environment, tmp_path, member)
    user_id = await resolver.resolve_active_member(
        DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
        DirectoryFreshness.FRESH,
    )
    token_hash = b"t" * 32
    csrf_hash = b"c" * 32
    session_id = uuid4()
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.web_sessions("
            "session_id,internal_user_id,token_hash,token_hash_key_version,"
            "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) "
            "values (%s,%s,%s,1,%s,1,now()+interval '1 hour',now()+interval '2 hours')",
            (session_id, user_id, token_hash, csrf_hash),
        )

    second = _stage_and_promote_generation(
        production_environment, resolver.identity_codec, (member,)
    )
    with psycopg.connect(
        production_environment["urls"]["platform_control_app"]
    ) as connection:
        authenticated = connection.execute(
            "select session_id from platform_control.authenticate_web_session_v22(%s,1,28800)",
            (token_hash,),
        ).fetchone()
    assert authenticated == (session_id,)
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select internal_user_id from platform_control.directory_members "
            "where generation_id=%s", (second,)
        ).fetchone() == (user_id,)
        assert connection.execute(
            "select last_confirmed_generation_id from platform_control.internal_users "
            "where internal_user_id=%s", (user_id,)
        ).fetchone() == (second,)

    _stage_and_promote_generation(
        production_environment, resolver.identity_codec, ()
    )
    with psycopg.connect(
        production_environment["urls"]["platform_control_app"]
    ) as connection:
        assert connection.execute(
            "select session_id from platform_control.authenticate_web_session_v22(%s,1,28800)",
            (token_hash,),
        ).fetchone() is None


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize(
    "broken_mapping",
    ("one_sided", "cross_user", "inactive", "locally_invalidated"),
)
async def test_promotion_never_inherits_unverified_or_invalid_binding(
    production_environment,
    tmp_path: Path,
    broken_mapping: str,
) -> None:
    member = DingTalkMember(
        f"continuity-{broken_mapping}", f"union-{broken_mapping}",
        "Continuity Boundary", True, (1,),
    )
    resolver = _resolver(production_environment, tmp_path, member)
    user_id = await resolver.resolve_active_member(
        DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
        DirectoryFreshness.FRESH,
    )
    union = resolver.identity_codec.seal("employee_union", member.unionid)
    with psycopg.connect(production_environment["admin"]) as connection:
        if broken_mapping == "one_sided":
            connection.execute(
                "delete from platform_control.provider_identities where "
                "subject_kind='employee_union' and lookup_key_version=%s "
                "and lookup_hmac=%s",
                (union.lookup_key_version, union.lookup_hmac),
            )
        elif broken_mapping == "cross_user":
            other_user = uuid4()
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) values (%s,'Other','active')",
                (other_user,),
            )
            connection.execute(
                "update platform_control.provider_identities set internal_user_id=%s "
                "where subject_kind='employee_union' and lookup_key_version=%s "
                "and lookup_hmac=%s",
                (other_user, union.lookup_key_version, union.lookup_hmac),
            )
        elif broken_mapping == "inactive":
            connection.execute(
                "update platform_control.internal_users set status='inactive' "
                "where internal_user_id=%s", (user_id,),
            )
        else:
            connection.execute(
                "update platform_control.internal_users set locally_invalidated_at=now() "
                "where internal_user_id=%s", (user_id,),
            )

    candidate = _stage_and_promote_generation(
        production_environment, resolver.identity_codec, (member,)
    )
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select internal_user_id from platform_control.directory_members "
            "where generation_id=%s", (candidate,),
        ).fetchone() == (None,)
        assert connection.execute(
            "select last_confirmed_generation_id=%s from "
            "platform_control.internal_users where internal_user_id=%s",
            (candidate, user_id),
        ).fetchone() == (False,)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize("mutation", ("display_name", "ciphertext", "membership"))
async def test_snapshot_mutation_rejects_promotion_and_rolls_back_binding(
    production_environment,
    tmp_path: Path,
    mutation: str,
) -> None:
    member = DingTalkMember(
        f"tamper-{mutation}", f"tamper-union-{mutation}",
        "Tamper Boundary", True, (1,),
    )
    resolver = _resolver(production_environment, tmp_path, member)
    user_id = await resolver.resolve_active_member(
        DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
        DirectoryFreshness.FRESH,
    )
    with psycopg.connect(production_environment["admin"]) as connection:
        old_active = connection.execute(
            "select active_generation_id from platform_control.directory_state "
            "where singleton"
        ).fetchone()[0]
    candidate = _stage_and_promote_generation(
        production_environment, resolver.identity_codec, (member,), promote=False
    )
    with psycopg.connect(production_environment["admin"]) as connection:
        if mutation == "display_name":
            connection.execute(
                "update platform_control.directory_members set display_name='Mutated' "
                "where generation_id=%s", (candidate,),
            )
        elif mutation == "ciphertext":
            connection.execute(
                "update platform_control.directory_members "
                "set encrypted_provider_id=decode(repeat('ab',29),'hex') "
                "where generation_id=%s", (candidate,),
            )
        else:
            connection.execute(
                "delete from platform_control.member_departments "
                "where generation_id=%s", (candidate,),
            )

    worker_url = production_environment["urls"]["platform_directory_worker"]
    with psycopg.connect(worker_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.promote_verified_directory_generation(%s)",
                (candidate,),
            )
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select active_generation_id from platform_control.directory_state "
            "where singleton"
        ).fetchone() == (old_active,)
        assert connection.execute(
            "select generation.status,member.internal_user_id from "
            "platform_control.directory_generations generation join "
            "platform_control.directory_members member using (generation_id) "
            "where generation_id=%s", (candidate,),
        ).fetchone() == ("staging", None)
        assert connection.execute(
            "select last_confirmed_generation_id from platform_control.internal_users "
            "where internal_user_id=%s", (user_id,),
        ).fetchone() == (old_active,)


@pytest.mark.postgres
def test_app_cannot_select_any_directory_generation_table(control_database) -> None:
    for environment in control_database["environments"].values():
        app_url = environment["urls"][environment["roles"][1]]
        with psycopg.connect(app_url) as connection:
            for table in (
                "directory_generations", "directory_state", "directory_members",
                "directory_departments", "department_closure", "member_departments",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute(
                            f"select * from platform_control.{table} limit 1"
                        ).fetchall()


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


@pytest.mark.postgres
def test_account_department_projection_is_active_member_only_and_app_role_only(
    production_environment,
) -> None:
    active_user_id = uuid4()
    no_membership_user_id = uuid4()
    inactive_user_id = uuid4()
    historical_user_id = uuid4()
    historical_generation_id = uuid4()
    active_generation_id = uuid4()

    def insert_generation(connection, generation_id, members) -> None:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',%s,%s,%s,now())",
            (generation_id, len(members), sum(len(values[1]) for values in members), "d" * 64),
        )
        for index, (user_id, departments, member_status) in enumerate(members):
            member_key = uuid4()
            connection.execute(
                "insert into platform_control.directory_members "
                "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
                "lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status) "
                "values (%s,%s,%s,'dingtalk_corporate',%s,1,%s,1,'Member',%s)",
                (generation_id, member_key, user_id, bytes([index + 1]) * 32, b"m" * 29, member_status),
            )
            for department_index, display_name in enumerate(departments):
                department_key = uuid4()
                connection.execute(
                    "insert into platform_control.directory_departments "
                    "(generation_id,department_key,lookup_hmac,lookup_key_version,"
                    "encrypted_provider_id,encryption_key_version,display_name) "
                    "values (%s,%s,%s,1,%s,1,%s)",
                    (
                        generation_id, department_key,
                        bytes([index * 10 + department_index + 1]) * 32,
                        b"d" * 29, display_name,
                    ),
                )
                connection.execute(
                    "insert into platform_control.member_departments "
                    "(generation_id,member_key,department_key) values (%s,%s,%s)",
                    (generation_id, member_key, department_key),
                )

    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Active','active'),(%s,'No membership','active'),"
            "(%s,'Inactive','active'),(%s,'Historical','active')",
            (active_user_id, no_membership_user_id, inactive_user_id, historical_user_id),
        )
        insert_generation(
            connection,
            historical_generation_id,
            ((historical_user_id, ("Legacy department",), "active"),),
        )
        insert_generation(
            connection,
            active_generation_id,
            (
                (active_user_id, (" 产品中心 ", "项目管理部", "产品中心"), "active"),
                (no_membership_user_id, (), "active"),
                (inactive_user_id, ("Inactive department",), "inactive"),
            ),
        )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s, "
            "last_complete_at=now(), updated_at=now() where singleton",
            (active_generation_id,),
        )

        signature = "platform_control.read_account_departments_v27(uuid)"
        assert connection.execute(
            "select has_function_privilege('public',%s,'execute'), "
            "has_function_privilege('platform_control_app',%s,'execute'), "
            "has_function_privilege('platform_control_app_preview',%s,'execute')",
            (signature, signature, signature),
        ).fetchone() == (False, True, False)
        for role in (
            "platform_control_migrator", "platform_directory_worker",
            "platform_stream_ingest", "platform_audit_append",
            "platform_control_maintenance", "platform_control_migrator_preview",
            "platform_directory_worker_preview", "platform_stream_ingest_preview",
            "platform_audit_append_preview", "platform_control_maintenance_preview",
        ):
            assert connection.execute(
                "select has_function_privilege(%s,%s,'execute')", (role, signature)
            ).fetchone() == (False,)
        for table in (
            "directory_generations", "directory_state", "directory_members",
            "directory_departments", "member_departments",
        ):
            assert connection.execute(
                "select has_table_privilege('platform_control_app',%s,'select')",
                (f"platform_control.{table}",),
            ).fetchone() == (False,)

    with psycopg.connect(
        production_environment["urls"]["platform_control_app"], row_factory=dict_row
    ) as connection:
        row = connection.execute(
            "select platform_control.read_account_departments_v27(%s) as departments",
            (active_user_id,),
        ).fetchone()
        assert row["departments"] == ["产品中心", "项目管理部"]
        assert all(
            forbidden not in department.lower()
            for department in row["departments"]
            for forbidden in ("provider", "mobile", "contact")
        )
        for user_id in (no_membership_user_id, inactive_user_id, historical_user_id):
            row = connection.execute(
                "select platform_control.read_account_departments_v27(%s) as departments",
                (user_id,),
            ).fetchone()
            assert row["departments"] == []
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="account department internal user invalid",
        ):
            connection.execute(
                "select platform_control.read_account_departments_v27(null)"
            )

    invalid_department_id = uuid4()
    with psycopg.connect(production_environment["admin"]) as connection:
        member_key = connection.execute(
            "select member_key from platform_control.directory_members "
            "where generation_id=%s and internal_user_id=%s",
            (active_generation_id, active_user_id),
        ).fetchone()[0]
        connection.execute(
            "insert into platform_control.directory_departments "
            "(generation_id,department_key,lookup_hmac,lookup_key_version,"
            "encrypted_provider_id,encryption_key_version,display_name) "
            "values (%s,%s,%s,1,%s,1,'   ')",
            (active_generation_id, invalid_department_id, b"z" * 32, b"z" * 29),
        )
        connection.execute(
            "insert into platform_control.member_departments "
            "(generation_id,member_key,department_key) values (%s,%s,%s)",
            (active_generation_id, member_key, invalid_department_id),
        )
    try:
        with psycopg.connect(
            production_environment["urls"]["platform_control_app"]
        ) as connection:
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="account department display invalid",
            ):
                connection.execute(
                    "select platform_control.read_account_departments_v27(%s)",
                    (active_user_id,),
                )
    finally:
        with psycopg.connect(production_environment["admin"]) as connection:
            connection.execute(
                "delete from platform_control.member_departments "
                "where generation_id=%s and department_key=%s",
                (active_generation_id, invalid_department_id),
            )
            connection.execute(
                "delete from platform_control.directory_departments "
                "where generation_id=%s and department_key=%s",
                (active_generation_id, invalid_department_id),
            )


@pytest.mark.postgres
def test_account_gender_projection_uses_only_one_active_current_member(
    production_environment,
) -> None:
    male_user_id = uuid4()
    female_user_id = uuid4()
    null_user_id = uuid4()
    inactive_user_id = uuid4()
    historical_user_id = uuid4()
    multiple_user_id = uuid4()
    invalid_user_id = uuid4()
    historical_generation_id = uuid4()
    active_generation_id = uuid4()

    def insert_generation(connection, generation_id, members) -> None:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',%s,0,%s,now())",
            (generation_id, len(members), "d" * 64),
        )
        for index, (user_id, status, gender) in enumerate(members):
            connection.execute(
                "insert into platform_control.directory_members "
                "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
                "lookup_key_version,encrypted_provider_id,encryption_key_version,"
                "display_name,status,gender) values "
                "(%s,%s,%s,'dingtalk_corporate',%s,1,%s,1,'Member',%s,%s)",
                (
                    generation_id, uuid4(), user_id,
                    bytes([index + 1]) * 32, b"m" * 29, status, gender,
                ),
            )

    with psycopg.connect(production_environment["admin"]) as connection:
        for user_id in (
            male_user_id, female_user_id, null_user_id, inactive_user_id,
            historical_user_id, multiple_user_id, invalid_user_id,
        ):
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status) values (%s,'Member','active')",
                (user_id,),
            )
        insert_generation(
            connection,
            historical_generation_id,
            (
                (female_user_id, "active", "male"),
                (historical_user_id, "active", "female"),
            ),
        )
        insert_generation(
            connection,
            active_generation_id,
            (
                (male_user_id, "active", "male"),
                (female_user_id, "active", "female"),
                (null_user_id, "active", None),
                (inactive_user_id, "inactive", "female"),
                (multiple_user_id, "active", "male"),
                (multiple_user_id, "active", "female"),
                (invalid_user_id, "active", None),
            ),
        )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s, "
            "last_complete_at=now(),updated_at=now() where singleton",
            (active_generation_id,),
        )

    def read_gender(user_id):
        with psycopg.connect(
            production_environment["urls"]["platform_control_app"],
            row_factory=dict_row,
        ) as connection:
            return connection.execute(
                "select platform_control.read_account_gender_v29(%s) as gender",
                (user_id,),
            ).fetchone()["gender"]

    def assert_gender(actual, expected) -> None:
        if actual != expected:
            pytest.fail("account gender projection mismatch")

    assert_gender(read_gender(male_user_id), "male")
    assert_gender(read_gender(female_user_id), "female")
    assert_gender(read_gender(null_user_id), None)
    assert_gender(read_gender(uuid4()), None)
    assert_gender(read_gender(inactive_user_id), None)
    assert_gender(read_gender(historical_user_id), None)

    for rejected_user_id in (None, multiple_user_id):
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="account gender projection invalid",
        ):
            read_gender(rejected_user_id)

    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "alter table platform_control.directory_members "
            "drop constraint directory_member_gender_v28"
        )
        connection.execute(
            "update platform_control.directory_members set gender='invalid-stored-value' "
            "where generation_id=%s and internal_user_id=%s",
            (active_generation_id, invalid_user_id),
        )
    try:
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="account gender projection invalid",
        ) as error:
            read_gender(invalid_user_id)
        if "invalid-stored-value" in str(error.value):
            pytest.fail("account gender projection error exposed stored data")
    finally:
        with psycopg.connect(production_environment["admin"]) as connection:
            connection.execute(
                "update platform_control.directory_members set gender=null "
                "where generation_id=%s and internal_user_id=%s",
                (active_generation_id, invalid_user_id),
            )
            connection.execute(
                "alter table platform_control.directory_members "
                "add constraint directory_member_gender_v28 "
                "check (gender is null or gender in ('male','female'))"
            )


@pytest.mark.postgres
def test_account_gender_projection_has_exact_environment_app_grant(
    control_database,
) -> None:
    signature = "platform_control.read_account_gender_v29(uuid)"
    all_roles = tuple(
        role
        for environment in control_database["environments"].values()
        for role in environment["roles"]
    )

    for environment in control_database["environments"].values():
        matched_app = environment["roles"][1]
        with psycopg.connect(environment["admin"]) as connection:
            metadata = connection.execute(
                "select proc.prosecdef,proc.proconfig from pg_proc proc "
                "where proc.oid=to_regprocedure(%s)",
                (signature,),
            ).fetchone()
            assert metadata == (True, ["search_path=pg_catalog, platform_control"])
            assert connection.execute(
                "select has_function_privilege('public',%s,'execute')",
                (signature,),
            ).fetchone() == (False,)
            for role in all_roles:
                assert connection.execute(
                    "select has_function_privilege(%s,%s,'execute')",
                    (role, signature),
                ).fetchone() == (role == matched_app,)
            assert connection.execute(
                "select has_table_privilege(%s,"
                "'platform_control.directory_members','select')",
                (matched_app,),
            ).fetchone() == (False,)

        with psycopg.connect(environment["urls"][matched_app]) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "select gender from platform_control.directory_members limit 1"
                )
