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
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


class DerivativeBuilder:
    def __init__(
        self,
        *,
        pdftoppm_path: str = "/usr/bin/pdftoppm",
        timeout_seconds: float = 10.0,
    ) -> None:
        path = Path(pdftoppm_path)
        if not path.is_absolute():
            raise ValueError("attachment renderer path invalid")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("attachment renderer timeout invalid")
        self._pdftoppm_path = str(path)
        self._timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return "DerivativeBuilder(renderer=<fixed>)"

    def build(self, source: OpenedObject, detected_mime: str) -> tuple[Derivative, ...]:
        if not isinstance(source, OpenedObject):
            raise DerivativeError()
        try:
            if detected_mime in {"image/png", "image/jpeg"}:
                return (self._image_thumbnail(source),)
            if detected_mime == "application/pdf":
                return (self._pdf_preview(source),)
            if detected_mime in _OFFICE_MIMES or detected_mime == "text/plain":
                return (
                    Derivative(
                        "preview",
                        "application/json",
                        _METADATA_ONLY,
                        inline_preview=False,
                    ),
                )
        except DerivativeError:
            raise
        except Exception:  # noqa: BLE001 - parser failures must be sanitized
            raise DerivativeError() from None
        raise DerivativeError()

    @staticmethod
    def _safe_png(stream) -> bytes:
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
                output = io.BytesIO()
                normalized.save(output, format="PNG", optimize=False)
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
            raise DerivativeError() from None

    def _image_thumbnail(self, source: OpenedObject) -> Derivative:
        data = self._safe_png(source.stream)
        return Derivative("thumbnail", "image/png", data, inline_preview=True)

    def _pdf_preview(self, source: OpenedObject) -> Derivative:
        if source.size <= 0 or source.size > MAX_FILE_BYTES:
            raise DerivativeError()
        try:
            with tempfile.TemporaryDirectory(prefix="attachment-preview-") as temporary:
                directory = Path(temporary)
                source_path = directory / "source.pdf"
                output_prefix = directory / "first-page"
                size = 0
                with source_path.open("xb") as output:
                    for chunk in source.iter_chunks():
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise DerivativeError()
                        output.write(chunk)
                if size != source.size:
                    raise DerivativeError()
                subprocess.run(
                    [
                        self._pdftoppm_path,
                        "-f",
                        "1",
                        "-l",
                        "1",
                        "-singlefile",
                        "-png",
                        "-scale-to",
                        "1600",
                        str(source_path),
                        str(output_prefix),
                    ],
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=directory,
                    env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                    close_fds=True,
                    timeout=self._timeout_seconds,
                    preexec_fn=_parser_limits if os.name == "posix" else None,
                )
                rendered = output_prefix.with_suffix(".png")
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
            raise DerivativeError() from None
