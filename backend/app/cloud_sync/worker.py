"""Independent bounded source polling; publication does not execute an Agent."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass, replace
from datetime import UTC, datetime, timedelta

from app.cloud_replica.exporter import build_session_record
from app.cloud_replica.sanitize import SanitizationPolicy, sanitize_session

from .models import SOURCE_IDENTITIES, SourceProfile, SourceReadError
from .sources import DatabaseAgentSource
from .store import PublishResult


class CloudSyncWorker:
    def __init__(self, *, store, identity_key: bytes, policy: SanitizationPolicy,
                 include_verified_subject: bool = False,
                 source_factory: Callable = DatabaseAgentSource,
                 clock: Callable = lambda: datetime.now(UTC),
                 emit: Callable[[dict], None] | None = None):
        if not isinstance(identity_key, bytes) or len(identity_key) != 32:
            raise ValueError("identity_key_invalid")
        if type(include_verified_subject) is not bool:
            raise ValueError("identity_mode_invalid")
        self.store = store
        self._identity_key = identity_key
        self.policy = policy
        self.include_verified_subject = include_verified_subject
        self._source_factory, self._clock = source_factory, clock
        self._emit = emit or (lambda value: print(json.dumps(value, sort_keys=True), flush=True))

    def run_once(self, profile: SourceProfile):
        attempted = self._clock()
        stage = "publication_unavailable"
        try:
            with self.store.observation_cycle(profile.agent_id) as lease:
                if lease is None:
                    self._emit({"event": "cloud_sync_busy", "agent_id": profile.agent_id})
                    return PublishResult("busy", 0, 0, 0, 0)
                stage = "source_unavailable"
                snapshot = self._source_factory(profile).read_snapshot()
                stage = "snapshot_invalid"
                expected = SOURCE_IDENTITIES[profile.kind]
                if (snapshot.complete is not True or snapshot.agent_id != profile.agent_id
                        or snapshot.source_kind != expected[1]):
                    raise ValueError
                # Same rolling retention floor as the existing cloud exporter.
                floor = snapshot.source_observed_at - timedelta(days=365)
                snapshot = replace(snapshot, sessions=tuple(
                    raw for raw in snapshot.sessions if raw.last_active_at >= floor
                ))
                records = []
                for raw in snapshot.sessions:
                    if raw.agent_id != profile.agent_id or raw.source_kind != expected[1]:
                        raise ValueError
                    records.append(build_session_record(
                        raw, sanitize_session(raw, self.policy), self._identity_key,
                        include_verified_subject=self.include_verified_subject,
                    ))
                stage = "publication_unavailable"
                result = self.store.publish_snapshot(snapshot, tuple(records), lease=lease)
                counters = asdict(result) if is_dataclass(result) else {
                    key: getattr(result, key) for key in (
                        "status", "changed_sessions", "deleted_sessions", "source_sessions", "source_turns")
                }
                self._emit({"event": "cloud_sync_complete", "agent_id": profile.agent_id,
                            "source_elapsed_ms": snapshot.elapsed_ms, **counters})
                return result
        except Exception as error:  # noqa: BLE001 -- isolate a source and never log driver payloads
            code = stage
            if isinstance(error, SourceReadError):
                # SourceReadError exposes only one of the adapter's stable codes.
                candidate = str(error)
                if candidate in {
                    "source_credentials_invalid", "source_identity_unverified", "source_schema_unverified",
                    "source_time_limit", "source_row_limit", "source_body_limit", "source_row_invalid",
                    "source_timestamp_invalid", "source_duplicate_row", "source_snapshot_incomplete",
                    "source_visibility_unverified",
                }:
                    code = candidate
            recorded = True
            try:
                self.store.record_failure(profile.agent_id, code, attempted)
            except Exception:  # noqa: BLE001 -- failure-report transport must not stop other sources
                recorded = False
            self._emit({"event": "cloud_sync_failed", "agent_id": profile.agent_id,
                        "error_code": code, "failure_recorded": recorded})
            return None

    def run(self, profiles: tuple[SourceProfile, ...], *, interval_seconds: int,
            stop: threading.Event) -> None:
        if type(interval_seconds) is not int or not 5 <= interval_seconds <= 60:
            raise ValueError("interval_invalid")

        def poll(profile):
            failures = 0
            while not stop.is_set():
                started = time.monotonic()
                result = self.run_once(profile)
                failures = min(failures + 1, 4) if result is None else 0
                interval = min(60, interval_seconds * (2 ** failures))
                stop.wait(max(0.1, interval - (time.monotonic() - started)))

        threads = [threading.Thread(target=poll, args=(p,), name=f"sync-{p.kind}") for p in profiles]
        for thread in threads:
            thread.start()
        try:
            while not stop.wait(0.5):
                if any(not thread.is_alive() for thread in threads):
                    raise RuntimeError("source_worker_stopped")
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=15)
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError("source_worker_shutdown_timeout")
