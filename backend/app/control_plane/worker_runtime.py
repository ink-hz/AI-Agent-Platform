from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Awaitable

from app.local_secrets import read_secret_file

from .crypto import IdentityKeyring, ProviderIdentityCodec
from .dingtalk import DingTalkClient
from .directory import DirectoryReconciler
from .directory_worker import DirectoryWorker, DirectoryWorkerRepository
from .event_worker import (
    DirectoryEventRepository,
    DirectoryEventWorker,
    TargetedMemberRefresher,
)
from .stream_consumer import (
    DurableOrganizationEventHandler,
    StreamConsumer,
    StreamInboxRepository,
    StreamPayloadCipher,
)


_INLINE_SECRETS = (
    "PLATFORM_CONTROL_DIRECTORY_DATABASE_URL",
    "PLATFORM_CONTROL_STREAM_DATABASE_URL",
    "PLATFORM_DINGTALK_APP_SECRET",
    "PLATFORM_IDENTITY_ENCRYPTION_KEYRING",
    "PLATFORM_IDENTITY_HMAC_KEYRING",
)


@dataclass(frozen=True)
class WorkerSettings:
    app_key: str = field(repr=False)
    corp_id: str = field(repr=False)
    app_secret: str = field(repr=False)
    encryption_keyring_file: Path
    hmac_keyring_file: Path | None
    directory_database_url: str | None = field(default=None, repr=False)
    stream_database_url: str | None = field(default=None, repr=False)


def _required_file_environment(name: str) -> Path:
    value = os.getenv(name, "")
    if not value:
        raise ValueError(f"{name} required")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path


def load_worker_settings(service: str | None = None) -> WorkerSettings:
    if service not in {None, "directory", "stream"}:
        raise ValueError("worker service invalid")
    if any(os.getenv(name) for name in _INLINE_SECRETS):
        raise ValueError("worker credentials must use secret files")

    app_key = read_secret_file(
        str(_required_file_environment("PLATFORM_DINGTALK_APP_KEY_FILE"))
    )
    corp_id = read_secret_file(
        str(_required_file_environment("PLATFORM_DINGTALK_CORP_ID_FILE"))
    )
    app_secret = read_secret_file(
        str(_required_file_environment("PLATFORM_DINGTALK_APP_SECRET_FILE"))
    )
    encryption_file = _required_file_environment(
        "PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE"
    )
    hmac_file = (
        _required_file_environment("PLATFORM_IDENTITY_HMAC_KEYRING_FILE")
        if service in {None, "directory"}
        else None
    )
    directory_url = (
        read_secret_file(
            str(
                _required_file_environment(
                    "PLATFORM_CONTROL_DIRECTORY_DATABASE_URL_FILE"
                )
            )
        )
        if service in {None, "directory"}
        else None
    )
    stream_url = (
        read_secret_file(
            str(
                _required_file_environment(
                    "PLATFORM_CONTROL_STREAM_DATABASE_URL_FILE"
                )
            )
        )
        if service in {None, "stream"}
        else None
    )
    return WorkerSettings(
        app_key=app_key,
        corp_id=corp_id,
        app_secret=app_secret,
        encryption_keyring_file=encryption_file,
        hmac_keyring_file=hmac_file,
        directory_database_url=directory_url,
        stream_database_url=stream_url,
    )


def _encryption_keyring(settings: WorkerSettings) -> IdentityKeyring:
    return IdentityKeyring.from_file(
        settings.encryption_keyring_file,
        expected_purpose="provider-encryption",
        expected_key_length=32,
    )


def build_directory_services():
    settings = load_worker_settings("directory")
    if settings.directory_database_url is None or settings.hmac_keyring_file is None:
        raise RuntimeError("directory worker configuration unavailable")
    encryption = _encryption_keyring(settings)
    lookup = IdentityKeyring.from_file(
        settings.hmac_keyring_file,
        expected_purpose="provider-lookup-hmac",
        expected_key_length=32,
    )
    codec = ProviderIdentityCodec(encryption, lookup)
    client = DingTalkClient(
        app_key=settings.app_key,
        app_secret=settings.app_secret,
        corp_id=settings.corp_id,
        login_flow="in_client",
    )
    repository = DirectoryWorkerRepository(settings.directory_database_url)
    reconciler = DirectoryReconciler(
        client,
        repository,
        codec,
        corp_id=settings.corp_id,
    )
    scheduled = DirectoryWorker(reconciler, repository)
    event_repository = DirectoryEventRepository(
        settings.directory_database_url,
        identity_codec=codec,
        corp_id=settings.corp_id,
    )
    event_worker = DirectoryEventWorker(
        event_repository,
        StreamPayloadCipher(encryption),
        member_refresher=TargetedMemberRefresher(client, reconciler),
        reconciler=reconciler,
    )
    return client, scheduled, event_worker


async def _run_peers(*coroutines: Awaitable[None]) -> None:
    tasks = tuple(asyncio.create_task(coroutine) for coroutine in coroutines)
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    failure: BaseException | None = None
    for task in done:
        if task.cancelled():
            failure = asyncio.CancelledError()
            break
        exception = task.exception()
        if exception is not None:
            failure = exception
            break
    if failure is None:
        failure = RuntimeError("worker service exited unexpectedly")
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    raise failure


async def serve_directory() -> None:
    provider, scheduled, events = build_directory_services()
    try:
        await _run_peers(scheduled.serve(), events.serve())
    finally:
        await provider.aclose()


async def serve_stream() -> None:
    settings = load_worker_settings("stream")
    if settings.stream_database_url is None:
        raise RuntimeError("stream worker configuration unavailable")
    cipher = StreamPayloadCipher(_encryption_keyring(settings))
    inbox = StreamInboxRepository(settings.stream_database_url)
    handler = DurableOrganizationEventHandler(
        inbox,
        cipher,
        expected_corp_id=settings.corp_id,
    )
    consumer = StreamConsumer(
        app_key=settings.app_key,
        app_secret=settings.app_secret,
        handler=handler,
    )
    try:
        await consumer.run()
    finally:
        await consumer.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Platform control workers")
    parser.add_argument("service", choices=("directory", "stream"))
    return parser


def main(argv: list[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    selected = serve_directory if namespace.service == "directory" else serve_stream
    asyncio.run(selected())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
