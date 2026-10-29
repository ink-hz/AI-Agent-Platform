"""Private file configuration; machine credentials are never command arguments."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .models import SourceProfile


class CloudSyncConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CloudSyncConfig:
    replica_database_url_file: str
    replica_encryption_key_file: str
    identity_key_file: str
    sources: tuple[SourceProfile, ...]
    interval_seconds: int = 10
    include_verified_subject: bool = False


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CloudSyncConfigError("configuration_invalid")
        result[key] = value
    return result


def load_config(path_value: str | Path) -> CloudSyncConfig:
    descriptor = None
    try:
        path = Path(path_value)
        if not path.is_absolute():
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid() or metadata.st_size > 65536):
            raise ValueError
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = None
            value = json.load(stream, object_pairs_hook=_object)
        allowed = {"replica_database_url_file", "replica_encryption_key_file", "identity_key_file",
                   "sources", "interval_seconds", "include_verified_subject"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError
        for key in ("replica_database_url_file", "replica_encryption_key_file", "identity_key_file"):
            if not isinstance(value[key], str) or not Path(value[key]).is_absolute():
                raise ValueError
        interval = value.get("interval_seconds", 10)
        identity = value.get("include_verified_subject", False)
        if type(interval) is not int or not 5 <= interval <= 60 or type(identity) is not bool:
            raise ValueError
        profiles = value["sources"]
        if not isinstance(profiles, list) or not 1 <= len(profiles) <= 4:
            raise ValueError
        if any(not isinstance(item, dict) for item in profiles):
            raise ValueError
        sources = tuple(SourceProfile(**item) for item in profiles)
        if len({p.agent_id for p in sources}) != len(sources):
            raise ValueError
        return CloudSyncConfig(
            replica_database_url_file=value["replica_database_url_file"],
            replica_encryption_key_file=value["replica_encryption_key_file"],
            identity_key_file=value["identity_key_file"], sources=sources,
            interval_seconds=interval, include_verified_subject=identity,
        )
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise CloudSyncConfigError("configuration_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
