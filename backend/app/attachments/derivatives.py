from __future__ import annotations

import hashlib
import io
import os
import resource
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from .conversation_models import MAX_FILE_BYTES
from .validation import OpenedObject

_MAX_IMAGE_PIXELS = 40_000_000
_MAX_PREVIEW_BYTES = 10 * 1024 * 1024
_THUMBNAIL_SIZE = (512, 512)
_OFFICE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_METADATA_ONLY = b'{"coverage":"metadata_only","download":true,"inline_preview":false}'


class DerivativeError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("attachment derivative unavailable")


@dataclass(frozen=True)
class Derivative:
    kind: str
    detected_mime: str
    data: bytes = field(repr=False)
    inline_preview: bool

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> bytes:
        return hashlib.sha256(self.data).digest()


def _parser_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (_MAX_PREVIEW_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))


class PdfSandboxRunner(Protocol):
    def render(
        self,
        source_path: Path,
        output_path: Path,
        *,
        timeout_seconds: float,
    ) -> None: ...


class BubblewrapPdfSandbox:
    """Linux production boundary: no network and only explicit mounts."""

    def __init__(
        self,
        *,
        bubblewrap_path: str = "/usr/bin/bwrap",
        pdftoppm_path: str = "/usr/bin/pdftoppm",
        runtime_ro_paths: tuple[str, ...] = ("/usr", "/lib", "/lib64"),
    ) -> None:
        paths = (bubblewrap_path, pdftoppm_path, *runtime_ro_paths)
        if not all(isinstance(value, str) and Path(value).is_absolute() for value in paths):
            raise ValueError("attachment renderer path invalid")
        self._bubblewrap_path = bubblewrap_path
        self._pdftoppm_path = pdftoppm_path
        self._runtime_ro_paths = tuple(runtime_ro_paths)

    def __repr__(self) -> str:
        return "BubblewrapPdfSandbox(paths=<fixed>)"

    def render(
        self,
        source_path: Path,
        output_path: Path,
        *,
        timeout_seconds: float,
    ) -> None:
        if (
            not source_path.is_absolute()
            or not output_path.is_absolute()
            or not source_path.is_file()
            or not output_path.parent.is_dir()
            or not Path(self._bubblewrap_path).exists()
            or not Path(self._pdftoppm_path).exists()
        ):
            raise DerivativeError()
        argv = [
            self._bubblewrap_path,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for runtime in self._runtime_ro_paths:
            if Path(runtime).exists():
                argv.extend(("--ro-bind", runtime, runtime))
        argv.extend(
            (
                "--dir",
                "/input",
                "--dir",
                "/output",
                "--ro-bind",
                str(source_path),
                "/input/source.pdf",
                "--bind",
                str(output_path.parent),
                "/output",
                "--chdir",
                "/output",
                "--setenv",
                "LANG",
                "C",
                "--setenv",
                "LC_ALL",
                "C",
                self._pdftoppm_path,
                "-f",
                "1",
                "-l",
                "1",
                "-singlefile",
                "-png",
                "-scale-to",
                "1600",
                "/input/source.pdf",
                "/output/first-page",
            )
        )
        render_failed = False
        try:
            subprocess.run(
                argv,
                check=True,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=output_path.parent,
                env={"LANG": "C", "LC_ALL": "C"},
                close_fds=True,
                timeout=timeout_seconds,
                preexec_fn=_parser_limits if os.name == "posix" else None,
            )
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            render_failed = True
        if render_failed:
            raise DerivativeError()
        if not output_path.is_file():
            raise DerivativeError()


class DerivativeBuilder:
    def __init__(
        self,
        *,
        sandbox_runner: PdfSandboxRunner | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("attachment renderer timeout invalid")
        self._sandbox_runner = sandbox_runner
        self._timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return "DerivativeBuilder(renderer=<fixed>)"

    def build(self, source: OpenedObject, detected_mime: str) -> tuple[Derivative, ...]:
        if not isinstance(source, OpenedObject):
            raise DerivativeError()
        parser_failed = False
        try:
            if detected_mime in {"image/png", "image/jpeg"}:
                return (self._image_thumbnail(source),)
            if detected_mime == "application/pdf":
                return (self._pdf_preview(source),)
            if detected_mime in _OFFICE_MIMES or detected_mime == "text/plain":
                return (
                    Derivative(
                        "metadata",
                        "application/json",
                        _METADATA_ONLY,
                        inline_preview=False,
                    ),
                )
        except DerivativeError:
            raise
        except Exception:  # noqa: BLE001 - parser failures must be sanitized
            parser_failed = True
        if parser_failed:
            raise DerivativeError()
        raise DerivativeError()

    @staticmethod
    def _safe_png(stream) -> bytes:
        parser_failed = False
        try:
            with Image.open(stream) as candidate:
                candidate.verify()
            stream.seek(0)
            with Image.open(stream) as image:
                if (
                    image.width <= 0
                    or image.height <= 0
                    or image.width * image.height > _MAX_IMAGE_PIXELS
                ):
                    raise DerivativeError()
                normalized = ImageOps.exif_transpose(image)
                normalized.thumbnail(_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                if normalized.mode not in {"RGB", "RGBA"}:
                    normalized = normalized.convert("RGBA")
                fresh = Image.new(normalized.mode, normalized.size)
                fresh.putdata(list(normalized.getdata()))
                output = io.BytesIO()
                fresh.save(output, format="PNG", optimize=False)
                data = output.getvalue()
                if not data or len(data) > _MAX_PREVIEW_BYTES:
                    raise DerivativeError()
                return data
        except DerivativeError:
            raise
        except (
            Image.DecompressionBombError,
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            ValueError,
        ):
            parser_failed = True
        if parser_failed:
            raise DerivativeError()

    def _image_thumbnail(self, source: OpenedObject) -> Derivative:
        data = self._safe_png(source.stream)
        return Derivative("thumbnail", "image/png", data, inline_preview=True)

    def _pdf_preview(self, source: OpenedObject) -> Derivative:
        if (
            source.size <= 0
            or source.size > MAX_FILE_BYTES
            or self._sandbox_runner is None
        ):
            raise DerivativeError()
        parser_failed = False
        try:
            with tempfile.TemporaryDirectory(prefix="attachment-preview-") as temporary:
                directory = Path(temporary)
                input_directory = directory / "input"
                output_directory = directory / "output"
                input_directory.mkdir(mode=0o700)
                output_directory.mkdir(mode=0o700)
                source_path = input_directory / "source.pdf"
                rendered = output_directory / "first-page.png"
                size = 0
                with source_path.open("xb") as output:
                    for chunk in source.iter_chunks():
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise DerivativeError()
                        output.write(chunk)
                if size != source.size:
                    raise DerivativeError()
                self._sandbox_runner.render(
                    source_path,
                    rendered,
                    timeout_seconds=self._timeout_seconds,
                )
                metadata = rendered.stat()
                if metadata.st_size <= 0 or metadata.st_size > _MAX_PREVIEW_BYTES:
                    raise DerivativeError()
                with rendered.open("rb") as image:
                    data = self._safe_png(image)
                return Derivative("preview", "image/png", data, inline_preview=True)
        except DerivativeError:
            raise
        except (
            OSError,
            subprocess.SubprocessError,
            subprocess.TimeoutExpired,
        ):
            parser_failed = True
        if parser_failed:
            raise DerivativeError()
