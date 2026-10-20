"""Persistent ZenTao account bindings and single-use SSO tickets."""

from __future__ import annotations

import re
from contextlib import contextmanager
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn


_ACCOUNT = re.compile(r"[a-z][a-z0-9._-]{1,39}\Z")
_RESERVED = frozenset({"admin", "sdkadmin", "sdk-api"})
_ZEN_LOGIN_LOCK = 0x53444B53534F


class SdkSsoRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        validate_control_dsn(database_url, purpose="app")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def bind(self, actor: UUID, target: UUID, account: str) -> dict[str, str]:
        if not isinstance(account, str) or not _ACCOUNT.fullmatch(account) or account in _RESERVED:
            raise HTTPException(422, "sdk_sso_account_invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "insert into sdk.sso_bindings(internal_user_id,account,bound_by) "
                    "values (%s,%s,%s) on conflict (internal_user_id) do update "
                    "set account=excluded.account,bound_by=excluded.bound_by,bound_at=clock_timestamp() "
                    "returning internal_user_id,account",
                    (target, account, actor),
                ).fetchone()
            return {"internal_user_id": str(row["internal_user_id"]), "account": row["account"]}
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "sdk_sso_account_already_bound") from None
        except psycopg.errors.ForeignKeyViolation:
            raise HTTPException(404, "sdk_sso_member_not_found") from None
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def issue(self, actor: UUID, session_id: UUID, token_digest: bytes) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select sdk.issue_sso_ticket(%s,%s,%s) as issued",
                    (actor, session_id, token_digest),
                ).fetchone()
            return bool(row and row["issued"])
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def consume(self, token_digest: bytes) -> str | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select sdk.consume_sso_ticket(%s) as account",
                    (token_digest,),
                ).fetchone()
            return row["account"] if row else None
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def next_login_time(self) -> int:
        try:
            with self._connection() as connection:
                row = connection.execute("select sdk.next_sso_time() as login_time").fetchone()
            return int(row["login_time"])
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    @contextmanager
    def login_slot(self):
        """Serialize a monotonic timestamp through the ZenTao response.

        ZenTao compares calledTime globally per entry. A session advisory lock
        survives the allocator commit, so a failed HTTP call cannot roll back
        an already accepted timestamp.
        """
        try:
            with self._connection() as connection:
                connection.execute("select pg_advisory_lock(%s)", (_ZEN_LOGIN_LOCK,))
                try:
                    row = connection.execute("select sdk.next_sso_time() as login_time").fetchone()
                    connection.commit()
                    yield int(row["login_time"])
                finally:
                    connection.rollback()
                    connection.execute("select pg_advisory_unlock(%s)", (_ZEN_LOGIN_LOCK,))
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None
