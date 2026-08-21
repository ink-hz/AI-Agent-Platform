from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import json
import logging
import os
from pathlib import Path
import random
import re
import secrets
import signal
import stat
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
from pydantic import ValidationError

from .metabot_client import MetaBotClient, MetaBotRuntimeMap
from .models import RelayEvent, RelayLease
from .worker_auth import WorkerRequestSigner
from .worker_store import WorkerStore


_API_PREFIX = "/api/v1/execution-worker"
_CALLBACK_BODY_LIMIT = 1_048_576
_CALLBACK_HEADER_LIMIT = 16_384
_CALLBACK_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_EVENT_TYPE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
_LOG = logging.getLogger("app.execution_relay.worker")


class CloudRelayError(RuntimeError):
    """Stable cloud-boundary failure without response or credential content."""

    def __init__(self) -> None:
        super().__init__("cloud relay request failed")


class WorkerRuntimeError(RuntimeError):
    """Stable runtime failure without job or event content."""

    def __init__(self) -> None:
        super().__init__("worker runtime failed")


class CallbackResult(Enum):
    ACCEPTED = 204
    INVALID = 400
    UNAUTHORIZED = 401
    CONFLICT = 409
    TOO_LARGE = 413


class ExponentialBackoff:
    def __init__(
        self,
        *,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._jitter = jitter
        self._attempt = 0

    def next_delay(self) -> float:
        base = _BACKOFF_SECONDS[min(self._attempt, len(_BACKOFF_SECONDS) - 1)]
        self._attempt += 1
        factor = float(self._jitter(0.8, 1.2))
        if not 0.8 <= factor <= 1.2:
            factor = 1.0
        return base * factor

    def reset(self) -> None:
        self._attempt = 0


class SignedCloudClient:
    def __init__(
        self,
        base_url: str,
        signer: WorkerRequestSigner,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        try:
            parsed = urlsplit(base_url)
            loopback_test_url = (
                parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            )
            if (
                not isinstance(base_url, str)
                or (parsed.scheme != "https" and not loopback_test_url)
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
                or not callable(getattr(signer, "sign", None))
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise WorkerRuntimeError() from None
        self._base_url = base_url.rstrip("/")
        self._signer = signer
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _body(value: Mapping[str, object]) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    async def _post(
        self,
        path: str,
        value: Mapping[str, object],
        *,
        accepted_statuses: frozenset[int] = frozenset({200}),
    ) -> httpx.Response:
        body = self._body(value)
        try:
            headers = self._signer.sign("POST", path, body)
            response = await self._client.request(
                "POST",
                self._base_url + path,
                content=body,
                headers={**headers, "Content-Type": "application/json"},
            )
            if response.status_code not in accepted_statuses:
                raise ValueError
            return response
        except Exception:
            raise CloudRelayError() from None

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, object]:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError
        return value

    async def lease(self) -> RelayLease | None:
        response = await self._post(
            f"{_API_PREFIX}/lease",
            {},
            accepted_statuses=frozenset({200, 204}),
        )
        if response.status_code == 204:
            if response.content:
                raise CloudRelayError()
            return None
        try:
            return RelayLease.model_validate(self._json_object(response))
        except (ValueError, ValidationError):
            raise CloudRelayError() from None

    async def heartbeat(self) -> tuple[UUID, ...]:
        response = await self._post(f"{_API_PREFIX}/heartbeat", {})
        try:
            value = self._json_object(response)
            if set(value) != {"cancel_requested_run_ids"} or not isinstance(
                value["cancel_requested_run_ids"], list
            ):
                raise ValueError
            return tuple(UUID(item) for item in value["cancel_requested_run_ids"])
        except (TypeError, ValueError):
            raise CloudRelayError() from None

    async def mark_dispatched(self, run_id: UUID) -> None:
        response = await self._post(f"{_API_PREFIX}/runs/{run_id}/dispatched", {})
        self._require_accepted(response)

    async def upload_events(
        self, run_id: UUID, events: Sequence[RelayEvent]
    ) -> None:
        if not events or any(event.run_id != run_id for event in events):
            raise CloudRelayError()
        response = await self._post(
            f"{_API_PREFIX}/runs/{run_id}/events",
            {"events": [event.model_dump(mode="json") for event in events]},
        )
        try:
            value = self._json_object(response)
            if (
                set(value) != {"accepted", "inserted"}
                or value["accepted"] != len(events)
                or isinstance(value["inserted"], bool)
                or not isinstance(value["inserted"], int)
                or not 0 <= value["inserted"] <= len(events)
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise CloudRelayError() from None

    async def finish(self, run_id: UUID, status: str) -> None:
        if status not in _TERMINAL_STATES:
            raise CloudRelayError()
        response = await self._post(
            f"{_API_PREFIX}/runs/{run_id}/terminal", {"status": status}
        )
        self._require_accepted(response)

    def _require_accepted(self, response: httpx.Response) -> None:
        try:
            value = self._json_object(response)
            if value != {"status": "accepted"}:
                raise ValueError
        except (TypeError, ValueError):
            raise CloudRelayError() from None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass
class _RunContext:
    agent_id: str
    metabot_accepted: bool
    cloud_dispatched: bool
    terminal_status: str | None = None
    cancel_sent: bool = False


class WorkerRuntime:
    def __init__(
        self,
        *,
        worker_id: str,
        cloud: Any,
        store: WorkerStore,
        runtime_map: MetaBotRuntimeMap,
        metabot: MetaBotClient,
        callback_port: int,
        heartbeat_interval: float = 15.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        jitter: Callable[[float, float], float] = random.uniform,
        logger: logging.Logger = _LOG,
    ) -> None:
        if (
            not isinstance(worker_id, str)
            or not worker_id
            or isinstance(callback_port, bool)
            or not isinstance(callback_port, int)
            or not 0 <= callback_port <= 65535
            or heartbeat_interval <= 0
        ):
            raise WorkerRuntimeError()
        self.worker_id = worker_id
        self.cloud = cloud
        self.store = store
        self.runtime_map = runtime_map
        self.metabot = metabot
        self.callback_port = callback_port
        self.heartbeat_interval = float(heartbeat_interval)
        self.sleep = sleep
        self.token_factory = token_factory
        self.jitter = jitter
        self.logger = logger
        self.stop_event = asyncio.Event()
        self.callback_ready = asyncio.Event()
        self._runs: dict[UUID, _RunContext] = {}
        self._operation_lock = asyncio.Lock()

    def stop(self) -> None:
        self.stop_event.set()

    def recover_run(
        self,
        run_id: UUID,
        agent_id: str,
        *,
        terminal_status: str | None = None,
        cloud_dispatched: bool = True,
        metabot_accepted: bool = True,
    ) -> None:
        if (
            not isinstance(run_id, UUID)
            or not isinstance(agent_id, str)
            or (terminal_status is not None and terminal_status not in _TERMINAL_STATES)
        ):
            raise WorkerRuntimeError()
        self._runs.setdefault(
            run_id,
            _RunContext(
                agent_id=agent_id,
                metabot_accepted=metabot_accepted,
                cloud_dispatched=cloud_dispatched,
                terminal_status=terminal_status,
            ),
        )

    def _database_recovery_rows(self) -> tuple[tuple[UUID, str, str], ...]:
        loader = getattr(self.store, "recoverable_runs", None)
        if callable(loader):
            return tuple(loader())
        connection_factory = getattr(self.store, "_connection", None)
        if not callable(connection_factory):
            return ()
        with connection_factory() as connection:
            rows = connection.execute(
                "select run_id,agent_id,state from execution_worker.local_runs "
                "where state<>'leased' order by leased_at,run_id"
            ).fetchall()
        return tuple(
            (row["run_id"], row["agent_id"], row["state"]) for row in rows
        )

    async def recover_local_state(self) -> None:
        try:
            rows = await asyncio.to_thread(self._database_recovery_rows)
            for run_id, agent_id, state in rows:
                if (
                    not isinstance(run_id, UUID)
                    or not isinstance(agent_id, str)
                    or not isinstance(state, str)
                ):
                    raise WorkerRuntimeError()
                if state == "dispatching":
                    await self._store_call("mark_terminal", run_id, "interrupted")
                    self.recover_run(
                        run_id,
                        agent_id,
                        terminal_status="interrupted",
                        cloud_dispatched=False,
                        metabot_accepted=False,
                    )
                elif state in {"dispatched", "running"}:
                    self.recover_run(
                        run_id, agent_id, cloud_dispatched=False
                    )
                elif state in _TERMINAL_STATES:
                    self.recover_run(
                        run_id,
                        agent_id,
                        terminal_status=state,
                        cloud_dispatched=state in {"cancelled", "interrupted"},
                        metabot_accepted=state in {"completed", "failed"},
                    )
                else:
                    raise WorkerRuntimeError()
        except Exception as error:
            self._safe_log("recovery_failed", error)
            raise WorkerRuntimeError() from None

    def _safe_log(
        self,
        state: str,
        error: BaseException,
        *,
        run_id: UUID | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.logger.warning(
            "worker_id=%s run_id=%s agent_id=%s state=%s error_class=%s",
            self.worker_id,
            str(run_id) if run_id is not None else "-",
            agent_id or "-",
            state,
            type(error).__name__,
        )

    async def _store_call(self, method: str, *args: object) -> Any:
        return await asyncio.to_thread(getattr(self.store, method), *args)

    async def lease_once(self) -> bool:
        async with self._operation_lock:
            if self._runs:
                return True
            try:
                lease = await self.cloud.lease()
            except Exception as error:
                self._safe_log("lease_failed", error)
                return False
            if lease is None:
                return True
            run_id = lease.payload.run_id
            agent_id = lease.payload.agent_id
            try:
                port = self.runtime_map.port_for(agent_id)
                token = self.token_factory()
                if (
                    not isinstance(token, str)
                    or _CALLBACK_TOKEN.fullmatch(token) is None
                ):
                    raise WorkerRuntimeError()
                await self._store_call("record_lease", lease, port, token)
                await self._store_call("mark_dispatching", run_id)
                context = _RunContext(agent_id, False, False)
                self._runs[run_id] = context
                if lease.cancel_requested:
                    await self._store_call("mark_terminal", run_id, "cancelled")
                    context.terminal_status = "cancelled"
                    return True
                callback_url = (
                    f"http://127.0.0.1:{self.callback_port}/callbacks/{run_id}/{token}"
                )
                await asyncio.to_thread(
                    self.metabot.start_run, lease.payload, callback_url
                )
                context.metabot_accepted = True
                await self._store_call("mark_dispatched", run_id)
                try:
                    await self.cloud.mark_dispatched(run_id)
                    context.cloud_dispatched = True
                except Exception as error:
                    self._safe_log(
                        "cloud_dispatch_ack_failed",
                        error,
                        run_id=run_id,
                        agent_id=agent_id,
                    )
                    return False
                return True
            except Exception as error:
                self._safe_log(
                    "dispatch_interrupted",
                    error,
                    run_id=run_id,
                    agent_id=agent_id,
                )
                context = self._runs.get(run_id)
                if context is not None and context.terminal_status is None:
                    try:
                        await self._store_call("mark_terminal", run_id, "interrupted")
                        context.terminal_status = "interrupted"
                    except Exception as store_error:
                        self._safe_log(
                            "interrupt_commit_failed",
                            store_error,
                            run_id=run_id,
                            agent_id=agent_id,
                        )
                return False

    async def upload_once(self) -> bool:
        async with self._operation_lock:
            success = True
            for run_id, context in tuple(self._runs.items()):
                try:
                    if context.metabot_accepted and not context.cloud_dispatched:
                        await self.cloud.mark_dispatched(run_id)
                        context.cloud_dispatched = True
                    events = await self._store_call("contiguous_outbox", run_id, 100)
                    if events:
                        await self.cloud.upload_events(run_id, events)
                        await self._store_call("mark_delivered", run_id, events[-1].seq)
                    remaining = await self._store_call("contiguous_outbox", run_id, 1)
                    if context.terminal_status is not None and not remaining:
                        await self.cloud.finish(run_id, context.terminal_status)
                        del self._runs[run_id]
                except Exception as error:
                    success = False
                    self._safe_log(
                        "upload_failed",
                        error,
                        run_id=run_id,
                        agent_id=context.agent_id,
                    )
            return success

    async def heartbeat_once(self) -> bool:
        try:
            cancel_ids = await self.cloud.heartbeat()
        except Exception as error:
            self._safe_log("heartbeat_failed", error)
            return False
        for run_id in cancel_ids:
            context = self._runs.get(run_id)
            if context is None or context.terminal_status is not None:
                continue
            try:
                if context.metabot_accepted and not context.cancel_sent:
                    await asyncio.to_thread(
                        self.metabot.cancel_run, run_id, context.agent_id
                    )
                    context.cancel_sent = True
                await self._store_call("mark_terminal", run_id, "cancelled")
                context.terminal_status = "cancelled"
            except Exception as error:
                self._safe_log(
                    "cancel_failed",
                    error,
                    run_id=run_id,
                    agent_id=context.agent_id,
                )
                return False
        return True

    async def accept_callback(
        self, run_id: UUID, token: str, body: bytes
    ) -> CallbackResult:
        if not isinstance(body, bytes) or len(body) > _CALLBACK_BODY_LIMIT:
            return CallbackResult.TOO_LARGE
        if (
            not isinstance(run_id, UUID)
            or not isinstance(token, str)
            or _CALLBACK_TOKEN.fullmatch(token) is None
        ):
            return CallbackResult.UNAUTHORIZED
        try:
            if not await self._store_call("callback_token_matches", run_id, token):
                return CallbackResult.UNAUTHORIZED
            value = json.loads(body.decode("utf-8"))
            if not isinstance(value, dict) or set(value) != {
                "run_id",
                "seq",
                "event_type",
                "created_at",
                "payload",
            }:
                return CallbackResult.INVALID
            event = RelayEvent.model_validate(value)
            if (
                event.run_id != run_id
                or _EVENT_TYPE.fullmatch(event.event_type) is None
                or event.created_at.tzinfo is None
                or event.created_at.utcoffset() is None
            ):
                return CallbackResult.INVALID
            inserted = await self._store_call("append_event", event)
            terminal = self._terminal_status(event)
            if terminal is not None:
                context = self._runs.get(run_id)
                if context is None:
                    context = _RunContext("", True, True)
                    self._runs[run_id] = context
                if context.terminal_status is None:
                    await self._store_call("mark_terminal", run_id, terminal)
                    context.terminal_status = terminal
            if inserted and run_id not in self._runs:
                self._runs[run_id] = _RunContext("", True, True)
            return CallbackResult.ACCEPTED
        except (
            UnicodeError,
            json.JSONDecodeError,
            ValidationError,
            TypeError,
            ValueError,
        ):
            return CallbackResult.INVALID
        except Exception:
            return CallbackResult.CONFLICT

    @staticmethod
    def _terminal_status(event: RelayEvent) -> str | None:
        status = event.payload.get("status")
        terminal_type = event.event_type in {
            "terminal",
            "run.terminal",
            "run.completed",
            "run.failed",
            "run.cancelled",
            "run.interrupted",
        }
        if terminal_type and isinstance(status, str) and status in _TERMINAL_STATES:
            return status
        suffix = event.event_type.removeprefix("run.")
        if suffix in _TERMINAL_STATES:
            return suffix
        return None

    async def interrupt_active(self) -> None:
        async with self._operation_lock:
            for run_id, context in tuple(self._runs.items()):
                if context.terminal_status is not None:
                    continue
                if context.metabot_accepted and not context.cancel_sent:
                    try:
                        await asyncio.to_thread(
                            self.metabot.cancel_run, run_id, context.agent_id
                        )
                        context.cancel_sent = True
                    except Exception as error:
                        self._safe_log(
                            "shutdown_cancel_failed",
                            error,
                            run_id=run_id,
                            agent_id=context.agent_id,
                        )
                try:
                    await self._store_call("mark_terminal", run_id, "interrupted")
                    context.terminal_status = "interrupted"
                except Exception as error:
                    self._safe_log(
                        "shutdown_commit_failed",
                        error,
                        run_id=run_id,
                        agent_id=context.agent_id,
                    )

    async def pause(self, seconds: float) -> None:
        if self.stop_event.is_set():
            return
        if self.sleep is asyncio.sleep:
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                pass
        else:
            await self.sleep(seconds)


async def lease_loop(runtime: WorkerRuntime) -> None:
    backoff = ExponentialBackoff(jitter=runtime.jitter)
    while not runtime.stop_event.is_set():
        succeeded = await runtime.lease_once()
        if succeeded:
            backoff.reset()
            await runtime.pause(0.25)
        else:
            await runtime.pause(backoff.next_delay())


async def upload_loop(runtime: WorkerRuntime) -> None:
    backoff = ExponentialBackoff(jitter=runtime.jitter)
    while not runtime.stop_event.is_set():
        succeeded = await runtime.upload_once()
        if succeeded:
            backoff.reset()
            await runtime.pause(0.25)
        else:
            await runtime.pause(backoff.next_delay())


async def heartbeat_loop(runtime: WorkerRuntime) -> None:
    backoff = ExponentialBackoff(jitter=runtime.jitter)
    while not runtime.stop_event.is_set():
        succeeded = await runtime.heartbeat_once()
        if succeeded:
            backoff.reset()
            await runtime.pause(runtime.heartbeat_interval)
        else:
            await runtime.pause(backoff.next_delay())


async def _send_callback_response(
    writer: asyncio.StreamWriter, result: CallbackResult
) -> None:
    reason = {
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        409: "Conflict",
        413: "Content Too Large",
    }[result.value]
    writer.write(
        f"HTTP/1.1 {result.value} {reason}\r\n"
        "Content-Length: 0\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n".encode("ascii")
    )
    await writer.drain()


async def _handle_callback_connection(
    runtime: WorkerRuntime,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    result = CallbackResult.INVALID
    try:
        header_block = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10.0)
        if len(header_block) > _CALLBACK_HEADER_LIMIT:
            raise ValueError
        lines = header_block[:-4].split(b"\r\n")
        request_line = lines[0].decode("ascii").split(" ")
        if (
            len(request_line) != 3
            or request_line[0] != "POST"
            or request_line[2] != "HTTP/1.1"
        ):
            raise ValueError
        target = request_line[1]
        if "?" in target or "#" in target:
            raise ValueError
        parts = target.split("/")
        if len(parts) != 4 or parts[1] != "callbacks":
            raise ValueError
        run_id = UUID(parts[2])
        if str(run_id) != parts[2]:
            raise ValueError
        token = parts[3]
        headers: dict[str, str] = {}
        for raw_line in lines[1:]:
            name, separator, value = raw_line.partition(b":")
            if not separator:
                raise ValueError
            key = name.decode("ascii").strip().lower()
            if not key or key in headers:
                raise ValueError
            headers[key] = value.decode("ascii").strip()
        if "transfer-encoding" in headers:
            raise ValueError
        content_type = (
            headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if content_type != "application/json":
            raise ValueError
        raw_length = headers.get("content-length")
        if raw_length is None or not raw_length.isdigit():
            raise ValueError
        length = int(raw_length)
        if length > _CALLBACK_BODY_LIMIT:
            result = CallbackResult.TOO_LARGE
        else:
            body = await asyncio.wait_for(reader.readexactly(length), 10.0)
            result = await runtime.accept_callback(run_id, token, body)
    except (
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        asyncio.TimeoutError,
    ):
        result = CallbackResult.INVALID
    except (UnicodeError, ValueError):
        result = CallbackResult.INVALID
    try:
        await _send_callback_response(writer, result)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def callback_server(runtime: WorkerRuntime) -> None:
    server = await asyncio.start_server(
        lambda reader, writer: _handle_callback_connection(
            runtime, reader, writer
        ),
        host="127.0.0.1",
        port=runtime.callback_port,
        limit=_CALLBACK_HEADER_LIMIT + 4,
    )
    socket = server.sockets[0]
    runtime.callback_port = int(socket.getsockname()[1])
    runtime.callback_ready.set()
    async with server:
        await runtime.stop_event.wait()


async def run_worker(runtime: WorkerRuntime) -> None:
    await runtime.recover_local_state()
    loop = asyncio.get_running_loop()
    shutdown_started = asyncio.Event()

    async def shutdown() -> None:
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        await runtime.interrupt_active()
        await runtime.upload_once()
        runtime.stop()

    def request_shutdown() -> None:
        asyncio.create_task(shutdown())

    installed: list[signal.Signals] = []
    for name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(name, request_shutdown)
            installed.append(name)
        except (NotImplementedError, RuntimeError):
            pass
    callback_task = asyncio.create_task(callback_server(runtime))
    ready_task = asyncio.create_task(runtime.callback_ready.wait())
    done, _pending = await asyncio.wait(
        {callback_task, ready_task}, return_when=asyncio.FIRST_COMPLETED
    )
    if callback_task in done:
        ready_task.cancel()
        await asyncio.gather(ready_task, return_exceptions=True)
        await callback_task
    ready_task.cancel()
    await asyncio.gather(ready_task, return_exceptions=True)
    tasks = (
        callback_task,
        asyncio.create_task(lease_loop(runtime)),
        asyncio.create_task(upload_loop(runtime)),
        asyncio.create_task(heartbeat_loop(runtime)),
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        runtime.stop()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for name in installed:
            loop.remove_signal_handler(name)
        close = getattr(runtime.cloud, "aclose", None)
        if callable(close):
            await close()


def _owner_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError
        parent = candidate.parent.stat()
        current = candidate.stat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.geteuid()
            or not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o600
            or current.st_uid != os.geteuid()
            or current.st_size > 16_384
        ):
            raise ValueError
        raw = candidate.read_bytes()
        if len(raw) == 32:
            return Ed25519PrivateKey.from_private_bytes(raw)
        key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError
        return key
    except Exception:
        raise WorkerRuntimeError() from None


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise WorkerRuntimeError()
    return value


def build_runtime_from_environment() -> WorkerRuntime:
    try:
        worker_id = _required_environment("PLATFORM_WORKER_ID")
        key_id = _required_environment("PLATFORM_WORKER_KEY_ID")
        private_key = _owner_private_key(
            Path(_required_environment("PLATFORM_WORKER_PRIVATE_KEY_FILE"))
        )
        store = WorkerStore.from_dsn_file(
            Path(_required_environment("PLATFORM_WORKER_DATABASE_URL_FILE"))
        )
        callback_port = int(_required_environment("PLATFORM_WORKER_CALLBACK_PORT"))
        runtime_map = MetaBotRuntimeMap.from_contract(
            Path(_required_environment("PLATFORM_METABOT_RUNTIME_CONTRACT"))
        )
        metabot = MetaBotClient(
            runtime_map,
            Path(_required_environment("PLATFORM_METABOT_API_SECRET_FILE")),
        )
        signer = WorkerRequestSigner(worker_id, key_id, private_key)
        cloud = SignedCloudClient(
            _required_environment("PLATFORM_WORKER_CLOUD_URL"), signer
        )
        return WorkerRuntime(
            worker_id=worker_id,
            cloud=cloud,
            store=store,
            runtime_map=runtime_map,
            metabot=metabot,
            callback_port=callback_port,
        )
    except WorkerRuntimeError:
        raise
    except Exception:
        raise WorkerRuntimeError() from None


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker(build_runtime_from_environment()))
    except (KeyboardInterrupt, WorkerRuntimeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
