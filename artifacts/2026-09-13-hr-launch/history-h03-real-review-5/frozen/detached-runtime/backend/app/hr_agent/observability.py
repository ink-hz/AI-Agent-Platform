"""Strict ordinary logs. Sensitive content has no representable field here."""

from __future__ import annotations

import json
import logging

from .types import OrdinaryLogRecord, validate_contract

_logger = logging.getLogger("app.hr_agent")


def emit_log(record: OrdinaryLogRecord) -> None:
    """Validate and emit one compact record without exceptions or extras."""
    safe = validate_contract("OrdinaryLogRecord", record)
    _logger.info(json.dumps(safe, sort_keys=True, separators=(",", ":")))


def guard_dependency_debug_logging() -> None:
    for name in ("httpx", "httpcore", "hpack", "h2"):
        logging.getLogger(name).setLevel(logging.WARNING)
