"""Durable, per-run raw-source drain on the existing signed Relay path."""

import asyncio
import json
import logging
from dataclasses import dataclass

import httpx

from .contracts_v5 import CallbackAckV5
from .worker import CloudRelayError


@dataclass(frozen=True, repr=False)
class Upload:
    run_id: object
    seq: int
    revision: int
    body: bytes | None


def _isolate(connection, upload, code):
    changed = connection.execute(
        "update execution_worker.v5_callback_runs set upload_isolation_code=%s,upload_revision=upload_revision+1 "
        "where run_id=%s and upload_revision=%s and upload_isolation_code is null returning run_id",
        (code, upload.run_id, upload.revision),
    ).fetchone()
    if changed:
        logging.getLogger(__name__).warning(
            "v5 source upload isolated run_id=%s code=%s", upload.run_id, code
        )


def failed(store, upload):
    with store._connection() as connection:
        connection.execute(
            "update execution_worker.v5_callback_runs set upload_failures=least(upload_failures+1,1000),"
            "upload_revision=upload_revision+1,upload_next_at=clock_timestamp()+"
            "make_interval(secs=>least(30.0,0.25*power(2,least(upload_failures,7)))) "
            "where run_id=%s and upload_revision=%s and upload_isolation_code is null",
            (upload.run_id, upload.revision),
        )


def claim(store, worker_id):
    with store._connection() as connection:
        row = connection.execute(
            "select * from execution_worker.v5_callback_runs where worker_id=%s "
            "and upload_next_seq<=accepted_through and upload_next_at<=clock_timestamp() "
            "and upload_isolation_code is null "
            "order by upload_next_at,run_id limit 1 for update skip locked",
            (worker_id,),
        ).fetchone()
        if row is None:
            return None
        source = connection.execute(
            "select event_json from execution_worker.v5_callback_events where run_id=%s and seq=%s",
            (row["run_id"], row["upload_next_seq"]),
        ).fetchone()
        upload = Upload(
            row["run_id"],
            row["upload_next_seq"],
            row["upload_revision"] + 1,
            None if source is None else source["event_json"].encode("utf-8"),
        )
        connection.execute(
            "update execution_worker.v5_callback_runs set upload_revision=upload_revision+1,"
            "upload_next_at=clock_timestamp()+interval '30 seconds' where run_id=%s",
            (row["run_id"],),
        )
        if source is None:
            _isolate(connection, upload, "source_missing")
        return upload


def acknowledge(store, upload, response):
    try:
        if len(response.content) > 4096:
            raise ValueError
        raw = json.loads(response.content)
        if type(raw) is not dict or set(raw) != {
            "status",
            "runId",
            "acceptedThrough",
            "expectedSeq",
        }:
            raise ValueError
        ack = CallbackAckV5.model_validate_json(response.content, strict=True)
        if (
            ack.run_id != upload.run_id
            or response.status_code
            != (409 if ack.status in {"gap", "conflict"} else 200)
            or (
                ack.status in {"accepted", "duplicate"}
                and ack.accepted_through < upload.seq
            )
            or (ack.status == "accepted" and ack.accepted_through != upload.seq)
            or (ack.status == "gap" and ack.expected_seq >= upload.seq)
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        with store._connection() as connection:
            _isolate(connection, upload, "ack_invalid")
        return
    with store._connection() as connection:
        row = connection.execute(
            "select upload_revision from execution_worker.v5_callback_runs where run_id=%s for update",
            (upload.run_id,),
        ).fetchone()
        if row is None or row["upload_revision"] != upload.revision:
            return
        if ack.status == "conflict":
            _isolate(connection, upload, "ack_conflict")
            return
        if ack.status == "gap":
            if (
                connection.execute(
                    "select 1 from execution_worker.v5_callback_events where run_id=%s and seq=%s",
                    (upload.run_id, ack.expected_seq),
                ).fetchone()
                is None
            ):
                _isolate(connection, upload, "source_missing")
                return
            connection.execute(
                "update execution_worker.v5_callback_runs set upload_next_seq=%s,upload_revision=upload_revision+1,"
                "upload_failures=least(upload_failures+1,1000),upload_next_at=clock_timestamp()+"
                "make_interval(secs=>least(30.0,0.25*power(2,least(upload_failures,7)))) where run_id=%s",
                (ack.expected_seq, upload.run_id),
            )
            return
        connection.execute(
            "update execution_worker.v5_callback_runs set uploaded_through=greatest(uploaded_through,%s),"
            "upload_next_seq=%s,upload_next_at=clock_timestamp(),upload_revision=upload_revision+1,upload_failures=0 "
            "where run_id=%s and upload_revision=%s",
            (upload.seq, upload.seq + 1, upload.run_id, upload.revision),
        )


async def drain_once(store, cloud, worker_id):
    upload = await asyncio.to_thread(claim, store, worker_id)
    if upload is None:
        return False
    if upload.body is None:
        return True
    try:
        response = await cloud.post_v5_bytes(
            f"/api/v1/execution-worker/v5/runs/{upload.run_id}/events", upload.body
        )
    except (OSError, httpx.HTTPError, CloudRelayError):
        await asyncio.to_thread(failed, store, upload)
        return True
    await asyncio.to_thread(acknowledge, store, upload, response)
    return True
