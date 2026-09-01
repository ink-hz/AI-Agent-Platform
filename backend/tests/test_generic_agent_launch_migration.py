from __future__ import annotations

import hashlib
from uuid import uuid4

import psycopg
import pytest
from test_agent_brain_migration import _insert_grant, _seed_active_directory
from test_control_plane_migration import (  # noqa: F401 - fixture registration
    MIGRATIONS,
    control_database,
)
from test_partner_operator_migration import _seed_owner

MIGRATION = MIGRATIONS / "059_generic_agent_launch_bindings.sql"
HISTORICAL_MIGRATION_HASHES = {
    "052_agent_launch_identity_binding.sql": (
        "1631e5ac1dbf359c5ed1fc977ede17cd0a241d7d5551a593a24e995b23ea29ed"
    ),
    "053_office_recipient_directory.sql": (
        "b0beb171e033dfdb0edc9fd023a62c69c52d8bc2e9766a747eb303f4ebc9deaa"
    ),
    "054_office_recipient_directory_department_order.sql": (
        "70fbd52845c54312b491f84955ad98f5a32f38e34533d367429290005fc90a1a"
    ),
    "055_agent_access_subjects.sql": (
        "84cead09785df7f44f651c443a4196055871e899a90739c01849fb674293bd88"
    ),
    "056_partner_operator_identity.sql": (
        "d1d89d5ca37d6c65c58e0362766173805d0262f9c9a5e02d790bf6ef03a421fc"
    ),
    "057_partner_management_rejection.sql": (
        "f076263e166129dae89ea95a25632124549d3a525795a499d78acdd2d48f7f62"
    ),
    "058_partner_authentication.sql": (
        "29fb354918c3627597cc5ae29a856e416a614dd60d166214c96e4b4d68999455"
    ),
}


def _normalized_sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_v52_through_v58_remain_byte_immutable() -> None:
    assert {
        name: hashlib.sha256((MIGRATIONS / name).read_bytes()).hexdigest()
        for name in HISTORICAL_MIGRATION_HASHES
    } == HISTORICAL_MIGRATION_HASHES


def test_generic_launch_migration_is_additive_and_backfills_enterprise_rows() -> None:
    sql = _normalized_sql()

    assert "alter table platform_control.agent_launch_codes add column subject_id" in sql
    assert sql.count("add column subject_type") == 2
    assert "alter table platform_control.agent_identity_bindings add column subject_id" in sql
    for table in ("agent_launch_codes", "agent_identity_bindings"):
        assert (
            f"update platform_control.{table} set subject_id=internal_user_id, "
            "subject_type='enterprise_member';"
        ) in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
    assert "delete from platform_control.agent_launch_codes" not in sql
    assert "delete from platform_control.agent_identity_bindings" not in sql


def test_generic_launch_tables_enforce_exact_enterprise_and_partner_shapes() -> None:
    sql = _normalized_sql().replace(" ", "")
    exact_shape = (
        "(subject_type='enterprise_member'andsource_session_idisnotnull"
        "andinternal_user_idisnotnullandsubject_id=internal_user_id)or"
        "(subject_type='partner_operator'andsource_session_idisnull"
        "andinternal_user_idisnull)"
    )

    assert sql.count(exact_shape) == 2
    assert sql.count("altercolumnsource_session_iddropnotnull") == 2
    assert sql.count("altercolumninternal_user_iddropnotnull") == 2


def test_v57_functions_are_security_definer_and_old_execute_is_retired() -> None:
    sql = _normalized_sql()

    for function in (
        "issue_agent_launch_v57",
        "exchange_agent_launch_v57",
        "validate_agent_identity_binding_v57",
        "revoke_agent_identity_binding_v57",
    ):
        assert f"create function platform_control.{function}" in sql
        assert f"revoke all on function platform_control.{function}" in sql
        assert f"grant execute on function platform_control.{function}" in sql
    assert sql.count("security definer") >= 4
    assert "drop function platform_control.issue_agent_launch_v52" not in sql
    assert "drop function platform_control.exchange_agent_launch_v52" not in sql
    assert "drop function platform_control.validate_agent_identity_binding_v52" not in sql
    assert "revoke all on function platform_control.issue_agent_launch_v52" in sql
    assert "revoke all on function platform_control.exchange_agent_launch_v52" in sql
    assert (
        "revoke all on function platform_control.validate_agent_identity_binding_v52"
        in sql
    )
    assert "if not selected_allowed then" not in sql
    assert sql.count("if selected_allowed is distinct from true then") == 2
    assert "if selected_active is true then" in sql
    assert "selected_active := coalesce(selected_active,false);" in sql
    for forbidden in ("provider_subject text", "provider_token", "cookie", "http://", "https://"):
        assert forbidden not in sql


@pytest.mark.postgres
def test_v57_lazily_projects_enterprise_user_created_after_migration(
    control_database,  # noqa: F811
) -> None:
    environment = control_database["environments"]["production"]
    session_id = uuid4()
    binding_id = uuid4()
    code_hash = hashlib.sha256(uuid4().bytes).digest()
    with psycopg.connect(environment["admin"]) as admin:
        internal_user_id, *_directory = _seed_active_directory(admin)
        _insert_grant(
            admin,
            agent_id="ai-fae-agent",
            target_kind="user",
            actor_id=internal_user_id,
            user_id=internal_user_id,
        )
        admin.execute(
            "insert into platform_control.web_sessions "
            "(session_id,internal_user_id,token_hash,token_hash_key_version,"
            "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) "
            "values (%s,%s,%s,1,%s,1,now()+interval '1 hour',"
            "now()+interval '2 hours')",
            (session_id, internal_user_id, b"t" * 32, b"c" * 32),
        )
        assert admin.execute(
            "select 1 from platform_control.agent_access_subjects "
            "where subject_id=%s",
            (internal_user_id,),
        ).fetchone() is None

    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        expires_at = app.execute(
            "select platform_control.issue_agent_launch_v57("
            "%s,%s,1,%s,'enterprise_member',%s,%s,'ai-fae-agent',%s,60)",
            (
                uuid4(),
                code_hash,
                internal_user_id,
                session_id,
                internal_user_id,
                binding_id,
            ),
        ).fetchone()[0]
        assert expires_at is not None
        exchanged = app.execute(
            "select * from platform_control.exchange_agent_launch_v57(%s,1)",
            (code_hash,),
        ).fetchone()

    assert exchanged == (
        internal_user_id,
        "enterprise_member",
        binding_id,
        "ai-fae-agent",
        internal_user_id,
        "Grant User",
    )
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select subject.subject_type::text,link.internal_user_id "
            "from platform_control.agent_access_subjects subject "
            "join platform_control.enterprise_subject_links link using (subject_id) "
            "where subject.subject_id=%s",
            (internal_user_id,),
        ).fetchone() == ("enterprise_member", internal_user_id)


@pytest.mark.postgres
@pytest.mark.parametrize("revoked_layer", ["session", "directory", "grant"])
def test_v57_exchange_consumes_and_rejects_enterprise_code_revoked_after_issue(
    control_database,  # noqa: F811
    revoked_layer: str,
) -> None:
    environment = control_database["environments"]["production"]
    app_url = environment["urls"]["platform_control_app"]
    session_id = uuid4()
    launch_code_id = uuid4()
    binding_id = uuid4()
    code_hash = hashlib.sha256(uuid4().bytes).digest()
    with psycopg.connect(environment["admin"]) as admin:
        internal_user_id, *_directory = _seed_active_directory(admin)
        grant_id = _insert_grant(
            admin,
            agent_id="ai-fae-agent",
            target_kind="user",
            actor_id=internal_user_id,
            user_id=internal_user_id,
        )
        admin.execute(
            "insert into platform_control.web_sessions "
            "(session_id,internal_user_id,token_hash,token_hash_key_version,"
            "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at) "
            "values (%s,%s,%s,1,%s,1,now()+interval '1 hour',"
            "now()+interval '2 hours')",
            (
                session_id,
                internal_user_id,
                hashlib.sha256(uuid4().bytes).digest(),
                hashlib.sha256(uuid4().bytes).digest(),
            ),
        )

    with psycopg.connect(app_url) as app:
        assert app.execute(
            "select platform_control.issue_agent_launch_v57("
            "%s,%s,1,%s,'enterprise_member',%s,%s,'ai-fae-agent',%s,60)",
            (
                launch_code_id,
                code_hash,
                internal_user_id,
                session_id,
                internal_user_id,
                binding_id,
            ),
        ).fetchone()[0] is not None

    with psycopg.connect(environment["admin"]) as admin:
        if revoked_layer == "session":
            admin.execute(
                "update platform_control.web_sessions set revoked_at=now() "
                "where session_id=%s",
                (session_id,),
            )
        elif revoked_layer == "directory":
            admin.execute(
                "update platform_control.internal_users set status='inactive' "
                "where internal_user_id=%s",
                (internal_user_id,),
            )
        else:
            admin.execute(
                "update platform_control.agent_use_grants "
                "set revoked_at=now(),revoked_by=%s "
                "where agent_use_grant_id=%s",
                (internal_user_id, grant_id),
            )

    with psycopg.connect(app_url) as app:
        rejected = app.execute(
            "select * from platform_control.exchange_agent_launch_v57(%s,1)",
            (code_hash,),
        ).fetchone()

    assert rejected is None
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select consumed_at is not null from platform_control.agent_launch_codes "
            "where launch_code_id=%s",
            (launch_code_id,),
        ).fetchone() == (True,)
        assert admin.execute(
            "select 1 from platform_control.agent_identity_bindings "
            "where identity_binding_id=%s",
            (binding_id,),
        ).fetchone() is None
        if revoked_layer == "session":
            admin.execute(
                "update platform_control.web_sessions set revoked_at=null "
                "where session_id=%s",
                (session_id,),
            )
        elif revoked_layer == "directory":
            admin.execute(
                "update platform_control.internal_users set status='active' "
                "where internal_user_id=%s",
                (internal_user_id,),
            )
        else:
            admin.execute(
                "update platform_control.agent_use_grants "
                "set revoked_at=null,revoked_by=null "
                "where agent_use_grant_id=%s",
                (grant_id,),
            )

    with psycopg.connect(app_url) as app:
        assert app.execute(
            "select * from platform_control.exchange_agent_launch_v57(%s,1)",
            (code_hash,),
        ).fetchone() is None


def _seed_partner_launch_subject(connection):
    owner_id = _seed_owner(connection)
    organization_id = uuid4()
    operator_id = uuid4()
    subject_id = uuid4()
    provider_identity_id = uuid4()
    grant_id = uuid4()
    connection.execute(
        "insert into platform_control.partner_organizations "
        "(partner_organization_id,status,name_ciphertext,name_key_version) "
        "values (%s,'active',%s,1)",
        (organization_id, b"o" * 32),
    )
    connection.execute(
        "insert into platform_control.agent_access_subjects "
        "(subject_id,subject_type,status,display_name_ciphertext,"
        "display_name_key_version) values (%s,'partner_operator','active',%s,1)",
        (subject_id, b"s" * 32),
    )
    connection.execute(
        "insert into platform_control.partner_operators "
        "(partner_operator_id,subject_id,partner_organization_id,status) "
        "values (%s,%s,%s,'active')",
        (operator_id, subject_id, organization_id),
    )
    connection.execute(
        "insert into platform_control.partner_provider_identities "
        "(provider_identity_id,partner_operator_id,provider_kind,"
        "provider_subject_lookup_hmac,lookup_key_version,"
        "provider_subject_ciphertext,encryption_key_version,verified_at) "
        "values (%s,%s,'fixture',%s,1,%s,1,now())",
        (
            provider_identity_id,
            operator_id,
            hashlib.sha256(subject_id.bytes).digest(),
            b"ciphertext-with-authentication-tag",
        ),
    )
    connection.execute(
        "insert into platform_control.partner_agent_grants "
        "(grant_id,subject_id,agent_id,created_by_internal_user_id) "
        "values (%s,%s,'ai-fae-agent',%s)",
        (grant_id, subject_id, owner_id),
    )
    return {
        "owner_id": owner_id,
        "organization_id": organization_id,
        "operator_id": operator_id,
        "subject_id": subject_id,
        "provider_identity_id": provider_identity_id,
        "grant_id": grant_id,
    }


def _issue_and_exchange_partner(app_url: str, subject_id):
    code_hash = hashlib.sha256(uuid4().bytes).digest()
    binding_id = uuid4()
    with psycopg.connect(app_url) as app:
        expires_at = app.execute(
            "select platform_control.issue_agent_launch_v57("
            "%s,%s,1,%s,'partner_operator',null,null,'ai-fae-agent',%s,60)",
            (uuid4(), code_hash, subject_id, binding_id),
        ).fetchone()[0]
        assert expires_at is not None
        exchanged = app.execute(
            "select * from platform_control.exchange_agent_launch_v57(%s,1)",
            (code_hash,),
        ).fetchone()
        replay = app.execute(
            "select * from platform_control.exchange_agent_launch_v57(%s,1)",
            (code_hash,),
        ).fetchone()
    assert exchanged[:5] == (
        subject_id,
        "partner_operator",
        binding_id,
        "ai-fae-agent",
        None,
    )
    assert replay is None
    return binding_id


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("layer", "mutation"),
    [
        (
            "organization",
            (
                "update platform_control.partner_organizations set status='suspended' "
                "where partner_organization_id=%(organization_id)s"
            ),
        ),
        (
            "operator",
            (
                "update platform_control.partner_operators set status='suspended' "
                "where partner_operator_id=%(operator_id)s"
            ),
        ),
        (
            "subject",
            (
                "update platform_control.agent_access_subjects set status='suspended' "
                "where subject_id=%(subject_id)s"
            ),
        ),
        (
            "grant",
            (
                "update platform_control.partner_agent_grants set revoked_at=now(),"
                "revoked_by_internal_user_id=%(owner_id)s "
                "where grant_id=%(grant_id)s"
            ),
        ),
        (
            "provider_mapping",
            (
                "update platform_control.partner_provider_identities "
                "set revoked_at=now() "
                "where provider_identity_id=%(provider_identity_id)s"
            ),
        ),
    ],
)
def test_partner_binding_validation_dynamically_revokes_every_access_layer(
    control_database,  # noqa: F811 - imported fixture name
    layer: str,
    mutation: str,
) -> None:
    del layer
    environment = control_database["environments"]["production"]
    app_url = environment["urls"][environment["roles"][1]]
    with psycopg.connect(environment["admin"], autocommit=True) as admin:
        seeded = _seed_partner_launch_subject(admin)
    binding_id = _issue_and_exchange_partner(app_url, seeded["subject_id"])

    with psycopg.connect(app_url) as app:
        active = app.execute(
            "select * from platform_control."
            "validate_agent_identity_binding_v57(%s,'ai-fae-agent')",
            (binding_id,),
        ).fetchone()
    assert active[:5] == (
        seeded["subject_id"],
        "partner_operator",
        binding_id,
        "ai-fae-agent",
        None,
    )
    assert active[-1] is True

    with psycopg.connect(environment["admin"], autocommit=True) as admin:
        admin.execute(mutation, seeded)
    with psycopg.connect(app_url) as app:
        inactive = app.execute(
            "select * from platform_control."
            "validate_agent_identity_binding_v57(%s,'ai-fae-agent')",
            (binding_id,),
        ).fetchone()

    assert inactive[:5] == active[:5]
    assert inactive[-1] is False
    with psycopg.connect(environment["admin"]) as admin:
        revoked_at = admin.execute(
            "select revoked_at from platform_control.agent_identity_bindings "
            "where identity_binding_id=%s",
            (binding_id,),
        ).fetchone()[0]
    assert revoked_at is not None
