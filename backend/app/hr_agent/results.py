"""One saved result identity across thread and business-object entrypoints."""

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
