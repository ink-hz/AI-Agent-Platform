"""Authenticated application boundary for the cloud HR loop."""

from .types import problem, validate_contract


class HrAgentService:
    def __init__(
        self,
        repository,
        access,
        *,
        materials=None,
        results=None,
        standards=None,
        knowledge=None,
        ready=True,
    ):
        self._repository = repository
        self.access = access
        self.materials = materials
        self.ready = ready
        self.standards = standards
        self.knowledge = knowledge
        from .results import ResultService

        self.results = results or (
            ResultService(repository) if repository is not None else None
        )

    def _owner(self, auth, writable=False):
        if self.access is None:
            raise problem("temporarily_unavailable", http_status=503)
        owner = self.access.authorize_user(auth, writable=writable)
        if not self.ready or self._repository is None:
            raise problem("temporarily_unavailable", retryable=True, http_status=503)
        return owner

    @property
    def repository(self):
        return self._repository

    def _call(self, method, auth, *args, writable=False, **kwargs):
        owner = self._owner(auth, writable)
        return getattr(self._repository, method)(owner, *args, **kwargs)

    def submit(self, auth, request, key):
        return self._call("submit", auth, request, key, writable=True)

    def append_input(self, auth, work_id, request, key):
        return self._call("append_input", auth, work_id, request, key, writable=True)

    def cancel(self, auth, work_id, request, key):
        request = validate_contract("CancelInput", request)
        return self._call(
            "cancel", auth, work_id, request["reason"], key, writable=True
        )

    def extend_budget(self, auth, work_id, request, key):
        return self._call("extend_budget", auth, work_id, request, key, writable=True)

    def get_work(self, auth, work_id):
        return self._call("get_work", auth, work_id)

    def list_threads(self, auth, cursor=None):
        return self._call("list_threads", auth, cursor=cursor)

    def list_works(self, auth, thread_id, cursor=None):
        return self._call("list_works", auth, thread_id, cursor=cursor)

    def list_messages(self, auth, work_id, after=0, limit=100):
        return self._call("list_messages", auth, work_id, after=after, limit=limit)

    def list_events(self, auth, work_id, after=0, limit=100):
        return self._call("list_events", auth, work_id, after=after, limit=limit)

    def resolve_material(self, auth, attachment_id):
        owner = self._owner(auth)
        if self.materials is None:
            raise problem("temporarily_unavailable", http_status=503)
        return self.materials.resolve(owner, attachment_id)

    def list_results(self, auth, query):
        owner = self._owner(auth)
        return self.results.list(owner, query)

    def read_result(self, auth, result_id, revision):
        owner = self._owner(auth)
        return self.results.read_revision(owner, result_id, revision)

    def export_result(self, auth, result_id, revision):
        owner = self._owner(auth)
        return self.results.export(owner, result_id, revision)

    def link_result(self, auth, result_id, request, key):
        owner = self._owner(auth, True)
        return self.results.link(owner, result_id, request, key)

    def standards_unavailable(self, auth, writable=False):
        self._owner(auth, writable)
        raise problem(
            "temporarily_unavailable",
            "Standards confirmation is not enabled",
            http_status=503,
        )

    def confirm_standard(self, auth, position_id, request, key):
        owner = self._owner(auth, True)
        if self.standards is None:
            return self.standards_unavailable(auth, True)
        return self.standards.confirm(owner, position_id, request, key)

    def current_standard(self, auth, position_id):
        owner = self._owner(auth)
        if self.standards is None:
            return self.standards_unavailable(auth)
        return self.standards.current(owner, position_id)

    def configuration(self, auth):
        self._owner(auth)
        if self.repository.settings is None:
            raise problem("configuration_unavailable", http_status=503)
        budget = self.repository.settings.budget_profile
        return {"budget_profile": budget["id"], "budget_limits": budget["limits"]}

    def knowledge_catalog(self, auth):
        self._owner(auth)
        if self.knowledge is None:
            raise problem("configuration_unavailable", http_status=503)
        published = self.knowledge.current()
        return {
            "release_id": published.release_id,
            "items": [
                {"ref": i["ref"], "title": i["title"], "description": i["description"]}
                for i in published.items
                if i["ref"]["kind"] == "method"
            ],
        }

    def knowledge_text(self, auth, ref):
        self._owner(auth)
        if self.knowledge is None:
            raise problem("configuration_unavailable", http_status=503)
        ref = validate_contract("ExactRef", ref)
        return {"ref": ref, "text": self.knowledge.read(ref)}

    def parse_material(self, auth, attachment_id, request, key):
        owner = self._owner(auth, True)
        if request != {}:
            raise problem("invalid_input")
        parser = getattr(self.materials, "parsing", None)
        if parser is None:
            raise problem("temporarily_unavailable", http_status=503)
        return parser.request(owner, attachment_id, key)

    def work_input(self, auth, work_id):
        owner = self._owner(auth)
        with self.repository.transaction() as cursor:
            work = self.repository._work(cursor, owner, work_id)
            current, _ = self.repository._input(cursor, work)
            return {"input_revision": work["input_revision"], **current}
