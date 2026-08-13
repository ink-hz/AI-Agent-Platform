from pathlib import PurePosixPath
from pathlib import Path
import json
import re

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


_PUBLIC_BUILD_ASSET = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*-[A-Za-z0-9_-]{8,64}\.(?:js|css|woff2?|png|jpe?g|webp)$"
)


def is_public_build_asset(name: str) -> bool:
    return (
        isinstance(name, str)
        and "/" not in name
        and "\\" not in name
        and "%" not in name
        and _PUBLIC_BUILD_ASSET.fullmatch(name) is not None
    )


def load_public_asset_manifest(static_dir: str) -> frozenset[str]:
    path = Path(static_dir) / ".vite" / "manifest.json"
    try:
        if not path.is_file() or path.stat().st_size > 1_048_576:
            return frozenset()
        document = json.loads(path.read_text(encoding="utf-8"))
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
