"""Persistent ZenTao account bindings and single-use SSO tickets."""

from __future__ import annotations

import re
from base64 import b32encode
from contextlib import contextmanager
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn


_ACCOUNT = re.compile(r"[a-z][a-z0-9._-]{1,39}\Z")
_RESERVED = frozenset({"admin", "sdkadmin", "sdk-api"})
_ZEN_LOGIN_LOCK = 0x53444B53534F
_ZEN_PROVISION_LOCK = 0x53444B50524F


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

    def identity(self, actor: UUID) -> dict | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select eligible,display_name,account,provision_state from sdk.sso_identity(%s)",
                    (actor,),
                ).fetchone()
            return dict(row) if row else None
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def set_allowed_department(self, actor: UUID, department: UUID, enabled: bool) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select sdk.set_allowed_department(%s,%s,%s) as allowed",
                    (actor, department, enabled),
                ).fetchone()
            return bool(row and row["allowed"])
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def departments(self) -> list[dict]:
        try:
            with self._connection() as connection:
                row = connection.execute("select sdk.sso_department_options() as items").fetchone()
            if not row or row["items"] is None:
                raise HTTPException(503, "sdk_sso_directory_stale")
            return row["items"]
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def access_changes(self, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("sdk_access_limit_invalid")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from sdk.sso_access_changes(%s)", (limit,),
                ).fetchall()
            return [dict(row) for row in rows]
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    @staticmethod
    def account_for(actor: UUID) -> str:
        return "sdk_" + b32encode(actor.bytes).decode("ascii").rstrip("=").lower()

    @contextmanager
    def provision_slot(self, actor: UUID):
        """Serialize account creation and the private-product whitelist update."""
        try:
            with self._connection() as connection:
                acquired = connection.execute(
                    "select pg_try_advisory_lock(%s) as acquired", (_ZEN_PROVISION_LOCK,)
                ).fetchone()
                if not acquired or not acquired["acquired"]:
                    raise HTTPException(409, "sdk_provision_in_progress")
                connection.commit()
                try:
                    row = connection.execute(
                        "select eligible,display_name,account,provision_state from sdk.sso_identity(%s)",
                        (actor,),
                    ).fetchone()
                    if not row or not row["eligible"]:
                        raise HTTPException(403, "sdk_sso_access_required")
                    intent = connection.execute(
                        "select marker from sdk.provision_intents where internal_user_id=%s", (actor,)
                    ).fetchone()
                    yield connection, {**dict(row), "marker": intent["marker"] if intent else None}
                finally:
                    connection.rollback()
                    connection.execute("select pg_advisory_unlock(%s)", (_ZEN_PROVISION_LOCK,))
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "sdk_sso_account_conflict") from None
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    @contextmanager
    def access_slot(self, actor: UUID):
        """Recheck one auto-created account under the same lock as first login."""
        try:
            with self._connection() as connection:
                acquired = connection.execute(
                    "select pg_try_advisory_lock(%s) as acquired", (_ZEN_PROVISION_LOCK,)
                ).fetchone()
                if not acquired or not acquired["acquired"]:
                    raise HTTPException(409, "sdk_provision_in_progress")
                connection.commit()
                try:
                    row = connection.execute(
                        "select intent.account,intent.marker,intent.state,"
                        "sdk.sso_eligible(intent.internal_user_id) as eligible "
                        "from sdk.provision_intents intent join sdk.members member "
                        "on member.internal_user_id=intent.internal_user_id and member.grant_source='directory' "
                        "where intent.internal_user_id=%s and intent.state in ('bound','suspended')",
                        (actor,),
                    ).fetchone()
                    if not row:
                        raise HTTPException(409, "sdk_access_state_changed")
                    yield connection, dict(row)
                finally:
                    connection.rollback()
                    connection.execute("select pg_advisory_unlock(%s)", (_ZEN_PROVISION_LOCK,))
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def mark_access_state(self, connection, actor: UUID, old_state: str, new_state: str) -> None:
        if (old_state, new_state) not in {("bound", "suspended"), ("suspended", "bound")}:
            raise ValueError("sdk_access_state_invalid")
        row = connection.execute(
            "update sdk.provision_intents set state=%s,updated_at=clock_timestamp() "
            "where internal_user_id=%s and state=%s and sdk.sso_eligible(%s)=%s "
            "returning internal_user_id",
            (new_state, actor, old_state, actor, new_state == "bound"),
        ).fetchone()
        if not row:
            raise HTTPException(409, "sdk_access_state_changed")
        connection.commit()

    def defer_access_retry(self, actor: UUID) -> None:
        """Keep a failing account from starving later revocations in the queue."""
        try:
            with self._connection() as connection:
                connection.execute(
                    "update sdk.provision_intents set last_access_attempt_at=clock_timestamp() "
                    "where internal_user_id=%s and state in ('bound','suspended')",
                    (actor,),
                )
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def mark_provision_attempt(self, connection, actor: UUID, account: str, marker: str) -> None:
        if account != self.account_for(actor) or not re.fullmatch(r"sdk:[0-9a-f]{32}", marker):
            raise HTTPException(422, "sdk_provision_identity_invalid")
        row = connection.execute(
            "insert into sdk.provision_intents(internal_user_id,account,marker,state) "
            "values (%s,%s,%s,'reconcile_required') on conflict do nothing returning account",
            (actor, account, marker),
        ).fetchone()
        if not row:
            raise HTTPException(409, "sdk_provision_already_started")
        connection.commit()

    def complete_provision(self, connection, actor: UUID, account: str) -> None:
        if account != self.account_for(actor):
            raise HTTPException(422, "sdk_provision_identity_invalid")
        with connection.transaction():
            eligible = connection.execute("select sdk.sso_eligible(%s) as eligible", (actor,)).fetchone()
            if not eligible or not eligible["eligible"]:
                raise HTTPException(403, "sdk_sso_access_required")
            intent = connection.execute(
                "select state from sdk.provision_intents where internal_user_id=%s and account=%s for update",
                (actor, account),
            ).fetchone()
            if not intent or intent["state"] not in {"reconcile_required", "bound"}:
                raise HTTPException(409, "sdk_provision_intent_missing")
            connection.execute(
                "insert into sdk.members(internal_user_id,role,granted_by,grant_source) "
                "values (%s,'contributor',%s,'directory') on conflict do nothing",
                (actor, actor),
            )
            connection.execute(
                "insert into sdk.sso_bindings(internal_user_id,account,bound_by) "
                "values (%s,%s,%s) on conflict do nothing", (actor, account, actor),
            )
            bound = connection.execute(
                "select account from sdk.sso_bindings where internal_user_id=%s", (actor,)
            ).fetchone()
            if not bound or bound["account"] != account:
                raise HTTPException(409, "sdk_sso_account_conflict")
            connection.execute(
                "update sdk.provision_intents set state='bound',updated_at=clock_timestamp() "
                "where internal_user_id=%s", (actor,),
            )
        connection.commit()

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

    def issue(self, actor: UUID, session_id: UUID, token_digest: bytes, target: str = "home") -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select sdk.issue_sso_ticket(%s,%s,%s,%s) as issued",
                    (actor, session_id, token_digest, target),
                ).fetchone()
            return bool(row and row["issued"])
        except psycopg.Error:
            raise HTTPException(503, "sdk_sso_store_unavailable") from None

    def consume(self, token_digest: bytes) -> tuple[str, str] | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select sdk.consume_sso_ticket(%s) as account",
                    (token_digest,),
                ).fetchone()
                if not row or row["account"] is None:
                    return None
                target = connection.execute(
                    "select target from sdk.sso_tickets where token_digest=%s", (token_digest,)
                ).fetchone()
            return (row["account"], target["target"]) if target else None
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
