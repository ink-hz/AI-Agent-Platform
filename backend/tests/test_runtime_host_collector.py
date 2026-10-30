"""Real local HTTP -> standalone collector -> actual dashboard file reader."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.runtime_dashboard.health import HostObservationReader

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'deploy/cloud/collect-runtime-observations.py'
SHA = '0123456789abcdef0123456789abcdef01234567'


def test_standalone_collector_exists():
    assert SCRIPT.is_file(), 'standalone host collector is missing'


@pytest.fixture
def collector():
    assert SCRIPT.is_file(), 'standalone host collector is missing'
    spec = importlib.util.spec_from_file_location('runtime_host_collector', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def server():
    config = {'/health/ready': (200, {'ready': True, 'release_sha': SHA, 'secret': 'PRIVATE_SENTINEL'}),
              '/health': (200, {'status': 'ok', 'employee': 'PRIVATE_SENTINEL'})}
    requests = []
    started = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            started.set()
            status, body = config[self.path][:2]
            options = config[self.path][2] if len(config[self.path]) > 2 else {}
            time.sleep(options.get('delay', 0))
            try:
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', '/must-not-follow')
                self.end_headers()
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                if options.get('drip'):
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(options['drip'])
                else:
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, config, requests, started
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def targets(collector, port):
    return (collector.Target('hr-bot', '127.0.0.1', port, '/health/ready'),
            collector.Target('ai-admin-agent', '127.0.0.1', port, '/health'))


def consume(path):
    return HostObservationReader(path).read(datetime.now(timezone.utc))


def test_real_http_file_reader_dto_and_privacy(collector, tmp_path, monkeypatch):
    with server() as (port, _, calls, _):
        monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
        monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
        monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
        monkeypatch.setenv('NO_PROXY', '')
        path = tmp_path / 'snapshot.json'
        collector.run_cycle(path, targets=targets(collector, port))
    document = json.loads(path.read_text())
    assert set(document) == {'schema_version', 'generated_at', 'observations'}
    assert len(document['observations']) == 2
    assert set(document['observations'][0]) == {'application_id', 'state', 'observed_at', 'error', 'version'}
    assert 'PRIVATE_SENTINEL' not in path.read_text()
    assert os.stat(path).st_mode & 0o777 == 0o644
    observed = consume(path)
    assert observed['hr-bot'].runtime.state == observed['ai-admin-agent'].runtime.state == 'healthy'
    assert observed['hr-bot'].deployment.version == SHA
    assert observed['hr-bot'].deployment.evidence == 'ready_release_sha'
    assert observed['ai-admin-agent'].deployment.version is None
    assert calls == ['/health/ready', '/health']


@pytest.mark.parametrize('status,body,state,error', [
    (503, b'PRIVATE_SENTINEL', 'degraded', 'health_http_error'),
    (404, b'PRIVATE_SENTINEL', 'unknown', 'health_http_error'),
    (302, b'PRIVATE_SENTINEL', 'unknown', 'health_redirect'),
    (200, b'{PRIVATE_SENTINEL', 'unknown', 'health_invalid_json'),
    (200, b'x' * 16385, 'unknown', 'health_payload_limit'),
    (200, {'ready': False, 'release_sha': SHA}, 'degraded', 'health_not_ready'),
    (200, {'status': 'ok'}, 'degraded', 'health_not_ready'),
])
def test_source_failure_is_current_and_independent(collector, tmp_path, status, body, state, error):
    path = tmp_path / 'snapshot.json'
    with server() as (port, config, calls, _):
        collector.run_cycle(path, targets=targets(collector, port))
        previous = json.loads(path.read_text())['observations'][0]['observed_at']
        config['/health/ready'] = status, body
        collector.run_cycle(path, targets=targets(collector, port))
    observed = consume(path)
    hr = observed['hr-bot']
    assert (hr.runtime.state, hr.runtime.error) == (state, error)
    assert hr.runtime.observed_at.isoformat() != previous
    assert hr.deployment.version is None
    assert observed['ai-admin-agent'].runtime.state == 'healthy'
    assert calls == ['/health/ready', '/health', '/health/ready', '/health']
    assert 'PRIVATE_SENTINEL' not in path.read_text()


@pytest.mark.parametrize('sha', ['main', 'A' * 40, 'f' * 39, None, 123])
def test_invalid_version_is_not_fabricated(collector, tmp_path, sha):
    with server() as (port, config, _, _):
        config['/health/ready'] = 200, {'ready': True, 'release_sha': sha}
        collector.run_cycle(tmp_path / 'snapshot.json', targets=targets(collector, port))
    hr = consume(tmp_path / 'snapshot.json')['hr-bot']
    assert hr.runtime.state == 'healthy'
    assert hr.deployment.version is None


@pytest.mark.parametrize('options', [{'delay': .3}, {'drip': .03}])
def test_absolute_deadline_including_dripping_body(collector, tmp_path, options):
    with server() as (port, config, calls, _):
        config['/health/ready'] = 200, {'ready': True}, options
        start = time.monotonic()
        collector.run_cycle(tmp_path / 'snapshot.json', targets=targets(collector, port), timeout_seconds=.1)
        elapsed = time.monotonic() - start
    observed = consume(tmp_path / 'snapshot.json')
    assert observed['hr-bot'].runtime.error == 'health_timeout'
    assert observed['ai-admin-agent'].runtime.state == 'healthy'
    assert elapsed < .3
    assert calls == ['/health/ready', '/health']


def test_refused_is_offline_with_no_retry(collector, tmp_path):
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # macOS times out on bound/not-listening sockets; close the reserved port.
    collector.run_cycle(tmp_path / 'snapshot.json', targets=targets(collector, port))
    assert all(item.runtime.state == 'offline' and item.runtime.error == 'health_connection_refused'
               for item in consume(tmp_path / 'snapshot.json').values())


@pytest.mark.parametrize('changes', [
    {'host': 'example.com'}, {'host': '127.0.0.2'}, {'host': 'localhost'},
    {'path': '/admin'}, {'path': '/health?token=secret'}, {'port': 0},
    {'application_id': 'unknown'}, {'application_id': 'hr-bot', 'path': '/health'},
])
def test_unsafe_target_rejected_before_transport(collector, tmp_path, changes):
    values = dict(application_id='hr-bot', host='127.0.0.1', port=8012, path='/health/ready')
    values.update(changes)
    with pytest.raises(ValueError, match='trusted_target_invalid'):
        collector.run_cycle(tmp_path / 'snapshot.json', targets=(collector.Target(**values),))
    assert not (tmp_path / 'snapshot.json').exists()


def test_duplicate_or_incomplete_target_set_rejected(collector, tmp_path):
    target = collector.Target('hr-bot', '127.0.0.1', 8012, '/health/ready')
    for configured in [(target,), (target, target)]:
        with pytest.raises(ValueError, match='trusted_target_invalid'):
            collector.run_cycle(tmp_path / 'snapshot.json', targets=configured)


@pytest.mark.parametrize('kind', ['symlink', 'directory', 'fifo'])
def test_unsafe_output_is_rejected_without_changing_other_files(collector, tmp_path, kind):
    path = tmp_path / 'snapshot.json'
    victim = tmp_path / 'keep'
    victim.write_text('private untouched')
    if kind == 'symlink':
        path.symlink_to(victim)
    elif kind == 'directory':
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(ValueError, match='unsafe_output'):
        collector.atomic_write(path, {'schema_version': 1})
    assert victim.read_text() == 'private untouched'
    assert not list(tmp_path.glob('.snapshot.json.*'))


def test_atomic_replace_fsync_and_own_temp_cleanup(collector, tmp_path, monkeypatch):
    path = tmp_path / 'snapshot.json'
    path.write_text('old complete file')
    unrelated = tmp_path / '.snapshot.json.unrelated'
    unrelated.write_text('keep')
    original_replace = collector.os.replace
    syncs = []
    original_fsync = collector.os.fsync
    def sync(fd):
        syncs.append(os.fstat(fd).st_mode)
        original_fsync(fd)
    def replace(src, dst, **kwargs):
        assert path.read_text() == 'old complete file'
        assert os.stat(src, dir_fd=kwargs['src_dir_fd']).st_mode & 0o777 == 0o644
        original_replace(src, dst, **kwargs)
    monkeypatch.setattr(collector.os, 'replace', replace)
    monkeypatch.setattr(collector.os, 'fsync', sync)
    collector.atomic_write(path, {'schema_version': 1})
    assert json.loads(path.read_text()) == {'schema_version': 1}
    assert len(syncs) == 2
    monkeypatch.setattr(collector.os, 'replace', lambda *a, **k: (_ for _ in ()).throw(OSError('PRIVATE_SENTINEL')))
    with pytest.raises(OSError):
        collector.atomic_write(path, {'schema_version': 2})
    assert json.loads(path.read_text()) == {'schema_version': 1}
    assert list(tmp_path.glob('.snapshot.json.*')) == [unrelated]


def test_world_writable_output_directory_rejected(collector, tmp_path):
    tmp_path.chmod(0o777)
    with pytest.raises(ValueError, match='unsafe_output_directory'):
        collector.atomic_write(tmp_path / 'snapshot.json', {})


def test_reader_time_missing_corrupt_and_per_row_boundaries(collector, tmp_path):
    path = tmp_path / 'snapshot.json'
    assert consume(path)['hr-bot'].runtime.error == 'observation_missing'
    path.write_text('{')
    assert consume(path)['hr-bot'].runtime.error == 'observation_invalid'
    with server() as (port, _, _, _):
        collector.run_cycle(path, targets=targets(collector, port))
    now = datetime.now(timezone.utc)
    document = json.loads(path.read_text())
    for offset, error in [(-91, 'observation_stale'), (1, 'observation_future')]:
        document['observations'][0]['observed_at'] = (now + timedelta(seconds=offset)).isoformat()
        collector.atomic_write(path, document)
        observations = HostObservationReader(path).read(now)
        assert observations['hr-bot'].runtime.error == error
        assert observations['ai-admin-agent'].runtime.state == 'healthy'
    document['generated_at'] = (now - timedelta(seconds=91)).isoformat()
    document['observations'][0]['observed_at'] = now.isoformat()
    collector.atomic_write(path, document)
    assert all(row.runtime.error == 'observation_stale' for row in HostObservationReader(path).read(now).values())


def process_command(port, path):
    # Trusted local fixture injection, deliberately absent from the deployed CLI.
    return [sys.executable, '-c',
            "import runpy; m=runpy.run_path(%r); f=m['main']; f.__globals__['TARGETS']=(m['Target']('hr-bot','127.0.0.1',%d,'/health/ready'),m['Target']('ai-admin-agent','127.0.0.1',%d,'/health')); raise SystemExit(f(['--output',%r]))" % (str(SCRIPT), port, port, str(path))]


def test_real_process_killed_cycle_does_not_publish_and_next_cycle_is_explicit(collector, tmp_path):
    path = tmp_path / 'snapshot.json'
    with server() as (port, config, calls, started):
        collector.run_cycle(path, targets=targets(collector, port))
        old = path.read_bytes()
        started.clear()
        config['/health/ready'] = 200, {'ready': True}, {'delay': 1}
        process = subprocess.Popen(process_command(port, path), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert started.wait(2)
        process.send_signal(signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode < 0 and stdout == stderr == b''
        assert path.read_bytes() == old
        config['/health/ready'] = 200, {'ready': False}
        cycle = subprocess.run(process_command(port, path), capture_output=True, timeout=4)
    assert cycle.returncode == 0
    assert cycle.stdout == b'runtime_observations_written\n' and cycle.stderr == b''
    assert consume(path)['hr-bot'].runtime.state == 'degraded'
    assert consume(path)['ai-admin-agent'].runtime.state == 'healthy'
    assert calls == ['/health/ready', '/health', '/health/ready', '/health/ready', '/health']


def test_cli_failure_is_sanitized_and_rejects_url_option(collector, tmp_path):
    with server() as (port, _, _, _):
        path = tmp_path / 'snapshot.json'
        path.mkdir()
        cycle = subprocess.run(process_command(port, path), capture_output=True, timeout=4)
    assert cycle.returncode == 1
    assert cycle.stdout == b'' and cycle.stderr == b'runtime_observations_write_failed\n'
    result = subprocess.run([sys.executable, str(SCRIPT), '--url', 'http://example.com/secret'], capture_output=True)
    assert result.returncode == 2


def test_systemd_static_install_contract(collector):
    service = (ROOT / 'deploy/cloud/systemd/orbbec-platform-runtime-observer.service').read_text()
    timer = (ROOT / 'deploy/cloud/systemd/orbbec-platform-runtime-observer.timer').read_text()
    for line in ['Type=oneshot', 'DynamicUser=yes', 'StateDirectory=orbbec-platform-runtime',
                 'StateDirectoryMode=0755', 'UMask=0022', 'NoNewPrivileges=yes',
                 'ProtectSystem=strict', 'ProtectHome=yes', 'PrivateTmp=yes',
                 'RestrictAddressFamilies=AF_INET', 'IPAddressDeny=any',
                 'IPAddressAllow=127.0.0.1/32', 'TimeoutStartSec=10s',
                 'ExecStart=/usr/bin/python3 /usr/local/libexec/orbbec-platform/collect-runtime-observations.py --output /var/lib/orbbec-platform-runtime/snapshot.json']:
        assert line in service.splitlines()
    assert 'PrivateNetwork=yes' not in service
    assert 'OnUnitActiveSec=30s' in timer
    assert 'Unit=orbbec-platform-runtime-observer.service' in timer
    assert collector.TARGETS == (collector.Target('hr-bot', '127.0.0.1', 8012, '/health/ready'),
                                 collector.Target('ai-admin-agent', '127.0.0.1', 8011, '/health'))


def test_temp_name_collision_never_deletes_someone_elses_file(collector, tmp_path, monkeypatch):
    unrelated = tmp_path / '.snapshot.json.collision'
    unrelated.write_text('keep')
    monkeypatch.setattr(collector.secrets, 'token_hex', lambda _: 'collision')
    with pytest.raises(FileExistsError):
        collector.atomic_write(tmp_path / 'snapshot.json', {})
    assert unrelated.read_text() == 'keep'


def test_state_directory_symlink_remains_supported(collector, tmp_path):
    state = tmp_path / 'owned-state'
    state.mkdir(mode=0o755)
    exposed = tmp_path / 'state-link'
    exposed.symlink_to(state, target_is_directory=True)
    collector.atomic_write(exposed / 'snapshot.json', {'schema_version': 1})
    assert json.loads((state / 'snapshot.json').read_text()) == {'schema_version': 1}


def test_one_refused_source_does_not_refresh_or_reuse_old_healthy(collector, tmp_path):
    import socket
    path = tmp_path / 'snapshot.json'
    with server() as (port, _, _, _):
        collector.run_cycle(path, targets=targets(collector, port))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            refused = sock.getsockname()[1]
        configured = (collector.Target('hr-bot', '127.0.0.1', refused, '/health/ready'),
                      collector.Target('ai-admin-agent', '127.0.0.1', port, '/health'))
        collector.run_cycle(path, targets=configured)
    observed = consume(path)
    assert observed['hr-bot'].runtime.state == 'offline'
    assert observed['hr-bot'].deployment.version is None
    assert observed['ai-admin-agent'].runtime.state == 'healthy'
    assert observed['hr-bot'].runtime.observed_at < observed['ai-admin-agent'].runtime.observed_at


def test_directory_descriptor_closes_even_when_temp_cleanup_fails(collector, tmp_path, monkeypatch):
    closed = []
    original_close = collector.os.close
    def close(fd):
        closed.append(fd)
        original_close(fd)
    monkeypatch.setattr(collector.os, 'close', close)
    monkeypatch.setattr(collector.os, 'replace', lambda *a, **k: (_ for _ in ()).throw(OSError('replace failed')))
    monkeypatch.setattr(collector.os, 'unlink', lambda *a, **k: (_ for _ in ()).throw(PermissionError('cleanup failed')))
    with pytest.raises(PermissionError):
        collector.atomic_write(tmp_path / 'snapshot.json', {})
    assert len(closed) == 1
