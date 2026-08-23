from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from test_control_plane_migration import ROLES, control_database


CONVERSATION_TABLES = (
    "conversations",
    "conversation_messages",
    "conversation_turns",
    "conversation_events",
    "conversation_feedback",
)


@pytest.mark.postgres
def test_conversation_schema_separates_history_from_mission_state(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            columns = {
                table: {
                    row[0]
                    for row in connection.execute(
                        "select column_name from information_schema.columns "
                        "where table_schema='platform_control' and table_name=%s",
                        (table,),
                    )
                }
                for table in CONVERSATION_TABLES
            }

            assert columns["conversations"] >= {
                "conversation_id",
                "owner_internal_user_id",
                "started_by_client_request_id",
                "mode",
                "direct_agent_id",
                "title",
                "status",
                "summary_ciphertext",
                "summary_key_version",
                "summary_through_seq",
                "created_at",
                "updated_at",
                "archived_at",
            }
            assert columns["conversation_messages"] >= {
                "message_id",
                "conversation_id",
                "seq",
                "role",
                "content_ciphertext",
                "encryption_key_version",
                "turn_id",
                "mission_id",
                "delivery_status",
                "created_at",
                "completed_at",
            }
            assert columns["conversation_feedback"] >= {
                "feedback_id",
                "owner_internal_user_id",
                "conversation_id",
                "message_id",
                "turn_id",
                "mission_id",
                "rating",
                "created_at",
            }
            assert not columns["conversation_feedback"] & {
                "content",
                "comment",
                "question",
                "answer",
            }
            assert not columns["conversation_messages"] & {
                "content",
                "prompt",
                "response",
            }
            assert {
                "conversation_id",
                "turn_id",
                "triggering_message_id",
            } <= {
                row[0]
                for row in connection.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema='platform_control' and table_name='missions'"
                )
            }

            constraints = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where connamespace='platform_control'::regnamespace "
                    "and conrelid = any(%s::regclass[])",
                    ([f"platform_control.{table}" for table in CONVERSATION_TABLES],),
                )
            )
            for value in ("'brain'", "'direct_agent'", "'active'", "'archived'"):
                assert value in constraints
            for value in ("'user'", "'assistant'", "'system'"):
                assert value in constraints
            for value in ("'helpful'", "'unhelpful'"):
                assert value in constraints
            for value in (
                "'accepted'",
                "'running'",
                "'completed'",
                "'failed'",
                "'cancelled'",
                "'interrupted'",
            ):
                assert value in constraints
            assert "DEFERRABLE INITIALLY DEFERRED" in constraints

            indexes = "\n".join(
                row[0]
                for row in connection.execute(
                    "select indexdef from pg_indexes where schemaname='platform_control' "
                    "and tablename=any(%s)",
                    (list(CONVERSATION_TABLES),),
                )
            )
            assert "one_active_conversation_turn" in indexes
            assert "WHERE (status = ANY (ARRAY['accepted'::text, 'running'::text]))" in indexes
            assert "owner_internal_user_id, started_by_client_request_id" in indexes


@pytest.mark.postgres
def test_conversation_schema_enforces_links_sequences_and_one_active_turn(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        owner_id = uuid4()
        conversation_id = uuid4()
        first_message_id = uuid4()
        first_turn_id = uuid4()
        mission_id = uuid4()
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Conversation Owner','active')",
            (owner_id,),
        )
        connection.execute(
            "insert into platform_control.conversations "
            "(conversation_id,owner_internal_user_id,started_by_client_request_id,"
            "mode,title,status) values (%s,%s,%s,'brain','第一轮对话','active')",
            (conversation_id, owner_id, uuid4()),
        )
        connection.execute("set constraints all deferred")
        connection.execute(
            "insert into platform_control.conversation_messages "
            "(message_id,conversation_id,seq,role,content_ciphertext,"
            "encryption_key_version,turn_id,mission_id,delivery_status) "
            "values (%s,%s,1,'user',%s,1,%s,%s,'accepted')",
            (first_message_id, conversation_id, b"c" * 29, first_turn_id, mission_id),
        )
        connection.execute(
            "insert into platform_control.conversation_turns "
            "(turn_id,conversation_id,user_message_id,client_request_id,mission_id,status) "
            "values (%s,%s,%s,%s,%s,'accepted')",
            (first_turn_id, conversation_id, first_message_id, uuid4(), mission_id),
        )
        connection.execute(
            "insert into platform_control.missions "
            "(mission_id,owner_internal_user_id,client_request_id,mode,status,"
            "conversation_id,turn_id,triggering_message_id) "
            "values (%s,%s,%s,'brain','planning',%s,%s,%s)",
            (
                mission_id,
                owner_id,
                uuid4(),
                conversation_id,
                first_turn_id,
                first_message_id,
            ),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                "insert into platform_control.conversation_turns "
                "(turn_id,conversation_id,user_message_id,client_request_id,status) "
                "values (%s,%s,%s,%s,'running')",
                (uuid4(), conversation_id, first_message_id, uuid4()),
            )
        connection.rollback()


@pytest.mark.postgres
def test_conversation_tables_are_environment_scoped_and_never_deletable(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        app_role = next(
            role for role in environment["roles"] if "control_app" in role
        )
        opposite_app = (
            "platform_control_app_preview"
            if app_role == "platform_control_app"
            else "platform_control_app"
        )
        with psycopg.connect(environment["admin"]) as connection:
            assert connection.execute(
                "select bool_and(not has_table_privilege('public', "
                "'platform_control.' || table_name, 'select,insert,update,delete')) "
                "from unnest(%s::text[]) table_name",
                (list(CONVERSATION_TABLES),),
            ).fetchone() == (True,)
            for table in CONVERSATION_TABLES:
                assert connection.execute(
                    "select has_table_privilege(%s,%s,'select'),"
                    "has_table_privilege(%s,%s,'insert'),"
                    "has_table_privilege(%s,%s,'delete'),"
                    "has_table_privilege(%s,%s,'select')",
                    (
                        app_role,
                        f"platform_control.{table}",
                        app_role,
                        f"platform_control.{table}",
                        app_role,
                        f"platform_control.{table}",
                        opposite_app,
                        f"platform_control.{table}",
                    ),
                ).fetchone() == (True, True, False, False)
            for role in ROLES:
                if role == app_role:
                    continue
                assert connection.execute(
                    "select bool_and(not has_table_privilege(%s, "
                    "'platform_control.' || table_name, 'delete')) "
                    "from unnest(%s::text[]) table_name",
                    (role, list(CONVERSATION_TABLES)),
                ).fetchone() == (True,)


@pytest.mark.postgres
def test_agent_brain_run_schema_supports_internal_summary_phase(
    control_database,
) -> None:
    for environment in control_database["environments"].values():
        with psycopg.connect(environment["admin"]) as connection:
            constraints = "\n".join(
                row[0]
                for row in connection.execute(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where connamespace='platform_control'::regnamespace "
                    "and conrelid='platform_control.mission_runs'::regclass "
                    "and contype='c'"
                )
            )
        assert "'summary'::text" in constraints
