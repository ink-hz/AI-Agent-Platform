"""Execute the deployment rollback filter without Docker or production access."""
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / 'deploy/cloud/remote-stage.sh'


def function_source():
    text = SCRIPT.read_text()
    return 'filter_rollback_consumers() {' + text.split('filter_rollback_consumers() {', 1)[1].split('\nrollback() {', 1)[0]


@pytest.mark.parametrize('started,expected', [
    ('0', ['platform-api', 'platform-attachments', 'platform-brain']),
    ('1', ['platform-api', 'platform-brain']),
    ('unknown', ['platform-api', 'platform-brain']),
])
def test_rollback_does_not_restart_old_erasure_after_possible_migration(started, expected):
    source = function_source()
    script = source + '\nprevious_control_consumers=(platform-api platform-attachments platform-brain)\n'
    if started != 'unknown':
        script += f'control_migrations_started={started}\n'
    script += 'filter_rollback_consumers\nprintf "%s\\n" "${previous_control_consumers[@]}"\n'
    result = subprocess.run(['bash', '-eu', '-c', script], capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == expected
    assert ('ATTACHMENT_WORKER_REMAINS_STOPPED' in result.stderr) == (started != '0')


def test_filter_is_wired_before_restart_and_marked_before_migration():
    text = SCRIPT.read_text()
    rollback = text.split('\nrollback() {', 1)[1].split('\ntrap rollback EXIT', 1)[0]
    assert rollback.index('filter_rollback_consumers') < rollback.index('"${previous_compose[@]}" up -d')
    assert text.index('control_migrations_started=1') < text.index('control_bootstrap_result=')
    assert text.index('control_migrations_started=0') < text.index('trap rollback EXIT')
