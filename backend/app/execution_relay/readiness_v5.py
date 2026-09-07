"""Strict authenticated machine observations, not execution/stop evidence."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

REASONS = {
    "ready",
    "unavailable",
    "configuration_invalid",
    "capacity_busy",
    "store_unavailable",
    "stopping",
}


def callback_origin(value):
    if type(value) is not str:
        raise ValueError("v5 readiness invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or value != f"http://127.0.0.1:{parsed.port}"
    ):
        raise ValueError("v5 readiness invalid")
    return value


def parse_service(value):
    if type(value) is not dict or set(value) != {
        "contractVersion",
        "callbackOrigin",
        "durableTerminal",
        "healthy",
        "capacity",
        "reason",
        "config",
    }:
        raise ValueError("v5 readiness invalid")
    if (
        value["contractVersion"] != "core_chat_collaboration_v5"
        or type(value["durableTerminal"]) is not bool
        or type(value["healthy"]) is not bool
        or type(value["capacity"]) is not str
        or value["capacity"] not in {"available", "busy", "unavailable"}
        or type(value["reason"]) is not str
        or value["reason"] not in REASONS
    ):
        raise ValueError("v5 readiness invalid")
    config = value["config"]
    callback_origin(value["callbackOrigin"])
    if type(config) is not dict or set(config) != {
        "targetBot",
        "engine",
        "backend",
        "model",
        "compatibilityProfile",
        "toolPolicy",
        "configHash",
    }:
        raise ValueError("v5 readiness invalid")
    if (
        config["targetBot"] != "hr-bot"
        or config["engine"] != "claude"
        or config["backend"] != "pty"
    ):
        raise ValueError("v5 readiness invalid")
    for key in ("model", "toolPolicy", "compatibilityProfile"):
        if key == "compatibilityProfile" and config[key] is None:
            continue
        if type(config[key]) is not str or not re.fullmatch(
            r"[A-Za-z0-9._:/-]{1,128}", config[key]
        ):
            raise ValueError("v5 readiness invalid")
    expected = hashlib.sha256(
        json.dumps(
            {key: item for key, item in config.items() if key != "configHash"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if config["configHash"] != expected:
        raise ValueError("v5 readiness invalid")
    return value


def parse_observation(value):
    if type(value) is not dict or set(value) != {
        "version",
        "sampledAt",
        "callbackOrigin",
        "service",
        "receiverReady",
        "uploadReady",
        "handoffReady",
        "reason",
    }:
        raise ValueError("v5 readiness invalid")
    if (
        value["version"] != "hr_v5_readiness_v1"
        or type(value["reason"]) is not str
        or value["reason"] not in REASONS
    ):
        raise ValueError("v5 readiness invalid")
    callback_origin(value["callbackOrigin"])
    if type(value["sampledAt"]) is not str:
        raise ValueError("v5 readiness invalid")
    sampled = datetime.fromisoformat(value["sampledAt"].replace("Z", "+00:00"))
    if sampled.tzinfo is None:
        raise ValueError("v5 readiness invalid")
    if any(
        type(value[key]) is not bool
        for key in ("receiverReady", "uploadReady", "handoffReady")
    ):
        raise ValueError("v5 readiness invalid")
    if value["service"] is not None:
        parse_service(value["service"])
        if value["service"]["callbackOrigin"] != value["callbackOrigin"]:
            raise ValueError("v5 readiness invalid")
    return sampled.astimezone(timezone.utc)


def record_observation(relay, worker_id, value):
    sampled = parse_observation(value)
    with relay._connection() as connection:
        worker = connection.execute(
            "select v5_observation from platform_control.execution_workers where worker_id=%s "
            "and status='active' and 'hr-bot'=any(allowed_agent_ids) for update",
            (worker_id,),
        ).fetchone()
        if worker is None:
            raise PermissionError("v5 readiness forbidden")
        now = connection.execute("select clock_timestamp() as now").fetchone()["now"]
        if not now - timedelta(seconds=30) <= sampled <= now + timedelta(seconds=5):
            raise ValueError("v5 readiness expired")
        prior = worker["v5_observation"]
        if prior and datetime.fromisoformat(prior["sampledAt"]) >= sampled:
            return False
        service = value["service"]
        ready = bool(
            service
            and service["healthy"]
            and service["durableTerminal"]
            and service["capacity"] == "available"
            and value["receiverReady"]
            and value["uploadReady"]
            and value["handoffReady"]
        )
        stored = {
            **value,
            "sampledAt": sampled.isoformat(),
            "receivedAt": now.isoformat(),
            "expiresAt": min(
                sampled + timedelta(seconds=30), now + timedelta(seconds=30)
            ).isoformat(),
            "ready": ready,
        }
        connection.execute(
            "update platform_control.execution_workers set v5_observation=%s where worker_id=%s",
            (Jsonb(stored), worker_id),
        )
    return True
