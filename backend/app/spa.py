from dataclasses import dataclass
from io import BufferedReader
import mimetypes
import os
from pathlib import PurePosixPath
from pathlib import Path
import json
import re
import stat

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


_PUBLIC_BUILD_ASSET = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*-[A-Za-z0-9_-]{8,64}\.(?:js|css|woff2?|png|jpe?g|webp)$"
)


class PublicAssetUnavailable(OSError):
    """A public static path could not be opened without following symlinks."""


@dataclass(frozen=True)
class OpenedPublicAsset:
    file: BufferedReader
    size: int
    media_type: str


def _open_regular_static_file(
    static_dir: str,
    parts: tuple[str, ...],
    *,
    maximum_size: int | None = None,
) -> OpenedPublicAsset:
    if not parts or any(
        not part or part in {".", ".."} or "/" in part or "\\" in part
        for part in parts
    ):
        raise PublicAssetUnavailable("public static path invalid")
    root = Path(static_dir)
    target = root.joinpath(*parts)
    try:
        root_metadata = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode):
            raise PublicAssetUnavailable("public static root unavailable")
        current = root
        for part in parts:
            current /= part
            if current.is_symlink():
                raise PublicAssetUnavailable(
                    "public static symlink unavailable"
                )
        resolved_root = root.resolve(strict=True)
        resolved_target = target.resolve(strict=True)
        if not resolved_target.is_relative_to(resolved_root):
            raise PublicAssetUnavailable("public static path escaped root")

        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        directory_fd = os.open(root, directory_flags)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
        try:
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or (
                maximum_size is not None and metadata.st_size > maximum_size
            ):
                raise PublicAssetUnavailable("public static file unavailable")
            stream = os.fdopen(file_fd, "rb")
            file_fd = -1
            media_type = mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
            return OpenedPublicAsset(stream, metadata.st_size, media_type)
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    except PublicAssetUnavailable:
        raise
    except (OSError, RuntimeError, ValueError):
        raise PublicAssetUnavailable("public static file unavailable") from None


def open_public_static_file(
    static_dir: str, filename: str
) -> OpenedPublicAsset:
    return _open_regular_static_file(static_dir, (filename,))


def open_public_build_asset(
    static_dir: str, filename: str
) -> OpenedPublicAsset:
    if not is_public_build_asset(filename):
        raise PublicAssetUnavailable("public build asset invalid")
    return _open_regular_static_file(static_dir, ("assets", filename))


def is_public_build_asset(name: str) -> bool:
    return (
        isinstance(name, str)
        and "/" not in name
        and "\\" not in name
        and "%" not in name
        and _PUBLIC_BUILD_ASSET.fullmatch(name) is not None
    )


def load_public_asset_manifest(static_dir: str) -> frozenset[str]:
    try:
        opened = _open_regular_static_file(
            static_dir,
            (".vite", "manifest.json"),
            maximum_size=1_048_576,
        )
        with opened.file:
            document = json.loads(opened.file.read().decode("utf-8"))
        if not isinstance(document, dict):
            return frozenset()
        assets: set[str] = set()
        for entry in document.values():
            if not isinstance(entry, dict):
                return frozenset()
            candidates = [entry.get("file")]
            for key in ("css", "assets"):
                values = entry.get(key, [])
                if not isinstance(values, list):
                    return frozenset()
                candidates.extend(values)
            for candidate in candidates:
                if candidate is None:
                    continue
                if not isinstance(candidate, str) or not candidate.startswith("assets/"):
                    return frozenset()
                name = candidate.removeprefix("assets/")
                if not is_public_build_asset(name):
                    return frozenset()
                assets.add(name)
        return frozenset(assets)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return frozenset()


class SpaStaticFiles(StaticFiles):
    """Serve index.html for client-side routes while preserving asset 404s."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or PurePosixPath(path).suffix:
                raise
            return await super().get_response("index.html", scope)
