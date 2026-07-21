from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class SpaStaticFiles(StaticFiles):
    """Serve index.html for client-side routes while preserving asset 404s."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or PurePosixPath(path).suffix:
                raise
            return await super().get_response("index.html", scope)
