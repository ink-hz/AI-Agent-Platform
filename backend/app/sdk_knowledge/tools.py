"""Read tools for SDK consumers; schemas alone do not register a platform Agent."""
from copy import deepcopy

from .models import ChangeRequest, PageRequest, SearchRequest

_TOOLS = (
    ('sdk_knowledge_search', SearchRequest, '检索静态SDK知识候选，返回原始条件与来源；不是设备支持判定'),
    ('sdk_knowledge_page', PageRequest, '按稳定页ID读取已核定静态结论、证据和未知项'),
    ('sdk_knowledge_inspect_change', ChangeRequest, '读取允许仓的两个固定提交，定位直接引用和知识过期候选'),
)


class SdkKnowledgeTools:
    def __init__(self, service, *, authorize, source_authorize=None):
        if not callable(authorize):
            raise TypeError('SDK knowledge authorization callback required')
        self.service, self.authorize = service, authorize
        self.source_authorize = source_authorize

    @property
    def definitions(self):
        return deepcopy([{'name': name, 'description': description, 'input_schema': model.model_json_schema()}
                         for name, model, description in _TOOLS])

    def _check_access(self, repository=None):
        try:
            allowed = self.authorize() is True
            source_allowed = (repository is None or
                              (self.source_authorize is not None and self.source_authorize(repository) is True))
        except Exception:  # noqa: BLE001 -- injected authorization must not leak backend failures
            raise PermissionError('sdk_authorization_unavailable') from None
        if not allowed:
            raise PermissionError('sdk_access_required')
        if not source_allowed:
            raise PermissionError('sdk_source_access_required')

    def call(self, name: str, arguments: dict):
        self._check_access()
        models = {name: model for name, model, _ in _TOOLS}
        if name not in models:
            raise ValueError('sdk_knowledge_tool_unknown')
        values = models[name].model_validate(arguments).model_dump()
        if name == 'sdk_knowledge_inspect_change':
            self._check_access(values['repository'])
        if name == 'sdk_knowledge_search':
            result = self.service.corpus.search(**values)
        elif name == 'sdk_knowledge_page':
            result = self.service.corpus.page(values['page_id'])
        else:
            result = self.service.inspect_change(**values)
        self._check_access(values['repository'] if name == 'sdk_knowledge_inspect_change' else None)
        return result
