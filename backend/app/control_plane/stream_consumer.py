from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import logging
import secrets
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dingtalk_stream import (
    AckMessage,
    Credential,
    DingTalkStreamClient,
    EventHandler,
    EventMessage,
)

from .crypto import IdentityCryptoError, IdentityKeyring
from .dsn import validate_control_dsn


_LOG = logging.getLogger(__name__)
_MAX_EVENT_BYTES = 256 * 1024

APPROVED_ORGANIZATION_EVENT_TYPES = frozenset(
    {
        "user_add_org",
        "user_modify_org",
        "user_leave_org",
        "org_user_active",
        "org_dept_create",
        "org_dept_modify",
        "org_dept_remove",
    }
)


@dataclass(frozen=True)
class EncryptedStreamPayload:
    ciphertext: bytes
    key_version: int


class StreamPayloadCipher:
    """Purpose-bound encryption for raw organization event payloads."""

    def __init__(self, keyring: IdentityKeyring) -> None:
        if (
            keyring.purpose != "provider-encryption"
            or keyring.key_lengths != {32}
        ):
            raise ValueError("stream payload keyring invalid")
        self._keyring = keyring

    @staticmethod
    def _aad(event_key: str, event_type: str, key_version: int) -> bytes:
        if (
            not isinstance(event_key, str)
            or len(event_key) != 64
            or any(character not in "0123456789abcdef" for character in event_key)
            or not isinstance(event_type, str)
            or not event_type
        ):
            raise ValueError("stream payload unavailable")
        return f"platform-stream:{event_type}:{event_key}:v{key_version}".encode(
            "ascii"
        )

    def seal(
        self, payload: dict[str, Any], *, event_key: str, event_type: str
    ) -> EncryptedStreamPayload:
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if len(encoded) > _MAX_EVENT_BYTES:
                raise ValueError
            version = self._keyring.active_version
            nonce = secrets.token_bytes(12)
            ciphertext = nonce + AESGCM(self._keyring.active_key).encrypt(
                nonce,
                encoded,
                self._aad(event_key, event_type, version),
            )
            return EncryptedStreamPayload(ciphertext, version)
        except (TypeError, ValueError, UnicodeError):
            raise ValueError("stream payload unavailable") from None

    def open(
        self,
        ciphertext: bytes,
        *,
        key_version: int,
        event_key: str,
        event_type: str,
    ) -> dict[str, Any]:
        try:
            if not isinstance(ciphertext, bytes) or not 28 <= len(ciphertext) <= (
                _MAX_EVENT_BYTES + 28
            ):
                raise ValueError
            plaintext = AESGCM(self._keyring.key_for_version(key_version)).decrypt(
                ciphertext[:12],
                ciphertext[12:],
                self._aad(event_key, event_type, key_version),
            )
            payload = json.loads(plaintext.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (
            IdentityCryptoError,
            InvalidTag,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            UnicodeError,
        ):
            raise ValueError("stream payload unavailable") from None


def _opaque_event_key(event_type: str, event_id: str) -> str:
    if (
        not isinstance(event_type, str)
        or not event_type
        or not isinstance(event_id, str)
        or not event_id
        or len(event_type) > 128
        or len(event_id) > 512
    ):
        raise ValueError("stream event invalid")
    framed = (
        len(event_type.encode("utf-8")).to_bytes(4, "big")
        + event_type.encode("utf-8")
        + event_id.encode("utf-8")
    )
    return hashlib.sha256(framed).hexdigest()


class DurableOrganizationEventHandler(EventHandler):
    def __init__(
        self,
        inbox: Any,
        cipher: StreamPayloadCipher,
        *,
        expected_corp_id: str,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__()
        if not expected_corp_id:
            raise ValueError("DingTalk corp ID invalid")
        self._inbox = inbox
        self._cipher = cipher
        self._expected_corp_id = expected_corp_id
        self.logger = logger or _LOG

    async def process(self, event: EventMessage) -> tuple[int, str]:
        headers = event.headers
        original_type = headers.event_type
        event_key = _opaque_event_key(original_type, headers.event_id)
        approved = (
            original_type in APPROVED_ORGANIZATION_EVENT_TYPES
            and headers.event_corp_id == self._expected_corp_id
        )
        stored_type = original_type if approved else "unapproved"
        payload = {
            "event_type": original_type,
            "event_id": headers.event_id,
            "corp_id": headers.event_corp_id,
            "born_time_ms": headers.event_born_time,
            "data": event.data,
        }
        sealed = self._cipher.seal(
            payload, event_key=event_key, event_type=stored_type
        )
        try:
            await asyncio.to_thread(
                self._inbox.insert_encrypted_once,
                event_key=event_key,
                event_type=stored_type,
                encrypted_payload=sealed.ciphertext,
                encryption_key_version=sealed.key_version,
            )
        except Exception:
            self.logger.error("dingtalk stream error_code=event_persist_failed")
            raise
        return AckMessage.STATUS_OK, "OK"


class StreamInboxRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        validate_control_dsn(database_url, purpose="stream")
        self._database_url = database_url
        self._connect = connect

    def __repr__(self) -> str:
        return "StreamInboxRepository(database_url=<redacted>)"

    def insert_encrypted_once(
        self,
        *,
        event_key: str,
        event_type: str,
        encrypted_payload: bytes,
        encryption_key_version: int,
    ) -> bool:
        if (
            not isinstance(event_key, str)
            or len(event_key) != 64
            or any(character not in "0123456789abcdef" for character in event_key)
            or not isinstance(event_type, str)
            or not event_type
            or len(event_type) > 128
            or not isinstance(encrypted_payload, bytes)
            or not 28 <= len(encrypted_payload) <= _MAX_EVENT_BYTES + 28
            or isinstance(encryption_key_version, bool)
            or not isinstance(encryption_key_version, int)
            or encryption_key_version <= 0
        ):
            raise ValueError("stream event invalid")
        try:
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "select platform_control.insert_stream_event_v21("
                    "%s,%s,%s,%s) as inserted",
                    (
                        event_key,
                        event_type,
                        encrypted_payload,
                        encryption_key_version,
                    ),
                )
                inserted = bool(cursor.fetchone()["inserted"])
            return inserted
        except psycopg.Error:
            raise RuntimeError("stream inbox unavailable") from None


class StreamConsumer:
    """Small lifecycle adapter around the pinned official Stream SDK."""

    RECONNECT_MIN_SECONDS = 3
    RECONNECT_MAX_SECONDS = 10

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        handler: DurableOrganizationEventHandler,
        client_factory: Callable[..., Any] = DingTalkStreamClient,
        logger: logging.Logger | None = None,
    ) -> None:
        if not app_key or not app_secret:
            raise ValueError("DingTalk Stream credentials unavailable")
        self._client = client_factory(Credential(app_key, app_secret), logger=logger)
        self._client.register_all_event_handler(handler)
        self._run_task: asyncio.Task | None = None

    async def run(self) -> None:
        current = asyncio.current_task()
        if current is None or self._run_task is not None:
            raise RuntimeError("DingTalk Stream consumer already running")
        self._run_task = current
        try:
            await self._client.start()
        finally:
            if self._run_task is current:
                self._run_task = None

    async def stop(self) -> None:
        # dingtalk-stream 0.24.3 has no public stop() method. Closing the active
        # websocket unblocks start(); the owning service then cancels its task.
        websocket = getattr(self._client, "websocket", None)
        if websocket is not None:
            await websocket.close()
        task = self._run_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
