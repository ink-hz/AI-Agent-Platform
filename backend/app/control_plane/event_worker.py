from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import logging
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

from .crypto import ProviderIdentityCodec
from .dsn import validate_control_dsn
from .identity import IdentityResolver
from .stream_consumer import APPROVED_ORGANIZATION_EVENT_TYPES


_LOG = logging.getLogger(__name__)
_MEMBER_REFRESH_EVENTS = frozenset(
    {"user_add_org", "user_modify_org", "org_user_active"}
)
_DEPARTMENT_EVENTS = frozenset(
    {"org_dept_create", "org_dept_modify", "org_dept_remove"}
)


class StreamEventDisposition(StrEnum):
    APPLIED = "applied"
    STALE = "stale"
    ALREADY_APPLIED = "already_applied"
    MEMBER_NOT_FOUND = "member_not_found"


@dataclass(frozen=True)
class ClaimedStreamEvent:
    inbox_id: int
    event_key: str
    event_type: str
    encrypted_payload: bytes
    encryption_key_version: int
    attempts: int


class TargetedMemberRefresher:
    """Verify the event subject before requesting an atomic directory snapshot.

    Directory generations are immutable, so an event must never patch the active
    generation in place. The provider read narrows and validates the trigger;
    the reconciler then stages and promotes a complete generation atomically.
    """

    def __init__(self, client: Any, reconciler: Any) -> None:
        self._client = client
        self._reconciler = reconciler

    async def refresh_user(self, userid: str) -> None:
        try:
            member = await self._client.get_member(userid)
            if member.userid != userid:
                raise ValueError
            await self._reconciler.run_full("targeted")
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RuntimeError("targeted member refresh failed") from None


class DirectoryEventRepository:
    def __init__(
        self,
        database_url: str,
        *,
        identity_codec: ProviderIdentityCodec,
        corp_id: str,
        lease_seconds: int = 60,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        validate_control_dsn(database_url, purpose="directory")
        if not isinstance(identity_codec, ProviderIdentityCodec):
            raise ValueError("provider identity codec required")
        if not isinstance(corp_id, str) or not corp_id.strip() or "\0" in corp_id:
            raise ValueError("DingTalk corp ID invalid")
        if not 1 <= lease_seconds <= 300:
            raise ValueError("stream event lease invalid")
        self._database_url = database_url
        self._codec = identity_codec
        self._corp_id = corp_id
        self._lease_seconds = lease_seconds
        self._connect = connect

    def __repr__(self) -> str:
        return (
            "DirectoryEventRepository(database_url=<redacted>, "
            "identity_codec=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c lock_timeout=3000",
            row_factory=dict_row,
        )

    def claim_next(self) -> ClaimedStreamEvent | None:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "with candidate as (select inbox_id from "
                    "platform_control.stream_inbox where "
                    "status in ('pending','processing') and available_at<=clock_timestamp() "
                    "order by available_at,inbox_id for update skip locked limit 1) "
                    "update platform_control.stream_inbox inbox set "
                    "status='processing',attempts=inbox.attempts+1,"
                    "available_at=clock_timestamp()+(%s * interval '1 second') "
                    "from candidate where inbox.inbox_id=candidate.inbox_id "
                    "returning inbox.inbox_id,inbox.event_key,inbox.event_type,"
                    "inbox.encrypted_payload,inbox.encryption_key_version,inbox.attempts",
                    (self._lease_seconds,),
                )
                row = cursor.fetchone()
            if row is None:
                return None
            return ClaimedStreamEvent(
                inbox_id=row["inbox_id"],
                event_key=row["event_key"],
                event_type=row["event_type"],
                encrypted_payload=bytes(row["encrypted_payload"]),
                encryption_key_version=row["encryption_key_version"],
                attempts=row["attempts"],
            )
        except psycopg.Error:
            raise RuntimeError("directory event repository unavailable") from None

    def apply_departure(
        self,
        *,
        userid: str,
        event_time: datetime,
        event_key: str,
    ) -> StreamEventDisposition:
        protected = self._codec.seal(
            IdentityResolver.CORPORATE_SUBJECT_KIND,
            IdentityResolver.corporate_provider_id(self._corp_id, userid),
        )
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select platform_control.apply_directory_departure_v21("
                    "%s,%s,%s,%s) as disposition",
                    (
                        protected.lookup_key_version,
                        protected.lookup_hmac,
                        event_time,
                        event_key,
                    ),
                )
                row = cursor.fetchone()
            return StreamEventDisposition(row["disposition"])
        except (psycopg.Error, KeyError, ValueError):
            raise RuntimeError("directory event repository unavailable") from None

    def mark_processed(self, inbox_id: int) -> None:
        self._transition(
            "status='processed',processed_at=clock_timestamp(),last_error_code=null",
            inbox_id,
        )

    def mark_ignored(self, inbox_id: int, reason: str) -> None:
        self._transition(
            "status='ignored',processed_at=clock_timestamp(),last_error_code=%s",
            inbox_id,
            reason,
        )

    def reschedule(
        self, inbox_id: int, error_code: str, delay_seconds: int
    ) -> None:
        if not 1 <= delay_seconds <= 300:
            raise ValueError("stream event retry invalid")
        self._transition(
            "status='pending',available_at=clock_timestamp()+(%s * interval '1 second'),"
            "last_error_code=%s",
            inbox_id,
            delay_seconds,
            error_code,
        )

    def mark_dead_letter(self, inbox_id: int, error_code: str) -> None:
        self._transition(
            "status='dead_letter',processed_at=clock_timestamp(),last_error_code=%s",
            inbox_id,
            error_code,
        )

    def _transition(self, assignment: str, inbox_id: int, *values: Any) -> None:
        if (
            isinstance(inbox_id, bool)
            or not isinstance(inbox_id, int)
            or inbox_id <= 0
            or any(
                isinstance(value, str) and (not value or len(value) > 64)
                for value in values
            )
        ):
            raise ValueError("stream event transition invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "update platform_control.stream_inbox set "
                    + assignment
                    + " where inbox_id=%s and status='processing'",
                    (*values, inbox_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("stream event transition unavailable")
        except psycopg.Error:
            raise RuntimeError("directory event repository unavailable") from None

    def heartbeat(self, status: str, error_code: str | None = None) -> None:
        if status not in {"healthy", "degraded"} or (
            error_code is not None and (not error_code or len(error_code) > 64)
        ):
            raise ValueError("worker heartbeat invalid")
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "insert into platform_control.worker_heartbeats "
                    "(worker_name,status,last_error_code,last_seen_at) "
                    "values ('dingtalk-directory-event',%s,%s,clock_timestamp()) "
                    "on conflict(worker_name) do update set status=excluded.status,"
                    "last_error_code=excluded.last_error_code,last_seen_at=excluded.last_seen_at",
                    (status, error_code),
                )
        except psycopg.Error:
            raise RuntimeError("directory event repository unavailable") from None


class DirectoryEventWorker:
    def __init__(
        self,
        repository: Any,
        payload_cipher: Any,
        *,
        member_refresher: Any,
        reconciler: Any,
        max_attempts: int = 5,
        idle_poll_seconds: float = 1.0,
        sleep=asyncio.sleep,
    ) -> None:
        if not 1 <= max_attempts <= 10:
            raise ValueError("event worker retry bound invalid")
        if not 0.1 <= idle_poll_seconds <= 10:
            raise ValueError("event worker poll interval invalid")
        self._repository = repository
        self._cipher = payload_cipher
        self._member_refresher = member_refresher
        self._reconciler = reconciler
        self._max_attempts = max_attempts
        self._idle_poll_seconds = idle_poll_seconds
        self._sleep = sleep

    async def serve(self) -> None:
        while True:
            worked = await self.process_once()
            if not worked:
                await self._sleep(self._idle_poll_seconds)

    async def process_once(self) -> bool:
        item = await asyncio.to_thread(self._repository.claim_next)
        if item is None:
            await asyncio.to_thread(self._repository.heartbeat, "healthy", None)
            return False
        try:
            payload = self._cipher.open(
                item.encrypted_payload,
                key_version=item.encryption_key_version,
                event_key=item.event_key,
                event_type=item.event_type,
            )
            await self._dispatch(item, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._fail(item, "payload_invalid")
        return True

    async def _dispatch(self, item: ClaimedStreamEvent, payload: dict) -> None:
        if item.event_type == "unapproved":
            await asyncio.to_thread(
                self._repository.mark_ignored,
                item.inbox_id,
                "event_unapproved",
            )
            return
        if item.event_type not in APPROVED_ORGANIZATION_EVENT_TYPES:
            raise ValueError("stream event invalid")
        if payload.get("event_type") != item.event_type:
            raise ValueError("stream event invalid")

        if item.event_type in _MEMBER_REFRESH_EVENTS:
            for userid in self._userids(payload):
                await self._member_refresher.refresh_user(userid)
        elif item.event_type == "user_leave_org":
            event_time = self._event_time(payload)
            dispositions = []
            for userid in self._userids(payload):
                dispositions.append(
                    await asyncio.to_thread(
                        self._repository.apply_departure,
                        userid=userid,
                        event_time=event_time,
                        event_key=item.event_key,
                    )
                )
            if dispositions and all(
                value is StreamEventDisposition.STALE for value in dispositions
            ):
                await asyncio.to_thread(
                    self._repository.mark_ignored,
                    item.inbox_id,
                    "stale_event",
                )
                return
        elif item.event_type in _DEPARTMENT_EVENTS:
            await self._reconciler.run_full("event")
        else:
            raise ValueError("stream event invalid")

        await asyncio.to_thread(self._repository.mark_processed, item.inbox_id)
        await asyncio.to_thread(self._repository.heartbeat, "healthy", None)

    async def _fail(self, item: ClaimedStreamEvent, error_code: str) -> None:
        if item.attempts >= self._max_attempts:
            await asyncio.to_thread(
                self._repository.mark_dead_letter,
                item.inbox_id,
                error_code,
            )
        else:
            delay = min(300, 2 ** item.attempts)
            await asyncio.to_thread(
                self._repository.reschedule,
                item.inbox_id,
                error_code,
                delay,
            )
        await asyncio.to_thread(self._repository.heartbeat, "degraded", error_code)

    @staticmethod
    def _userids(payload: dict) -> tuple[str, ...]:
        data = payload.get("data")
        values = data.get("UserId") if isinstance(data, dict) else None
        if isinstance(values, str):
            values = [values]
        if (
            not isinstance(values, list)
            or not values
            or len(values) > 1000
            or any(
                not isinstance(value, str)
                or not value
                or len(value) > 512
                or "\0" in value
                for value in values
            )
        ):
            raise ValueError("stream event invalid")
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _event_time(payload: dict) -> datetime:
        value = payload.get("born_time_ms")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("stream event invalid")
        try:
            result = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise ValueError("stream event invalid") from None
        return result
