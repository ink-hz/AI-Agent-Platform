
import httpx
import pytest

from app.agent_brain.anthropic_adapter import AnthropicMessagesAdapter
from app.agent_brain.model_adapter import (
    BrainModelResponse,
    BrainUsage,
    ProviderTruncated,
    ProviderUnavailable,
)
from app.sdk_knowledge.analysis import AnalysisFailure, ChangeAnalyzer


def packet():
    return {'baseline_id': 'fixture', 'observation': {'files': [{'diff': '-old\n+new'}]},
            'affected_pages': [{'page_id': 'KB-VERSION', 'conditions': {'firmware': ['not_checked']},
                                'claims': [{'claim_id': 'KB-VERSION-C1', 'text': '旧版本事实', 'evidence_ids': ['E1']}],
                                'evidence': [{'evidence_id': 'E1', 'path': 'SDK/version.cpp'}],
                                'changed_references': [{'evidence_id': 'E1', 'line_overlap': True}],
                                'knowledge_state': 'needs_revalidation', 'source_checks': [], 'unknowns': []}],
            'runtime_validation': 'not_run'}


def proposal():
    return {'summary': '版本传播需要复核',
            'impacts': [{'area': '版本', 'rationale': '版本号变化', 'claim_ids': ['KB-VERSION-C1']}],
            'regressions': [{'scenario': '检查运行版本', 'preconditions': ['确认版本接口与受测平台'], 'steps': ['构建隔离候选', '读取版本接口'],
                             'expected_behavior': '版本与目标匹配', 'claim_ids': ['KB-VERSION-C1']}],
            'questions': ['设备固件组合待确认'], 'limitations': ['未执行构建或设备测试']}


class Model:
    def __init__(self, value=None):
        self.value = proposal() if value is None else value
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return BrainModelResponse('request-fixture', ({'type': 'tool_use', 'id': 'tool-1',
                                   'name': 'submit_sdk_analysis', 'input': self.value},),
                                  'tool_use', usage=BrainUsage(input_tokens=100, output_tokens=50))


def test_model_receives_actual_packet_and_result_is_only_a_cited_proposal():
    model = Model()
    result = ChangeAnalyzer(model, model_id='configured-model').analyze(packet(), '检查版本影响')
    assert len(model.requests) == 1
    request = model.requests[0]
    assert '-old\\n+new' in request.messages[0]['content']
    assert 'not_checked' in request.messages[0]['content']
    assert request.max_tokens == 4096
    assert [tool['name'] for tool in request.tools] == ['submit_sdk_analysis']
    assert result['kind'] == 'model_proposal' and result['runtime_validation'] == 'not_run'
    assert result['proposal'] == proposal()
    assert result['usage']['input_tokens'] == 100
    assert result['provider_request_id'] == 'request-fixture'
    assert result['citations'][0]['claim_id'] == 'KB-VERSION-C1'
    assert len(result['input_sha256']) == len(result['output_sha256']) == 64


def test_invented_citations_invalid_schema_and_missing_tool_are_not_repaired():
    wrong = proposal()
    wrong['impacts'][0]['claim_ids'] = ['KB-INVENTED']
    for value in (wrong, {**proposal(), 'tests_passed': True}, {}):
        model = Model(value)
        with pytest.raises(AnalysisFailure):
            ChangeAnalyzer(model, model_id='configured-model').analyze(packet(), '检查')
        assert len(model.requests) == 1
    model = Model()
    model.complete = lambda request: BrainModelResponse('bad', ({'type': 'text', 'text': '已完成'},), 'end_turn')
    with pytest.raises(AnalysisFailure):
        ChangeAnalyzer(model, model_id='configured-model').analyze(packet(), '检查')


def test_provider_failure_has_no_generated_success_or_additional_attempt():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, text='private failure')

    adapter = AnthropicMessagesAdapter(base_url='https://model.invalid', api_key='private-token',
                                      client=httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=1)
    with pytest.raises(ProviderUnavailable):
        ChangeAnalyzer(adapter, model_id='configured-model').analyze(packet(), '检查')
    assert len(calls) == 1


def test_context_limit_is_explicit_instead_of_silent_truncation():
    model = Model()
    data = packet()
    data['observation']['files'][0]['diff'] = 'x' * (160 * 1024 + 1)
    with pytest.raises(AnalysisFailure, match='analysis_context_too_large'):
        ChangeAnalyzer(model, model_id='configured-model').analyze(data, '检查')
    assert not model.requests


def test_sdk_analysis_refuses_a_provider_that_would_retry():
    adapter = AnthropicMessagesAdapter(base_url='https://model.invalid', api_key='token', client=httpx.Client())
    with pytest.raises(ValueError, match='single_provider_attempt'):
        ChangeAnalyzer(adapter, model_id='configured-model')


def test_model_context_keeps_changed_claim_conditions_and_explicit_omissions():
    model = Model()
    data = packet()
    data['affected_pages'][0]['claims'].append({'claim_id': 'KB-VERSION-C2', 'text': '无关结论' * 500,
                                               'evidence_ids': ['E2']})
    ChangeAnalyzer(model, model_id='configured-model').analyze(data, '检查')
    context = model.requests[0].messages[0]['content']
    assert '无关结论' not in context
    assert 'KB-VERSION-C2' in context  # Omission identity remains visible.
    assert 'needs_revalidation' in context and 'not_checked' in context


def test_truncated_tool_json_is_classified_before_json_decoding():
    from app.agent_brain.anthropic_adapter import _aggregate_events

    events = [
        {'type': 'message_start', 'message': {'id': 'partial', 'usage': {'input_tokens': 10}}},
        {'type': 'content_block_start', 'index': 0,
         'content_block': {'type': 'tool_use', 'id': 'one', 'name': 'submit_sdk_analysis', 'input': {}}},
        {'type': 'content_block_delta', 'index': 0,
         'delta': {'type': 'input_json_delta', 'partial_json': '{"summary":"unfinished'}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': 'max_tokens'}, 'usage': {'output_tokens': 4096}},
        {'type': 'message_stop'},
    ]
    with pytest.raises(ProviderTruncated):
        _aggregate_events(events)


def test_context_preserves_one_hop_supporting_claims_for_coherent_contracts():
    model = Model()
    data = packet()
    page = data['affected_pages'][0]
    page['claims'][0]['evidence_ids'].append('E2')
    page['evidence'].append({'evidence_id': 'E2', 'path': 'SDK/generated.cpp'})
    page['claims'].append({'claim_id': 'KB-VERSION-C2', 'text': '生成头与运行接口的关联约束', 'evidence_ids': ['E2']})
    ChangeAnalyzer(model, model_id='configured-model').analyze(data, '检查')
    context = model.requests[0].messages[0]['content']
    assert '生成头与运行接口的关联约束' in context
    assert 'supporting_claim_ids' in context


def test_provider_tool_schema_is_self_contained_and_preserves_nested_constraints():
    import json

    model = Model()
    ChangeAnalyzer(model, model_id='configured-model').analyze(packet(), '检查')
    schema = model.requests[0].tools[0]['input_schema']
    assert '$ref' not in json.dumps(schema) and '$defs' not in schema
    assert schema['properties']['regressions']['maxItems'] == 3
    item = schema['properties']['regressions']['items']
    assert 'preconditions' in item['required'] and item['additionalProperties'] is False
