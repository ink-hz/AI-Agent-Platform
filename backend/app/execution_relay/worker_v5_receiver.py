"""Opt-in v5 transport evidence; dispatch authentication belongs to the caller."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from .contracts_v5 import CallbackAckV5, parse_v5_command, parse_v5_event


@dataclass(frozen=True)
class TrustedV5CallbackBinding:
    worker_id: str
    command_id: UUID
    run_id: UUID
    attempt_id: UUID
    command_hash: str
    launch_lease_epoch: int
    transport_lease_epoch: int
    callback_origin: str


def register(store, command: dict, binding: TrustedV5CallbackBinding) -> None:
    """Internal trusted-dispatch seam, never exposed over callback HTTP."""
    parsed = parse_v5_command(command)
    url = urlsplit(parsed.event_callback_url)
    if (
        not isinstance(binding, TrustedV5CallbackBinding)
        or not binding.worker_id
        or type(binding.launch_lease_epoch) is not int
        or not 1 <= binding.launch_lease_epoch <= parsed.lease_epoch
        or (
            parsed.command_id,
            parsed.run_id,
            parsed.attempt_id,
            parsed.command_hash,
            parsed.lease_epoch,
        )
        != (
            binding.command_id,
            binding.run_id,
            binding.attempt_id,
            binding.command_hash,
            binding.transport_lease_epoch,
        )
        or f"{url.scheme}://{url.netloc}" != binding.callback_origin
    ):
        raise ValueError("v5 registration invalid")
    token_hash = hashlib.sha256(url.path.split("/")[3].encode()).digest()
    with store._connection() as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (str(parsed.run_id),),
        )
        if (
            connection.execute(
                "SELECT 1 FROM execution_worker.local_runs WHERE run_id=%s",
                (parsed.run_id,),
            ).fetchone()
            is not None
        ):
            raise ValueError("v5 registration conflict")
        preflight(connection)
        existing = connection.execute(
            "SELECT * FROM execution_worker.v5_callback_runs WHERE run_id=%s FOR UPDATE",
            (parsed.run_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["command_id"],
                existing["attempt_id"],
                existing["command_hash"],
                existing["worker_id"],
                existing["launch_lease_epoch"],
                existing["callback_origin"],
            ) != (
                parsed.command_id,
                parsed.attempt_id,
                parsed.command_hash,
                binding.worker_id,
                binding.launch_lease_epoch,
                binding.callback_origin,
            ) or parsed.lease_epoch < existing["transport_lease_epoch"]:
                raise ValueError("v5 registration conflict")
            connection.execute(
                "UPDATE execution_worker.v5_callback_runs SET transport_lease_epoch=%s,token_hash=%s WHERE run_id=%s",
                (parsed.lease_epoch, token_hash, parsed.run_id),
            )
            return
        connection.execute(
            "INSERT INTO execution_worker.v5_callback_runs "
            "(run_id,command_id,attempt_id,command_hash,worker_id,launch_lease_epoch,transport_lease_epoch,callback_origin,token_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                parsed.run_id,
                parsed.command_id,
                parsed.attempt_id,
                parsed.command_hash,
                binding.worker_id,
                binding.launch_lease_epoch,
                parsed.lease_epoch,
                binding.callback_origin,
                token_hash,
            ),
        )


def registered(store, run_id: UUID) -> bool:
    with store._connection() as connection:
        preflight(connection)
        return (
            connection.execute(
                "SELECT 1 FROM execution_worker.v5_callback_runs WHERE run_id=%s",
                (run_id,),
            ).fetchone()
            is not None
        )


def preflight(connection) -> None:
    metadata = connection.execute(
        "SELECT version FROM execution_worker.v5_callback_metadata WHERE singleton"
    ).fetchall()
    if len(metadata) != 1 or metadata[0]["version"] != 1:
        raise RuntimeError("v5 receiver unavailable")


def accept(
    store, worker_id: str, run_id: UUID, token: str, body: bytes
) -> CallbackAckV5:
    candidate = hashlib.sha256(token.encode()).digest()
    with store._connection() as connection:
        row = connection.execute(
            "SELECT * FROM execution_worker.v5_callback_runs WHERE run_id=%s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if (
            row is None
            or row["worker_id"] != worker_id
            or not hmac.compare_digest(bytes(row["token_hash"]), candidate)
        ):
            raise PermissionError("v5 callback unauthorized")
        raw = json.loads(body)
        event = parse_v5_event(raw)
        if (event.run_id, event.command_id, event.attempt_id, event.lease_epoch) != (
            run_id,
            row["command_id"],
            row["attempt_id"],
            row["launch_lease_epoch"],
        ):
            raise ValueError("v5 event identity invalid")
        cursor = row["accepted_through"]

        def ack(status):
            return CallbackAckV5(
                status=status,
                run_id=run_id,
                accepted_through=cursor,
                expected_seq=cursor + 1,
            )

        original = connection.execute(
            "SELECT event_json FROM execution_worker.v5_callback_events WHERE run_id=%s AND seq=%s",
            (run_id, event.seq),
        ).fetchone()
        if original is not None:
            return ack(
                "duplicate" if json.loads(original["event_json"]) == raw else "conflict"
            )
        if (
            row["terminal_seq"] is not None
            or event.seq <= cursor
            or event.seq >= 9007199254740991
        ):
            return ack("conflict")
        if event.seq != cursor + 1:
            return ack("gap")
        connection.execute(
            "INSERT INTO execution_worker.v5_callback_events(run_id,seq,event_type,event_json) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (run_id, event.seq, event.event_type, body.decode("utf-8")),
        )
        connection.execute(
            "UPDATE execution_worker.v5_callback_runs SET accepted_through=%s,terminal_seq=%s WHERE run_id=%s",
            (
                event.seq,
                event.seq
                if event.event_type not in {"raw_progress", "run_heartbeat"}
                else None,
                run_id,
            ),
        )
        return CallbackAckV5(
            status="accepted",
            run_id=run_id,
            accepted_through=event.seq,
            expected_seq=event.seq + 1,
        )
