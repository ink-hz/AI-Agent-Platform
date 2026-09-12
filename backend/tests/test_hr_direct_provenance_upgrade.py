"""Historical HR turn provenance across the real 089-091 upgrade order."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from app.agent_brain.conversation_repository import ConversationRepository
from app.agent_brain.repository import MissionRepository
from app.agent_brain.turn_attempts import AttemptNotFound, TurnAttemptRepository
from hr_agent_support import hr_agent_database
from test_agent_brain_conversation_repository import _codec

pytestmark = pytest.mark.postgres
HR_WEB = Path(__file__).parents[1] / "control_migrations/hr_web"


def test_real_091_backfills_preexisting_hr_turn_as_legacy_provenance():
    with hr_agent_database(migrate_hr=False) as database:
        environment = {
            "admin": database.admin_dsn,
            "urls": {"platform_control_app": database.dsn},
        }
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute("set local role platform_control_owner")
            connection.execute((HR_WEB / "089_hr_turn_attempts.sql").read_text())
            connection.execute((HR_WEB / "090_hr_v5_readiness.sql").read_text())

        owner_id = uuid4()
        with psycopg.connect(environment["admin"]) as connection:
            connection.execute(
                "insert into platform_control.internal_users"
                "(internal_user_id,display_name,status) "
                "values(%s,'Historical HR owner','active')",
                (owner_id,),
            )
        codec = _codec()
        missions = MissionRepository(
            environment["urls"]["platform_control_app"], content_codec=codec
        )
        conversations = ConversationRepository(
            environment["urls"]["platform_control_app"],
            content_codec=codec,
            mission_repository=missions,
        )

        # 094-era intake cannot execute before 091. Create the same historical
        # direct rows through the older non-HR path, then label their persisted
        # identities as HR before applying the real migration under test.
        historical = conversations.start(
            owner_id,
            uuid4(),
            "Historical HR input before provenance columns",
            mode="direct_agent",
            direct_agent_id="fae-bot",
        )
        with psycopg.connect(environment["admin"]) as connection:
            columns_absent = connection.execute(
                "select not exists(select 1 from pg_attribute "
                "where attrelid='platform_control.conversation_turns'::regclass "
                "and attname in ('execution_owner','origin_route_epoch') "
                "and not attisdropped)"
            ).fetchone()[0]
            assert columns_absent
            connection.execute(
                "update platform_control.conversations set "
                "direct_agent_id='hr-bot',execution_owner='worker_direct',route_epoch=7 "
                "where conversation_id=%s",
                (historical.conversation.conversation_id,),
            )
            connection.execute(
                "update platform_control.missions set direct_agent_id='hr-bot' "
                "where mission_id=%s",
                (historical.mission.mission_id,),
            )
            connection.execute("set local role platform_control_owner")
            connection.execute((HR_WEB / "091_hr_direct_dispatch.sql").read_text())
            provenance = connection.execute(
                "select execution_owner,origin_route_epoch "
                "from platform_control.conversation_turns where turn_id=%s",
                (historical.turn.turn_id,),
            ).fetchone()

        assert provenance == ("legacy_api_v1", 0)
        assert historical.mission.mission_id in {
            claim.mission_id
            for claim in missions.claim_pending(50, modes=("direct_agent",))
        }
        attempts = TurnAttemptRepository(
            environment["urls"]["platform_control_app"], codec
        )
        with pytest.raises(AttemptNotFound):
            attempts.create_queued(historical.turn.turn_id, "worker_direct")
