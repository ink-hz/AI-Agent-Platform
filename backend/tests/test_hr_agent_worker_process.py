"""Independent local Worker processes and HTTP SSE transport, no live provider."""
import threading
from app.hr_agent.worker import run_worker


def test_worker_shutdown_event_stops_claiming():
    class Repository:
        def claim(self, *args):
            raise AssertionError('shutdown must stop new claims')
    stop = threading.Event()
    stop.set()
    run_worker(Repository(), None, None, stop_event=stop)

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from app.hr_agent.repository import HrAgentRepository
from hr_agent_support import hr_agent_database, make_hr_settings


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.lock = threading.Lock()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert self.headers['Authorization'] == 'Bearer fake-local-secret'
                with owner.lock:
                    owner.requests.append(body)
                    reply = owner.responses.pop(0)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                if isinstance(reply, tuple):
                    delay, reply = reply
                    time.sleep(delay)
                try:
                    self.wfile.write(reply.encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
    @property
    def endpoint(self):
        return f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions'


def wire(*, tool=False):
    if tool:
        args = {'result_id': None, 'expected_revision': None, 'kind': 'research', 'title': '公开研究', 'body': '已保存公开结论', 'objects': [], 'source_refs': [], 'preceding_refs': [], 'base_standard_ref': None, 'changes': [], 'basis': []}
        delta = {'tool_calls': [{'index': 0, 'id': 'call-save', 'function': {'name': 'save_result', 'arguments': json.dumps(args)}}]}
        reason = 'tool_calls'
    else:
        delta, reason = {'content': '本地模拟结论'}, 'stop'
    event = {'choices': [{'delta': delta, 'finish_reason': reason}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
    return 'data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n'


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('local process condition timed out')


@pytest.fixture(scope='module')
def database():
    with hr_agent_database() as db:
        yield db


def process_config(tmp_path, db, endpoint):
    make_hr_settings(tmp_path, PLATFORM_HR_AGENT_LEASE_SECONDS='3', PLATFORM_HR_AGENT_HEARTBEAT_SECONDS='1')
    profile = tmp_path/'provider_profile_file.json'
    value = json.loads(profile.read_text()); value['endpoint'] = endpoint
    profile.write_text(json.dumps(value))
    env = {'PLATFORM_HR_AGENT_ENABLED': '1', 'PLATFORM_HR_AGENT_LEASE_SECONDS': '3', 'PLATFORM_HR_AGENT_HEARTBEAT_SECONDS': '1', 'PLATFORM_HR_AGENT_WORK_DIR': str(tmp_path/'work'), 'PLATFORM_HR_AGENT_KNOWLEDGE_DIR': str(tmp_path/'knowledge')}
    for key in ('CONTENT_KEYRING_FILE','PROVIDER_PROFILE_FILE','BUDGET_PROFILE_FILE','DIAGNOSTIC_PROFILE_FILE'):
        env['PLATFORM_HR_AGENT_'+key] = str(tmp_path/(key.lower()+'.json'))
    config = tmp_path/'process.json'; config.write_text(json.dumps(env))
    dsn = tmp_path/'dsn'; dsn.write_text(db.dsn); dsn.chmod(0o600)
    from app.hr_agent.config import load_hr_agent_settings
    return config, load_hr_agent_settings(env)


def launch(config, point='', scenario='work'):
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(config), point, scenario], cwd=Path(__file__).parents[1], env={**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1])}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.mark.parametrize('point', ['model_prepared', 'model_committed', 'tool_committed'])
def test_kill_restart_same_database_preserves_attempt_and_result(database, tmp_path, point):
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    with Provider([wire(tool=True), wire()]) as provider:
        config, settings = process_config(tmp_path, database, provider.endpoint)
        repo = HrAgentRepository(database.connection, settings.create_codec(), settings=settings)
        owner = uuid4()
        view = repo.submit(owner, {'thread_id': None, 'text': '公开研究', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
        first = launch(config, point)
        try:
            wait_until(lambda: (tmp_path/'marker').exists() or first.poll() is not None)
            assert first.poll() is None, first.communicate()[1].decode()
            with database.connection() as c:
                old_attempt = c.execute('select attempt_id from platform_hr_agent.model_attempts order by ordinal limit 1').fetchone()[0]
            # Stall beyond the lease: heartbeat must remain independent of the
            # provider/runtime observer, preventing another worker from stealing.
            time.sleep(3.2)
            assert repo.claim('contender', 3) is None
            first.kill(); first.wait(timeout=5)
            time.sleep(3.2)
            second = launch(config)
            try:
                wait_until(lambda: repo.get_work(owner, view['work_id'])['state'] == 'completed' or second.poll() is not None)
                done = repo.get_work(owner, view['work_id'])
                assert done['state'] == 'completed', second.communicate(timeout=1)[1].decode() if second.poll() is not None else done
                assert len(provider.requests) == 2
                with database.connection() as c:
                    assert c.execute('select count(*) from platform_hr_agent.results').fetchone()[0] == 1
                    attempts = c.execute('select attempt_id,retry_no from platform_hr_agent.model_attempts order by ordinal').fetchall()
                    assert attempts[0][0] == old_attempt
                    assert [a[1] for a in attempts] == [0, 0]
                assert done['budget']['charged_calls'] == 2
            finally:
                second.terminate(); second.wait(timeout=5)
        finally:
            if first.poll() is None:
                first.kill(); first.wait(timeout=5)


def _process_fixture():
    """Test-only entrypoint injects a fault observer; production has no fault CLI."""
    import psycopg
    from app.hr_agent.config import load_hr_agent_settings
    from app.hr_agent.model import ConfiguredHttpModelPort
    from app.hr_agent.runtime import run_work
    from app.hr_agent.types import ModelContext
    config = Path(sys.argv[1]); point = sys.argv[2]; scenario = sys.argv[3]
    settings = load_hr_agent_settings(json.loads(config.read_text()))
    dsn = (config.parent/'dsn').read_text()
    repo = HrAgentRepository(lambda: psycopg.connect(dsn), settings.create_codec(), settings=settings)
    def context(repository, resources, fence):
        entries = repository.read_selected_entries(fence)
        if scenario == 'summary' and not any(e.kind == 'summary' for e in entries):
            provenance = {'derived_from': [{'entry_id': str(e.entry_id), 'seq': e.seq, 'input_revision': e.input_revision} for e in entries], 'policy_revision': 'process-v1'}
            return ModelContext('summary', ({'role': 'user', 'content': '压缩公开材料'},), (), (), 20, fence.input_revision, summary_provenance=provenance)
        return ModelContext('work', ({'role': 'user', 'content': repository.context_input(fence)[0]['text']},), (), (), 20, fence.input_revision)
    def observe(event, identity):
        if event == point:
            (config.parent/'marker').write_text(str(identity))
            threading.Event().wait()
    original_receipt = repo._receipt
    def receipt(cursor, operation_id, value):
        result = original_receipt(cursor, operation_id, value)
        if point == 'write_uncommitted':
            observe('write_uncommitted', operation_id)
        return result
    repo._receipt = receipt
    def runner(repository, model, resources, fence, **kwargs):
        return run_work(repository, model, resources, fence, context_builder=context, tool_executor=lambda r, rs, f, op: r.execute_local_tool(f, op), observer=observe, **kwargs)
    run_worker(repo, ConfiguredHttpModelPort.from_mapping(settings.provider_profile), None, poll_seconds=.05, runner=runner)


@pytest.mark.parametrize('scenario', ['work', 'summary', 'sending'])
def test_real_process_reply_projection_and_silent_sending_recovery(database, tmp_path, scenario):
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    replies = [(10, wire()), wire()] if scenario == 'sending' else [wire(), wire()]
    with Provider(replies) as provider:
        config, settings = process_config(tmp_path, database, provider.endpoint)
        repo = HrAgentRepository(database.connection, settings.create_codec(), settings=settings)
        owner = uuid4()
        view = repo.submit(owner, {'thread_id': None, 'text': '公开研究', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
        first = launch(config, '' if scenario == 'sending' else 'model_committed', scenario)
        try:
            wait_until(lambda: bool(provider.requests) if scenario == 'sending' else (tmp_path/'marker').exists())
            if scenario == 'sending':
                time.sleep(3.2)
                assert repo.claim('competitor', 3) is None
            first.kill(); first.wait(timeout=5)
            time.sleep(3.2)
            second = launch(config, scenario=scenario)
            try:
                wait_until(lambda: repo.get_work(owner, view['work_id'])['state'] == 'completed')
                assert len(provider.requests) == (1 if scenario == 'work' else 2)
                with database.connection() as c:
                    attempts = c.execute('select logical_step_id,retry_no,status from platform_hr_agent.model_attempts order by ordinal').fetchall()
                    summaries = c.execute("select count(*) from platform_hr_agent.entries where kind='summary'").fetchone()[0]
                    answers = c.execute("select count(*) from platform_hr_agent.entries where kind='assistant'").fetchone()[0]
                assert answers == 1
                assert summaries == int(scenario == 'summary')
                if scenario == 'sending':
                    assert attempts[0][0] == attempts[1][0]
                    assert [a[1] for a in attempts] == [0, 1]
                    assert [a[2] for a in attempts] == ['interrupted', 'committed']
                    assert repo.get_work(owner, view['work_id'])['budget']['charged_tokens'] > 15
            finally:
                second.terminate(); second.wait(timeout=5)
        finally:
            if first.poll() is None:
                first.kill(); first.wait(timeout=5)


def test_production_entrypoint_disabled_and_safe_configuration_failure(tmp_path):
    base = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1])}
    disabled = subprocess.run([sys.executable, '-m', 'app.hr_agent.worker'], env={**base, 'PLATFORM_HR_AGENT_ENABLED': '0'}, capture_output=True, text=True, timeout=10)
    assert disabled.returncode == 0
    failed = subprocess.run([sys.executable, '-m', 'app.hr_agent.worker'], env={**base, 'PLATFORM_HR_AGENT_ENABLED': '1', 'PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE': '/missing/secret-token-must-not-log'}, capture_output=True, text=True, timeout=10)
    assert failed.returncode == 1
    assert failed.stderr == 'HR Worker stopped: configuration_or_runtime_unavailable\n'
    assert 'secret-token-must-not-log' not in failed.stdout + failed.stderr


def test_compose_hr_worker_is_opt_in_and_has_no_relay():
    import yaml
    document = yaml.safe_load((Path(__file__).parents[2]/'deploy/cloud/compose.yaml').read_text())
    worker = document['services']['platform-hr-agent-worker']
    assert worker['profiles'] == ['hr-agent']
    assert worker['command'] == ['python', '-m', 'app.hr_agent.worker']
    assert worker['environment']['PLATFORM_EXECUTION_RELAY_ENABLED'] == '0'
    assert worker['read_only'] is True
    assert worker['cap_drop'] == ['ALL']


def test_uncommitted_result_rolls_back_on_real_process_kill(database, tmp_path):
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    with Provider([wire(tool=True), wire()]) as provider:
        config, settings = process_config(tmp_path, database, provider.endpoint)
        repo = HrAgentRepository(database.connection, settings.create_codec(), settings=settings)
        owner = uuid4()
        view = repo.submit(owner, {'thread_id': None, 'text': '公开研究', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
        first = launch(config, 'write_uncommitted')
        try:
            wait_until(lambda: (tmp_path/'marker').exists())
            with database.connection() as c:
                assert c.execute('select count(*) from platform_hr_agent.results').fetchone()[0] == 0
            first.kill(); first.wait(timeout=5)
            time.sleep(3.2)
            second = launch(config)
            try:
                wait_until(lambda: repo.get_work(owner, view['work_id'])['state'] == 'completed')
                with database.connection() as c:
                    assert c.execute('select count(*) from platform_hr_agent.results').fetchone()[0] == 1
                    assert c.execute('select count(*) from platform_hr_agent.result_revisions').fetchone()[0] == 1
                assert len(provider.requests) == 2
            finally:
                second.terminate(); second.wait(timeout=5)
        finally:
            if first.poll() is None:
                first.kill(); first.wait(timeout=5)


def test_cancel_wins_before_new_side_effect_in_real_process(database, tmp_path):
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    with Provider([wire(tool=True)]) as provider:
        config, settings = process_config(tmp_path, database, provider.endpoint)
        repo = HrAgentRepository(database.connection, settings.create_codec(), settings=settings)
        owner = uuid4()
        view = repo.submit(owner, {'thread_id': None, 'text': '公开研究', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
        first = launch(config, 'model_committed')
        try:
            wait_until(lambda: (tmp_path/'marker').exists())
            repo.cancel(owner, view['work_id'], '取消尚未保存的操作', uuid4())
            first.kill(); first.wait(timeout=5)
            assert repo.claim('restart-after-cancel', 3) is None
            with database.connection() as c:
                assert c.execute('select count(*) from platform_hr_agent.results').fetchone()[0] == 0
            assert repo.get_work(owner, view['work_id'])['state'] == 'cancelled'
            assert len(provider.requests) == 1
        finally:
            if first.poll() is None:
                first.kill(); first.wait(timeout=5)


def test_real_process_new_input_rejects_old_answer_and_only_settles_usage(database, tmp_path):
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    with Provider([(1, wire()), wire()]) as provider:
        config, settings = process_config(tmp_path, database, provider.endpoint)
        repo = HrAgentRepository(database.connection, settings.create_codec(), settings=settings)
        owner = uuid4()
        view = repo.submit(owner, {'thread_id': None, 'text': '旧公开目标', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
        process = launch(config)
        try:
            wait_until(lambda: len(provider.requests) == 1)
            repo.append_input(owner, view['work_id'], {'expected_input_revision': 1, 'text': '新公开目标', 'objects': [], 'references': [], 'question_id': None}, uuid4())
            wait_until(lambda: repo.get_work(owner, view['work_id'])['state'] == 'completed')
            assert provider.requests[1]['messages'][0]['content'] == '新公开目标'
            with database.connection() as c:
                assert c.execute("select input_revision from platform_hr_agent.entries where kind='assistant'").fetchall() == [(2,)]
                assert c.execute('select status from platform_hr_agent.model_attempts order by ordinal').fetchall() == [('superseded',), ('committed',)]
            done = repo.get_work(owner, view['work_id'])
            assert done['budget']['charged_calls'] == 2
            assert done['budget']['charged_tokens'] == 30
        finally:
            process.terminate(); process.wait(timeout=5)


def test_finalizing_survives_real_restart_then_explicit_budget_extension(database, tmp_path):
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    with Provider([wire(), wire()]) as provider:
        config, settings = process_config(tmp_path, database, provider.endpoint)
        repo = HrAgentRepository(database.connection, settings.create_codec(), settings=settings)
        owner = uuid4()
        view = repo.submit(owner, {'thread_id': None, 'text': '公开研究', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
        with repo.transaction() as c:
            work = repo._work(c, owner, view['work_id'], True)
            budget = repo._unseal('works', work['work_id'], 'sealed_budget', work)
            budget['limits']['total_tokens'] = 16100
            repo._save_budget(c, work, budget)
        first = launch(config, 'model_committed')
        try:
            wait_until(lambda: (tmp_path/'marker').exists())
            assert repo.get_work(owner, view['work_id'])['phase'] == 'finalizing'
            first.kill(); first.wait(timeout=5)
            time.sleep(3.2)
            second = launch(config)
            try:
                wait_until(lambda: repo.get_work(owner, view['work_id'])['state'] == 'waiting_budget')
                paused = repo.get_work(owner, view['work_id'])
                assert paused['phase'] == 'finalizing'
                assert len(provider.requests) == 1
                repo.extend_budget(owner, view['work_id'], {'expected_budget_revision': paused['budget']['revision'], 'addition': {'model_calls': 5, 'total_tokens': 20000, 'active_seconds': 0}, 'reason': '显式继续'}, uuid4())
                wait_until(lambda: repo.get_work(owner, view['work_id'])['state'] == 'completed')
                done = repo.get_work(owner, view['work_id'])
                assert done['phase'] == 'research'
                assert done['budget']['charged_calls'] == 2
                assert len(provider.requests) == 2
            finally:
                second.terminate(); second.wait(timeout=5)
        finally:
            if first.poll() is None:
                first.kill(); first.wait(timeout=5)


def test_production_worker_module_claims_public_work_and_exits_on_sigterm(database, tmp_path):
    from app.hr_agent.config import load_hr_agent_settings
    from app.hr_agent.resources import build_runtime_services
    from test_hr_agent_context import publication
    with database.admin_connection() as c:
        c.execute('truncate platform_hr_agent.threads, platform_hr_agent.operations cascade')
    with Provider([wire()]) as provider:
        config, _ = process_config(tmp_path, database, provider.endpoint)
        publication(tmp_path/'knowledge')
        environment = json.loads(config.read_text())
        environment['PLATFORM_CONTROL_DATABASE_URL_FILE'] = str(tmp_path/'dsn')
        environment['PLATFORM_CONVERSATION_ATTACHMENT_ENABLED'] = '0'
        settings = load_hr_agent_settings(environment)
        repo, _ = build_runtime_services(settings, database.connection)
        owner = grant_test_owner(database)
        view = repo.submit(owner, {'thread_id': None, 'text': '公开岗位咨询', 'objects': [], 'references': [], 'budget_profile': 'test'}, uuid4())
        process = subprocess.Popen([sys.executable, '-m', 'app.hr_agent.worker'], env={**os.environ, **environment, 'PYTHONPATH': str(Path(__file__).parents[1])}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            wait_until(lambda: repo.get_work(owner, view['work_id'])['state'] == 'completed' or process.poll() is not None)
            assert repo.get_work(owner, view['work_id'])['state'] == 'completed'
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0
            assert len(provider.requests) == 1
            assert b'model_request' in stderr
            assert b'fake-local-secret' not in stdout + stderr
            assert '公开岗位咨询'.encode() not in stdout + stderr
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)


def grant_test_owner(database):
    """Real disposable directory identity and HR grant, no permission bypass."""
    from test_agent_brain_migration import _seed_active_directory, _insert_grant
    with database.admin_connection() as connection:
        owner, *_ = _seed_active_directory(connection)
        _insert_grant(connection, agent_id='hr-bot', target_kind='user', actor_id=owner, user_id=owner)
    return owner


def test_compose_api_opt_in_uses_same_hr_configuration_paths():
    import yaml
    root = Path(__file__).parents[2]/'deploy/cloud'
    base = yaml.safe_load((root/'compose.yaml').read_text())
    override = yaml.safe_load((root/'compose.hr-agent.yaml').read_text())
    assert 'PLATFORM_HR_AGENT_ENABLED' not in base['services']['platform-api']['environment']
    api = override['services']['platform-api']
    worker = base['services']['platform-hr-agent-worker']
    for key, value in api['environment'].items():
        assert worker['environment'][key] == value
    assert 'PLATFORM_EXECUTION_RELAY_ENABLED' not in api['environment']
    assert 'platform-hr-agent-secrets:/run/hr-agent-secrets:ro' in api['volumes']


if __name__ == '__main__':
    _process_fixture()
