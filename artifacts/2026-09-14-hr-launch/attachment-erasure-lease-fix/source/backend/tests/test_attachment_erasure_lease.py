from __future__ import annotations

import threading
from uuid import uuid4

import pytest
from app.attachments.erasure import (
    AttachmentErasureError,
    AttachmentErasureService,
    ErasureJob,
)


class Repository:
    def __init__(self, job, renewals=(True, True)):
        self.job = job
        self.renewals = list(renewals)
        self.records = []
        self.renew_calls = []

    def claim(self, worker_id):
        assert worker_id == "worker-1"
        return self.job

    def renew(self, job):
        self.renew_calls.append(job.attempt_token)
        return self.renewals.pop(0) if self.renewals else True

    def record(self, job, *, failed):
        self.records.append((job.erasure_job_id, job.attempt_token, failed))


class Store:
    def __init__(self, gate=None):
        self.gate = gate
        self.deleted = []

    def delete(self, object_ref):
        self.deleted.append(object_ref)
        if self.gate is not None:
            assert self.gate.wait(2)


def test_service_records_exact_claim_token_after_final_renewal():
    token = uuid4()
    job = ErasureJob(uuid4(), uuid4(), token, ("one", "two"))
    repository = Repository(job)
    service = AttachmentErasureService(repository, Store(), heartbeat_interval=30)

    assert service.process_next("worker-1") is True

    assert repository.records == [(job.erasure_job_id, token, 0)]
    assert repository.renew_calls[-1] == token


def test_heartbeat_renewal_loss_during_delete_forbids_result_commit():
    token = uuid4()
    job = ErasureJob(uuid4(), uuid4(), token, ("one", "two"))
    gate = threading.Event()
    repository = Repository(job, renewals=(False,))
    store = Store(gate)
    service = AttachmentErasureService(repository, store, heartbeat_interval=0.01)
    threading.Timer(0.05, gate.set).start()

    with pytest.raises(AttachmentErasureError):
        service.process_next("worker-1")

    assert store.deleted == ["one"]
    assert repository.records == []
    assert repository.renew_calls == [token]
