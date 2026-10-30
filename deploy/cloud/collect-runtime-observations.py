#!/usr/bin/python3
"""Content-free host-loopback observations; Python 3.12 standard library only.

The CLI has no URL/target option. Target injection is for trusted local tests and
maintenance code only; even injected targets must retain loopback/ID/path rules.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
import time


@dataclass(frozen=True)
class Target:
    application_id: str
    host: str
    port: int
    path: str


TARGETS = (Target('hr-bot', '127.0.0.1', 8012, '/health/ready'),
           Target('ai-admin-agent', '127.0.0.1', 8011, '/health'))
PATHS = {'hr-bot': '/health/ready', 'ai-admin-agent': '/health'}
MAX_BODY_BYTES = 16_384
MAX_WIRE_BYTES = 32_768
TIMEOUT_SECONDS = 2.0


class PayloadLimit(ValueError):
    pass


class DeadlineSocket(socket.socket):
    """Each low-level receive uses the remaining *total* deadline, not idle time."""
    def __init__(self, deadline):
        super().__init__(socket.AF_INET, socket.SOCK_STREAM)
        self.deadline = deadline
        self.received = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.settimeout(remaining)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self.remaining()
        count = super().recv_into(buffer, nbytes, flags)
        self.received += count
        if self.received > MAX_WIRE_BYTES:
            raise PayloadLimit
        return count


class LoopbackConnection(http.client.HTTPConnection):
    def __init__(self, target, deadline):
        super().__init__(target.host, target.port)
        self.deadline = deadline

    def connect(self):
        # No URL resolver, environment proxy, redirect handler, or DNS lookup.
        self.sock = DeadlineSocket(self.deadline)
        self.sock.remaining()
        self.sock.connect((self.host, self.port))
        self.sock.remaining()


def validate_targets(targets):
    if len(targets) != 2 or {target.application_id for target in targets} != set(PATHS):
        raise ValueError('trusted_target_invalid')
    for target in targets:
        if (target.host != '127.0.0.1' or type(target.port) is not int
                or not 1 <= target.port <= 65535 or target.path != PATHS[target.application_id]):
            raise ValueError('trusted_target_invalid')


def observe(target, timeout_seconds):
    state, error, version = 'unknown', None, None
    connection = LoopbackConnection(target, time.monotonic() + timeout_seconds)
    try:
        connection.request('GET', target.path, headers={'Accept': 'application/json', 'Connection': 'close'})
        with connection.getresponse() as response:
            if 300 <= response.status < 400:
                error = 'health_redirect'
            elif response.status != 200:
                state = 'degraded' if response.status >= 500 else 'unknown'
                error = 'health_http_error'
            else:
                raw = response.read(MAX_BODY_BYTES + 1)
                if len(raw) > MAX_BODY_BYTES:
                    raise PayloadLimit
                body = json.loads(raw)
                ready = isinstance(body, dict) and (body.get('ready') is True
                    if target.application_id == 'hr-bot' else body.get('status') == 'ok')
                state = 'healthy' if ready else 'degraded'
                error = None if ready else 'health_not_ready'
                if ready and target.application_id == 'hr-bot':
                    reported = body.get('release_sha')
                    if isinstance(reported, str) and re.fullmatch('[0-9a-f]{40}', reported):
                        version = reported
    except PayloadLimit:
        error = 'health_payload_limit'
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        error = 'health_invalid_json'
    except ConnectionRefusedError:
        state, error = 'offline', 'health_connection_refused'
    except TimeoutError:
        error = 'health_timeout'
    except (OSError, http.client.HTTPException, ValueError):
        error = 'health_unavailable'
    finally:
        connection.close()
    return {'application_id': target.application_id, 'state': state,
            'observed_at': datetime.now(timezone.utc).isoformat(), 'error': error, 'version': version}


def _check_destination(directory_fd, name):
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError('unsafe_output')


def atomic_write(path, document):
    """Pin owned directory; publish only a complete 0644 file and fsync both."""
    path = Path(path)
    # StateDirectory may intentionally be a systemd-created /var/lib/private link.
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    temporary = None
    try:
        metadata = os.fstat(directory_fd)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise ValueError('unsafe_output_directory')
        _check_destination(directory_fd, path.name)
        candidate = '.' + path.name + '.' + secrets.token_hex(16)
        descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=directory_fd)
        temporary = candidate  # Cleanup begins only after exclusive ownership succeeded.
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write((json.dumps(document, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8'))
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        _check_destination(directory_fd, path.name)
        os.replace(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory_fd)


def run_cycle(path, *, targets=None, timeout_seconds=TIMEOUT_SECONDS):
    configured = tuple(TARGETS if targets is None else targets)
    validate_targets(configured)
    if not 0 < timeout_seconds <= TIMEOUT_SECONDS:
        raise ValueError('timeout_invalid')
    observations = [observe(target, timeout_seconds) for target in configured]
    document = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
                'observations': observations}
    atomic_write(path, document)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'runtime_observations_arguments_invalid\n')


def main(argv=None):
    parser = SafeArgumentParser(description='Write safe fixed-loopback runtime metadata.')
    parser.add_argument('--output', type=Path, default=Path('/var/lib/orbbec-platform-runtime/snapshot.json'))
    args = parser.parse_args(argv)
    try:
        run_cycle(args.output)
    except (OSError, ValueError):
        print('runtime_observations_write_failed', file=sys.stderr)
        return 1
    print('runtime_observations_written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
