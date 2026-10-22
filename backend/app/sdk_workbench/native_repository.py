"""Read-only source and display-name supplements for native ZenTao records."""

from __future__ import annotations

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn


class NativeSdkRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        validate_control_dsn(database_url, purpose="app")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(self._database_url, connect_timeout=3,
                             options="-c statement_timeout=10000", row_factory=dict_row)

    def sources_for(self, story_ids: list[int]) -> dict[int, dict]:
        if not story_ids:
            return {}
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select source.story_id,source.proposer_name,source.responsible_raw,"
                    "source.planned_start,source.planned_end,source.source_version,"
                    "source.source_risk,source.source_status,source.source_note,plan.module "
                    "from sdk.plan_story_sources source "
                    "join sdk.plan_rows plan on plan.id=source.plan_row_id "
                    "where source.story_id=any(%s)", (story_ids,),
                ).fetchall()
            return {int(row["story_id"]): {
                key: value.isoformat() if hasattr(value, "isoformat") else value
                for key, value in dict(row).items() if key != "story_id"
            } for row in rows}
        except psycopg.Error:
            raise HTTPException(503, "sdk_source_store_unavailable") from None

    def source_for(self, story_id: int) -> dict | None:
        return self.sources_for([story_id]).get(story_id)

    def names_for(self, accounts: list[str]) -> dict[str, str]:
        selected = sorted({account for account in accounts if account})
        if not selected:
            return {}
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select binding.account,person.display_name "
                    "from sdk.sso_bindings binding "
                    "join platform_control.internal_users person "
                    "on person.internal_user_id=binding.internal_user_id "
                    "where binding.account=any(%s)", (selected,),
                ).fetchall()
            return {row["account"]: row["display_name"] for row in rows}
        except psycopg.Error:
            raise HTTPException(503, "sdk_source_store_unavailable") from None
