"""Independent HR Worker; importing this module never starts a process."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from uuid import uuid4

from .cutover import CutoverRejected
from .runtime import run_work


def run_worker(
    repository,
    model,
    resources,
    *,
    stop_event=None,
    worker_id=None,
    poll_seconds=1.0,
    runner=run_work,
    health=None,
):
    stop_event = stop_event or threading.Event()
    worker_id = worker_id or "hr-" + str(uuid4())
    while not stop_event.is_set():
        parser = getattr(getattr(resources, "materials", None), "parsing", None)
        if parser is not None:
            try:
                parser.process_one(worker_id, repository.settings.lease_seconds)
            except CutoverRejected:
                pass
        candidates = getattr(resources, "candidates", None)
        if candidates is not None:
            try:
                candidates.advance_one(worker_id)
            except CutoverRejected:
                pass
        if stop_event.is_set():
            break
        try:
            fence = repository.claim(worker_id, repository.settings.lease_seconds)
        except CutoverRejected:
            fence = None
        if health is not None:
            health.progress()
        if fence is None:
            stop_event.wait(poll_seconds)
            continue
        heartbeat_done = threading.Event()
        lease_lost = threading.Event()

        def heartbeat(current_fence=fence, done=heartbeat_done, lost=lease_lost):
            while not done.wait(repository.settings.heartbeat_seconds):
                try:
                    valid = repository.renew(
                        current_fence, repository.settings.lease_seconds
                    )
                except Exception:  # noqa: BLE001 - failed heartbeat or startup must stop safely
                    valid = False
                if valid and health is not None:
                    health.progress()
                if not valid:
                    lost.set()
                    return

        thread = threading.Thread(
            target=heartbeat, name="hr-lease-heartbeat", daemon=True
        )
        thread.start()

        class StopCurrentWork:
            def __init__(self, shutdown, lost):
                self.shutdown = shutdown
                self.lost = lost

            def is_set(self):
                return self.shutdown.is_set() or self.lost.is_set()

        try:
            runner(
                repository,
                model,
                resources,
                fence,
                stop_event=StopCurrentWork(stop_event, lease_lost),
            )
        finally:
            heartbeat_done.set()
            thread.join(timeout=5)


def main():
    if sys.argv[1:] == ["healthcheck"]:
        import json

        from .worker_health import check_worker

        report = check_worker()
        print(json.dumps(report))
        return 0 if report["ready"] else 1

    import psycopg

    from app.control_plane.dsn import validate_control_dsn
    from app.local_secrets import read_secret_file

    from .config import check_schema_ready, load_hr_agent_settings
    from .materials import build_material_service_from_environment
    from .model import ConfiguredHttpModelPort
    from .resources import build_runtime_services

    try:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        settings = load_hr_agent_settings(os.environ)
        if not settings.enabled:
            return 0
        dsn = read_secret_file(os.environ["PLATFORM_CONTROL_DATABASE_URL_FILE"])
        validate_control_dsn(dsn, purpose="app")

        def connect():
            return psycopg.connect(
                dsn, connect_timeout=3, options="-c statement_timeout=10000"
            )

        if not check_schema_ready(connect):
            raise ValueError("schema unavailable")
        materials = build_material_service_from_environment(
            os.environ, temporary_root=settings.work_dir
        )
        repository, resources = build_runtime_services(
            settings, connect, material_service=materials
        )
        model = ConfiguredHttpModelPort.from_mapping(settings.provider_profile)
        stop = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
        from .readiness import HrReadiness
        from .worker_health import WorkerHealth

        def readiness_connect():
            return psycopg.connect(dsn, connect_timeout=1,
                                   options="-c statement_timeout=1500 -c lock_timeout=500")

        readiness = HrReadiness(settings, os.environ, readiness_connect, role="worker")
        with WorkerHealth(readiness, max_age=settings.heartbeat_seconds * 3) as health:
            run_worker(repository, model, resources, stop_event=stop, health=health)
        return 0
    except Exception:  # noqa: BLE001 - failed heartbeat or startup must stop safely
        # Never print exception strings, DSNs, provider bodies, or prompts.
        print(
            "HR Worker stopped: configuration_or_runtime_unavailable", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
