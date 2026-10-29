from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.cloud_replica.models import RawSession

SOURCE_IDENTITIES = {
    'fae': ('ai-fae-agent', 'fae', 'public'),
    'admin': ('ai-admin-agent', 'admin', 'public'),
    'daq': ('ai-daq-fae-agent', 'fae', 'daq_fae'),
    'voc': ('voc', 'voc', 'voc_raw/voc_core'),
}


class SourceReadError(ValueError):
    """A payload-free stable failure code; never include driver/credential text."""


@dataclass(frozen=True, slots=True)
class SourceProfile:
    agent_id: str
    kind: str
    dsn_file: str
    expected_database: str
    schema: str = 'public'

    def __post_init__(self) -> None:
        expected = SOURCE_IDENTITIES.get(self.kind)
        if (expected is None or self.agent_id != expected[0] or self.schema != expected[2]
                or not isinstance(self.expected_database, str) or not self.expected_database.strip()
                or not isinstance(self.dsn_file, str) or not Path(self.dsn_file).is_absolute()):
            raise ValueError('source_profile_invalid')


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    agent_id: str
    source_kind: str
    source_observed_at: datetime
    sessions: tuple[RawSession, ...]
    complete: bool = True
    elapsed_ms: int = 0
