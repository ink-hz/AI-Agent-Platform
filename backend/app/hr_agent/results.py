"""One saved result identity across thread and business-object entrypoints."""

import hashlib
from uuid import UUID

from .types import problem, validate_contract


class ResultService:
    def __init__(self, repository):
        self.repository = repository

    def list(self, owner_id, query):
        return self.repository.list_results(owner_id, query)

    def read(self, owner_id, ref):
        ref = validate_contract("ExactRef", ref)
        if ref["kind"] != "result":
            raise problem("invalid_input")
        view = self.repository.read_result(
            owner_id, UUID(ref["id"]), UUID(ref["revision"])
        )
        if view["ref"] != ref:
            raise problem("reference_unavailable", http_status=410)
        return view

    def read_revision(self, owner_id, result_id, revision):
        return self.repository.read_result(owner_id, result_id, revision)

    def link(self, owner_id, result_id, request, key):
        return self.repository.link_result(owner_id, result_id, request, key)

    def export(self, owner_id, result_id, revision):
        """Export the authorized immutable document, without model regeneration."""
        view = self.read_revision(owner_id, result_id, revision)
        content = ("# " + view["title"] + "\n\n" + view["body"] + "\n").encode("utf-8")
        info = {
            "result_ref": view["ref"],
            "format": "markdown",
            "media_type": "text/markdown; charset=utf-8",
            "filename": f"hr-result-{view['ref']['id']}-{view['ref']['revision']}.md",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
        return info, content
