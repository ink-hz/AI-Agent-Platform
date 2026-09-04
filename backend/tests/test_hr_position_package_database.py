from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest
from test_control_plane_migration import control_database  # noqa: F401, F811

CREATE_VERSION = (
    "select (platform_hr.create_position_draft_version_v76("
    "%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)).*"
)
CONFIRM_PACKAGE = (
    "select * from platform_hr.confirm_position_package_v76("
    "%s,%s,%s,%s,%s)"
)
MODULES = (
    '{"mission":{"text":"负责新产品结构落地"},'
    '"jd":{"text":"负责精密结构设计与量产。"},'
    '"jr":{"text":"具备五年以上结构设计经验。"}}'
)


def _seed_package(admin: psycopg.Connection, *, title: str = "原始请求标题"):
    owner_id = uuid4()
    conversation_id = uuid4()
    turn_id = uuid4()
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    draft_id = uuid4()
    admin.execute(
        "insert into platform_control.internal_users "
        "(internal_user_id,display_name,status) values (%s,'Package Owner','active')",
        (owner_id,),
    )
    admin.execute(
        "insert into platform_control.conversations("
        "conversation_id,owner_internal_user_id,started_by_client_request_id,"
        "mode,direct_agent_id,title,status) values "
        "(%s,%s,%s,'direct_agent','hr-bot','岗位草拟','active')",
        (conversation_id, owner_id, uuid4()),
    )
    admin.execute(
        "insert into platform_control.conversation_messages("
        "message_id,conversation_id,seq,role,content_ciphertext,"
        "encryption_key_version,turn_id,delivery_status,completed_at) values "
        "(%s,%s,1,'user',%s,1,%s,'completed',now()),"
        "(%s,%s,2,'assistant',%s,1,%s,'completed',now())",
        (
            user_message_id, conversation_id, b"u" * 29, turn_id,
            assistant_message_id, conversation_id, b"a" * 29, turn_id,
        ),
    )
    admin.execute(
        "insert into platform_control.conversation_turns("
        "turn_id,conversation_id,user_message_id,assistant_message_id,"
        "client_request_id,status) values (%s,%s,%s,%s,%s,'completed')",
        (turn_id, conversation_id, user_message_id, assistant_message_id, uuid4()),
    )
    admin.execute(
        "insert into platform_hr.position_drafts("
        "draft_id,owner_internal_user_id,client_request_id,source_kind,"
        "source_key,source_conversation_id,title,proposal,evidence,"
        "discovery_rule_version) values ("
        "%s,%s,%s,'new_conversation',%s,%s,%s,'{}','{}','interactive-v1')",
        (
            draft_id, owner_id, uuid4(), f"conversation:{conversation_id}",
            conversation_id, title,
        ),
    )
    admin.commit()
    return owner_id, draft_id, conversation_id, turn_id, assistant_message_id


def _create_values(seed, *, request_id: UUID, draft_version_id: UUID | None = None):
    owner_id, draft_id, conversation_id, turn_id, assistant_message_id = seed
    return (
        draft_version_id or uuid4(), owner_id, draft_id, request_id,
        "最终高级结构工程师", MODULES, conversation_id, turn_id,
        assistant_message_id, "hr-bot", "gpt-5",
    )


def _add_completed_turn(
    admin: psycopg.Connection, conversation_id: UUID, seq: int
):
    turn_id, user_message_id, assistant_message_id = uuid4(), uuid4(), uuid4()
    admin.execute(
        "insert into platform_control.conversation_messages("
        "message_id,conversation_id,seq,role,content_ciphertext,"
        "encryption_key_version,turn_id,delivery_status,completed_at) values "
        "(%s,%s,%s,'user',%s,1,%s,'completed',now()),"
        "(%s,%s,%s,'assistant',%s,1,%s,'completed',now())",
        (
            user_message_id, conversation_id, seq, b"u" * 29, turn_id,
            assistant_message_id, conversation_id, seq + 1, b"a" * 29,
            turn_id,
        ),
    )
    admin.execute(
        "insert into platform_control.conversation_turns("
        "turn_id,conversation_id,user_message_id,assistant_message_id,"
        "client_request_id,status) values (%s,%s,%s,%s,%s,'completed')",
        (turn_id, conversation_id, user_message_id, assistant_message_id, uuid4()),
    )
    return turn_id, assistant_message_id


@pytest.mark.postgres
def test_draft_version_creation_is_deterministic_scoped_and_immutable(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        seed = _seed_package(admin)
    values = _create_values(seed, request_id=uuid4())
    with psycopg.connect(environment["urls"]["platform_control_app"]) as app:
        first = app.execute(CREATE_VERSION, values).fetchone()
        replay = app.execute(CREATE_VERSION, values).fetchone()
        app.commit()

    assert replay == first
    assert first[4] == 1
    assert first[5] == "最终高级结构工程师"
    with psycopg.connect(environment["admin"]) as admin:
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                "update platform_hr.position_draft_versions set title='篡改' "
                "where draft_version_id=%s",
                (first[0],),
            )
        admin.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                "delete from platform_hr.position_draft_versions "
                "where draft_version_id=%s",
                (first[0],),
            )


@pytest.mark.postgres
def test_draft_version_numbers_serialize_across_distinct_source_messages(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        seed = _seed_package(admin)
        sources = tuple(
            _add_completed_turn(admin, seed[2], seq) for seq in range(3, 19, 2)
        )
        admin.commit()
    values = tuple(
        (
            uuid4(), seed[0], seed[1], uuid4(), f"最终岗位 {index}", MODULES,
            seed[2], turn_id, message_id, "hr-bot", "gpt-5",
        )
        for index, (turn_id, message_id) in enumerate(sources, start=1)
    )
    app_url = environment["urls"]["platform_control_app"]

    def create(row):
        with psycopg.connect(app_url) as app:
            return app.execute(CREATE_VERSION, row).fetchone()

    with ThreadPoolExecutor(max_workers=len(values)) as pool:
        versions = tuple(pool.map(create, values))

    assert {version[4] for version in versions} == set(range(1, len(values) + 1))


@pytest.mark.postgres
def test_draft_version_rejects_non_exact_modules_and_cross_owner_scope(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        seed = _seed_package(admin)
        other_owner = uuid4()
        admin.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values "
            "(%s,'Other Package Owner','active')",
            (other_owner,),
        )
        admin.commit()
    values = list(_create_values(seed, request_id=uuid4()))
    values[5] = '{"mission":{"text":"M"},"jd":{"text":"JD"}}'
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as app:
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute(CREATE_VERSION, values)
        app.rollback()
        values = list(_create_values(seed, request_id=uuid4()))
        values[1] = other_owner
        with pytest.raises(psycopg.errors.NoDataFound):
            app.execute(CREATE_VERSION, values)


@pytest.mark.postgres
def test_confirmation_atomically_uses_selected_package_and_replays(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        seed = _seed_package(admin, title="用户最初只说想招聘")
    create_request = uuid4()
    confirm_request = uuid4()
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as app:
        version = app.execute(
            CREATE_VERSION, _create_values(seed, request_id=create_request)
        ).fetchone()
        result = app.execute(
            CONFIRM_PACKAGE, (seed[0], seed[1], version[0], confirm_request, 1)
        ).fetchone()
        replay = app.execute(
            CONFIRM_PACKAGE, (seed[0], seed[1], version[0], confirm_request, 1)
        ).fetchone()
        app.commit()

    assert replay == result
    position_id, context_id, conversation_id = result
    assert conversation_id == seed[2]
    with psycopg.connect(environment["admin"]) as admin:
        position = admin.execute(
            "select source_kind,title,internal_status,current_context_version_id "
            "from platform_hr.positions where position_id=%s",
            (position_id,),
        ).fetchone()
        context = admin.execute(
            "select version_number,state,modules,source_conversation_id,"
            "source_turn_id,agent_id,model_version from "
            "platform_hr.position_context_versions where context_version_id=%s",
            (context_id,),
        ).fetchone()
        binding = admin.execute(
            "select position_id,binding_kind from platform_hr.position_conversations "
            "where conversation_id=%s",
            (conversation_id,),
        ).fetchone()
        draft = admin.execute(
            "select state,resolved_position_id,row_version from "
            "platform_hr.position_drafts where draft_id=%s",
            (seed[1],),
        ).fetchone()
    assert position == ("manual", "最终高级结构工程师", "active", context_id)
    assert context == (
        1, "confirmed", {
            "mission": {"text": "负责新产品结构落地"},
            "jd": {"text": "负责精密结构设计与量产。"},
            "jr": {"text": "具备五年以上结构设计经验。"},
        }, seed[2], seed[3], "hr-bot", "gpt-5",
    )
    assert binding == (position_id, "draft_confirmed")
    assert draft == ("confirmed", position_id, 2)

    with psycopg.connect(app_url) as app:
        with pytest.raises(psycopg.errors.UniqueViolation):
            app.execute(
                CONFIRM_PACKAGE,
                (seed[0], seed[1], uuid4(), confirm_request, 1),
            )


@pytest.mark.postgres
def test_confirmation_rolls_back_every_write_when_binding_conflicts(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as admin:
        seed = _seed_package(admin)
        existing_position = uuid4()
        admin.execute(
            "insert into platform_hr.positions("
            "position_id,owner_internal_user_id,client_request_id,source_kind,title) "
            "values (%s,%s,%s,'manual','已有岗位')",
            (existing_position, seed[0], uuid4()),
        )
        admin.execute(
            "insert into platform_hr.position_conversations("
            "conversation_id,owner_internal_user_id,position_id,"
            "client_request_id,binding_kind) values "
            "(%s,%s,%s,%s,'historical_exact')",
            (seed[2], seed[0], existing_position, uuid4()),
        )
        admin.commit()
    app_url = environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as app:
        version = app.execute(
            CREATE_VERSION, _create_values(seed, request_id=uuid4())
        ).fetchone()
        app.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            app.execute(
                CONFIRM_PACKAGE,
                (seed[0], seed[1], version[0], uuid4(), 1),
            )
        app.rollback()
    with psycopg.connect(environment["admin"]) as admin:
        assert admin.execute(
            "select state,resolved_position_id from platform_hr.position_drafts "
            "where draft_id=%s", (seed[1],),
        ).fetchone() == ("proposed", None)
        assert admin.execute(
            "select count(*) from platform_hr.positions where "
            "owner_internal_user_id=%s", (seed[0],),
        ).fetchone()[0] == 1
        assert admin.execute(
            "select count(*) from platform_hr.position_context_versions where "
            "owner_internal_user_id=%s", (seed[0],),
        ).fetchone()[0] == 0
