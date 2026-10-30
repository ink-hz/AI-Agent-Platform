"""Published Web services, independent of the legacy execution catalog."""

from types import MappingProxyType
from typing import Literal, TypedDict


class WebEntry(TypedDict):
    kind: Literal["independent_service"]
    path: str


_WEB_PATHS = MappingProxyType({
    "ai-fae-agent": "/fae/",
    "ai-daq-fae-agent": "/daq/",
    "hr-bot": "/hr/",
    "voc": "/voc/",
    "ai-admin-agent": "/office/?view=services",
})


def web_entry_for(agent_id: str) -> WebEntry | None:
    """Project a fixed publication path for an already authorized card."""
    path = _WEB_PATHS.get(agent_id)
    if path is None:
        return None
    return {"kind": "independent_service", "path": path}
