from uuid import uuid4

from app.attachments.erasure import AttachmentErasureService, ErasureJob


class Repository:
    def __init__(self, job):
        self.job = job
        self.records = []

    def claim(self, worker_id):
        assert worker_id == "worker-1"
        return self.job

    def record(self, job, *, failed):
        self.records.append((job.erasure_job_id, failed))


class Store:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.deleted = []

    def delete(self, object_ref):
        self.deleted.append(object_ref)
        if object_ref in self.failing:
            raise RuntimeError("storage unavailable")


def test_erasure_deletes_original_derivatives_and_version_object_refs():
    job = ErasureJob(uuid4(), uuid4(), ("original", "preview", "version-2"))
    repository = Repository(job)
    store = Store()

    assert AttachmentErasureService(repository, store).process_next("worker-1") is True

    assert store.deleted == ["original", "preview", "version-2"]
    assert repository.records == [(job.erasure_job_id, 0)]


def test_partial_object_failure_is_recorded_for_retry_without_false_success():
    job = ErasureJob(uuid4(), uuid4(), ("original", "preview"))
    repository = Repository(job)
    store = Store(("preview",))

    assert AttachmentErasureService(repository, store).process_next("worker-1") is True

    assert repository.records == [(job.erasure_job_id, 1)]
