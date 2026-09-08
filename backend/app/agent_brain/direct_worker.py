"""Independent, opt-in HR web executor coordinator; never runs a model itself."""

import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

from .conversation_context import ConversationContextError
from .direct_command_binding import BindingRejected
from .turn_attempts import Lease, LeaseRejected


class DirectWorker:
    def __init__(
        self, attempts, adapter, *, executor_id=None, lease_seconds=60, limit=16
    ):
        if not 1 <= limit <= 64 or lease_seconds < 10:
            raise ValueError("direct worker bounds invalid")
        self.attempts, self.adapter = attempts, adapter
        self.executor_id = executor_id or uuid4()
        self.lease_seconds, self.limit = lease_seconds, limit
        self._preparations = {}
        self._pool = ThreadPoolExecutor(
            max_workers=limit, thread_name_prefix="hr-context"
        )

    def _prepare(self, lease):
        try:
            self.adapter.prepare(lease)
            return
        except ConversationContextError as error:
            reason = (
                "material_execution_unavailable"
                if str(error) == "material_execution_unavailable"
                else "context_unavailable"
            )
        except BindingRejected:
            reason = "context_changed_before_dispatch"
        except LeaseRejected:
            return
        try:
            # Only an unoffered command can be closed here. An expired writer or
            # an uncertain dispatch must instead follow durable reconciliation.
            self.adapter.failed_prepare(lease, reason)
        except (LeaseRejected, BindingRejected):
            pass

    def tick(self) -> int:
        processed = 0
        for attempt_id, pending in tuple(self._preparations.items()):
            if pending.done():
                del self._preparations[attempt_id]
                pending.result()
        # Renewal and recovery have independent bounded work before new dispatch.
        with self.attempts.transaction() as connection:
            rows = connection.execute(
                "select * from platform_control.turn_attempts where executor_kind='worker_direct' and executor_id=%s and status in ('running','reconciling') order by updated_at,attempt_id limit %s",
                (str(self.executor_id), self.limit),
            ).fetchall()
        leases = []
        for row in rows:
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
                continue
        for lease in leases:
            try:
                self.adapter.reconcile(lease)
                processed += 1
            except (LeaseRejected, BindingRejected):
                continue
        # Active leases AND still-running preparations consume bounded capacity.
        occupied = {row["attempt_id"] for row in rows} | self._preparations.keys()
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

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)

    def run(self, stopping):
        try:
            while not stopping.is_set():
                self.tick()
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
