from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import stat
import sys
from typing import BinaryIO, Callable, Sequence

from app.local_secrets import read_secret_file

from .crypto import BatchSigner, BatchVerifier, FieldCipher, read_key_file
from .exporter import ReplicaExporter
from .protocol import BatchLimits, decode_and_verify_batch
from .sanitize import SanitizationPolicy
from .source import ReplicaSource
from .store import ReplicaStore


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError("replica export configuration unavailable")
    return value


def _starting_watermark(state_path: Path, fallback: datetime) -> datetime:
    if not state_path.exists():
        return fallback
    try:
        metadata = state_path.lstat()
        if (
            state_path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError
        value = json.loads(state_path.read_text(encoding="utf-8"))
        watermark = value["upper_watermark"]
        if not isinstance(watermark, str) or not watermark.endswith("Z"):
            raise ValueError
        return datetime.fromisoformat(watermark[:-1] + "+00:00").astimezone(UTC)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise RuntimeError("replica export state unavailable") from None


def _export(clock: Callable[[], datetime]) -> int:
    through = clock().astimezone(UTC)
    database_url = read_secret_file(
        _required_environment("PLATFORM_REPLICA_SOURCE_DATABASE_URL_FILE")
    )
    state_path = Path(
        _required_environment("PLATFORM_REPLICA_EXPORT_STATE_PATH")
    )
    exporter = ReplicaExporter(
        source=ReplicaSource(database_url),
        policy=SanitizationPolicy.from_private_file(
            _required_environment("PLATFORM_REPLICA_SANITIZER_DICTIONARY_FILE")
        ),
        identity_key=read_key_file(
            _required_environment("PLATFORM_REPLICA_IDENTITY_KEY_FILE"),
            expected_size=32,
        ),
        signer=BatchSigner.from_private_key_file(
            _required_environment("PLATFORM_REPLICA_SIGNING_PRIVATE_KEY_FILE")
        ),
        source_instance_id=_required_environment(
            "PLATFORM_REPLICA_SOURCE_INSTANCE_ID"
        ),
        state_path=state_path,
        queue_dir=_required_environment("PLATFORM_REPLICA_EXPORT_QUEUE_DIR"),
        clock=lambda: through,
    )
    after = _starting_watermark(state_path, through - timedelta(days=365))
    result = exporter.export_batch(after=after, through=through, limit=100)
    print(
        json.dumps(
            {
                "status": "queued",
                "sequence": result.sequence,
                "record_count": result.record_count,
                "lower_watermark": result.lower_watermark.isoformat(),
                "upper_watermark": result.upper_watermark.isoformat(),
                "digest": result.digest,
            },
            sort_keys=True,
        )
    )
    return 0


def _store_from_environment() -> ReplicaStore:
    database_url = read_secret_file(
        _required_environment("PLATFORM_REPLICA_DATABASE_URL_FILE")
    )
    encryption_key = read_key_file(
        _required_environment("PLATFORM_REPLICA_ENCRYPTION_KEY_FILE"),
        expected_size=32,
    )
    return ReplicaStore(database_url, cipher=FieldCipher(encryption_key))


def _verifier_from_environment() -> BatchVerifier:
    return BatchVerifier.from_public_key_file(
        _required_environment("PLATFORM_REPLICA_SIGNING_PUBLIC_KEY_FILE")
    )


def _import(input_stream: BinaryIO) -> int:
    batch = decode_and_verify_batch(
        input_stream,
        _verifier_from_environment(),
        BatchLimits(),
    )
    result = _store_from_environment().import_batch(batch)
    print(
        json.dumps(
            {
                "status": result.status,
                "sequence": result.sequence,
                "record_count": result.record_count,
                "digest": result.digest,
            },
            sort_keys=True,
        )
    )
    return 0


def _retention(clock: Callable[[], datetime], dry_run: bool) -> int:
    result = _store_from_environment().expire(
        now=clock().astimezone(UTC), dry_run=dry_run
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "dry_run": result.dry_run,
                "session_count": result.session_count,
                "agent_count": result.agent_count,
            },
            sort_keys=True,
        )
    )
    return 0


def _migrate() -> int:
    _store_from_environment().migrate()
    print('{"status":"migrated"}')
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    input_stream: BinaryIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="platform-cloud-replica")
    parser.add_argument(
        "command", choices=("export", "import", "retention", "migrate")
    )
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "export":
            return _export(clock)
        if arguments.command == "import":
            return _import(input_stream or sys.stdin.buffer)
        if arguments.command == "retention":
            return _retention(clock, arguments.dry_run)
        if arguments.command == "migrate":
            return _migrate()
    except Exception:
        error = f"{arguments.command}_failed"
        print(
            json.dumps({"error": error, "status": "failed"}, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
