"""Real HR persistence; only the model provider and public context are scripted."""
from uuid import uuid4

import pytest

from app.hr_agent.model import ModelTransportError
from app.hr_agent.repository import HrAgentRepository
from app.hr_agent.runtime import run_work
from app.hr_agent.types import ModelContext, ModelEvent
from hr_agent_support import hr_agent_database, make_hr_settings


@pytest.fixture(scope='module')
def database():
    with hr_agent_database() as db:
        yield db


@pytest.fixture
def setup(database, tmp_path):
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    settings = make_hr_settings(tmp_path)
    repo = HrAgentRepository(database.connection, settings.create_codec(), settings=settings)
    owner = uuid4()
    view = repo.submit(owner, {'thread_id': None, 'text': '公开招聘建议', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
    return repo, owner, view, repo.claim('test-worker', 60)


def context(repo, resources, fence):
    return ModelContext('work', ({'role': 'user', 'content': '公开招聘建议'},), (), (), 20, fence.input_revision)


class ScriptModel:
    def __init__(self, scripts):
        self.scripts = iter(scripts)
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        script = next(self.scripts)
        if isinstance(script, Exception):
            raise script
        yield from script


def answer(text='已有结论'):
    return [ModelEvent('text_delta', {'text': text}), ModelEvent('usage', {'prompt_tokens': 10, 'completion_tokens': 5}), ModelEvent('stop', {'reason': 'stop'})]


def test_normal_answer_is_persisted_without_fixed_steps(setup):
    repo, owner, view, fence = setup
    model = ScriptModel([answer()])
    done = run_work(repo, model, None, fence, context_builder=context)
    assert done['state'] == 'completed'
    assert done['answer_state'] == 'ended'
    assert repo.list_messages(owner, view['work_id'])['items'][-1]['body'] == '已有结论'
    assert len(model.requests) == 1
    assert done['budget']['charged_calls'] == 1
    assert done['budget']['charged_tokens'] == 15


def test_transport_retries_are_persistent_and_refusal_is_terminal(setup, database):
    repo, owner, view, fence = setup
    model = ScriptModel([ModelTransportError('transport_error'), ModelTransportError('provider_refused')])
    done = run_work(repo, model, None, fence, context_builder=context)
    assert done['state'] == 'failed'
    with database.connection() as c:
        rows = c.execute('select logical_step_id,retry_no,status from platform_hr_agent.model_attempts order by ordinal').fetchall()
    assert rows[0][0] == rows[1][0]
    assert [r[1] for r in rows] == [0, 1]
    assert done['budget']['charged_calls'] == 2


def test_prepared_recovery_reuses_attempt(setup):
    repo, owner, view, fence = setup
    prepared = repo.prepare_model(fence, context(repo, None, fence))
    model = ScriptModel([answer()])
    done = run_work(repo, model, None, fence, context_builder=context)
    assert model.requests[0].attempt_id == prepared.attempt_id
    assert done['budget']['charged_calls'] == 1


def test_committed_answer_recovery_never_calls_provider(setup):
    from app.hr_agent.model import collect_reply
    repo, owner, view, fence = setup
    prepared = repo.prepare_model(fence, context(repo, None, fence))
    repo.mark_model_sending(fence, prepared.attempt_id)
    repo.commit_model(fence, prepared.attempt_id, collect_reply(answer()))
    model = ScriptModel([])
    assert run_work(repo, model, None, fence, context_builder=context)['state'] == 'completed'
    assert model.requests == []


def test_retry_limit_is_three_actual_sends(setup, database):
    repo, owner, view, fence = setup
    model = ScriptModel([ModelTransportError('transport_error') for _ in range(3)])
    done = run_work(repo, model, None, fence, context_builder=context)
    assert done['state'] == 'failed'
    assert done['budget']['charged_calls'] == 3
    with database.connection() as c:
        assert [r[0] for r in c.execute('select retry_no from platform_hr_agent.model_attempts order by ordinal').fetchall()] == [0, 1, 2]


def test_appended_input_during_provider_call_rejects_old_answer_but_settles_usage(setup):
    repo, owner, view, fence = setup
    class AppendingModel:
        def stream(self, request):
            repo.append_input(owner, view['work_id'], {'expected_input_revision': 1, 'text': '新的公开目标', 'objects': [], 'references': [], 'question_id': None}, uuid4())
            yield from answer('过时回答禁止展示')
    done = run_work(repo, AppendingModel(), None, fence, context_builder=context)
    assert done['state'] == 'queued'
    assert done['input_revision'] == 2
    assert done['budget']['charged_tokens'] == 15
    assert all(m['body'] != '过时回答禁止展示' for m in repo.list_messages(owner, view['work_id'])['items'])


def test_cancel_during_provider_call_keeps_cancelled_state(setup):
    repo, owner, view, fence = setup
    class CancellingModel:
        def stream(self, request):
            repo.cancel(owner, view['work_id'], '停止', uuid4())
            yield from answer()
    done = run_work(repo, CancellingModel(), None, fence, context_builder=context)
    assert done['state'] == 'cancelled'
    assert done['answer_state'] != 'ended'
    assert done['budget']['charged_tokens'] == 15


def test_summary_commit_recovery_projects_summary_and_then_answers(setup, database):
    from app.hr_agent.model import collect_reply
    repo, owner, view, fence = setup
    entry = repo.read_selected_entries(fence)[0]
    provenance = {'derived_from': [{'entry_id': str(entry.entry_id), 'seq': entry.seq, 'input_revision': entry.input_revision}], 'policy_revision': 'test-v1'}
    ctx = ModelContext('summary', ({'role': 'user', 'content': '压缩已有公开材料'},), (), (), 20, fence.input_revision, summary_provenance=provenance)
    prepared = repo.prepare_model(fence, ctx)
    repo.mark_model_sending(fence, prepared.attempt_id)
    repo.commit_model(fence, prepared.attempt_id, collect_reply(answer('内部摘要正文')))
    model = ScriptModel([answer('真正回答')])
    done = run_work(repo, model, None, fence, context_builder=context)
    assert done['state'] == 'completed'
    bodies = [m['body'] for m in repo.list_messages(owner, view['work_id'])['items']]
    assert '内部摘要正文' not in bodies
    assert bodies[-1] == '真正回答'
    assert len(model.requests) == 1
    with database.connection() as c:
        assert c.execute("select count(*) from platform_hr_agent.entries where kind='summary'").fetchone()[0] == 1
        assert c.execute("select count(*) from platform_hr_agent.events where type='context_compacted'").fetchone()[0] == 1


@pytest.mark.parametrize('gate', ['model_calls', 'total_tokens', 'active_seconds'])
def test_each_budget_gate_independently_enters_durable_finalizing(setup, database, gate):
    repo, owner, view, fence = setup
    # This fixture changes only the configured gate, never writes a success.
    with repo.transaction() as c:
        work = repo._fence(c, fence)
        budget = repo._unseal('works', work['work_id'], 'sealed_budget', work)
        if gate == 'model_calls':
            budget['limits'][gate] = 3
        elif gate == 'total_tokens':
            budget['limits'][gate] = budget['reserve'][gate] + 100
        else:
            budget['limits'][gate] = budget['reserve'][gate] + .01
        repo._save_budget(c, work, budget)
    if gate == 'active_seconds':
        import time
        time.sleep(.02)
    phases = []
    def builder(repo, resources, fence):
        phases.append(repo.worker_view(fence)['phase'])
        return context(repo, resources, fence)
    scripts = [answer()]
    if gate == 'model_calls':
        scripts.insert(0, tool('save_note', {'body': '已完成一个调用', 'source_refs': [], 'open_questions': [], 'reading_targets': []}))
    done = run_work(repo, ScriptModel(scripts), None, fence, context_builder=builder, tool_executor=local_tool)
    assert phases == (['research', 'research', 'finalizing'] if gate == 'model_calls' else ['research', 'finalizing'])
    assert done['phase'] == 'finalizing'
    assert done['state'] == 'waiting_budget'
    assert done['answer_state'] == 'partial'
    assert done['budget']['charged_calls'] == (2 if gate == 'model_calls' else 1)
    assert done['checkpoint']['discovery_state'] == 'open'


def test_no_total_budget_does_not_call_provider(setup):
    repo, owner, view, fence = setup
    with repo.transaction() as c:
        work = repo._fence(c, fence)
        budget = repo._unseal('works', work['work_id'], 'sealed_budget', work)
        budget['limits']['total_tokens'] = 17000
        repo._save_budget(c, work, budget)
    model = ScriptModel([])
    def oversized(repository, resources, fence):
        return ModelContext('work', ({'role': 'user', 'content': 'x' * 20000},), (), (), 21000, fence.input_revision)
    done = run_work(repo, model, None, fence, context_builder=oversized)
    assert done['state'] == 'waiting_budget'
    assert model.requests == []
    assert done['budget']['charged_calls'] == 0


def tool(name, arguments):
    import json
    return [ModelEvent('tool_delta', {'index': 0, 'provider_call_id': 'call-1', 'name': name, 'arguments_delta': json.dumps(arguments)}), ModelEvent('usage', {'prompt_tokens': 10, 'completion_tokens': 5}), ModelEvent('stop', {'reason': 'tool_calls'})]


def local_tool(repo, resources, fence, operation_id):
    return repo.execute_local_tool(fence, operation_id)


def test_ask_user_waits_without_claiming_until_exact_question_answer(setup):
    repo, owner, view, fence = setup
    model = ScriptModel([tool('ask_user', {'question': '优先解决什么问题？', 'options': []})])
    done = run_work(repo, model, None, fence, context_builder=context, tool_executor=local_tool)
    assert done['state'] == 'waiting_user'
    assert repo.claim('another-worker', 60) is None
    question = next(m for m in repo.list_messages(owner, view['work_id'])['items'] if m['kind'] == 'question')
    changed = repo.append_input(owner, view['work_id'], {'expected_input_revision': 1, 'text': '先研究公开岗位', 'objects': [], 'references': [], 'question_id': question['entry_id']}, uuid4())
    assert changed['state'] == 'queued'
    next_fence = repo.claim('another-worker', 60)
    done = run_work(repo, ScriptModel([answer()]), None, next_fence, context_builder=context)
    assert done['state'] == 'completed'
    assert done['budget']['charged_calls'] == 2


def test_incomplete_tool_arguments_never_execute_side_effects(setup, database):
    repo, owner, view, fence = setup
    fragments = [ModelEvent('tool_delta', {'index': 0, 'provider_call_id': 'call-fragment', 'name': 'save_result', 'arguments_delta': '{"body":'}), ModelEvent('stop', {'reason': 'tool_calls'})]
    model = ScriptModel([fragments, fragments, fragments])
    done = run_work(repo, model, None, fence, context_builder=context, tool_executor=local_tool)
    assert done['state'] == 'failed'
    with database.connection() as c:
        assert c.execute("select count(*) from platform_hr_agent.operations where namespace like 'tool:%'").fetchone()[0] == 0
        assert c.execute('select count(*) from platform_hr_agent.results').fetchone()[0] == 0
    assert done['budget']['charged_calls'] == 3
    assert done['budget']['charged_tokens'] == (4096 + 20) * 3


def test_saved_result_survives_later_provider_refusal(setup, database):
    repo, owner, view, fence = setup
    args = {'result_id': None, 'expected_revision': None, 'kind': 'research', 'title': '公开结论', 'body': '已保存成果', 'objects': [], 'source_refs': [], 'preceding_refs': [], 'base_standard_ref': None, 'changes': [], 'basis': []}
    model = ScriptModel([tool('save_result', args), ModelTransportError('provider_refused')])
    done = run_work(repo, model, None, fence, context_builder=context, tool_executor=local_tool)
    assert done['state'] == 'failed'
    with database.connection() as c:
        assert c.execute('select count(*) from platform_hr_agent.results').fetchone()[0] == 1
        assert c.execute("select count(*) from platform_hr_agent.operations where namespace='tool:save_result' and status='committed'").fetchone()[0] == 1


def test_budget_extension_resumes_research_without_resetting_consumption(setup):
    repo, owner, view, fence = setup
    with repo.transaction() as c:
        work = repo._fence(c, fence)
        budget = repo._unseal('works', work['work_id'], 'sealed_budget', work)
        budget['limits']['total_tokens'] = 16100
        repo._save_budget(c, work, budget)
    done = run_work(repo, ScriptModel([answer('部分答案')]), None, fence, context_builder=context)
    assert done['state'] == 'waiting_budget'
    previous_tokens = done['budget']['charged_tokens']
    updated = repo.extend_budget(owner, view['work_id'], {'expected_budget_revision': done['budget']['revision'], 'addition': {'model_calls': 5, 'total_tokens': 20000, 'active_seconds': 0}, 'reason': '继续研究'}, uuid4())
    assert updated['phase'] == 'research'
    assert updated['budget']['charged_calls'] == 1
    assert updated['budget']['charged_tokens'] == previous_tokens
    fence = repo.claim('continue-worker', 60)
    done = run_work(repo, ScriptModel([answer('完整答案')]), None, fence, context_builder=context)
    assert done['state'] == 'completed'
    assert done['budget']['charged_calls'] == 2


def test_first_request_logs_exclude_sensitive_payloads(setup, caplog):
    import logging
    from app.hr_agent.model import ConfiguredHttpModelPort
    from test_hr_agent_worker_process import Provider, wire
    repo, owner, view, fence = setup
    caplog.set_level(logging.DEBUG)
    with Provider([wire()]) as provider:
        profile = {**repo.settings.provider_profile, 'endpoint': provider.endpoint}
        model = ConfiguredHttpModelPort.from_mapping(profile)
        run_work(repo, model, None, fence, context_builder=context)
    assert 'model_request' in caplog.text
    for forbidden in ('fake-local-secret', '公开招聘建议', '本地模拟结论', 'Bearer', 'http://127.0.0.1'):
        assert forbidden not in caplog.text


def test_real_http_provider_with_production_context_resource_and_save_tools(database, tmp_path):
    import json
    from app.hr_agent.model import ConfiguredHttpModelPort
    from app.hr_agent.resources import build_runtime_services
    from test_hr_agent_context import publication
    from test_hr_agent_worker_process import Provider, wire, grant_test_owner
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    settings = make_hr_settings(tmp_path)
    ref = publication(settings.knowledge_dir)
    def response(name, args):
        return 'data: '+json.dumps({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call-'+name, 'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})+'\n\ndata: [DONE]\n\n'
    saved = {'result_id': None, 'expected_revision': None, 'kind': 'research', 'title': '公开结论', 'body': '先检验假设，再比较证据。', 'objects': [], 'source_refs': [ref], 'preceding_refs': [], 'base_standard_ref': None, 'changes': [], 'basis': []}
    responses = [response('list_resources', {'kinds': ['method'], 'query': '', 'objects': [], 'cursor': None}), response('read_resource', {'ref': ref, 'offset': 0, 'limit': 8000}), response('save_result', saved), wire()]
    with Provider(responses) as provider:
        from dataclasses import replace
        settings = replace(settings, provider_profile={**settings.provider_profile, 'endpoint': provider.endpoint}, budget_profile={**settings.budget_profile, 'input_trigger_tokens': 24000, 'input_target_tokens': 20000})
        repo, resources = build_runtime_services(settings, database.connection)
        owner = grant_test_owner(database)
        view = repo.submit(owner, {'thread_id': None, 'text': '发现证据方法，读取正文并保存研究结论。', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
        done = run_work(repo, ConfiguredHttpModelPort.from_mapping(settings.provider_profile), resources, repo.claim('real-loop', 60))
        assert done['state'] == 'completed'
        assert len(provider.requests) == 4
        assert len(done['result_refs']) == 1
        encoded = json.dumps(provider.requests[-1], ensure_ascii=False)
        assert '先检验假设，再比较证据。' in encoded
        assert 'tool_call_id' in encoded
        assert done['checkpoint']['readings'][0]['state'] == 'returned'
        assert repo.list_messages(owner, view['work_id'])['items'][-1]['body'] == '本地模拟结论'
        with database.connection() as c:
            assert c.execute('select count(*) from platform_hr_agent.read_records').fetchone()[0] == 1
            assert c.execute('select count(*) from platform_hr_agent.results').fetchone()[0] == 1


def test_growing_context_exhausts_tokens_before_call_cap(setup):
    """Growing UTF-8 provider payload and missing usage; conservative accounting.

    This isolates the token gate with a controlled context assembler. It does
    not claim to calibrate a real tokenizer or assess summary quality.
    """
    import json
    from app.hr_agent.context import estimate_input_tokens
    from app.hr_agent.model import ConfiguredHttpModelPort
    from test_hr_agent_worker_process import Provider
    repo, owner, view, fence = setup
    with repo.transaction() as c:
        work = repo._fence(c, fence)
        budget = repo._unseal('works', work['work_id'], 'sealed_budget', work)
        budget['limits']['total_tokens'] = 45000
        repo._save_budget(c, work, budget)
    arguments = {'body': 'evidence ' * 220, 'source_refs': [], 'open_questions': ['尚需继续核对'], 'reading_targets': []}
    packet = 'data: ' + json.dumps({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'growing-note', 'function': {'name': 'save_note', 'arguments': json.dumps(arguments)}}]}, 'finish_reason': 'tool_calls'}]}) + '\n\ndata: [DONE]\n\n'
    estimates, phases = [], []
    def growing_context(repository, resources, current_fence):
        entries = repository.read_selected_entries(current_fence)
        messages = tuple({'role': 'user', 'content': e.body['body']} for e in entries if e.kind in {'user', 'note'})
        estimated = estimate_input_tokens(messages, (), 'conservative_utf8')
        estimates.append(estimated)
        phases.append(repository.worker_view(current_fence)['phase'])
        return ModelContext('work', messages, (), (), estimated, current_fence.input_revision)
    with Provider([packet] * 20) as provider:
        model = ConfiguredHttpModelPort.from_mapping({**repo.settings.provider_profile, 'endpoint': provider.endpoint})
        done = run_work(repo, model, None, fence, context_builder=growing_context, tool_executor=local_tool)
        lengths = [len(json.dumps(r['messages'])) for r in provider.requests]
        assert len(lengths) >= 3
        assert all(right > left for left, right in zip(lengths, lengths[1:]))
        assert done['state'] == 'waiting_budget'
        assert done['phase'] == 'finalizing'
        assert len(provider.requests) == done['budget']['charged_calls'] < 32
        assert done['budget']['charged_tokens'] > 16000
        assert done['budget']['charged_tokens'] + estimates[-1] + 4096 > 45000
        assert 'research' in phases and 'finalizing' in phases
        assert done['checkpoint']['open_questions'] == ['尚需继续核对']
        assert repo.claim('after-token-cap', 60) is None


@pytest.mark.parametrize('case', ['unknown_tool', 'asked_question', 'incomplete_arguments'])
def test_real_http_tool_failures_and_question_are_durable(setup, database, case):
    import json
    from app.hr_agent.model import ConfiguredHttpModelPort
    from test_hr_agent_worker_process import Provider, wire
    repo, owner, view, fence = setup
    name = 'unknown_action' if case == 'unknown_tool' else 'ask_user'
    arguments = '{"question":' if case == 'incomplete_arguments' else json.dumps({'question': '请明确公开研究范围', 'options': []})
    packet = 'data: ' + json.dumps({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'test-call', 'function': {'name': name, 'arguments': arguments}}]}, 'finish_reason': 'tool_calls'}]}) + '\n\ndata: [DONE]\n\n'
    responses = [packet, wire()] if case == 'unknown_tool' else [packet] * (3 if case == 'incomplete_arguments' else 1)
    with Provider(responses) as provider:
        model = ConfiguredHttpModelPort.from_mapping({**repo.settings.provider_profile, 'endpoint': provider.endpoint})
        from app.hr_agent.tools import execute_tool
        done = run_work(repo, model, None, fence, context_builder=context, tool_executor=execute_tool)
        expected = {'unknown_tool': 'completed', 'asked_question': 'waiting_user', 'incomplete_arguments': 'failed'}[case]
        assert done['state'] == expected
        assert len(provider.requests) == len(responses)
        if case == 'unknown_tool':
            events = repo.list_events(owner, view['work_id'])['items']
            assert any(e['error'] and e['error']['code'] == 'invalid_input' for e in events)
        elif case == 'asked_question':
            assert repo.list_messages(owner, view['work_id'])['items'][-1]['body'] == '请明确公开研究范围'
            assert repo.claim('waiting-question', 60) is None
        else:
            with database.connection() as c:
                assert c.execute("select count(*) from platform_hr_agent.operations where namespace like 'tool:%'").fetchone()[0] == 0
