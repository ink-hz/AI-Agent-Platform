"""Cloud-only source publication; config contains paths, never inline credentials."""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from datetime import UTC, datetime

from app.cloud_replica.crypto import FieldCipher, read_key_file
from app.cloud_replica.sanitize import SanitizationPolicy

from .config import load_config
from .health import healthy_sources
from .sources import _read_dsn
from .store import CloudSyncStore
from .worker import CloudSyncWorker


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="platform-cloud-agent-sync")
    parser.add_argument("command", choices=("once", "run", "status", "health"))
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        store = CloudSyncStore(
            _read_dsn(config.replica_database_url_file),
            cipher=FieldCipher(read_key_file(config.replica_encryption_key_file, expected_size=32)),
        )
        if args.command == "status":
            print(json.dumps(store.source_statuses(), default=str, sort_keys=True))
            return 0
        if args.command == "health":
            healthy = healthy_sources(store.source_statuses(), config.sources,
                                      now=datetime.now(UTC), max_age_seconds=max(60, config.interval_seconds * 3))
            print(json.dumps({"healthy": healthy, "agents": [p.agent_id for p in config.sources]}))
            return 0 if healthy else 1
        worker = CloudSyncWorker(
            store=store,
            identity_key=read_key_file(config.identity_key_file, expected_size=32),
            policy=SanitizationPolicy(), include_verified_subject=config.include_verified_subject,
        )
        if args.command == "once":
            results = [worker.run_once(p) for p in config.sources]
            return int(any(result is None for result in results))
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *unused: stop.set())
        signal.signal(signal.SIGINT, lambda *unused: stop.set())
        worker.run(config.sources, interval_seconds=config.interval_seconds, stop=stop)
        return 0
    except Exception:  # noqa: BLE001 -- CLI boundary excludes credentials from all failure output
        print("CLOUD_AGENT_SYNC_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
