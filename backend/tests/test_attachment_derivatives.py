from __future__ import annotations

import hashlib
import io
import json
import resource
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from app.attachments import derivatives as derivative_module
from app.attachments.derivatives import (
    BubblewrapPdfSandbox,
    DerivativeBuilder,
    DerivativeError,
)
from app.attachments.validation import OpenedObject
from app.attachments.worker import (
    AttachmentProcessor,
    DerivativeFinalizeError,
    ProcessingJob,
    ProcessingTransitionError,
    ReconciliationStatus,
    StoredDerivative,
)
from app.attachments.worker_runtime import AttachmentProcessingRepository
from PIL import Image, PngImagePlugin

FIXTURES = Path(__file__).parent / "fixtures" / "conversation_attachments"


def opened(name: str) -> OpenedObject:
    data = (FIXTURES / name).read_bytes()
    return OpenedObject(io.BytesIO(data), len(data))


@pytest.mark.parametrize("name", ["valid.png", "valid.jpg"])
def test_images_are_reencoded_as_bounded_png_thumbnails(name: str) -> None:
    derivatives = DerivativeBuilder().build(
        opened(name), "image/png" if name.endswith("png") else "image/jpeg"
    )

    assert len(derivatives) == 1
    thumbnail = derivatives[0]
    assert thumbnail.kind == "thumbnail"
    assert thumbnail.detected_mime == "image/png"
    assert thumbnail.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert thumbnail.inline_preview is True
    assert "JFIF" not in thumbnail.data.decode("latin1")


class DeterministicSandbox:
    def __init__(self) -> None:
        self.calls = []

    def render(self, source_path: Path, output_path: Path, *, timeout_seconds: float) -> None:
        self.calls.append((source_path, output_path, timeout_seconds))
        assert source_path.name == "source.pdf"
        assert b"/Type /Page" in source_path.read_bytes()
        output_path.write_bytes((FIXTURES / "valid.png").read_bytes())


def test_pdf_first_page_requires_explicit_sandbox() -> None:
    with pytest.raises(DerivativeError, match="derivative unavailable"):
        DerivativeBuilder().build(opened("valid.pdf"), "application/pdf")


def test_pdf_first_page_uses_injected_sandbox_and_safe_png_reencoding() -> None:
    sandbox = DeterministicSandbox()

    derivatives = DerivativeBuilder(sandbox_runner=sandbox).build(
        opened("valid.pdf"), "application/pdf"
    )

    assert len(derivatives) == 1
    preview = derivatives[0]
    assert preview.kind == "preview"
    assert preview.detected_mime == "image/png"
    assert preview.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert preview.inline_preview is True
    assert len(sandbox.calls) == 1
    assert sandbox.calls[0][2] == 10.0


def test_linux_pdf_sandbox_uses_fixed_first_page_no_network_argv(monkeypatch) -> None:
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        Path(kwargs["cwd"], "first-page.png").write_bytes(
            (FIXTURES / "valid.png").read_bytes()
        )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    sandbox = BubblewrapPdfSandbox(
        bubblewrap_path="/usr/bin/bwrap",
        pdftoppm_path="/usr/bin/pdftoppm",
        runtime_ro_paths=("/usr", "/lib"),
    )
    with pytest.MonkeyPatch.context() as context:
        context.setattr(Path, "exists", lambda _path: True)
        DerivativeBuilder(sandbox_runner=sandbox).build(
            opened("valid.pdf"), "application/pdf"
        )

    argv = captured["argv"]
    assert argv[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in argv
    assert "--ro-bind" in argv
    assert "/usr/bin/pdftoppm" in argv
    assert argv[argv.index("-f") + 1] == "1"
    assert argv[argv.index("-l") + 1] == "1"
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 10.0
    assert callable(captured["kwargs"]["preexec_fn"])


def test_pdf_sandbox_preexec_limits_allow_the_renderer_to_start(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda kind, value: calls.append((kind, value)))

    derivative_module._parser_limits()

    assert (resource.RLIMIT_CPU, (5, 5)) in calls
    assert (resource.RLIMIT_NOFILE, (32, 32)) in calls
    if hasattr(resource, "RLIMIT_NPROC"):
        nproc = next(value for kind, value in calls if kind == resource.RLIMIT_NPROC)
        assert nproc[0] > 0


def test_linux_pdf_sandbox_timeout_fails_closed(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "first-page.png"
    source.write_bytes((FIXTURES / "valid.pdf").read_bytes())
    monkeypatch.setattr(Path, "exists", lambda _path: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("<redacted>", 1.0)
        ),
    )
    sandbox = BubblewrapPdfSandbox(
        bubblewrap_path="/usr/bin/bwrap",
        pdftoppm_path="/usr/bin/pdftoppm",
        runtime_ro_paths=("/usr",),
    )

    with pytest.raises(DerivativeError, match="derivative unavailable"):
        sandbox.render(source, output, timeout_seconds=1.0)


@pytest.mark.parametrize(
    "name",
    ["valid.docx", "valid.xlsx", "valid.pptx"],
)
def test_office_p0_returns_only_safe_coverage_metadata(name: str) -> None:
    mime = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }[Path(name).suffix]

    derivatives = DerivativeBuilder().build(opened(name), mime)

    assert len(derivatives) == 1
    metadata = derivatives[0]
    assert metadata.kind == "metadata"
    assert metadata.detected_mime == "application/json"
    assert metadata.inline_preview is False
    assert json.loads(metadata.data) == {
        "coverage": "metadata_only",
        "download": True,
        "inline_preview": False,
    }
    assert b"document.xml" not in metadata.data


def test_derivative_values_redact_content_from_repr() -> None:
    derivative = DerivativeBuilder().build(opened("valid.png"), "image/png")[0]
    assert "PNG" not in repr(derivative)


def test_pdf_sandbox_requires_fixed_absolute_executable_paths() -> None:
    with pytest.raises(ValueError, match="renderer path invalid"):
        BubblewrapPdfSandbox(
            bubblewrap_path="bwrap",
            pdftoppm_path="/usr/bin/pdftoppm",
        )


def test_truncated_images_never_produce_a_derivative() -> None:
    with pytest.raises(DerivativeError, match="derivative unavailable") as captured:
        DerivativeBuilder().build(opened("truncated.png"), "image/png")
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_image_thumbnail_drops_exif_icc_and_text_metadata_and_normalizes_orientation() -> None:
    candidate = Image.new("RGB", (2, 1))
    candidate.putdata([(255, 0, 0), (0, 0, 255)])
    candidate.getexif()[274] = 6
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("comment", "source-secret")
    source = io.BytesIO()
    candidate.save(
        source,
        format="PNG",
        exif=candidate.getexif(),
        icc_profile=b"source-secret-icc",
        pnginfo=metadata,
    )
    raw = source.getvalue()

    result = DerivativeBuilder().build(
        OpenedObject(io.BytesIO(raw), len(raw)), "image/png"
    )[0]

    assert b"source-secret" not in result.data
    with Image.open(io.BytesIO(result.data)) as output:
        assert output.size == (1, 2)
        assert output.getexif() == {}
        assert "icc_profile" not in output.info
        assert "comment" not in output.info


class DeriveRepository:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.results = []
        self.derivatives = []
        self.committed = False

    def claim(self, _worker_id):
        return self.jobs.pop(0) if self.jobs else None

    def record_result(self, _job, state, reason, *, validation=None):
        self.results.append((state, reason))

    def reconcile_result(self, _job, state):
        return (
            ReconciliationStatus.COMMITTED
            if self.results and self.results[-1][0] == state
            else ReconciliationStatus.RUNNING
        )

    def record_derivative(self, job, derivative, stored):
        self.derivatives.append((job, derivative, stored))

    def reconcile_derivative(self, _job, _stored):
        return (
            ReconciliationStatus.COMMITTED
            if self.committed
            else ReconciliationStatus.RUNNING
        )


class DeriveStore:
    def __init__(self, source: bytes):
        self.source = source
        self.keys = []
        self.deleted = []

    def open(self, _object_ref, immutable_locator=None):
        if immutable_locator is not None:
            assert immutable_locator == "version:v1"
        return OpenedObject(io.BytesIO(self.source), len(self.source))

    def put_derivative(self, data, *, object_key):
        self.keys.append(object_key)
        return StoredDerivative(object_key, len(data), hashlib.sha256(data).digest())

    def delete(self, object_ref):
        self.deleted.append(object_ref)


class NeverBuild:
    def build(self, *_args):
        raise AssertionError("substituted bytes reached renderer")


@pytest.mark.asyncio
async def test_derive_rejects_same_size_replacement_before_rendering() -> None:
    expected = (FIXTURES / "valid.png").read_bytes()
    replacement = b"x" * len(expected)
    job = ProcessingJob(
        uuid4(), uuid4(), "derive", "thumbnail", "opaque", len(expected), hashlib.sha256(expected).digest(), "image/png", "version:v1"
    )
    repository = DeriveRepository([job])
    processor = AttachmentProcessor(
        repository=repository,
        object_store=DeriveStore(replacement),
        validator=None,
        scanner=None,
        derivatives=NeverBuild(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert repository.results == [("rejected", "integrity_mismatch")]


@pytest.mark.asyncio
async def test_integrity_rejection_response_loss_does_not_issue_retry() -> None:
    expected = (FIXTURES / "valid.png").read_bytes()
    replacement = b"x" * len(expected)
    job = ProcessingJob(
        uuid4(), uuid4(), "derive", "thumbnail", "opaque", len(expected), hashlib.sha256(expected).digest(), "image/png"
    )

    class Repository(DeriveRepository):
        def record_result(self, job, state, reason, *, validation=None):
            self.results.append((state, reason))
            raise RuntimeError("response lost")

    repository = Repository([job])
    processor = AttachmentProcessor(
        repository=repository,
        object_store=DeriveStore(replacement),
        validator=None,
        scanner=None,
        derivatives=NeverBuild(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert repository.results == [("rejected", "integrity_mismatch")]


@pytest.mark.asyncio
async def test_known_pre_db_derivative_failure_deletes_written_object() -> None:
    source = (FIXTURES / "valid.png").read_bytes()
    job = ProcessingJob(
        uuid4(), uuid4(), "derive", "thumbnail", "opaque", len(source), hashlib.sha256(source).digest(), "image/png"
    )

    class Repository(DeriveRepository):
        def record_derivative(self, *_args):
            raise DerivativeFinalizeError(ambiguous=False)

    repository = Repository([job])
    store = DeriveStore(source)
    processor = AttachmentProcessor(
        repository=repository,
        object_store=store,
        validator=None,
        scanner=None,
        derivatives=DerivativeBuilder(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert store.deleted == store.keys
    assert repository.results == [("retry", "derivative_unavailable")]


@pytest.mark.asyncio
async def test_real_repository_pre_db_seal_failure_deletes_written_object() -> None:
    source = (FIXTURES / "valid.png").read_bytes()
    job = ProcessingJob(
        uuid4(), uuid4(), "derive", "thumbnail", "opaque", len(source),
        hashlib.sha256(source).digest(), "image/png", None, uuid4(),
    )

    class UnsealOnlyCodec:
        def unseal_json(self, *_args):
            return {"object_ref": "opaque"}

    class Repository(AttachmentProcessingRepository):
        def __init__(self):
            super().__init__(
                "postgresql://platform_brain_worker@localhost/agent_platform_control",
                content_codec=UnsealOnlyCodec(),
            )
            self.jobs = [job]
            self.results = []

        def claim(self, _worker_id):
            return self.jobs.pop()

        def record_result(self, _job, state, reason, *, validation=None):
            self.results.append((state, reason))

        def reconcile_result(self, *_args):
            return ReconciliationStatus.RUNNING

        def reconcile_derivative(self, *_args):
            return ReconciliationStatus.RUNNING

    repository = Repository()
    store = DeriveStore(source)
    processor = AttachmentProcessor(
        repository=repository,
        object_store=store,
        validator=None,
        scanner=None,
        derivatives=DerivativeBuilder(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert store.deleted == store.keys
    assert repository.results == [("retry", "derivative_unavailable")]


@pytest.mark.asyncio
async def test_ambiguous_derivative_commit_is_reconciled_without_delete() -> None:
    source = (FIXTURES / "valid.png").read_bytes()
    job = ProcessingJob(
        uuid4(), uuid4(), "derive", "thumbnail", "opaque", len(source), hashlib.sha256(source).digest(), "image/png"
    )

    class Repository(DeriveRepository):
        def record_derivative(self, job, derivative, stored):
            self.derivatives.append((job, derivative, stored))
            self.committed = True
            raise DerivativeFinalizeError(ambiguous=True)

    repository = Repository([job])
    store = DeriveStore(source)
    processor = AttachmentProcessor(
        repository=repository,
        object_store=store,
        validator=None,
        scanner=None,
        derivatives=DerivativeBuilder(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert store.deleted == []
    assert repository.results == []


@pytest.mark.asyncio
async def test_unknown_derivative_commit_preserves_object_without_retry_transition() -> None:
    source = (FIXTURES / "valid.png").read_bytes()
    job = ProcessingJob(
        uuid4(), uuid4(), "derive", "thumbnail", "opaque", len(source), hashlib.sha256(source).digest(), "image/png"
    )

    class Repository(DeriveRepository):
        def record_derivative(self, *_args):
            raise DerivativeFinalizeError(ambiguous=True)

        def reconcile_derivative(self, _job, _stored):
            return ReconciliationStatus.UNKNOWN

    repository = Repository([job])
    store = DeriveStore(source)
    processor = AttachmentProcessor(
        repository=repository,
        object_store=store,
        validator=None,
        scanner=None,
        derivatives=DerivativeBuilder(),
        worker_id="attachment-worker.1",
    )

    with pytest.raises(ProcessingTransitionError):
        await processor.process_next()
    assert store.deleted == []
    assert repository.results == []


@pytest.mark.asyncio
async def test_derivative_retry_reuses_one_stable_opaque_object_key() -> None:
    source = (FIXTURES / "valid.png").read_bytes()
    job = ProcessingJob(
        uuid4(), uuid4(), "derive", "thumbnail", "opaque", len(source), hashlib.sha256(source).digest(), "image/png"
    )

    class Repository(DeriveRepository):
        attempts = 0

        def record_derivative(self, job, derivative, stored):
            self.attempts += 1
            if self.attempts == 1:
                raise DerivativeFinalizeError(ambiguous=False)
            self.derivatives.append((job, derivative, stored))

    repository = Repository([job, job])
    store = DeriveStore(source)
    processor = AttachmentProcessor(
        repository=repository,
        object_store=store,
        validator=None,
        scanner=None,
        derivatives=DerivativeBuilder(),
        worker_id="attachment-worker.1",
    )

    assert await processor.process_next() is True
    assert await processor.process_next() is True
    assert len(set(store.keys)) == 1
    assert len(store.keys[0]) == 64
