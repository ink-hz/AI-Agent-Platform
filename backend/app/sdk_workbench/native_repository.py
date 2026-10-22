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
                supplements = connection.execute(
                    "select story_id,proposer_name,device_platform "
                    "from sdk.native_story_supplements where story_id=any(%s)",
                    (story_ids,),
                ).fetchall()
            sources = {int(row["story_id"]): {
                key: value.isoformat() if hasattr(value, "isoformat") else value
                for key, value in dict(row).items() if key != "story_id"
            } for row in rows}
            for row in supplements:
                sources.setdefault(int(row["story_id"]), {}).update(
                    proposer_name=row["proposer_name"], device_platform=row["device_platform"])
            return sources
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

    def member_account(self, account: str) -> bool:
        if not account:
            return False
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select binding.account from sdk.sso_bindings binding "
                    "where binding.account=%s and sdk.sso_eligible(binding.internal_user_id)",
                    (account,),
                ).fetchone()
            return row is not None
        except psycopg.Error:
            raise HTTPException(503, "sdk_member_store_unavailable") from None

    def members(self) -> list[dict]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select binding.account,person.display_name as name "
                    "from sdk.sso_bindings binding "
                    "join platform_control.internal_users person "
                    "on person.internal_user_id=binding.internal_user_id "
                    "where sdk.sso_eligible(binding.internal_user_id) "
                    "order by person.display_name,binding.account limit 500",
                ).fetchall()
            return [dict(row) for row in rows]
        except psycopg.Error:
            raise HTTPException(503, "sdk_member_store_unavailable") from None

    def reserve_create(self, actor_id, request_id, digest: str, marker: str, *,
                       requested_assignee: str | None, proposer: str | None,
                       device: str) -> tuple[dict, bool]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "insert into sdk.native_create_intents"
                    "(actor_id,request_id,payload_digest,marker,requested_assignee,proposer_name,device_platform,state) "
                    "values (%s,%s,%s,%s,%s,%s,%s,'pending') "
                    "on conflict (actor_id,request_id) do nothing "
                    "returning actor_id,request_id,payload_digest,marker,requested_assignee,"
                    "proposer_name,device_platform,state,story_id,rejection_status,rejection_detail",
                    (actor_id, request_id, digest, marker, requested_assignee, proposer, device),
                ).fetchone()
                fresh = row is not None
                if not fresh:
                    row = connection.execute(
                        "select actor_id,request_id,payload_digest,marker,requested_assignee,"
                        "proposer_name,device_platform,state,story_id,rejection_status,rejection_detail "
                        "from sdk.native_create_intents where actor_id=%s and request_id=%s",
                        (actor_id, request_id),
                    ).fetchone()
            if row is None:
                raise HTTPException(503, "sdk_create_intent_unavailable")
            if row["payload_digest"] != digest:
                raise HTTPException(409, "sdk_request_id_conflict")
            return self._intent(row), fresh
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "sdk_create_pending_reconciliation") from None
        except psycopg.Error:
            raise HTTPException(503, "sdk_create_intent_unavailable") from None

    @staticmethod
    def _intent(row: dict) -> dict:
        return {"actor": row["actor_id"], "request_id": row["request_id"],
                "digest": row["payload_digest"], "marker": row["marker"],
                "requested_assignee": row["requested_assignee"],
                "proposer_name": row["proposer_name"],
                "device_platform": row["device_platform"],
                "state": row["state"], "story_id": row["story_id"],
                "rejection_status": row["rejection_status"],
                "rejection_detail": row["rejection_detail"]}

    def pending_create(self, actor_id) -> dict | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select actor_id,request_id,payload_digest,marker,requested_assignee,"
                    "proposer_name,device_platform,state,story_id,rejection_status,rejection_detail "
                    "from sdk.native_create_intents where actor_id=%s and state='pending'",
                    (actor_id,),
                ).fetchone()
            return self._intent(row) if row else None
        except psycopg.Error:
            raise HTTPException(503, "sdk_create_intent_unavailable") from None

    def reject_create(self, actor_id, request_id, status: int, detail: str) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    "update sdk.native_create_intents set state='rejected',rejection_status=%s,"
                    "rejection_detail=%s,updated_at=clock_timestamp() "
                    "where actor_id=%s and request_id=%s and state='pending'",
                    (status, detail, actor_id, request_id),
                )
        except psycopg.Error:
            raise HTTPException(503, "sdk_create_intent_unavailable") from None

    def complete_create(self, actor_id, request_id, story_id: int,
                        proposer: str | None, device: str | None) -> None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "update sdk.native_create_intents set state='created',story_id=%s,"
                    "updated_at=clock_timestamp() where actor_id=%s and request_id=%s "
                    "and state='pending' returning actor_id,request_id,story_id",
                    (story_id, actor_id, request_id),
                ).fetchone()
                if row is None:
                    raise HTTPException(409, "sdk_create_intent_conflict")
                connection.execute(
                    "insert into sdk.native_story_supplements(story_id,proposer_name,device_platform) "
                    "values (%s,%s,%s) on conflict (story_id) do nothing",
                    (story_id, proposer, device),
                )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "sdk_create_story_conflict") from None
        except psycopg.Error:
            raise HTTPException(503, "sdk_create_intent_unavailable") from None

    def save_supplement(self, story_id: int, proposer: str | None, device: str | None) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    "insert into sdk.native_story_supplements(story_id,proposer_name,device_platform) "
                    "values (%s,%s,%s) on conflict (story_id) do update "
                    "set proposer_name=excluded.proposer_name,device_platform=excluded.device_platform,"
                    "updated_at=clock_timestamp()",
                    (story_id, proposer, device),
                )
        except psycopg.Error:
            raise HTTPException(503, "sdk_source_store_unavailable") from None
