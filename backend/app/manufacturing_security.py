"""Workflow protocol denials before library authentication, via the existing host sink."""

import json
import logging

from .manufacturing_paths import WORKFLOW_AUTH_ROUTES

_LOG = logging.getLogger("manufacturing_platform.workflow.security")
_ROUTES = frozenset(path for _, path in WORKFLOW_AUTH_ROUTES)


def log_workflow_protocol_denial(status: int, method: str, route: str | None) -> None:
    event = {
        "event": "workflow_denied",
        "status": status,
        "code": "HOST_PROTOCOL_DENIED",
        "method": method
        if method in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
        else "OTHER",
        "route": route
        if route in _ROUTES
        else "/api/v1/manufacturing/workflow/<unlisted>",
        "actor_id": None,
    }
    _LOG.warning(
        "%s",
        json.dumps(event, separators=(",", ":")),
        extra={"workflow_security": event},
    )
