"""Independent, opt-in HR web executor coordinator; never runs a model itself."""

import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from uuid import UUID, uuid4

import psycopg

from .conversation_context import (
    ConversationContextError,
    ConversationContextStorageUnavailable,
)
from .direct_command_binding import BindingRejected
from .turn_attempts import Lease, LeaseRejected


class DirectWorker:
    def __init__(
        self,
        attempts,
        adapter,
        *,
        executor_id=None,
        lease_seconds=60,
        limit=16,
        artifact_recovery=None,
    ):
        if not 1 <= limit <= 64 or lease_seconds < 10:
            raise ValueError("direct worker bounds invalid")
        self.attempts, self.adapter = attempts, adapter
        self.artifact_recovery = artifact_recovery
        self._artifact_pool = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="hr-result-files")
            if artifact_recovery
            else None
        )
        self._artifact_pending = None
        self._artifact_after = 0.0
        self.executor_id = executor_id or uuid4()
        self.lease_seconds, self.limit = lease_seconds, limit
        self._preparations = {}
        self._pool = ThreadPoolExecutor(
            max_workers=limit, thread_name_prefix="hr-context"
        )

    def _prepare(self, lease):
        for retry in range(3):
            try:
                self.adapter.prepare(lease)
                return None
            except ConversationContextStorageUnavailable:
                # Only the classified read phase may be retried. This wait owns
                # one bounded preparation slot, never the renewal thread.
                if retry < 2:
                    threading.Event().wait(0.25 * (2**retry))
                    continue
                return "context_storage_unavailable"
            except ConversationContextError as error:
                return (
                    "material_execution_unavailable"
                    if str(error) == "material_execution_unavailable"
                    else "context_unavailable"
                )
            except BindingRejected:
                return "context_changed_before_dispatch"
            except LeaseRejected:
                return None
            except psycopg.Error:
                # An uncertain command write must not repeat prepare. The
                # existing unoffered fence decides whether failure is safe.
                return "context_storage_unavailable"

    def _finish_failed_prepare(self, lease, reason):
        try:
            # Only an unoffered command can be closed here. An expired writer or
            # an uncertain dispatch must instead follow durable reconciliation.
            self.adapter.failed_prepare(lease, reason)
        except psycopg.Error:
            # Keep the decision in its existing Future until storage recovers;
            # never report a terminal state that did not durably commit.
            return reason
        except (LeaseRejected, BindingRejected):
            pass
        return None

    def tick(self) -> int:
        try:
            return self._advance_execution()
        finally:
            # Schedule only after this tick releases execution/conversation
            # locks. Otherwise each file sweep can collide with its own renewal
            # and repeatedly SKIP LOCKED while text/native reconciliation lives.
            self._advance_artifacts()

    def _advance_execution(self) -> int:
        processed = 0
        failures = {}
        for attempt_id, pending in tuple(self._preparations.items()):
            if pending.done():
                reason = pending.result()
                if reason is None:
                    del self._preparations[attempt_id]
                else:
                    failures[attempt_id] = reason
        # Renewal and recovery have independent bounded work before new dispatch.
        with self.attempts.transaction() as connection:
            rows = connection.execute(
                "select *,lease_expires_at>clock_timestamp() as lease_live from platform_control.turn_attempts where executor_kind='worker_direct' and executor_id=%s and status in ('running','reconciling') order by updated_at,attempt_id limit %s",
                (str(self.executor_id), self.limit),
            ).fetchall()
        leases = []
        rejected_ids = set()
        for row in rows:
            if (
                row["status"] == "running"
                and row["transport_run_id"] is None
                and row["attempt_id"] not in self._preparations
                and row["lease_live"]
            ):
                # A lost claim response has no preparation Future/admission.
                # Do not adopt it or renew forever: existing expiry recovery
                # will decide whether it was ever offered.
                continue
            try:
                leases.append(
                    self.attempts.renew(
                        Lease(
                            row["attempt_id"],
                            "worker_direct",
                            UUID(row["executor_id"]),
                            row["lease_epoch"],
                            row["lease_expires_at"],
                            row["status"],
                        ),
                        self.lease_seconds,
                    )
                )
            except LeaseRejected:
                rejected_ids.add(row["attempt_id"])
                continue
            except psycopg.Error:
                continue
        for lease in leases:
            try:
                if lease.attempt_id in failures:
                    self._preparations[lease.attempt_id] = self._pool.submit(
                        self._finish_failed_prepare, lease, failures[lease.attempt_id]
                    )
                    continue
                self.adapter.reconcile(lease)
                processed += 1
            except (LeaseRejected, BindingRejected, psycopg.Error):
                continue
        # A cancelled/replaced lease must not leave a completed local decision
        # occupying capacity after its durable owner has gone away.
        owned_ids = {row["attempt_id"] for row in rows} - rejected_ids
        for attempt_id in failures.keys() - owned_ids:
            del self._preparations[attempt_id]
        # Active leases AND still-running preparations consume bounded capacity.
        occupied = owned_ids | self._preparations.keys()
        for _ in range(max(0, self.limit - len(occupied))):
            lease = self.attempts.claim_due(self.executor_id, self.lease_seconds)
            if lease is None:
                break
            try:
                if lease.status == "reconciling":
                    self.adapter.reconcile(lease)
                else:
                    self._preparations[lease.attempt_id] = self._pool.submit(
                        self._prepare, lease
                    )
                processed += 1
            except (BindingRejected, LeaseRejected):
                continue
        return processed

    def _advance_artifacts(self):
        if self._artifact_pool is None:
            return
        if self._artifact_pending is not None:
            if not self._artifact_pending.done():
                return
            try:
                self._artifact_pending.result()
            except psycopg.Error:
                # Original fixed Result and ready bytes survive storage loss.
                # No retry of model execution follows a file-consumer failure.
                pass
            self._artifact_pending = None
        if monotonic() >= self._artifact_after:
            self._artifact_after = monotonic() + 2.0
            self._artifact_pending = self._artifact_pool.submit(
                self.artifact_recovery.retry_due, 20
            )

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)
        if self._artifact_pool is not None:
            self._artifact_pool.shutdown(wait=True)

    def run(self, stopping):
        try:
            while not stopping.is_set():
                try:
                    self.tick()
                except psycopg.Error:
                    # Storage loss is not evidence that an executor stopped.
                    # Keep local decisions and retry the next bounded cycle.
                    pass
                stopping.wait(0.25)
        finally:
            self.close()


def main():
    from app.main import create_app

    app = create_app(start_poller=False)
    worker = app.state.direct_worker_factory()
    stopping = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopping.set())
    worker.run(stopping)


if __name__ == "__main__":
    main()
